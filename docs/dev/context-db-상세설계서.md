# Deep Interview Spec: context-DB 상세설계서 (Detailed Design)

## Metadata
- Interview ID: ctxdb-detaildesign-2026-08-10
- Rounds: 5 (+ Round 0 topology)
- Final Ambiguity Score: 17%
- Type: greenfield (코드 없음, docs만 존재)
- Generated: 2026-08-10
- Threshold: 0.2
- Threshold Source: default
- Initial Context Summarized: no
- Status: PASSED

## Revision Log
- **2026-08-10 (리뷰 패치)**: 상세설계 리뷰 반영. 채널↔프로젝트 관계를 **1:N(`source.project_id`) → M:N(`source_project`)** 로 변경하고 **채널 정체성을 프로젝트와 분리**(`UNIQUE(source_type,name)`) → 다주제/무주제 채널 지원 + 프로젝트 재매핑 시 이중적재 방지. `external_id NOT NULL` + dedup 자연키에 `line_no` 추가(같은 분 반복 발화 보존). 관련 질의·뷰·온톨로지·수용기준 동기화. **패치 스키마 실행 검증 완료**(재매핑 멱등성 포함).

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.85 | 0.40 | 0.340 |
| Constraint Clarity | 0.78 | 0.30 | 0.234 |
| Success Criteria | 0.85 | 0.30 | 0.255 |
| **Total Clarity** | | | **0.829** |
| **Ambiguity** | | | **0.171** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| Schema/DDL | active | 테이블·제약·인덱스·FTS 트리거·시드 | §4 DDL 전체 + §5 FTS 동기화 트리거 + §6 시드 |
| Ingest (ingest.py) | active | 메신저 로그 파서·정규화·중복처리 | §7 파서 상세설계 (구현은 실행 단계) |
| Query 인터페이스 | active | 대표 쿼리·뷰(읽기 전용) | §8 쿼리/뷰. FTS 동기화는 스키마 트리거(§5) 소관 |
| Skill 연동 | active | 스키마 요약·질의 템플릿·사용규칙 | §9 skill 설계 (구현은 실행 단계) |

## Goal
흩어지고 휘발되는 맥락 데이터(메신저 채팅 로그·웹 문서 링크·받은파일 메타데이터)를
정규화된 SQLite 관계형 스키마에 적재하고, 에이전트가 skill로 스키마를 숙지해 SQL/FTS로
필요한 맥락만 질의·회수하는 시스템의 **상세설계**를 확정한다.
본 요청의 산출물은 **(1) 이 상세설계서 + (2) 실행 가능한 `schema.sql`** 이다.

## Constraints (인터뷰로 확정된 결정)
- **적재 범위**: 메신저 채팅 **전 채널**(사적 채널 포함) + 웹 문서(링크·메타) + 받은파일(메타).
- **적재 깊이**: 웹 문서/받은파일은 **링크·메타데이터만** 저장(본문 추출 없음). FTS 전문검색은 **채팅 content 중심**.
- **채널→PROJECT 매핑**: 채널 폴더 = **SOURCE(messenger)** 한 개. 채널 정체성은 프로젝트와 분리(`UNIQUE(source_type,name)`). `project`는 **M:N 매핑표(`source_project`)로 수동 부여**, 기본값 `미분류`. 다주제 채널은 여러 project, 1:1/잡담은 미분류. → **[리뷰 패치] 1:N `source.project_id` → M:N `source_project`**.
- **프라이버시**: 사내·사적 대화 포함 → **로컬 파일 저장, 외부 전송 금지**. 백업 = DB 파일 복사.
- **무결성**: FK·UNIQUE·CHECK. SQLite FK 기본 비활성 → 커넥션마다 `PRAGMA foreign_keys=ON` 필수.
- **멱등 재적재**: 같은 로그 재적재 시 중복 방지 → `context_item`에 dedup 자연키(`external_id`) + `UNIQUE(source_id, external_id)`, `INSERT OR IGNORE`.
- **이식성**: 표준 SQL 유지(추후 PostgreSQL+tsvector 이관 가능).

## Non-Goals (차기 분리)
- 의미(벡터) 검색·임베딩, 웹 UI, 실시간/자동 수집·크롤링, 권한/멀티유저.
- 웹 문서 본문·PDF/문서 텍스트 추출(현재는 링크·메타만).
- `query_log`(질의 이력) — 선택 2차 기능.
- ingest.py·skill.md 실제 구현 및 시연 — 본 요청 이후 실행 단계로 분리(설계는 §7·§9에 포함).

