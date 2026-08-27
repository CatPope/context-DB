---
name: context-db
description: 에이전트가 흩어진 맥락(메신저 대화·웹 문서·받은파일)을 로컬 context-db CLI로 회수한다. "이 작업 맥락 줘", "지난 논의 찾아줘", 타임라인 복원, 키워드/태그/인물 검색이 필요할 때 사용.
---

# context-db skill

흩어지고 휘발되는 맥락을 적재한 **로컬 SQLite DB(`context.db`)** 를 `context-db` CLI로 질의해
필요한 맥락만 가져온다. SQL을 몰라도 되고, **읽기전용 조회 명령**만 사용한다.

> 소스 유형: 메신저 대화·웹 문서·받은파일. **메신저 적재는 현재 하이웍스 채팅 저장 포맷만 지원**한다.

## 사용 규칙 (중요)
- **조회 명령만** 사용(아래 목록). 적재/수정은 보통 `watch`·스케줄러가 처리(운영자 설정 시)하므로 건드리지 않는다.
- 항상 `--limit` 로 결과를 제한(기본 20).
- 에이전트가 파싱할 때는 **`--json`** 을 붙인다.
- 프로젝트를 알면 `--project <id|name>` 로 범위를 좁힌다. 값이 불확실하면 **먼저 `projects`/`sources` 로 유효한 값을 확인**한 뒤 필터링한다(오타/없는 값은 조용히 `[]` 를 반환).
- **`--project` 로 좁혔는데 결과가 유난히 적으면 프로젝트 배정 누락을 의심한다.** 소스가 `미분류` 로 남아 있으면 필터에서 조용히 탈락한다(에러가 아니라 적은 결과로 나타난다). `context-db sources --json` 으로 관련 소스가 그 프로젝트에 실제로 속해 있는지 확인하고, 아니면 운영자에게 `context-db rules` 확인을 요청한다.
- 호출: `context-db <cmd>`. PATH에 없으면 `python <context-DB-path>/src/cli.py <cmd>` 로 동일하게 실행.
- **`SchemaVersionError` 가 나면 재시도하지 말 것.** DB가 코드보다 낡은 스키마라는 뜻이라 모든 명령이 똑같이 실패한다. 에이전트가 고칠 수 있는 문제가 아니므로 **운영자에게 DB 재구축을 요청**한다(메시지에 절차가 포함돼 있다).

## 조회 명령 (읽기전용)

| 목적 | 명령 |
|---|---|
| 키워드 전문검색(FTS) | `context-db search "<키워드>" [--project P] [--limit N] [--json]` |
| 최근 맥락 타임라인 | `context-db timeline [--project P] [--channel C] [--limit N] [--json]` |
| 태그로 맥락+링크 | `context-db by-tag <태그> [--json]` |
| 인물별 맥락 | `context-db by-person "<이름>" [--limit N] [--json]` |
| 프로젝트 목록 | `context-db projects [--json]` |
| 소스(채널/문서/파일) 목록 | `context-db sources [--project P] [--json]` |
| 외부 링크 목록 | `context-db links [--type web_doc\|file] [--json]` |
| 건수·무결성 요약 | `context-db stats [--json]` |

## 검색 팁 & 주의 (중요)
- **한국어 FTS는 어절(토큰) 단위 매칭** — 부분문자열은 매치되지 않는다.
  - `search 안녕` → `[]` (❌ '안녕하세요'에 부분매치 안 됨). `search 안녕*`(접두어) 또는 온전한 어절 `search 회의` 사용.
- 여러 단어: 공백 = **AND**, `OR` 지원. **정확 구문**은 내부 따옴표로: `search '"서버 권장사양"'`.
  - 예: `search "회의 OR 일정"`, `search "서버 권장사양"`(둘 다 포함), `search 서버*`
- **결과 `[]` 는 에러가 아니라 무매치** — 맥락이 없다고 단정하지 말고 (a) 접두어 `키워드*` (b) 유의어/축약어 (c) `timeline`으로 시간대 훑기 로 재시도.
- 검색이 계속 비고 `stats` 의 `context_item` 이 0 이면 적재가 안 된 것 → 운영자에게 `ingest`/`watch` 확인 요청(에이전트가 직접 적재하지 않음).

## 동작 흐름
1. "이 작업 맥락 줘" / "지난 논의 찾아줘" 요청 수신.
2. 목적에 맞는 명령 선택: 키워드→`search`, 시간순→`timeline`, 주제태그→`by-tag`, 사람→`by-person`.
3. `--json` 으로 실행해 결과(맥락)를 받는다.
4. 반환된 맥락을 작업 컨텍스트로 사용.

## 예시
```bash
# 특정 주제의 최근 논의를 JSON으로
context-db search "서버 권장사양" --limit 10 --json

# 특정 프로젝트 타임라인
context-db timeline --project 예시프로젝트 --limit 20 --json

# 특정 인물이 남긴 맥락
context-db by-person "홍길동" --limit 10 --json
```

## 설치 & 상시 적재 (권장)
운영자는 **한 번의 `setup` 명령**으로 이 skill을 배포하고 백그라운드 상시 적재까지 켤 수 있다.

```bash
# skill을 전역(~/.claude/skills/context-db)에 배포
# 진입점: Windows=context-db.bat, macOS/Linux=context-db(POSIX 셸 스크립트, 최초 1회 chmod +x 필요)
context-db setup

# 특정 폴더(프로젝트 .claude/skills 등)에 배포
context-db setup --path <프로젝트>/.claude/skills

# 배포 + 백그라운드 상시 적재 등록(기본 10분 주기) — 등록 방식은 플랫폼마다 다르다(아래 참고)
context-db setup --background --interval 10
```
`setup` 은 skill 본문의 `<context-DB-path>` 자리표시자를 실제 저장소 경로로 치환해 배포하므로,
PATH 미설정 환경에서도 위 폴백 명령(`python <context-DB-path>/src/cli.py`)이 그대로 동작한다.

