#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context-DB 공용 DB 접근 계층 (db.py)

모든 커넥션은 이 모듈의 connect() 를 통과한다. FK 강제(PRAGMA foreign_keys)와
스키마 버전 검사가 한 곳에만 있어야 우회 경로가 생기지 않는다.
schema.sql 시드와 중복되는 코드 문자열도 여기 상수로 모은다.
"""
from __future__ import annotations

import os
import re
import sqlite3

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
DEFAULT_SCHEMA = os.path.join(SRC_DIR, "schema.sql")
DEFAULT_DB = os.path.join(ROOT_DIR, "context.db")

# schema.sql 의 `PRAGMA user_version` 값과 반드시 일치해야 한다.
# 스키마를 구조적으로 바꿀 때마다 양쪽을 함께 올린다.
SCHEMA_VERSION = 8

# 버전 도입 이전(user_version=0)에 만들어진 DB를 현행으로 인정하는 값.
# SCHEMA_VERSION 이 1을 넘어가는 순간 이 경로는 자동으로 닫히고,
# 낡은 DB는 조용히 통과하는 대신 SchemaVersionError 로 실패한다.
_LEGACY_ADOPT_VERSION = 1

DEFAULT_PROJECT = "미분류"


class SourceType:
    """source_type.code 시드값 (schema.sql)."""

    MESSENGER = "messenger"
    WEB_DOC = "web_doc"
    WEB_LINK = "web_link"
    PAPER = "paper"
    SERVER_INFO = "server_info"
    FILE = "file"
    NOTE = "note"


class ItemType:
    """item_type.code 시드값 (schema.sql). context_item 은 item_type_id 로 참조한다."""

    MESSAGE = "message"
    NOTE = "note"
    EXCERPT = "excerpt"
    SYSTEM = "system"
    FILE = "file"


class SchemaVersionError(RuntimeError):
    """DB의 스키마 버전이 코드가 기대하는 버전과 다를 때."""


# ─────────────────────────── 경로 토큰 ───────────────────────────
# source.uri / link.url 은 머신 고유 절대경로 대신 루트 토큰으로 저장한다.
#   {chat_root}/AI 플랫폼 팀   ·   {files_root}/보고서.pdf   ·   https://... (그대로)
# DB 를 다른 PC 로 옮겨도 그 PC 의 config 로 해소되므로 경로가 죽지 않는다.
# skill 배포의 <context-DB-path> 자리표시자와 같은 관용구다.
PATH_TOKENS = ("chat_root", "files_root")

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def is_url(value: str) -> bool:
    """스킴이 붙은 URL 이면 True. 경로 토큰화 대상에서 제외된다."""
    return bool(value) and bool(_SCHEME_RE.match(value))


def _norm(p: str) -> str:
    """구분자를 / 로 정규화하고 끝의 구분자를 제거한다."""
    return p.replace("\\", "/").rstrip("/")


def pack_path(value: str, cfg: dict) -> str:
    """절대경로를 루트 토큰 표기로 바꾼다. URL·미매칭 경로는 그대로 둔다.

    가장 긴 루트가 우선한다(한 루트가 다른 루트의 하위일 때 올바르게 잡히도록).
    """
    if not value or is_url(value):
        return value
    v = _norm(value)
    roots = sorted(
        ((k, _norm(str(cfg[k]))) for k in PATH_TOKENS if cfg.get(k)),
        key=lambda kv: len(kv[1]), reverse=True,
    )
    for key, root in roots:
        if v == root:
            return "{%s}" % key
        if v.startswith(root + "/"):
            return "{%s}/%s" % (key, v[len(root) + 1:])
    return v


def resolve_path(value: str, cfg: dict) -> str:
    """루트 토큰 표기를 실제 절대경로로 되돌린다.

    config 에 해당 루트가 없으면 토큰을 그대로 노출한다 — 조용히 빈 문자열로 만들면
    "루트 미설정"과 "파일이 최상위에 있음"을 구분할 수 없게 된다.
    """
    if not value or is_url(value):
        return value
    for key in PATH_TOKENS:
        token = "{%s}" % key
        if value == token or value.startswith(token + "/"):
            root = cfg.get(key)
            if not root:
                return value
            rest = value[len(token):].lstrip("/")
            return _norm(root) + ("/" + rest if rest else "")
    return value


# ─────────────────────────── 프로젝트 규칙 ───────────────────────────
def resolve_project(name: str, rules, default: str = DEFAULT_PROJECT) -> str:
    """소스 이름으로 프로젝트를 정한다. 규칙은 **순서대로, 먼저 맞는 것이 이긴다**.

    매칭은 대소문자 무시 **부분 문자열**이다. glob 을 쓰지 않는 이유가 있다:
    채널명에 대괄호가 실제로 들어가는데(`[피지컬 AI]`), fnmatch 는 `[...]` 를 문자
    클래스로 해석해서 `"[피지컬 AI]*"` 같은 패턴이 **아무것도 매칭하지 않는다**.
    게다가 조용히 실패한다 — 이 기능이 고치려는 버그와 정확히 같은 실패 형태다.

    rules: [{"match": "피지컬", "project": "피지컬AI"}, ...]
    """
    if not name or not rules:
        return default
    low = name.lower()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        needle = (rule.get("match") or "").strip()
        project = (rule.get("project") or "").strip()
        if needle and project and needle.lower() in low:
            return project
    return default


def _apply_schema(con: sqlite3.Connection, schema_path: str) -> None:
    with open(schema_path, encoding="utf-8") as fh:
        con.executescript(fh.read())
    # executescript 는 커넥션을 새로 열지 않지만, 스크립트 안의 PRAGMA 가
    # 커넥션 상태를 덮어쓸 수 있으므로 FK 강제를 다시 확정한다.
    con.execute("PRAGMA foreign_keys = ON")
    con.commit()


def _check_version(con: sqlite3.Connection, db_path: str) -> None:
    v = con.execute("PRAGMA user_version").fetchone()[0]
    if v == SCHEMA_VERSION:
        return
    if v == 0 and SCHEMA_VERSION == _LEGACY_ADOPT_VERSION:
        # 버전 도입 이전에 만들어진 DB. 구조는 현행과 동일하므로 도장만 찍는다.
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
        return
    raise SchemaVersionError(
        f"DB 스키마 버전 불일치: {db_path} 는 v{v}, 코드는 v{SCHEMA_VERSION} 을 기대합니다.\n"
        f"\n"
        f"  ** 이 DB 를 삭제하지 마세요. ** 메신저 원본 로그는 약 1개월 후 사라지므로,\n"
        f"  이 DB 에만 남아 있는 맥락이 상당수입니다(ADR-017). 재적재로 복원되지 않습니다.\n"
        f"\n"
        f"  1) 먼저 백업:  context-db backup\n"
        f"  2) 그다음 조치를 결정하세요 — 코드를 이 DB 에 맞는 버전으로 되돌리거나,\n"
        f"     스키마 변경분을 이 DB 에 적용하는 마이그레이션이 필요합니다.\n"
        f"     (자동 마이그레이션 경로는 아직 없습니다. ADR §7 참조)"
    )


def backup(db_path: str, dest: str) -> tuple:
    """SQLite 온라인 백업 API 로 DB 를 통째로 복사한다.

    파일 복사가 아니라 백업 API 를 쓰는 이유: watch/스케줄러가 동시에 쓰고 있어도
    일관된 스냅샷이 나온다. 파일 복사는 쓰기 도중이면 깨진 사본을 만들 수 있다.

    returns: (경로, 바이트 수, context_item 건수)
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    src = sqlite3.connect(f"file:{db_path.replace(os.sep, '/')}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
            n = dst.execute("SELECT count(*) FROM context_item").fetchone()[0]
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise RuntimeError(f"백업본 무결성 검사 실패: {ok}")
        finally:
            dst.close()
    finally:
        src.close()
    return dest, os.path.getsize(dest), n


def connect(db_path: str, schema_path: str | None = None) -> sqlite3.Connection:
    """FK 강제 + 스키마 적용 + 버전 검증을 마친 커넥션을 돌려준다.

    스키마가 아직 없으면 schema_path 를 적용한다(멱등 — 전 DDL이 IF NOT EXISTS).
    낡은 스키마의 DB는 첫 INSERT 가 아니라 여기서 즉시 실패한다.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='context_item'"
    ).fetchone()
    if row is None:
        _apply_schema(con, schema_path or DEFAULT_SCHEMA)
    _check_version(con, db_path)
    return con
