#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context-DB 통합 CLI (cli.py)

설치/설정:        setup (skill 배포 + 백그라운드 상시 적재 등록)
운영/적재(쓰기):  init, ingest, watch, set-project, tag
조회(읽기전용):   search, timeline, by-tag, by-person, projects, sources, links, stats

경로 기본값은 context-db.config.json 에서 읽는다(없으면 내장 기본값).
CLI 인자가 항상 config 보다 우선한다. 조회 명령은 --json 을 지원(에이전트용).

예:
  context-db setup                      # skill을 전역(~/.claude/skills)에 배포
  context-db setup --background         # + 상시 적재 스케줄러 등록
  context-db init
  context-db ingest
  context-db watch --interval 60
  context-db search "회의 일정" --project 2 --limit 20 --json
  context-db timeline --channel "[채널명]" --json
  context-db by-tag 예시태그 --json
  context-db set-project "[채널명]" "프로젝트명"
  context-db tag "회의 OR 일정" --add 예시태그
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

# 콘솔 인코딩(Windows cp949) 문제 방지 + 라인 버퍼링
# (watch를 파일/파이프로 백그라운드 실행할 때도 로그가 즉시 보이도록 flush)
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import ingest as ing  # 동일 폴더의 ingest.py 재사용

HERE = os.path.dirname(os.path.abspath(__file__))          # src/
ROOT = os.path.dirname(HERE)                                # 저장소 루트
CONFIG_PATH = os.path.join(ROOT, "context-db.config.json")
DEFAULT_DB = os.path.join(ROOT, "context.db")
SKILL_SRC = os.path.join(ROOT, "context-db.skill.md")      # 배포 원본 skill


