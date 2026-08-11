# ADR & 인수인계 문서 — context-DB

> **이 문서의 목적**: 다른 에이전트/개발자가 이 저장소를 **맥락 손실 없이 이어받도록** 하는 단일 진입 문서다.
> 아키텍처 결정 기록(ADR) + 현재 상태 스냅샷 + 운영법 + 열린 과제를 담는다.
> 세부는 각 전용 문서를 링크한다(§10).

| 구분 | 내용 |
|---|---|
| 과제 | context-DB — 에이전트 맥락 저장·질의 DB (SQLite + FTS5) |
| 작성일자 | 2026-08-10 (최종 갱신 2026-08-11) |
| 상태 | MVP + `src/` 재구성 + 용어 일반화 + `setup` 명령 · 테스트 40/40 PASS · 커밋 완료 |
| 데이터 | 사내·사적 대화 포함 → **로컬 전용, 외부 전송 금지** |

---

## 1. 프로젝트 개요 & 현재 상태

흩어지고 휘발되는 맥락(메신저 대화·웹 문서·받은파일)을 정규화된 SQLite 스키마에 적재하고,
`context-db` CLI(및 에이전트 skill)로 SQL/FTS 질의해 회수한다.
(메신저 적재는 현재 하이웍스 채팅 저장 포맷만 지원.)

**현재 상태 스냅샷 (2026-08-11)**
- Git: `main` 브랜치, 원격 `origin`=`github.com/CatPope/context-DB` **동기화됨**(HEAD=`a2e016c` 병합 커밋). `196d57e`(MVP 스크럽본) → 로컬 재구성·용어·`setup`(`bc4fe16`)과 원격 직접 커밋(설계서 리네임·`맥락 정보.md` 삭제 등)을 병합.
- 구조: 소스는 **`src/`** 로 이동(`cli.py`/`ingest.py`/`schema.sql`/`queries.sql`). 진입점 `context-db.bat` → `src\cli.py`.
- 구현: `src/*`·`context-db.bat`·스케줄러 배치·`skill`·테스트 완비. **`context-db setup`** 으로 skill 배포 + 상시 적재 등록 일원화.
- 실 DB: `context.db` = context_item 611 · source 21 · person 16 · link 179 · `fts_in_sync=True` · `orphan=0` (용어 마이그레이션 반영: `web_doc`/`메신저`).
- 백그라운드: 세션용 `context-db watch` + 상시용 작업 스케줄러(`setup --background` 또는 `scheduler-register.bat`).
- 프라이버시: `context.db`, `context-db.config.json`은 `.gitignore`로 커밋 차단(검증됨).

**변경 이력(작업 기록)**
- 2026-08-10: MVP 구현(스키마·적재·CLI·skill·테스트) + 민감내용 스크럽 후 첫 커밋(`196d57e`).
- 2026-08-11: 소스 `src/` 재구성 · `맥락 정보.md` 폴백 파싱 제거 · `context-db setup` 명령 추가(제공자·경로·`--background`) · 용어 일반화(메신저/웹 문서/`<context-DB-path>`, "현재 하이웍스만 지원" 안내) · 실 DB 용어 마이그레이션 · 테스트 40/40. (`bc4fe16`)
- 2026-08-11(병합): 원격 직접 커밋과 분기 발생 → `a2e016c` 로 병합. **`src/` 레이아웃 유지**(사용자 결정), 원격 결정 수용(상세설계서 → `docs/context-db-상세설계서.md`, `docs/맥락 정보.md` 삭제), 용어는 완전본(`web_doc`) 채택. 리네임 참조 일괄 갱신 후 push 완료.

## 2. 파일 맵

