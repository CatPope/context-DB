#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context-DB 테스트 스위트 (결정적 합성 데이터 기반)

실행: python tests/test_context_db.py
- 임시 폴더에 가짜 채팅 로그를 만들고 파서/멱등성/FTS/회귀/CLI를 검증한다.
- 실제 context.db 는 건드리지 않는다.
"""
from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ingest as ing  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


CHANNEL = "테스트채널A"
LOG = """[2026-08-01 오전 12:05] 김철수
안녕하세요

여러 줄 메시지입니다
[2026-08-01 오후 12:30] 이영희
점심 뭐 드셨어요?
[2026-08-01 오후 1:00] 김철수
(이모티콘)
[2026-08-01 오후 1:01] 김철수
report.pdf
[2026-08-01 오후 1:02] 이영희
이영희님이 report.pdf 다운로드를 완료했습니다.
[2026-08-01 오후 1:05] 김철수
ㅇㅇ
[2026-08-01 오후 1:05] 김철수
ㅇㅇ
"""


def main():
    tmp = tempfile.mkdtemp(prefix="ctxdb_test_")
    chat_root = os.path.join(tmp, "chat")
    cdir = os.path.join(chat_root, CHANNEL)
    os.makedirs(cdir)
    with open(os.path.join(cdir, "2026-08-01.txt"), "w", encoding="utf-8") as fh:
        fh.write(LOG)
    db = os.path.join(tmp, "test.db")

    print("== 1. 파서/적재 ==")
    con = ing.connect(db, ing.DEFAULT_SCHEMA)
    stats = ing.ingest_chat_root(con, chat_root, "테스트프로젝트")
    # 7개 블록 중 마지막 'ㅇㅇ' 2건이 동일(ts/speaker/content) → dedup → 6행
    check("신규 6건(중복 1 제외)", stats["inserted"] == 6 and stats["skipped"] == 1,
          f"inserted={stats['inserted']}, skipped={stats['skipped']}")
    rows = con.execute("SELECT count(*) FROM context_item").fetchone()[0]
    check("context_item 6행", rows == 6, f"rows={rows}")

    print("== 2. 시각 변환(오전/오후 12시간→24시간) ==")
    ts = [r[0] for r in con.execute("SELECT event_ts FROM context_item ORDER BY event_ts")]
    check("오전 12:05 → 00:05", "2026-08-01 00:05:00" in ts, ts)
    check("오후 12:30 → 12:30", "2026-08-01 12:30:00" in ts, ts)
    check("오후 1:00 → 13:00", "2026-08-01 13:00:00" in ts, ts)

    print("== 3. 멀티라인 본문 ==")
    ml = con.execute("SELECT content FROM context_item WHERE event_ts='2026-08-01 00:05:00'").fetchone()[0]
    check("멀티라인 결합(줄바꿈 포함)", "\n" in ml and "여러 줄 메시지입니다" in ml, repr(ml))

    print("== 4. item_type 분류 ==")
    types = dict(con.execute("SELECT item_type, count(*) FROM context_item GROUP BY item_type").fetchall())
    check("system 1건(다운로드 알림)", types.get("system") == 1, types)
    check("file 1건(report.pdf)", types.get("file") == 1, types)
    check("message 4건", types.get("message") == 4, types)

    print("== 5. FTS 동기화/검색 ==")
    fts_n = con.execute("SELECT count(*) FROM context_fts").fetchone()[0]
    check("FTS 행수 == context_item", fts_n == rows, f"fts={fts_n}, rows={rows}")
    # 표준 토큰 매치 ('점심 뭐 드셨어요?'의 토큰 '점심')
    m = con.execute("SELECT ci.content FROM context_fts f JOIN context_item ci "
                    "ON ci.context_item_id=f.rowid WHERE context_fts MATCH '점심'").fetchall()
    check("FTS 검색 '점심' 매치", len(m) == 1, m)

    print("== 5b. 한국어 토큰화 특성(문서화된 동작) ==")
    # '안녕'은 '안녕하세요' 토큰의 부분문자열 → 기본 토크나이저에서 미매치
    sub = con.execute("SELECT count(*) FROM context_fts WHERE context_fts MATCH '안녕'").fetchone()[0]
    pre = con.execute("SELECT count(*) FROM context_fts WHERE context_fts MATCH '안녕*'").fetchone()[0]
    check("부분문자열 '안녕' 미매치(토큰 경계)", sub == 0, f"sub={sub}")
    check("접두어 '안녕*' 매치", pre == 1, f"pre={pre}")

    print("== 5c. FTS 트리거(임시 행, 원본 6행 불변) ==")
    tcur = con.execute("INSERT INTO context_item(source_id,item_type,event_ts,content,external_id) "
                       "SELECT source_id,'message','2026-08-01 23:59:00','임시검색어ZZZ','__tmp__' "
                       "FROM source LIMIT 1")
    tid = tcur.lastrowid
    con.commit()
    ai = con.execute("SELECT count(*) FROM context_fts WHERE context_fts MATCH '임시검색어ZZZ'").fetchone()[0]
    check("INSERT 트리거 반영", ai == 1, f"ai={ai}")
    con.execute("UPDATE context_item SET content='임시변경어ZZZ' WHERE context_item_id=?", (tid,))
    con.commit()
    au_old = con.execute("SELECT count(*) FROM context_fts WHERE context_fts MATCH '임시검색어ZZZ'").fetchone()[0]
    au_new = con.execute("SELECT count(*) FROM context_fts WHERE context_fts MATCH '임시변경어ZZZ'").fetchone()[0]
    check("UPDATE 트리거 반영(구0/신1)", au_old == 0 and au_new == 1, f"old={au_old}, new={au_new}")
    con.execute("DELETE FROM context_item WHERE context_item_id=?", (tid,))
    con.commit()
    dn = con.execute("SELECT count(*) FROM context_fts").fetchone()[0]
    check("DELETE 트리거 반영(원본 6행 복귀)", dn == 6, f"fts={dn}")

    print("== 6. 멱등 재적재 ==")
    s2 = ing.ingest_chat_root(con, chat_root, "테스트프로젝트")
    check("재적재 신규 0", s2["inserted"] == 0, f"inserted={s2['inserted']}")

    print("== 7. 회귀: project 재매핑 후 이중적재 없음(Issue 1) ==")
    pid = ing.get_or_create_project(con, "다른프로젝트")
    st = ing.source_type_id(con, "messenger")
    con.execute("UPDATE source SET project_id=? WHERE source_type_id=? AND name=?", (pid, st, CHANNEL))
    con.commit()
    s3 = ing.ingest_chat_root(con, chat_root, "테스트프로젝트")
    src_cnt = con.execute("SELECT count(*) FROM source WHERE name=?", (CHANNEL,)).fetchone()[0]
    check("재매핑 후 source 1개 유지", src_cnt == 1, f"sources={src_cnt}")
    check("재매핑 후 재적재 신규 0", s3["inserted"] == 0, f"inserted={s3['inserted']}")

    print("== 8. 무결성 ==")
    orphan = con.execute("SELECT count(*) FROM context_item ci LEFT JOIN source s "
                         "ON s.source_id=ci.source_id WHERE s.source_id IS NULL").fetchone()[0]
    check("고아 context_item 0", orphan == 0, f"orphan={orphan}")

    print("== 10. 인코딩 폴백(cp949 / utf-8-sig BOM) ==")
    cp_dir = os.path.join(chat_root, "CP949채널")
    os.makedirs(cp_dir)
    with open(os.path.join(cp_dir, "2026-08-02.txt"), "w", encoding="cp949") as fh:
        fh.write("[2026-08-02 오전 9:00] 박인코\n한글cp949정상표시\n")
    bom_dir = os.path.join(chat_root, "BOM채널")
    os.makedirs(bom_dir)
    with open(os.path.join(bom_dir, "2026-08-02.txt"), "w", encoding="utf-8-sig") as fh:
        fh.write("[2026-08-02 오전 9:01] 김봄\nBOM테스트내용\n")
    ing.ingest_chat_root(con, chat_root, "테스트프로젝트")
    cp_c = con.execute("SELECT ci.content FROM context_item ci JOIN source s ON s.source_id=ci.source_id "
                       "WHERE s.name='CP949채널'").fetchone()
    check("cp949 로그 정상 디코딩", cp_c and cp_c[0] == "한글cp949정상표시", cp_c)
    bom_c = con.execute("SELECT ci.content FROM context_item ci JOIN source s ON s.source_id=ci.source_id "
                        "WHERE s.name='BOM채널'").fetchone()
    check("utf-8-sig BOM 제거", bom_c and bom_c[0] == "BOM테스트내용" and not bom_c[0].startswith("﻿"), bom_c)

    print("== 11. 받은파일/구글독스 적재 + 링크 멱등 ==")
    files_dir = os.path.join(tmp, "files")
    os.makedirs(files_dir)
    for fn in ("a.pdf", "b.txt"):
        open(os.path.join(files_dir, fn), "w").close()
    ing.ingest_files_root(con, files_dir, "테스트프로젝트")
    ing.ingest_files_root(con, files_dir, "테스트프로젝트")  # 재실행(멱등)
    flink = con.execute("SELECT count(*) FROM link l JOIN source s ON s.source_id=l.source_id "
                        "WHERE s.name='하이웍스 받은파일'").fetchone()[0]
    check("받은파일 링크 2건(재실행 멱등)", flink == 2, f"links={flink}")
    ing.ingest_gdoc(con, "https://docs.google.com/document/d/TEST/edit", "테스트문서", "테스트프로젝트")
    ing.ingest_gdoc(con, "https://docs.google.com/document/d/TEST/edit", "테스트문서", "테스트프로젝트")
    gsrc = con.execute("SELECT count(*) FROM source WHERE source_type_id=(SELECT source_type_id "
                       "FROM source_type WHERE code='google_doc')").fetchone()[0]
    glink = con.execute("SELECT count(*) FROM link WHERE url='https://docs.google.com/document/d/TEST/edit'").fetchone()[0]
    check("구글독스 source 1개(멱등)", gsrc == 1, f"gsrc={gsrc}")
    check("구글독스 링크 1건(멱등)", glink == 1, f"glink={glink}")

    print("== 12. 제약 강제(FK/CHECK) ==")
    sid = con.execute("SELECT source_id FROM source WHERE name=?", (CHANNEL,)).fetchone()[0]
    check("FK 강제: 없는 source_id 거부",
          expect_fail(con, "INSERT INTO context_item(source_id,content,external_id) VALUES (999999,'x','fk')"))
    check("CHECK item_type 거부",
          expect_fail(con, "INSERT INTO context_item(source_id,item_type,content,external_id) "
                           "VALUES (?, 'bogus','x','ck1')", (sid,)))
    check("CHECK link 귀속 없음 거부",
          expect_fail(con, "INSERT INTO link(url) VALUES ('http://x')"))
    check("CHECK is_ephemeral 거부",
          expect_fail(con, "INSERT INTO source(project_id,source_type_id,name,is_ephemeral) "
                           "VALUES (1,1,'ck채널',5)"))

    print("== 13. 뷰 동작 ==")
    check("v_project_sources 반환", con.execute("SELECT count(*) FROM v_project_sources").fetchone()[0] >= 1)
    check("v_recent_context 반환", con.execute("SELECT count(*) FROM v_recent_context").fetchone()[0] >= 1)
    cid = con.execute("SELECT context_item_id FROM context_item LIMIT 1").fetchone()[0]
    con.execute("INSERT OR IGNORE INTO tag(name) VALUES ('뷰태그')")
    con.execute("INSERT OR IGNORE INTO context_item_tag(context_item_id,tag_id) "
                "SELECT ?, tag_id FROM tag WHERE name='뷰태그'", (cid,))
    con.commit()
    check("v_tag_links 반환", con.execute("SELECT count(*) FROM v_tag_links WHERE tag='뷰태그'").fetchone()[0] == 1)
    con.close()

    print("== 14. CLI 스모크(subprocess + returncode + --json) ==")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cli = os.path.join(ROOT, "cli.py")

    def run(*a):
        return subprocess.run([sys.executable, cli, "--db", db, *a],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def ok_json(r):
        return r.returncode == 0 and _is_json(r.stdout)

    r = run("stats", "--json")
    check("stats --json (rc=0, 유효)", ok_json(r), f"rc={r.returncode} {r.stderr[:80]}")
    r = run("search", "여러", "--json")
    check("search 결과 반환", ok_json(r) and len(json.loads(r.stdout)) >= 1, r.stdout[:120])
    r = run("search", "존재안함ZZZ", "--json")
    check("빈 검색 → 유효 [] ", ok_json(r) and json.loads(r.stdout) == [], r.stdout[:120])
    r = run("timeline", "--channel", CHANNEL, "--json")
    rows = json.loads(r.stdout) if ok_json(r) else None
    check("timeline --channel 필터", rows is not None and all(x["source"] == CHANNEL for x in rows), r.stdout[:120])
    r = run("links", "--type", "file", "--json")
    ln = json.loads(r.stdout) if ok_json(r) else None
    check("links --type file 필터", ln is not None and len(ln) >= 1 and all(x["type"] == "파일" for x in ln), r.stdout[:120])
    r = run("projects", "--json")
    check("projects --json", ok_json(r), r.stdout[:120])
    # set-project CLI 왕복
    run("set-project", CHANNEL, "재매핑테스트")
    r = run("sources", "--json")
    src = json.loads(r.stdout) if ok_json(r) else []
    check("set-project 반영", any(x["name"] == CHANNEL and x["project"] == "재매핑테스트" for x in src), r.stdout[:160])
    r = run("tag", "여러", "--add", "인사")
    r2 = run("by-tag", "인사", "--json")
    check("tag→by-tag 왕복", ok_json(r2) and len(json.loads(r2.stdout)) >= 1, r2.stdout[:120])

    print(f"\n결과: PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


def _is_json(s):
    try:
        json.loads(s)
        return True
    except Exception:
        return False


def expect_fail(con, sql, params=()):
    """제약 위반으로 IntegrityError 가 나면 True(기대대로 거부됨)."""
    try:
        con.execute(sql, params)
        con.commit()
        return False
    except sqlite3.IntegrityError:
        con.rollback()
        return True
    except Exception:
        con.rollback()
        return False


if __name__ == "__main__":
    main()