## Acceptance Criteria (완료 기준)
- [ ] `sqlite3 context.db < schema.sql` 실행 시 오류 없이 완료된다.
- [ ] 9개 테이블(project, source_type, source, source_project, person, context_item, link, tag, context_item_tag) 생성.
- [ ] FTS5 가상 테이블 `context_fts` + INSERT/UPDATE/DELETE 동기화 트리거 3종 생성.
- [ ] `source_type` 7종 코드 + `project('미분류')` 시드 삽입.
- [ ] 대표 인덱스(`ix_item_source`, `ix_item_ts`) 및 뷰(`v_project_sources`, `v_recent_context`, `v_tag_links`) 생성.
- [ ] 샘플 데이터 삽입 후 트리거로 `context_fts`가 자동 동기화되고 Q2(FTS MATCH)가 결과를 반환한다.
- [ ] `UNIQUE(source_id, external_id)` 로 동일 메시지 재삽입이 무시됨(멱등성) 검증.
- [ ] 채널을 다른 project로 재매핑(`source_project` 추가)해도 source·메시지가 이중 적재되지 않음(채널 정체성 분리) 검증.
- [ ] 다주제 채널이 여러 project에 매핑되고, project별 Q1이 각각 결과를 반환한다.
- [ ] 대표 질의 Q1~Q3가 시드/샘플 데이터에서 동작한다.

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| 로그 포맷은 `[시각] 이름` 단순형 | 실물 로그 확인 필요 | 실제 포맷 확인: `[YYYY-MM-DD 오전/오후 H:MM] 이름` + 멀티라인 본문, 시스템/이모티콘/첨부 라인 존재 |
| 적재 대상은 Physical AI 프로젝트 위주 | 실제로는 다채널 혼재 | **전 채널 + 웹 문서 + 받은파일메타** 로 확정 |
| 문서/파일 본문까지 넣어야 함 | 파서 복잡도·범위 팽창 위험 | **링크·메타데이터만**(가벼운 MVP)로 확정 |
| PROJECT가 최상위 조직 단위 (Contrarian) | 실제 데이터는 채널 단위, 1:1은 프로젝트 없음, 다주제 채널 존재 | **채널=SOURCE**(프로젝트 무관 정체성), project는 **M:N `source_project`** 수동 매핑(기본 '미분류'). [리뷰 패치: 1:N→M:N] |
| "상세설계"는 문서만 | 실행가능 산출물 필요 여부 | **설계서 + 실행가능 schema.sql** 로 확정 |

## Technical Context
- 관측된 실제 데이터 구조(증거):
  - `<context-DB-path>/메신저 채팅저장/<채널명>/<YYYY-MM-DD>.txt` (현재 하이웍스 저장 포맷)
    - 예: `[채널 A]/2026-07-28.txt`, `이순신/2026-08-03.txt`
    - 라인 헤더: `[2026-07-24 오후 2:12] 홍길동` → 다음 헤더 전까지 본문(멀티라인, 빈 줄 포함).
    - 시스템 라인: `…님이 …다운로드를 완료했습니다.`, 첨부 파일명 단독 라인, `(이모티콘)`.
    - **날짜 = 파일명**, **시각 = 헤더**(오전/오후 12시간제) → 합쳐 `event_ts`.
  - `<context-DB-path>/메신저 받은파일/*` (PDF/PPTX/HWP/… 바이너리) → 메타만.
  - 웹 문서: `https://example.com/doc/<doc-id>` → 링크만.
- DB: SQLite 3 + FTS5. 적재: Python 3 표준 `sqlite3`.

## 4. 논리 스키마

> 이 절이 담고 있던 완전 DDL 초안(테이블·FTS 가상 테이블 정의)은 실행 가능한 산출물인
> `src/schema.sql` 과 중복되어 스키마가 바뀔 때마다 양쪽이 따로 놀 위험이 있었다.
> **정본(canonical) 스키마는 `src/schema.sql` 하나뿐**이며, 사람이 읽는 상세 설명은
> `docs/dev/스키마-설명서.md` 를 참조한다. 이 설계서에는 위 두 문서에 없는 배경·근거·논의만 남긴다.

## 5. FTS 동기화 트리거

`context_fts` 는 external content FTS5 표준 패턴으로, `context_item` 의 INSERT/UPDATE/DELETE 를
트리거 3종(`trg_ci_ai`/`trg_ci_au`/`trg_ci_ad`)이 자동으로 반영한다. 정확한 트리거 정의는
`src/schema.sql`(및 `docs/dev/스키마-설명서.md`)을 참조한다.