| 경로 | 역할 | 커밋 |
|---|---|---|
| `src/schema.sql` | DDL 8테이블 + 인덱스 + FTS5 + 트리거 3종 + 뷰 3종 + 시드 | ✅ |
| `src/ingest.py` | 로그 파서 + 적재(멱등) + 웹 문서/파일 메타 등록 | ✅ |
| `src/cli.py` | 통합 CLI(운영/조회). config 로딩·`--json` | ✅ |
| `context-db.bat` | CLI 래퍼(`chcp 65001` + `python src/cli.py %*`) | ✅ |
| `context-db.config.example.json` | 설정 예시 | ✅ |
| `context-db.config.json` | **실 설정(사설 경로)** | ❌ gitignore |
| `context.db` | **실 DB(사적 데이터)** | ❌ gitignore |
| `scheduler-register.bat` / `scheduler-unregister.bat` | 작업 스케줄러 등록/해제 | ✅ |
| `context-db.skill.md` | 에이전트 연동 skill(CLI 기반) | ✅ |
| `src/queries.sql` | 대표 질의 Q1~Q5 + 뷰 | ✅ |
| `README.md` | 사용법 | ✅ |
| `tests/test_context_db.py` | 결정적 테스트 스위트(40 케이스) | ✅ |
| `docs/*.md` | 제안서·상위설계서·상세설계·비교보고서·테스트보고서·본 ADR | ✅ |
| `docs/context-db-상세설계서.md` | 상세설계 spec(커밋본; `.omc/specs/` 사본은 gitignore) | ✅ |

## 3. 아키텍처 결정 기록 (ADR)

각 결정: **상태 / 맥락 / 결정 / 대안 / 결과**. 리뷰로 뒤집힌 항목은 명시.

### ADR-001 — 저장소 엔진: SQLite 3 + FTS5 (순수 SQL)
- **상태**: 채택(제안서 확정)
- **맥락**: 단기 MVP, 무설치, DB 실습(정규화·인덱스·전문검색) 평가 목적.
- **결정**: 파일 기반 SQLite + 내장 FTS5. 표준 SQL 유지.
- **대안**: PostgreSQL+tsvector(과설비), JSON 파일(질의성 부족).
- **결과**: 이식성↑(추후 PG 이관 가능). 단, 일부 `sqlite3.exe` CLI에 FTS5 부재 → ADR-009.

### ADR-002 — 채널 = SOURCE, 자연키 `(source_type_id, name)`, project는 가변 속성
- **상태**: 채택 (초안 뒤집음 — 리뷰 Issue 1)
- **맥락**: 실데이터는 채널(폴더) 단위. 초안 자연키가 `(project_id, type, name)`이라 채널 project 재매핑(UPDATE) 후 재적재 시 **새 source 생성 → 이중 적재**(멱등성 붕괴) 위험.
- **결정**: source 자연키에서 `project_id` 제거 → `UNIQUE(source_type_id, name)`. `project_id`는 정체성이 아닌 **가변 FK 속성**(재매핑=UPDATE). ingest는 `(type, name)`로 조회/삽입하고 기존 소스의 project는 건드리지 않음.
- **대안**: `uri`(폴더경로) 자연키 — 채널명 변경엔 강하나 MVP엔 과함.
- **결과**: 재매핑 후에도 멱등(테스트 §7로 회귀 검증).

### ADR-003 — project ↔ source는 1:N 유지 + 교차주제는 tag(M:N) 보완
- **상태**: 채택 (리뷰 Issue 2, 사용자 선택)
- **맥락**: 한 채널이 여러 프로젝트 주제를 다룰 수 있음. 1:N은 이를 project 레벨에서 표현 못 함.
- **결정**: 과제 headline 1:N을 보존하고, 채널 내 교차 주제는 `context_item`의 `tag`(M:N)로 분류. "한 채널=project 1개" 한계는 명시.
- **대안(미채택)**: `source_project` M:N — Issue1+2 동시 해결하나 headline 1:N 소실·조인 복잡·MVP 초과. → 차기 확장으로 DDL 스케치만 기록(상세설계 §10.2).
- **결과**: 스키마 단순 유지. 다중 프로젝트 소속 필요 시 ADR 재검토.

### ADR-004 — 웹 문서/받은파일은 링크·메타데이터만
- **상태**: 채택
- **맥락**: 웹 문서=URL, 받은파일=바이너리(PDF/PPTX/HWP…). 본문추출은 파서 복잡·범위 팽창.
- **결정**: 웹 문서=`source(web_doc)`+`link`, 받은파일=`source(file)`+파일별 `link`(경로/제목/mtime). 본문추출 없음. FTS는 채팅 content 중심.
- **대안**: 본문·PDF 텍스트 추출(차기).
- **결과**: 가벼운 MVP. 의미검색은 차기(§7).

