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
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
import ingest as ing  # noqa: E402

PASS = 0
FAIL = 0


def _mk_item(con, source_id, **cols):
    """context_item 한 행 삽입 — 컬럼 목록을 이 함수 한 곳에 가둔다.

    스키마가 바뀌어도 테스트 호출부 N곳이 아니라 여기만 고치면 된다.
    """
    cols = {"source_id": source_id, **cols}
    names = ",".join(cols)
    ph = ",".join("?" * len(cols))
    cur = con.execute(f"INSERT INTO context_item({names}) VALUES ({ph})", tuple(cols.values()))
    con.commit()
    return cur.lastrowid


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
    types = dict(con.execute("SELECT t.code, count(*) FROM context_item ci "
                             "JOIN item_type t USING(item_type_id) GROUP BY t.code").fetchall())
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
    tsrc = con.execute("SELECT source_id FROM source LIMIT 1").fetchone()[0]
    tid = _mk_item(con, tsrc, item_type_id=ing.item_type_map(con)[ing.ItemType.MESSAGE],
                   event_ts="2026-08-01 23:59:00", content="임시검색어ZZZ",
                   external_id="__tmp__")
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

    print("== 11. 받은파일/웹 문서 적재 + 링크 멱등 ==")
    files_dir = os.path.join(tmp, "files")
    os.makedirs(files_dir)
    for fn in ("a.pdf", "b.txt"):
        open(os.path.join(files_dir, fn), "w").close()
    ing.ingest_files_root(con, files_dir, "테스트프로젝트")
    ing.ingest_files_root(con, files_dir, "테스트프로젝트")  # 재실행(멱등)
    flink = con.execute("SELECT count(*) FROM link l JOIN source s ON s.source_id=l.source_id "
                        "WHERE s.name='메신저 받은파일'").fetchone()[0]
    check("받은파일 링크 2건(재실행 멱등)", flink == 2, f"links={flink}")
    ing.ingest_webdoc(con, "https://example.com/doc/TEST", "테스트문서", "테스트프로젝트")
    ing.ingest_webdoc(con, "https://example.com/doc/TEST", "테스트문서", "테스트프로젝트")
    gsrc = con.execute("SELECT count(*) FROM source WHERE source_type_id=(SELECT source_type_id "
                       "FROM source_type WHERE code='web_doc')").fetchone()[0]
    glink = con.execute("SELECT count(*) FROM link WHERE url='https://example.com/doc/TEST'").fetchone()[0]
    check("웹 문서 source 1개(멱등)", gsrc == 1, f"gsrc={gsrc}")
    check("웹 문서 링크 1건(멱등)", glink == 1, f"glink={glink}")

    print("== 12. 제약 강제(FK/CHECK) ==")
    sid = con.execute("SELECT source_id FROM source WHERE name=?", (CHANNEL,)).fetchone()[0]
    # item_type_id 를 명시 공급한다. 생략하면 NOT NULL/DEFAULT 쪽이 먼저 걸려서
    # 이 테스트가 FK 가 아닌 다른 제약을 검증하게 되고, expect_fail 은 IntegrityError 를
    # 구분하지 않으므로 그 사실이 PASS 뒤에 숨는다.
    check("FK 강제: 없는 source_id 거부",
          expect_fail(con, "INSERT INTO context_item(source_id,item_type_id,content,external_id) "
                           "VALUES (999999,1,'x','fk')"))
    check("FK 강제: 없는 item_type_id 거부",
          expect_fail(con, "INSERT INTO context_item(source_id,item_type_id,content,external_id) "
                           "VALUES (?,999999,'x','ck1')", (sid,)))
    # ON CONFLICT 로 충돌 대상을 명시했으므로 NOT NULL 위반은 삼켜지지 않고 올라와야 한다.
    # (OR IGNORE 였다면 조용히 무시돼 행이 유실된다)
    check("ON CONFLICT 절이 external_id NOT NULL 위반을 삼키지 않음",
          expect_fail(con, "INSERT INTO context_item(source_id,content,external_id) "
                           "VALUES (?,'x',NULL) ON CONFLICT(source_id,external_id) DO NOTHING",
                      (sid,)))
    check("CHECK link 귀속 없음 거부",
          expect_fail(con, "INSERT INTO link(url) VALUES ('http://x')"))
    # link 중복 제거가 Python(IFNULL 흉내)이 아니라 DB 제약으로 강제되는가
    lcid = con.execute("SELECT context_item_id FROM context_item LIMIT 1").fetchone()[0]
    con.execute("INSERT INTO link(context_item_id,url) VALUES (?,'http://dup')", (lcid,))
    con.commit()
    check("부분 UNIQUE: 같은 (context_item_id,url) 재삽입 거부",
          expect_fail(con, "INSERT INTO link(context_item_id,url) VALUES (?,'http://dup')", (lcid,)))
    check("배타적 아크: 양쪽 동시 부착 거부",
          expect_fail(con, "INSERT INTO link(context_item_id,source_id,url) "
                           "VALUES (?,?,'http://both')", (lcid, sid)))
    # is_ephemeral 은 source 가 아니라 source_type 의 속성(3NF 이관)
    check("CHECK is_ephemeral 거부(source_type)",
          expect_fail(con, "INSERT INTO source_type(code,label,is_ephemeral) "
                           "VALUES ('ck','CK유형',5)"))

    print("== 12b. FK 참조 액션(CASCADE / SET NULL) ==")
    # context_item 삭제 → 접합행·링크가 따라 지워지는가
    cpid = ing.upsert_person(con, "삭제될사람")
    cid_c = _mk_item(con, sid, person_id=cpid, content="캐스케이드대상", external_id="__cascade__")
    con.execute("INSERT OR IGNORE INTO tag(name) VALUES ('캐스케이드태그')")
    ctid = con.execute("SELECT tag_id FROM tag WHERE name='캐스케이드태그'").fetchone()[0]
    con.execute("INSERT INTO context_item_tag(context_item_id,tag_id) VALUES (?,?)", (cid_c, ctid))
    con.execute("INSERT INTO link(context_item_id,url) VALUES (?,'http://cascade')", (cid_c,))
    con.commit()
    con.execute("DELETE FROM context_item WHERE context_item_id=?", (cid_c,))
    con.commit()
    left_t = con.execute("SELECT count(*) FROM context_item_tag WHERE context_item_id=?",
                         (cid_c,)).fetchone()[0]
    left_l = con.execute("SELECT count(*) FROM link WHERE context_item_id=?", (cid_c,)).fetchone()[0]
    check("context_item 삭제 → 접합행·link CASCADE", left_t == 0 and left_l == 0,
          f"tag={left_t}, link={left_l}")

    # person 삭제 → 맥락은 남고 발화자만 비워지는가
    spid = ing.upsert_person(con, "널이될사람")
    cid_n = _mk_item(con, sid, person_id=spid, content="셋널대상", external_id="__setnull__")
    con.execute("DELETE FROM person WHERE person_id=?", (spid,))
    con.commit()
    pv = con.execute("SELECT person_id FROM context_item WHERE context_item_id=?",
                     (cid_n,)).fetchone()[0]
    check("person 삭제 → context_item.person_id SET NULL", pv is None, f"person_id={pv}")

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
    cli = os.path.join(SRC, "cli.py")

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

    # set-project — 메신저 외 타입(file)도 재매핑 가능해야 함(타입 제약 제거 회귀)
    run("set-project", "메신저 받은파일", "파일프로젝트")
    r = run("sources", "--json")
    src = json.loads(r.stdout) if ok_json(r) else []
    check("set-project(file 타입) 반영",
          any(x["name"] == "메신저 받은파일" and x["project"] == "파일프로젝트" for x in src), r.stdout[:200])

    # rename-project — 단순 오타 수정
    run("rename-project", "파일프로젝트", "파일프로젝트-수정")
    r = run("projects", "--json")
    proj = json.loads(r.stdout) if ok_json(r) else []
    check("rename-project 단순 변경",
          any(x["name"] == "파일프로젝트-수정" for x in proj)
          and not any(x["name"] == "파일프로젝트" for x in proj), r.stdout[:200])

    # rename-project — 기존 프로젝트로 병합(빈 프로젝트 삭제)
    run("rename-project", "파일프로젝트-수정", "테스트프로젝트")
    r = run("sources", "--json")
    src = json.loads(r.stdout) if ok_json(r) else []
    r2 = run("projects", "--json")
    proj = json.loads(r2.stdout) if ok_json(r2) else []
    check("rename-project 병합 반영",
          any(x["name"] == "메신저 받은파일" and x["project"] == "테스트프로젝트" for x in src)
          and not any(x["name"] == "파일프로젝트-수정" for x in proj), r.stdout[:200] + r2.stdout[:200])
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