**`--background`가 실제로 "자동"인지는 플랫폼마다 다르다 — 에이전트는 이를 알고 있어야 한다:**
- **Windows**: 작업 스케줄러에 직접 등록까지 완료된다. 이후 새 대화·문서가 자동으로 DB에 반영된다.
- **macOS**: `~/Library/LaunchAgents/com.contextdb.ingest.plist` 파일만 생성하고 `launchctl load -w <path>`
  명령을 안내한다 — 이 명령을 운영자가 직접 실행해야 상시 적재가 켜진다. 실행 전까지는 자동 반영이 시작되지 않는다.
- **Linux/기타 POSIX**: crontab 한 줄을 출력·안내만 한다 — 운영자가 `crontab -e` 등으로 직접 추가해야 켜진다.

즉 macOS/Linux 에서는 운영자가 안내된 명령을 실제로 실행했는지에 따라 상시 적재 여부가 갈린다.
이 skill로 조회한 결과가 **최신이 아닐 수 있음**을 감안한다 — 특히 최근 대화·문서를 찾는데 결과가
비거나 오래돼 보이면 적재가 자동으로 돌고 있다고 가정하지 말고 운영자에게 확인을 요청한다.

## 운영 명령 (참고 — 보통 자동 처리)
| 목적 | 명령 |
|---|---|
| 스키마 생성 | `context-db init` |
| 1회 적재 | `context-db ingest` |
| 백그라운드 지속 적재 | `context-db watch --interval 60` |
| 소스→프로젝트 재매핑(미분류 배정·오배정 수정) | `context-db set-project "<소스명>" "<프로젝트>" [--type <코드>]` |
| 프로젝트명 오타 수정/병합 | `context-db rename-project "<기존명>" "<새이름>"` |
| 수동 태깅 | `context-db tag "<키워드>" --add <태그>` |
| 소스 일괄 재매핑(부분 문자열) | `context-db set-project "<부분문자열>" "<프로젝트>" --match [--dry-run]` |
| 프로젝트 자동 배정 규칙 확인 | `context-db rules [--json]` |

## 유지보수 절차 (코드 변경 시 작업 문서/skill 갱신) — 개발 에이전트용
`src/cli.py`·`src/ingest.py`·`src/schema.sql` 등 **동작을 바꾸는 코드 변경**을 했다면, 커밋 전에 아래 순서로 문서·skill을 함께 갱신한다(ADR-013에서 확립된 절차).

1. **코드 변경 + 테스트 추가**: 새 명령/옵션이면 `tests/test_context_db.py`의 "14. CLI 스모크" 섹션에 회귀 케이스 추가.
2. **테스트 실행**: `python tests/test_context_db.py` → exit 0(전체 PASS) 확인. 실패하면 문서화 이전에 원인 해결.
3. **`--help` 정합성**: 새/변경된 명령·인자에 `argparse`의 `help=` 문구를 한글로 채운다. `context-db <cmd> --help` 로 직접 확인.
4. **문서 갱신 대상**:
   - `README.md` — 명령 표(운영/조회)에 새 명령·옵션 반영.
   - `context-db.skill.md`(본 파일) — "조회 명령"/"운영 명령" 표 반영. 에이전트 동작에 영향을 주면 "사용 규칙"·"검색 팁"도 함께 수정.
   - `docs/dev/ADR-context-DB.md` — 새 ADR 항목(상태/맥락/결정/대안/결과) 추가, 상단 요약·변경 이력(작업 기록)·§6 테스트 카운트 갱신. 심화 질의응답이 있었다면 `docs/dev/질의응답.md` 에도 기록.
   - `docs/dev/스키마-설명서.md` · `docs/dev/상위설계서.md` · `docs/dev/context-db-상세설계서.md` — 테이블/컬럼/제약이 바뀌면 함께 갱신. 단, DDL 원본은 `src/schema.sql` 한 곳에만 두고(중복 방지), 뒤 두 문서는 그 스키마를 가리키는 설명·근거만 담는다.
5. **스키마를 구조적으로 바꾸는 변경이면(테이블/컬럼/제약 추가·삭제·변경)**: `src/schema.sql`의 `PRAGMA user_version`과 `src/db.py`의 `SCHEMA_VERSION`을 **반드시 함께** 올린다(한쪽만 올리면 기존 DB가 조용히 낡은 스키마인 채로 통과되거나, 최신 DB가 `SchemaVersionError`로 잘못 거부된다). 이어서 `docs/dev/스키마-설명서.md`와 정본 ERD `docs/dev/context-db-erd.mmd`를 최신 스키마에 맞게 갱신한다(`docs/presentation/` 아래 사본은 발표·제출 시점 기록이므로 갱신하지 않는다).
6. **skill 재배포**: `context-db setup`(또는 `python src/cli.py setup`)으로 전역 `~/.claude/skills/context-db/SKILL.md` 를 다시 배포한다. `<context-DB-path>` 자리표시자가 실제 경로로 치환됐는지 확인.
7. **git 반영은 사용자 승인 후에만**: 커밋/푸시는 명시적으로 요청받았을 때만 수행한다(문서·skill 갱신 자체는 커밋 전 준비 단계).

## 프라이버시
사내·사적 대화 포함 → **로컬 전용, 외부 전송 금지**. `context.db`·설정은 커밋 금지(.gitignore).