### ADR-005 — 멱등 적재: `external_id = sha1(채널|event_ts|발화자|내용)` + `UNIQUE(source_id, external_id)`
- **상태**: 채택
- **맥락**: 메신저 로그(하이웍스)엔 메시지 ID가 없음. watch/스케줄러가 같은 파일을 반복 스캔.
- **결정**: 자연 dedup 키를 해시로 생성, `INSERT OR IGNORE`. 동일(채널·시각·발화자·내용) 메시지는 1건으로 수렴.
- **결과**: 재실행 안전(테스트 §6). 완전 동일 메시지(예: "ㅇㅇ" 연속 2회 같은 분)는 의도적으로 1건 처리.

### ADR-006 — FTS5 외부 콘텐츠 테이블 + 트리거 동기화
- **상태**: 채택 (상위설계서 누락분 보강)
- **맥락**: 상위설계서는 FTS 가상테이블만 만들고 동기화 수단이 없었음.
- **결정**: `content='context_item'` 외부콘텐츠 방식 + INSERT/UPDATE/DELETE 트리거 3종.
- **결과**: content 변경이 FTS에 자동 반영(테스트 §5c). `fts_in_sync=True` 유지.

### ADR-007 — DB 위에 CLI 계층(`context-db`) 도입: 읽기전용 계약 + `--json`
- **상태**: 채택
- **맥락**: 에이전트가 raw SQL을 쓰면 스키마 프롬프트 부담·쓰기 사고·오류 여지. (DB직접 vs CLI 비교: `docs/DB기반_vs_CLI기반_비교보고서.md`)
- **결정**: 조회는 읽기전용 명령(search/timeline/by-tag/by-person/projects/sources/links/stats) + `--json`. 쓰기는 ingest/watch로 일원화. skill은 CLI만 안내.
- **대안**: DB 직접(유연하나 위험). → 하이브리드: CLI 기본, DB직접은 애드혹 분석용 예외.
- **결과**: 안전·에이전트 친화·이식성↑. 임의 질의는 코드 추가 필요(트레이드오프).

### ADR-008 — 백그라운드 적재: watch 루프 + Windows 작업 스케줄러(둘 다)
- **상태**: 채택 (사용자 선택)
- **맥락**: 메신저가 새 `.txt`를 계속 저장 → 지속 적재 필요.
- **결정**: 세션/임시용 `watch`(폴링 루프) + 상시용 작업 스케줄러(`scheduler-register.bat`, 기본 10분).
- **결과**: 멱등이라 폴링 안전. watch는 세션 스코프(주의: §8).

### ADR-009 — 실행 경로를 Python으로 통일 (sqlite3 CLI의 FTS5 부재)
- **상태**: 채택 (환경 제약)
- **맥락**: 이 머신의 `sqlite3.exe`(3.42)엔 FTS5 미포함 → `sqlite3 context.db < schema.sql` 실패. Python 내장 sqlite(3.49)엔 FTS5 있음.
- **결정**: 스키마 생성·적재·질의를 Python(`context-db`/`cli.py`)으로 수행. `sqlite3` CLI에 의존하지 않음.
- **결과**: 수용 기준의 "sqlite3 CLI 실행" 문구는 Python 경로로 대체(문서화).

### ADR-010 — watch 로그 라인 버퍼링
- **상태**: 채택 (버그 수정)
- **맥락**: 백그라운드(파일 리다이렉트) 실행 시 stdout 블록 버퍼링 → 무한 루프 동안 로그 미출력.
- **결정**: `sys.stdout.reconfigure(line_buffering=True)` 로 줄 단위 flush.
- **결과**: watch 진행 로그 즉시 확인 가능.