# ─────────────────────────── 설정 로딩 ───────────────────────────
def load_config() -> dict:
    cfg = {
        "db": DEFAULT_DB,
        "chat_root": None,
        "files_root": None,
        "webdoc": None,
        "webdoc_title": "공유 문서",
        "project": "미분류",
        "watch_interval": 60,
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg.update({k: v for k, v in json.load(fh).items() if v is not None})
    # 상대 db 경로는 저장소 루트 기준으로 절대화(스케줄러 cwd 무관)
    if cfg["db"] and not os.path.isabs(cfg["db"]):
        cfg["db"] = os.path.join(ROOT, cfg["db"])
    return cfg


def resolve(args, cfg) -> dict:
    """CLI 인자 > config 우선순위로 병합."""
    return {
        "db": getattr(args, "db", None) or cfg["db"],
        "chat_root": getattr(args, "chat_root", None) or cfg.get("chat_root"),
        "files_root": getattr(args, "files_root", None) or cfg.get("files_root"),
        "webdoc": getattr(args, "webdoc", None) or cfg.get("webdoc"),
        "webdoc_title": getattr(args, "webdoc_title", None) or cfg.get("webdoc_title", "공유 문서"),
        "project": getattr(args, "project", None) or cfg.get("project", "미분류"),
    }


# ─────────────────────────── 출력 헬퍼 ───────────────────────────
def emit(rows, cols, as_json: bool):
    if as_json:
        print(json.dumps([dict(zip(cols, r)) for r in rows], ensure_ascii=False, indent=2))
        return
    if not rows:
        print("(결과 없음)")
        return
    widths = [max(len(str(c)), *(len(_clip(str(r[i]))) for r in rows)) for i, c in enumerate(cols)]
    print(" | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(_clip(str(r[i])).ljust(widths[i]) for i in range(len(cols))))


def _clip(s: str, n: int = 70) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def db_connect(db_path):
    return ing.connect(db_path, ing.DEFAULT_SCHEMA)


# ─────────────────────────── 운영/적재 ───────────────────────────
def cmd_init(args, cfg):
    r = resolve(args, cfg)
    con = db_connect(r["db"])
    con.close()
    print(f"[init] 스키마 준비 완료 → {r['db']}")


def _run_ingest(r) -> None:
    con = db_connect(r["db"])
    try:
        if r["chat_root"]:
            s = ing.ingest_chat_root(con, r["chat_root"], r["project"])
            print(f"[채팅] 채널 {s['channels']} · 파일 {s['files']} · "
                  f"신규 {s['inserted']} · 중복무시 {s['skipped']}")
        if r["files_root"]:
            n = ing.ingest_files_root(con, r["files_root"], r["project"])
            print(f"[받은파일] 링크 {n}건")
        if r["webdoc"]:
            ing.ingest_webdoc(con, r["webdoc"], r["webdoc_title"], r["project"])
            print(f"[웹 문서] 1건: {r['webdoc_title']}")
        total = con.execute("SELECT count(*) FROM context_item").fetchone()[0]
        print(f"[요약] context_item {total} → {r['db']}")
    finally:
        con.close()


def cmd_ingest(args, cfg):
    _run_ingest(resolve(args, cfg))


def cmd_watch(args, cfg):
    r = resolve(args, cfg)
    interval = args.interval or cfg.get("watch_interval", 60)
    print(f"[watch] {interval}s 주기 지속 적재 시작 (Ctrl+C 종료). DB={r['db']}")
    try:
        while True:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n--- {stamp} ---")
            _run_ingest(r)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[watch] 종료")


def cmd_set_project(args, cfg):
    r = resolve(args, cfg)
    con = db_connect(r["db"])
    try:
        pid = ing.get_or_create_project(con, args.project_name)
        st = ing.source_type_id(con, "messenger")
        cur = con.execute(
            "UPDATE source SET project_id=? WHERE source_type_id=? AND name=?",
            (pid, st, args.channel),
        )
        con.commit()
        if cur.rowcount:
            print(f"[set-project] '{args.channel}' → '{args.project_name}' 재매핑 완료")
        else:
            print(f"[set-project] 채널 '{args.channel}' 를 찾지 못함")
    finally:
        con.close()


def cmd_tag(args, cfg):
    r = resolve(args, cfg)
    con = db_connect(r["db"])
    try:
        con.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (args.add,))
        tid = con.execute("SELECT tag_id FROM tag WHERE name=?", (args.add,)).fetchone()[0]
        if args.id:
            ids = [args.id]
        else:
            ids = [row[0] for row in con.execute(
                "SELECT ci.context_item_id FROM context_fts f "
                "JOIN context_item ci ON ci.context_item_id=f.rowid "
                "WHERE context_fts MATCH ? LIMIT ?", (args.keyword, args.limit))]
        con.executemany(
            "INSERT OR IGNORE INTO context_item_tag(context_item_id, tag_id) VALUES (?,?)",
            [(i, tid) for i in ids])
        con.commit()
        print(f"[tag] '{args.add}' → {len(ids)}개 항목에 부여")
    finally:
        con.close()


# ─────────────────────────── 조회(읽기전용) ───────────────────────────
def _pid_from(con, project):
    if project is None:
        return None
    if str(project).isdigit():
        return int(project)
    row = con.execute("SELECT project_id FROM project WHERE name=?", (project,)).fetchone()
    return row[0] if row else -1


def cmd_search(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        sql = ("SELECT ci.event_ts, s.name, ci.content FROM context_fts f "
               "JOIN context_item ci ON ci.context_item_id=f.rowid "
               "JOIN source s ON s.source_id=ci.source_id WHERE context_fts MATCH ?")
        params = [args.keyword]
        pid = _pid_from(con, args.project)
        if pid is not None:
            sql += " AND s.project_id=?"; params.append(pid)
        sql += " ORDER BY rank LIMIT ?"; params.append(args.limit)
        emit(con.execute(sql, params).fetchall(), ["event_ts", "source", "content"], args.json)
    finally:
        con.close()


def cmd_timeline(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        sql = ("SELECT ci.event_ts, s.name, p.display_name, ci.content FROM context_item ci "
               "JOIN source s ON s.source_id=ci.source_id "
               "LEFT JOIN person p ON p.person_id=ci.person_id WHERE 1=1")
        params = []
        pid = _pid_from(con, args.project)
        if pid is not None:
            sql += " AND s.project_id=?"; params.append(pid)
        if args.channel:
            sql += " AND s.name=?"; params.append(args.channel)
        sql += " ORDER BY ci.event_ts DESC LIMIT ?"; params.append(args.limit)
        emit(con.execute(sql, params).fetchall(),
             ["event_ts", "source", "author", "content"], args.json)
    finally:
        con.close()


def cmd_by_tag(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        rows = con.execute(
            "SELECT ci.event_ts, ci.content, l.url FROM context_item ci "
            "JOIN context_item_tag t ON t.context_item_id=ci.context_item_id "
            "JOIN tag g ON g.tag_id=t.tag_id "
            "LEFT JOIN link l ON l.context_item_id=ci.context_item_id "
            "WHERE g.name=? ORDER BY ci.event_ts DESC", (args.tag,)).fetchall()
        emit(rows, ["event_ts", "content", "url"], args.json)
    finally:
        con.close()


def cmd_by_person(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        rows = con.execute(
            "SELECT ci.event_ts, s.name, ci.content FROM context_item ci "
            "JOIN person p ON p.person_id=ci.person_id "
            "JOIN source s ON s.source_id=ci.source_id "
            "WHERE p.display_name=? ORDER BY ci.event_ts DESC LIMIT ?",
            (args.name, args.limit)).fetchall()
        emit(rows, ["event_ts", "source", "content"], args.json)
    finally:
        con.close()


def cmd_projects(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        rows = con.execute(
            "SELECT p.project_id, p.name, count(s.source_id) FROM project p "
            "LEFT JOIN source s ON s.project_id=p.project_id GROUP BY p.project_id "
            "ORDER BY p.project_id").fetchall()
        emit(rows, ["project_id", "name", "sources"], args.json)
    finally:
        con.close()


def cmd_sources(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        sql = ("SELECT s.source_id, pr.name, st.label, s.name FROM source s "
               "JOIN project pr ON pr.project_id=s.project_id "
               "JOIN source_type st ON st.source_type_id=s.source_type_id WHERE 1=1")
        params = []
        pid = _pid_from(con, args.project)
        if pid is not None:
            sql += " AND s.project_id=?"; params.append(pid)
        sql += " ORDER BY st.label, s.name"
        emit(con.execute(sql, params).fetchall(),
             ["source_id", "project", "type", "name"], args.json)
    finally:
        con.close()


def cmd_links(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        sql = ("SELECT st.label, s.name, l.title, l.url FROM link l "
               "JOIN source s ON s.source_id=l.source_id "
               "JOIN source_type st ON st.source_type_id=s.source_type_id WHERE 1=1")
        params = []
        if args.type:
            sql += " AND st.code=?"; params.append(args.type)
        sql += " ORDER BY st.label, l.title LIMIT ?"; params.append(args.limit)
        emit(con.execute(sql, params).fetchall(),
             ["type", "source", "title", "url"], args.json)
    finally:
        con.close()


def cmd_stats(args, cfg):
    con = db_connect(resolve(args, cfg)["db"])
    try:
        q = con.execute
        data = {
            "context_item": q("SELECT count(*) FROM context_item").fetchone()[0],
            "source": q("SELECT count(*) FROM source").fetchone()[0],
            "person": q("SELECT count(*) FROM person").fetchone()[0],
            "link": q("SELECT count(*) FROM link").fetchone()[0],
            "tag": q("SELECT count(*) FROM tag").fetchone()[0],
            "fts_in_sync": bool(q("SELECT (SELECT count(*) FROM context_fts)="
                                  "(SELECT count(*) FROM context_item)").fetchone()[0]),
            "orphan_items": q("SELECT count(*) FROM context_item ci "
                              "LEFT JOIN source s ON s.source_id=ci.source_id "
                              "WHERE s.source_id IS NULL").fetchone()[0],
        }
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for k, v in data.items():
                print(f"  {k:16}: {v}")
    finally:
        con.close()


# ─────────────────────────── 설치/설정(setup) ───────────────────────────
# provider → skills 루트 폴더. 현재는 Claude Code(전역 ~/.claude/skills)만 지원.
PROVIDER_SKILL_DIRS = {
    "claude": os.path.join(os.path.expanduser("~"), ".claude", "skills"),
}


def _skill_target_dir(args) -> str:
    """skill 설치 대상 폴더(= skills 루트/context-db). --path 지정 시 해당 폴더가 루트."""
    if getattr(args, "path", None):
        root = os.path.abspath(os.path.expanduser(args.path))
    else:
        root = PROVIDER_SKILL_DIRS.get(args.provider)
        if root is None:
            raise SystemExit(f"[setup] 알 수 없는 provider: {args.provider} "
                             f"(지원: {', '.join(PROVIDER_SKILL_DIRS)})")
    return os.path.join(root, "context-db")


def _install_skill(target_dir: str) -> str:
    """context-db.skill.md 를 target_dir/SKILL.md 로 배포.
    본문의 <context-DB-path> 자리표시자를 실제 저장소 경로로 치환한다."""
    if not os.path.exists(SKILL_SRC):
        raise SystemExit(f"[setup] skill 원본을 찾을 수 없음: {SKILL_SRC}")
    os.makedirs(target_dir, exist_ok=True)
    text = open(SKILL_SRC, encoding="utf-8").read().replace("<context-DB-path>", ROOT)
    dst = os.path.join(target_dir, "SKILL.md")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dst


def _register_background(interval_min: int) -> None:
    """백그라운드 상시 적재 등록. Windows=작업 스케줄러(schtasks)."""
    bat = os.path.join(ROOT, "context-db.bat")
    if os.name == "nt" and os.path.exists(bat):
        cmd = ["schtasks", "/Create", "/TN", "context-db-ingest",
               "/TR", f'"{bat}" ingest', "/SC", "MINUTE", "/MO", str(interval_min), "/F"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"[setup] 상시 적재 등록 완료: {interval_min}분 주기 (작업명 context-db-ingest)")
            print("        즉시 실행: schtasks /Run /TN context-db-ingest")
            print("        해제     : schtasks /Delete /TN context-db-ingest /F")
        else:
            print("[setup] 스케줄러 등록 실패(관리자 권한이 필요할 수 있음):")
            print("        " + (r.stderr or r.stdout).strip())
    else:
        print("[setup] 이 플랫폼에서는 자동 등록을 지원하지 않습니다.")
        print("        대신 별도 창에서 `context-db watch --interval 60` 을 실행하세요.")


def cmd_setup(args, cfg):
    dst = _install_skill(_skill_target_dir(args))
    print(f"[setup] skill 배포 완료 → {dst}")
    print(f"        (자리표시자 <context-DB-path> → {ROOT} 치환)")
    if args.background:
        _register_background(args.interval or 10)
    else:
        print("[setup] 상시 적재까지 켜려면: context-db setup --background [--interval 분]")


# ─────────────────────────── 파서 구성 ───────────────────────────
def build_parser():
    p = argparse.ArgumentParser(prog="context-db", description="context-DB CLI")
    p.add_argument("--db", default=None, help="DB 경로(기본: config/context.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_roots(sp):
        sp.add_argument("--chat-root"); sp.add_argument("--files-root")
        sp.add_argument("--webdoc"); sp.add_argument("--webdoc-title")
        sp.add_argument("--project")

    sp = sub.add_parser("setup", help="skill 배포 + (선택) 상시 적재 등록")
    sp.add_argument("--provider", default="claude",
                    help="skill 제공자(기본 claude → 전역 ~/.claude/skills)")
    sp.add_argument("--path", default=None,
                    help="skill을 설치할 skills 루트 폴더(예: <프로젝트>/.claude/skills)")
    sp.add_argument("--background", action="store_true",
                    help="백그라운드 상시 적재를 작업 스케줄러에 등록")
    sp.add_argument("--interval", type=int, default=None, help="상시 적재 주기(분, 기본 10)")
    sp.set_defaults(func=cmd_setup)

    sub.add_parser("init").set_defaults(func=cmd_init)

    sp = sub.add_parser("ingest"); add_roots(sp); sp.set_defaults(func=cmd_ingest)
    sp = sub.add_parser("watch"); add_roots(sp)
    sp.add_argument("--interval", type=int, default=None); sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("set-project")
    sp.add_argument("channel"); sp.add_argument("project_name"); sp.set_defaults(func=cmd_set_project)

    sp = sub.add_parser("tag")
    sp.add_argument("keyword", nargs="?", default=None)
    sp.add_argument("--id", type=int, default=None)
    sp.add_argument("--add", required=True); sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_tag)

    sp = sub.add_parser("search"); sp.add_argument("keyword")
    sp.add_argument("--project"); sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("timeline")
    sp.add_argument("--project"); sp.add_argument("--channel")
    sp.add_argument("--limit", type=int, default=20); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_timeline)

    sp = sub.add_parser("by-tag"); sp.add_argument("tag")
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_by_tag)

    sp = sub.add_parser("by-person"); sp.add_argument("name")
    sp.add_argument("--limit", type=int, default=20); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_by_person)

    sp = sub.add_parser("projects"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_projects)

    sp = sub.add_parser("sources"); sp.add_argument("--project")
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_sources)

    sp = sub.add_parser("links"); sp.add_argument("--type"); sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_links)

    sp = sub.add_parser("stats"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stats)
    return p


def main():
    args = build_parser().parse_args()
    cfg = load_config()
    args.func(args, cfg)


if __name__ == "__main__":
    main()
