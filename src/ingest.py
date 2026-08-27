#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context-DB 적재기 (ingest.py)

설계: .omc/specs/deep-interview-context-db-detailed-design.md §7

기능:
  1) 메신저 채팅 로그(<채널>/<YYYY-MM-DD>.txt)를 파싱해 context_item으로 적재
     (현재는 하이웍스 채팅 저장 포맷만 지원)
  2) 웹 문서 / 받은파일을 링크·메타데이터만 등록 (본문 추출 없음)

특징:
  - 채널 폴더 = SOURCE(messenger) 1개, 자연키 (source_type_id, name) 기준 멱등
  - project는 신규 소스에만 부여(기본 '미분류'), 기존 소스의 project는 건드리지 않음
  - context_item 은 external_id=sha1(채널|event_ts|발화자|내용) + UNIQUE(source_id,external_id) 로 멱등
  - FTS는 트리거가 자동 동기화 → 별도 처리 불필요
  - 커넥션마다 PRAGMA foreign_keys=ON, 파일 단위 트랜잭션

사용 예:
  python ingest.py --db context.db \
      --chat-root "<context-DB-path>/메신저 채팅저장" \
      --files-root "<context-DB-path>/메신저 받은파일" \
      --webdoc "https://example.com/doc/xxx" --webdoc-title "공유 문서"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime

# DB 접근은 전부 db.py 경유(FK 강제·스키마 버전 검사가 그 한 곳에 있다).
# connect/DEFAULT_SCHEMA 는 기존 호출부 호환을 위해 여기서도 그대로 노출한다.
from db import (  # noqa: F401
    DEFAULT_DB,
    DEFAULT_PROJECT,
    DEFAULT_SCHEMA,
    ItemType,
    SourceType,
    connect,
)

# 콘솔 인코딩(Windows cp949) 문제 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# [2026-07-24 오후 2:12] 홍길동
HEADER_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (오전|오후) (\d{1,2}):(\d{2})\] (.+)$")

# 시스템/다운로드 알림
SYSTEM_RE = re.compile(r"다운로드를 완료했습니다\.?$|님이 .+ 다운로드")
# 첨부 파일명 단독 라인 (확장자로 끝나는 단일 라인)
FILE_LINE_RE = re.compile(
    r"^[^\n]+\.(pdf|pptx?|docx?|hwp|xlsx?|zip|7z|png|jpe?g|gif|txt|csv|md|py|ipynb|html?)$",
    re.IGNORECASE,
)


# ─────────────────────────── DB 헬퍼 ───────────────────────────
# connect() 는 db.py 로 이관됨(위 import 에서 재노출).


def get_or_create_project(con, name: str) -> int:
    con.execute("INSERT OR IGNORE INTO project(name) VALUES (?)", (name,))
    return con.execute("SELECT project_id FROM project WHERE name=?", (name,)).fetchone()[0]


def source_type_id(con, code: str) -> int:
    row = con.execute("SELECT source_type_id FROM source_type WHERE code=?", (code,)).fetchone()
    if row is None:
        raise ValueError(f"알 수 없는 source_type code: {code} (schema.sql 시드 확인)")
    return row[0]


def upsert_source(con, type_code: str, name: str, uri: str, project_name: str) -> int:
    """자연키 (source_type_id, name)로 조회/삽입. 기존이면 project는 유지(리뷰 Issue 1).

    휘발성 여부는 source_type 이 결정하므로 여기서 받지 않는다.
    """
    st_id = source_type_id(con, type_code)
    row = con.execute(
        "SELECT source_id FROM source WHERE source_type_id=? AND name=?", (st_id, name)
    ).fetchone()
    if row:
        return row[0]
    pid = get_or_create_project(con, project_name)
    cur = con.execute(
        "INSERT INTO source(project_id, source_type_id, name, uri) VALUES (?,?,?,?)",
        (pid, st_id, name, uri),
    )
    return cur.lastrowid


def upsert_person(con, display_name: str):
    if not display_name:
        return None
    con.execute("INSERT OR IGNORE INTO person(display_name) VALUES (?)", (display_name,))
    return con.execute(
        "SELECT person_id FROM person WHERE display_name=?", (display_name,)
    ).fetchone()[0]


def add_link_once(con, url: str, title: str, source_id=None, context_item_id=None,
                  last_checked_at=None):
    """(부착 대상, url) 중복 없으면 삽입 → 멱등.

    중복 판정은 schema.sql 의 부분 유니크 인덱스(ux_link_item / ux_link_source)가 한다.
    한 statement 는 충돌 대상을 하나만 지정할 수 있으므로 아크별로 분기한다.
    OR IGNORE 를 쓰지 않는 이유는 그것이 배타적 아크 CHECK 위반까지 삼키기 때문이다.
    """
    if (context_item_id is None) == (source_id is None):
        raise ValueError("link 는 context_item_id 또는 source_id 중 정확히 하나에 부착해야 합니다")
    # 부분 인덱스를 conflict target 으로 지정하려면 인덱스의 WHERE 절까지 같이 적어야
    # SQLite 가 매칭한다(생략하면 OperationalError: ... does not match any ... UNIQUE constraint).
    params = (context_item_id, source_id, url, title, last_checked_at)
    if context_item_id is not None:
        con.execute(
            "INSERT INTO link(context_item_id, source_id, url, title, last_checked_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(context_item_id, url) WHERE context_item_id IS NOT NULL "
            "DO NOTHING", params)
    else:
        con.execute(
            "INSERT INTO link(context_item_id, source_id, url, title, last_checked_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(source_id, url) WHERE source_id IS NOT NULL "
            "DO NOTHING", params)