### ADR-011 — `context-db setup` 으로 skill 배포 + 상시 적재 일원화
- **상태**: 채택 (2026-08-11)
- **맥락**: skill을 수동 복사하고 스케줄러를 따로 등록해야 했다. 저장소 경로가 고정 하드코딩이라 이식성이 낮았다.
- **결정**: `setup` 서브커맨드 추가. `--provider`(기본 `claude` → 전역 `~/.claude/skills`)·`--path`(특정 폴더)로 skill을 `<대상>/context-db/SKILL.md` 에 배포하고, 배포 시 skill 본문의 `<context-DB-path>` 자리표시자를 **실제 저장소 절대경로로 치환**한다. `--background [--interval 분]` 은 `schtasks` 로 상시 적재를 등록한다.
- **결과**: 한 명령으로 설치·상시화 완료. PATH 미설정 환경에서도 폴백 명령(`python <경로>/src/cli.py`)이 그대로 동작. skill 문서에서 `--background` 를 권장.

### ADR-012 — 용어 일반화(메신저/웹 문서) + `맥락 정보.md` 폴백 제거
- **상태**: 채택 (2026-08-11)
- **맥락**: 특정 제공자(하이웍스/구글 문서)·특정 사용자 경로가 코드·문서·스키마에 하드코딩되어 확장·공유에 부적합. `맥락 정보.md`(개발용 참고자료)를 설정 폴백으로 파싱하던 경로도 불필요.
- **결정**: 제품 용어를 **메신저**(source_type `messenger`)·**웹 문서**(코드 `google_doc`→`web_doc`, CLI `--gdoc`→`--webdoc`, config 키 `gdoc*`→`webdoc*`)로 일반화하고, 사용자 경로는 `<context-DB-path>` 자리표시자로 대체. "**메신저 적재는 현재 하이웍스 채팅 저장 포맷만 지원**" 안내를 명시. `맥락 정보.md` 폴백 파싱은 제거(설정은 `context-db.config.json` 단일 소스).
- **마이그레이션**: 기존 `context.db`의 `source_type` 코드/라벨과 `source.name`(받은파일)을 UPDATE로 일괄 갱신(건수·무결성 불변, 611건 검증).
- **결과**: 다른 메신저·웹 문서로 확장 가능한 형태. 코드 식별자 `google_doc`→`web_doc` 변경은 **기존 DB 마이그레이션 필요**(주의). 원본 입력문서(과제제안서·상위설계서)와 인터뷰 부록은 역사 기록으로 원문 유지.

## 4. 데이터 모델 요약

엔티티: `project`(1:N)→`source`←`source_type`(1:N), `source`(1:N)→`context_item`←`person`(1:N),
`context_item`↔`tag`(**M:N** via `context_item_tag`), `link`(context_item/source에 귀속), `context_fts`(FTS5).
- 자연키: `source(source_type_id, name)`, `context_item(source_id, external_id)`, `person(display_name)`, `tag(name)`.
- 파서 규칙: 헤더 `[YYYY-MM-DD 오전/오후 H:MM] 이름`, 이후 다음 헤더 전까지 멀티라인 본문. 날짜=파일명·시각=헤더. item_type ∈ {message, system, file, note, excerpt}.
- 전체 DDL·근거: `docs/context-db-상세설계서.md` §4~7.

## 5. 운영 방법 (빠른 시작)
```bash
# 0) skill 배포 + 상시 적재(권장): 자리표시자 <context-DB-path> 자동 치환
python src/cli.py setup --background --interval 10

# 1) 설정: 예시 복사 후 경로 채우기
cp context-db.config.example.json context-db.config.json

# 2) 스키마 생성 + 1회 적재
python src/cli.py init
python src/cli.py ingest

# 3) 조회 (에이전트는 --json)
python src/cli.py search "서버 권장사양" --limit 10 --json
python src/cli.py timeline --channel "[채널명]" --json
python src/cli.py by-tag 예시태그 --json
python src/cli.py stats

# 4) 지속 적재(수동)
python src/cli.py watch --interval 60          # 세션/임시
scheduler-register.bat 10                   # 상시(작업 스케줄러, 관리자 권장)

# 5) 테스트
python tests/test_context_db.py             # exit 0 = 전체 통과
```
**PATH 등록** — `context-db.bat` 폴더를 PATH에 넣으면 `python src/cli.py` 대신 `context-db` 로 호출 가능.
```powershell
# 사용자 영구(관리자 불필요, PowerShell). 새 터미널부터 적용
# 확인: where.exe context-db (PowerShell의 `where`는 Where-Object 별칭이라 안 됨)
$dir = '<context-DB-path>'   # context-db.bat 이 있는 폴더
$p = [Environment]::GetEnvironmentVariable('Path','User')
if ($p -notlike "*$dir*") { [Environment]::SetEnvironmentVariable('Path', ($p.TrimEnd(';') + ';' + $dir), 'User') }
```
현재 세션에만 임시 적용: `$env:Path += ';<context-DB-path>'`(PowerShell) / `set PATH=%PATH%;...`(cmd).
※ `setx PATH "%PATH%;..."` 는 1024자 잘림·시스템경로 혼입 위험이 있어 위 방식 권장.