## 6. 시드 데이터

시드 값(프로젝트 `'미분류'`, `source_type` 7종, `item_type` 5종)은 `src/schema.sql` 하단에서
관리한다. 상세 내용은 `docs/dev/스키마-설명서.md` 참조.

## 7. Ingest 상세설계 (ingest.py — 실행 단계 구현 대상)

### 7.1 채팅 로그 적재
1. 루트: `메신저 채팅저장/<채널>/<YYYY-MM-DD>.txt` 순회.
2. 채널별 SOURCE 확보: `source(source_type='messenger', name=채널명, uri=폴더 절대경로, is_ephemeral=1)` — `UNIQUE(source_type,name)`로 멱등(프로젝트 무관).
2-1. 프로젝트 매핑: 채널→project(들) 설정표를 참조해 `source_project`에 `INSERT OR IGNORE`. 매핑 없으면 **'미분류'** 1건. 다주제 채널은 project 여러 건 매핑. (프로젝트 재지정 시 소스·메시지 재적재 없이 매핑만 추가/변경)
3. 라인 파싱 정규식:
   `^\[(?P<date>\d{4}-\d{2}-\d{2}) (?P<ampm>오전|오후) (?P<h>\d{1,2}):(?P<m>\d{2})\] (?P<name>.+)$`
   - 오전/오후 12→24시 변환(오전 12시=00시, 오후 12시=12시).
   - `event_ts = f"{date} {HH}:{MM}:00"` (ISO), 단 **날짜는 파일명과 헤더가 일치**(헤더 date 우선).
4. 본문: 헤더 다음 라인부터 다음 헤더 직전까지 결합(내부 빈 줄 보존, 끝 공백 트림).
5. 특수 라인 분류:
   - `…다운로드를 완료했습니다.` / 다운로드 알림 → `item_type='system'`.
   - `(이모티콘)` → `item_type='message'`(content 그대로).
   - 첨부 파일명 단독 라인 → `item_type='file'`(선택: 받은파일과 매칭 시 `link` 부착).
6. PERSON upsert: `display_name` 기준 `INSERT OR IGNORE`.
7. dedup: `external_id = sha1(f"{source_id}|{event_ts}|{name}|{content}|{line_no}")` → `INSERT OR IGNORE`. **line_no(파일 내 라인순번) 포함**으로 같은 분 동일 발화 반복도 보존하면서 재적재 멱등 유지.
8. FTS는 트리거가 자동 동기화 → 별도 처리 불필요.
9. 트랜잭션: 파일 단위 커밋, 커넥션 시작 시 `PRAGMA foreign_keys=ON`.

### 7.2 웹 문서/받은파일 (링크·메타만)
- 웹 문서: `source(type='web_doc', name='공유 문서', uri=문서 URL)` + `link(source_id, url=URL, title=문서명)`.
- 받은파일: `source(type='file', name='메신저 받은파일', uri=폴더 경로)` + 파일별 `link(source_id, url=파일 절대경로, title=파일명, last_checked_at=수정시각)`.
- 설정 입력: `context-db.config.json`의 경로/URL을 사용(과거 `docs/맥락 정보.md` 폴백은 제거됨).

## 8. 대표 질의·뷰

```sql
-- Q1. 프로젝트 최근 맥락 N건(타임라인) — source_project(M:N) 경유
SELECT ci.event_ts, p.display_name, ci.content
FROM context_item ci
JOIN source s          ON s.source_id = ci.source_id
JOIN source_project sp ON sp.source_id = s.source_id
LEFT JOIN person p     ON p.person_id = ci.person_id
WHERE sp.project_id = :project_id
ORDER BY ci.event_ts DESC, ci.context_item_id DESC LIMIT :n;

-- Q2. 키워드 전문검색(FTS)
SELECT ci.event_ts, ci.content
FROM context_fts f
JOIN context_item ci ON ci.context_item_id = f.rowid
WHERE context_fts MATCH :keyword
ORDER BY rank;

-- Q3. 태그로 맥락+링크 조회(M:N + LINK)
SELECT ci.content, l.url
FROM context_item ci
JOIN context_item_tag t ON t.context_item_id = ci.context_item_id
JOIN tag g ON g.tag_id = t.tag_id
LEFT JOIN link l ON l.context_item_id = ci.context_item_id
WHERE g.name = :tag;
```