# ─────────────────────────── 파서 ───────────────────────────
def to_24h(ampm: str, hh: int) -> int:
    if ampm == "오전":
        return 0 if hh == 12 else hh
    return 12 if hh == 12 else hh + 12  # 오후


def read_text(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with open(path, encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def classify(content: str) -> str:
    if SYSTEM_RE.search(content):
        return ItemType.SYSTEM
    if "\n" not in content and FILE_LINE_RE.match(content.strip()):
        return ItemType.FILE
    return ItemType.MESSAGE


def parse_chat_file(path: str):
    """(event_ts, speaker, content, item_type) 튜플을 yield."""
    lines = read_text(path).splitlines()
    cur = None  # dict(ts, speaker, body[])
    for line in lines:
        m = HEADER_RE.match(line)
        if m:
            if cur is not None:
                yield _finalize(cur)
            date, ampm, hh, mm, speaker = m.group(1), m.group(2), int(m.group(3)), m.group(4), m.group(5)
            h24 = to_24h(ampm, hh)
            cur = {"ts": f"{date} {h24:02d}:{mm}:00", "speaker": speaker.strip(), "body": []}
        else:
            if cur is not None:
                cur["body"].append(line)
    if cur is not None:
        yield _finalize(cur)


def _finalize(cur: dict):
    content = "\n".join(cur["body"]).strip("\n").rstrip()
    if content == "":
        content = "(내용 없음)"
    return cur["ts"], cur["speaker"], content, classify(content)


# ─────────────────────────── 적재 루틴 ───────────────────────────
def ingest_chat_root(con, chat_root: str, project_name: str) -> dict:
    stats = {"channels": 0, "files": 0, "inserted": 0, "skipped": 0}
    if not os.path.isdir(chat_root):
        print(f"[경고] 채팅 루트 없음: {chat_root}")
        return stats
    for channel in sorted(os.listdir(chat_root)):
        cdir = os.path.join(chat_root, channel)
        if not os.path.isdir(cdir):
            continue
        txts = sorted(f for f in os.listdir(cdir) if f.lower().endswith(".txt"))
        if not txts:
            continue
        stats["channels"] += 1
        src_id = upsert_source(con, SourceType.MESSENGER, channel, cdir, project_name)
        for txt in txts:
            fpath = os.path.join(cdir, txt)
            stats["files"] += 1
            for ts, speaker, content, item_type in parse_chat_file(fpath):
                pid = upsert_person(con, speaker)
                ext = hashlib.sha1(
                    f"{channel}|{ts}|{speaker}|{content}".encode("utf-8")
                ).hexdigest()
                thread_key = f"{channel}|{txt[:-4]}"
                # OR IGNORE 를 쓰지 않는다 — UNIQUE 뿐 아니라 NOT NULL·CHECK 위반까지
                # 삼켜서 "조용한 중복"을 "조용한 유실"로 바꿔놓는다. 충돌 대상을 명시한다.
                cur = con.execute(
                    "INSERT INTO context_item"
                    "(source_id, person_id, item_type, event_ts, content, thread_key, external_id) "
                    "VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(source_id, external_id) DO NOTHING",
                    (src_id, pid, item_type, ts, content, thread_key, ext),
                )
                # rowcount는 실제 삽입 1 / OR IGNORE 무시 0 (FTS 트리거 행 미포함) — 정확
                if cur.rowcount == 1:
                    stats["inserted"] += 1
                else:
                    stats["skipped"] += 1
            con.commit()  # 파일 단위 트랜잭션
    return stats


def ingest_files_root(con, files_root: str, project_name: str) -> int:
    if not files_root or not os.path.isdir(files_root):
        return 0
    src_id = upsert_source(con, SourceType.FILE, "메신저 받은파일", files_root, project_name)
    n = 0
    for entry in os.scandir(files_root):
        if entry.is_file():
            mtime = datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            add_link_once(con, url=entry.path, title=entry.name, source_id=src_id,
                          last_checked_at=mtime)
            n += 1
    con.commit()
    return n


def ingest_webdoc(con, url: str, title: str, project_name: str) -> None:
    if not url:
        return
    src_id = upsert_source(con, SourceType.WEB_DOC, title or "공유 문서", url, project_name)
    add_link_once(con, url=url, title=title or "공유 문서", source_id=src_id)
    con.commit()


# ─────────────────────────── main ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="context-DB 적재기")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--chat-root", default=None, help="메신저 채팅저장 루트 폴더")
    ap.add_argument("--files-root", default=None, help="메신저 받은파일 폴더(메타만)")
    ap.add_argument("--webdoc", default=None, help="웹 문서 URL(링크만)")
    ap.add_argument("--webdoc-title", default="공유 문서")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="신규 소스에 부여할 프로젝트명")
    args = ap.parse_args()

    con = connect(args.db, args.schema)
    try:
        if args.chat_root:
            s = ingest_chat_root(con, args.chat_root, args.project)
            print(f"[채팅] 채널 {s['channels']} · 파일 {s['files']} · "
                  f"신규 {s['inserted']} · 중복무시 {s['skipped']}")
        if args.files_root:
            n = ingest_files_root(con, args.files_root, args.project)
            print(f"[받은파일] 링크 {n}건 등록")
        if args.webdoc:
            ingest_webdoc(con, args.webdoc, args.webdoc_title, args.project)
            print(f"[웹 문서] 1건 등록: {args.webdoc_title}")

        total = con.execute("SELECT count(*) FROM context_item").fetchone()[0]
        srcs = con.execute("SELECT count(*) FROM source").fetchone()[0]
        ppl = con.execute("SELECT count(*) FROM person").fetchone()[0]
        print(f"[요약] context_item {total} · source {srcs} · person {ppl}  → DB: {args.db}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