## 6. 테스트 상태
- 자동화: `tests/test_context_db.py` **40 PASS / 0 FAIL** (파서·시각변환·멀티라인·item_type·FTS 트리거/검색·토큰화·멱등·Issue1 회귀·인코딩 폴백(cp949/BOM)·받은파일/웹 문서 링크·무결성(FK/CHECK)·뷰·CLI `--json`).
- 실데이터 스모크: 검색·watch 멱등 통과. 상세: `docs/테스트보고서.md`.

## 7. 알려진 한계 & 열린 과제 (다음 에이전트가 이어받을 후보)
1. **한국어 FTS는 토큰 단위 매칭**(부분문자열 불가). 완화: 접두어(`키워드*`) 안내 → 차기 `trigram`/`unicode61` 토크나이저 또는 벡터(의미) 검색. (상세설계 §10, 테스트보고서 §6)
2. **동시성**: watch 상시 쓰기 환경에 `PRAGMA journal_mode=WAL` 미적용(제안). 읽기/쓰기 경합 완화용.
3. **다중 프로젝트 소속**: 필요 시 `source_project` M:N 도입(ADR-003 대안).
4. **본문 추출**: 웹 문서/PDF 텍스트 인덱싱(차기).
5. **query_log**: 질의 이력 기록(선택 2차 기능, 미구현).
6. **커밋/원격**: 아직 커밋 0. 원격 미연결(`origin` gone). 첫 커밋 + 원격 설정 필요.
7. **파서 견고성**: 첨부 파일명 라인 → 받은파일 `link` 자동 매칭은 미구현(현재 item_type='file' 분류만).

## 8. 이어받는 에이전트를 위한 주의사항 (체크리스트)
- [ ] **프라이버시**: `context.db`/`context-db.config.json`은 사적 데이터 → 절대 커밋·외부 전송 금지(이미 gitignore).
- [ ] **FTS5는 Python sqlite로만**: `sqlite3.exe` CLI로 스키마 실행 금지(FTS5 부재). `python src/cli.py` 사용.
- [ ] **백그라운드 watch**가 세션에서 돌고 있을 수 있음(멱등이라 중복 실행해도 안전하나, 종료하려면 프로세스/작업 중단).
- [ ] **멱등 규칙**을 깨지 말 것: source 자연키 `(type,name)`·context_item `(source_id, external_id)` 유지(ADR-002/005).
- [ ] 스키마 변경 시 **CLI 계약(명령/`--json` 필드)** 하위호환 고려(ADR-007). skill·테스트 동반 갱신.
- [ ] 인코딩: Windows cp949 → 스크립트는 `PYTHONIOENCODING=utf-8`/`reconfigure` 유지, `.bat`은 ASCII 주석.
- [ ] 변경 후 **`python tests/test_context_db.py`** 통과 확인.

## 9. 재현/검증 명령 모음
```bash
python src/cli.py stats --json            # 현황
python tests/test_context_db.py       # 회귀 테스트(exit 0)
python src/cli.py ingest                   # 멱등 재적재(신규 0 기대)
```

## 10. 관련 문서
- 요구/설계: `docs/과제제안서.md`, `docs/상위설계서.md`, `docs/context-db-상세설계서.md`
- 맥락 출처: 개발용 참고자료였던 `docs/맥락 정보.md` 는 삭제됨(적재 대상 아님, 폴백 파싱도 제거). 설정은 `context-db.config.json` 단일 소스.
- 비교/테스트: `docs/DB기반_vs_CLI기반_비교보고서.md`, `docs/테스트보고서.md`
- 사용/연동: `README.md`, `context-db.skill.md`
