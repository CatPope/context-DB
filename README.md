# context-DB

에이전트 맥락(context) 저장·질의 데이터베이스. 흩어지고 휘발되는 맥락
(메신저 대화·웹 문서·받은파일)을 **SQLite + FTS5** 에 정규화 저장하고,
`context-db` CLI(및 에이전트 skill)로 필요한 맥락만 질의·회수한다.

> **메신저 적재는 현재 하이웍스 채팅 저장 포맷만 지원**한다(다른 메신저는 추후 확장).
> 아래 `<context-DB-path>` 는 이 저장소를 클론한 실제 경로로 바꿔 읽는다.

- 상세설계: `docs/dev/context-db-상세설계서.md`
- 소스: `src/schema.sql` · 공용 DB 접근 계층(`connect()`/버전 검사) `src/db.py` · 적재 `src/ingest.py` · CLI `src/cli.py` · 질의 모음 `src/queries.sql`
- 에이전트 연동 skill 3종:

  | skill | 역할 |
  |---|---|
  | `context-db.skill.md` | **조회 엔진** — CLI로 맥락을 회수하는 방법 |
  | `context-briefing.skill.md` | 응용 — 모아서 **"지금 어디까지 왔는가"** 를 종합 |
  | `task-roundup.skill.md` | 응용 — 그 결과로 **"다음에 뭘 해야 하는가"** 를 도출 |

  응용 skill 2종은 **대화는 DB에서, 영속 자료는 `links` 로 원본을** 읽는 ADR-017 원칙
  위에서 동작한다. 대화 로그 폴더를 직접 훑으면 원본에서 이미 사라진 구간을 잃는다.

  > ⚠️ 현재 `setup` 은 `context-db.skill.md` **한 개만** 배포한다.
  > 응용 skill 2종은 수동 복사가 필요하다(다중 배포는 후속 과제).

## 요구 사항
- Python 3.9+ (표준 라이브러리만 사용; `sqlite3`에 **FTS5 내장** 필요 — 공식 Windows 빌드는 포함)
- 별도 패키지 설치 불필요

> 참고: 일부 독립 `sqlite3.exe`(CLI)에는 FTS5가 빠져 있어 `sqlite3 context.db < schema.sql`이
> 실패할 수 있다. 스키마 생성·적재는 Python 경로(`context-db init` / `ingest`)를 사용한다.

## 지원 환경
| 항목 | 상태 |
|---|---|
| 진입점 | Windows=`context-db.bat`, POSIX=`context-db`(chmod +x 필요) |
| 조회 명령 8종 | 전 플랫폼 동일 |
| 적재(`ingest`/`watch`) | 전 플랫폼 동일 |
| 상시 적재 자동 등록 | Windows=자동, macOS=plist 생성 후 launchctl 수동, Linux=crontab 안내 |
| DB 파일 이식 | 가능(루트 토큰 저장) |
| **원본 로그** | **하이웍스 채팅 저장 폴더가 그 PC 에 있어야 적재 가능** |

마지막 행이 핵심이다. 이건 코드로 해결할 수 없는 **데이터 소재** 문제다 — 저장소를 클론해도 그 PC에
하이웍스 채팅 저장 폴더(원본 로그)가 없으면 채울 데이터가 없다. 반면 `context.db` 파일 자체를 다른
PC로 복사해 오는 것은 이제 가능하다 — `source.uri`/`link.url`이 절대경로가 아니라 루트 토큰
(`{chat_root}/...`)으로 저장되므로 옮겨도 경로가 죽지 않는다(그 PC의 config로 해소).

## 설정
`context-db.config.example.json` 을 `context-db.config.json` 으로 복사해 경로를 채운다.
(실제 설정 파일은 사설 경로를 담으므로 `.gitignore` 로 제외된다. 없으면 내장 기본값을 사용한다.)

```json
{
  "db": "context.db",
  "chat_root": "<context-DB-path>/메신저 채팅저장",
  "files_root": "<context-DB-path>/메신저 받은파일",
  "webdoc": "https://example.com/doc/<doc-id>",
  "webdoc_title": "공유 문서",
  "project": "미분류",
  "watch_interval": 60
}
```

## 빠른 시작 (skill 배포 + 상시 적재)
운영자는 **한 번의 `setup` 명령**으로 에이전트 skill을 배포하고 백그라운드 상시 적재까지 켤 수 있다.

```bash
context-db setup                          # skill을 전역(~/.claude/skills/context-db)에 배포
context-db setup --path <프로젝트>/.claude/skills   # 특정 폴더에 배포
context-db setup --background --interval 10         # 배포 + 상시 적재 등록(권장, 10분 주기)
```
- `--provider` (기본 `claude`) : skill 제공자. 현재 Claude Code(전역 `~/.claude/skills`)를 지원.
- `--path` : skills 루트 폴더를 직접 지정(전역 대신 특정 폴더에 설치).
- `--background [--interval 분]` : 백그라운드 상시 적재를 Windows 작업 스케줄러에 등록.
- `setup` 은 skill 본문의 `<context-DB-path>` 자리표시자를 실제 저장소 경로로 치환해 배포한다.

