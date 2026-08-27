-- context-DB 대표 질의 모음 (queries.sql)
-- 설계: .omc/specs/deep-interview-context-db-detailed-design.md §8
-- 사용: 파라미터(:name)를 채워 실행. 읽기 전용(SELECT) + LIMIT 권장.
--       커넥션에서 PRAGMA foreign_keys=ON 권장.

-- ─────────────────────────────────────────────────────────────
-- Q1. 프로젝트의 최근 맥락 N건 (타임라인 복원)
-- ─────────────────────────────────────────────────────────────
SELECT ci.event_ts, p.display_name AS author, ci.content
FROM context_item ci
JOIN source s      ON s.source_id = ci.source_id
LEFT JOIN person p ON p.person_id = ci.person_id
WHERE s.project_id = :project_id
ORDER BY ci.event_ts DESC
LIMIT :n;

-- ─────────────────────────────────────────────────────────────
-- Q2. 키워드 전문검색 (FTS5) — rank 순
-- ─────────────────────────────────────────────────────────────
SELECT ci.event_ts, s.name AS source, ci.content
FROM context_fts f
JOIN context_item ci ON ci.context_item_id = f.rowid
JOIN source s        ON s.source_id = ci.source_id
WHERE context_fts MATCH :keyword
ORDER BY rank
LIMIT :n;

-- ─────────────────────────────────────────────────────────────
-- Q3. 태그로 맥락 + 링크 조회 (M:N 접합 + LINK)
-- ─────────────────────────────────────────────────────────────
SELECT ci.event_ts, ci.content, l.url
FROM context_item ci
JOIN context_item_tag t ON t.context_item_id = ci.context_item_id
JOIN tag g              ON g.tag_id = t.tag_id
LEFT JOIN link l        ON l.context_item_id = ci.context_item_id
WHERE g.name = :tag
ORDER BY ci.event_ts DESC;

-- ─────────────────────────────────────────────────────────────
-- Q4. 특정 채널(소스)의 하루 대화 타임라인
--     스레드 = (source_id, 날짜). 별도 thread_key 컬럼은 두 값을 한 문자열에 패킹한
--     1NF 위반이자 중복이라 제거했다.
-- ─────────────────────────────────────────────────────────────
SELECT ci.event_ts, p.display_name AS author, ci.content
FROM context_item ci
JOIN source s      ON s.source_id = ci.source_id
LEFT JOIN person p ON p.person_id = ci.person_id
WHERE s.name = :channel
  AND date(ci.event_ts) = :date     -- 예: '2026-07-28'
ORDER BY ci.event_ts ASC;

-- ─────────────────────────────────────────────────────────────
-- Q5. 특정 인물이 남긴 최근 맥락
-- ─────────────────────────────────────────────────────────────
SELECT ci.event_ts, s.name AS source, ci.content
FROM context_item ci
JOIN person p ON p.person_id = ci.person_id
JOIN source s ON s.source_id = ci.source_id
WHERE p.display_name = :person
ORDER BY ci.event_ts DESC
LIMIT :n;

-- ─────────────────────────────────────────────────────────────
-- 뷰 활용 (schema.sql에 정의됨)
-- ─────────────────────────────────────────────────────────────
-- 프로젝트별 소스 요약
SELECT * FROM v_project_sources ORDER BY project, type;
-- 전체 최근 맥락(프로젝트·소스·작성자 포함)
SELECT * FROM v_recent_context LIMIT :n;
-- 태그별 맥락+링크
SELECT * FROM v_tag_links WHERE tag = :tag;

-- ─────────────────────────────────────────────────────────────
-- 참고: 외부 링크(웹 문서/받은파일) 목록
-- ─────────────────────────────────────────────────────────────
SELECT st.label AS type, s.name AS source, l.title, l.url
FROM link l
JOIN source s        ON s.source_id = l.source_id
JOIN source_type st  ON st.source_type_id = s.source_type_id
ORDER BY st.label, l.title
LIMIT :n;