뷰(`v_project_sources`/`v_recent_context`/`v_tag_links`) 정의는 `src/schema.sql` 이 정본이다.
이 절의 Q1~Q3 는 뷰가 나오기 전 설계 단계에서 검토한 대표 질의 형태를 남긴 것으로,
현재 뷰·CLI 구현과 세부 조인 경로가 다를 수 있다(정확한 동작은 `src/queries.sql`/`src/cli.py` 참조).

## 9. Skill 연동 설계 (context-db.skill.md — 실행 단계 구현 대상)
- **스키마 요약**: 8개 테이블 + FTS + 관계(1:N, M:N) 축약본(§3~4).
- **질의 템플릿**: §8 Q1~Q3의 파라미터화 SQL. 에이전트는 파라미터만 채워 실행.
- **사용 규칙**: 읽기 전용(SELECT) 우선, `LIMIT` 필수, 가능하면 `project_id` 범위 지정, 커넥션에 `PRAGMA foreign_keys=ON`.
- **동작 흐름**: "이 작업 맥락 줘" → 템플릿 선택 → 파라미터 채워 질의 → 결과를 컨텍스트로 사용.

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| PROJECT | core domain | project_id, name, description, created_at | M:N ↔ SOURCE (via SOURCE_PROJECT) |
| SOURCE_TYPE | supporting(lookup) | source_type_id, code, label | 1:N → SOURCE |
| SOURCE | core domain | source_id, source_type_id, name, uri, is_ephemeral | M:N ↔ PROJECT, 1:N → CONTEXT_ITEM, 1:N → LINK |
| SOURCE_PROJECT | associative(M:N) | source_id, project_id | SOURCE ↔ PROJECT 매핑 |
| PERSON | supporting | person_id, display_name, role | 1:N → CONTEXT_ITEM |
| CONTEXT_ITEM | core domain | context_item_id, source_id, person_id, item_type, event_ts, content, external_id | N:1 SOURCE/PERSON, M:N TAG, 1:N LINK, FTS 대상 |
| LINK | supporting | link_id, context_item_id, source_id, url, title | N:1 CONTEXT_ITEM/SOURCE |
| TAG | supporting | tag_id, name | M:N ↔ CONTEXT_ITEM |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 7 | 7 | - | - | N/A |
| 2 | 7 | 0 | 0 | 7 | 100% |
| 3 | 7 | 0 | 0 | 7 | 100% |
| 4 | 7 | 0 | 0 | 7 | 100% |
| 5 | 7 | 0 | 0 | 7 | 100% |
> 5라운드 연속 동일 → 도메인 모델 수렴. [리뷰 패치] 채널↔프로젝트만 1:N→M:N(`source_project` 접합 추가)로 조정, 핵심 엔티티 집합은 유지.

## Interview Transcript
<details>
<summary>Full Q&A (Round 0 + 5 rounds)</summary>

### Round 0 (Topology)
**Q:** 상세설계 대상 최상위 컴포넌트 4개(Schema/DDL, Ingest, Query, Skill) 맞나?
**A:** 4개 그대로 맞음.

### Round 1 (Ingest / Goal·Context)
**Q:** 실제 하이웍스 로그 .txt 샘플/포맷?
**A:** `docs/맥락 정보.md`에 경로 추가 → 실제 로그 폴더 확인: `[YYYY-MM-DD 오전/오후 H:MM] 이름` + 멀티라인 본문.
**Ambiguity:** 46% (Goal 0.65, Constraints 0.45, Criteria 0.5)

### Round 2 (Ingest·Query / Constraints)
**Q:** 적재 대상 소스 범위?
**A:** 하이웍스 채팅 전부 + 웹 문서 + 받은파일 메타데이터.
**Ambiguity:** 38%

### Round 3 (Ingest / Context)
**Q:** 웹 문서/받은파일 적재 깊이?
**A:** 링크·메타데이터만(가벼운 MVP).
**Ambiguity:** 34%

### Round 4 (Schema·Ingest / Goal — Contrarian)
**Q:** 채널→PROJECT 매핑? (PROJECT 최상위 가정 도전)
**A:** 채널=SOURCE, PROJECT는 수동 부여(기본 '미분류'). 스키마 유지.
**Ambiguity:** 29%

### Round 5 (전 컴포넌트 / Success Criteria)
**Q:** 이번 상세설계 결과물(완료 기준)?
**A:** 설계서 + 실행가능 schema.sql.
**Ambiguity:** 17% (임계값 통과)
</details>