## CLI 사용
`context-db.bat`(Windows) 또는 `context-db`(POSIX) 가 있는 폴더를 PATH에 추가하면 어디서나
`context-db <command>` 로 실행할 수 있다.

**PATH 추가(사용자 영구, 관리자 불필요 — PowerShell):**
```powershell
$dir = '<context-DB-path>'   # context-db.bat 이 있는 폴더
$p = [Environment]::GetEnvironmentVariable('Path','User')
if ($p -notlike "*$dir*") { [Environment]::SetEnvironmentVariable('Path', ($p.TrimEnd(';') + ';' + $dir), 'User') }
```
새 터미널부터 적용된다(확인: `where.exe context-db` 또는 `Get-Command context-db` — PowerShell에서 `where`는 `Where-Object` 별칭이라 안 됨). 임시로 현재 세션에만 넣으려면
`$env:Path += ';<context-DB-path>'`(PowerShell) / `set PATH=%PATH%;...`(cmd).
※ `setx PATH "%PATH%;..."` 는 1024자 잘림·시스템경로 혼입 위험이 있어 위 방식을 권장한다.

**POSIX(macOS/Linux) 설치:**
```bash
chmod +x context-db
export PATH="$PATH:<context-DB-path>"
```
위 `export`는 현재 세션에만 적용된다. 영구 적용하려면 쉘 설정 파일(`~/.bashrc`, `~/.zshrc` 등)에
같은 줄을 추가한다.

(PATH에 넣지 않으면 `python <context-DB-path>/src/cli.py <command>` 로도 동일)

### 운영/적재
| 명령 | 설명 |
|---|---|
| `context-db setup [--background]` | skill 배포 + (선택) 상시 적재 등록 |
| `context-db init` | 빈 DB에 스키마 적용. `PRAGMA user_version`을 현재 스키마 버전으로 도장 찍고, 이후 모든 연결에서 이 값을 검증한다. 낡은 버전의 기존 DB로 연결하면 `SchemaVersionError`가 발생하며, 에러 메시지에 재구축 절차(백업 확인 → `context.db` 삭제 → `init`/`ingest` 재실행 → 수동 상태 복원)가 함께 안내된다 |
| `context-db ingest` | 1회 전체 적재(멱등 — 신규분만) |
| `context-db watch [--interval 60]` | **백그라운드 지속 적재**(폴링 루프, Ctrl+C 종료) |
| `context-db set-project "<소스명>" "<프로젝트>" [--type <코드>]` | 소스(채널/웹문서/파일함)의 프로젝트 재매핑 — 미분류 소스 배정·오배정 수정. 이름이 여러 유형에 겹칠 때만 `--type` 필요 |
| `context-db rename-project "<기존명>" "<새이름>"` | 프로젝트명 오타 수정(새이름이 이미 있으면 그 프로젝트로 병합 후 빈 프로젝트 삭제) |
| `context-db tag "<키워드>" --add <태그>` | 검색 결과에 수동 태깅 |

### 조회(읽기전용, `--json` 지원)
| 명령 | 설명 |
|---|---|
| `context-db search "<키워드>" [--project P] [--limit N]` | FTS 전문검색 |
| `context-db timeline [--project P] [--channel C] [--limit N]` | 최근 맥락 타임라인 |
| `context-db by-tag <태그>` | 태그로 맥락+링크(M:N) |
| `context-db by-person "<이름>" [--limit N]` | 인물별 맥락 |
| `context-db projects` / `sources [--project P]` | 프로젝트/소스 목록 |
| `context-db links [--type web_doc\|file]` | 외부 링크 목록 |
| `context-db stats` | 건수·무결성 요약 |

## 백그라운드 상시 적재 (Windows 작업 스케줄러)
`context-db setup --background` 가 아래 등록을 대신 수행한다. 수동으로 관리하려면:

```bat
scheduler-register.bat 10      REM 10분마다 context-db ingest
schtasks /Run   /TN context-db-ingest    REM 즉시 1회 실행
schtasks /Query /TN context-db-ingest    REM 상태 확인
scheduler-unregister.bat                 REM 해제
```

세션 중 임시로 돌릴 때는 `context-db watch --interval 60` 을 별도 창에서 실행하면 된다.

## 프라이버시
사내·사적 대화 포함 → **로컬 전용, 외부 전송 금지**. 백업은 `context.db` 파일 복사.
`context.db`, `context-db.config.json` 은 커밋 금지(`.gitignore`).
