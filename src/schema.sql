-- context-DB — schema.sql
-- 에이전트 맥락 저장·질의 데이터베이스 (SQLite 3 + FTS5)
-- 설계: .omc/specs/deep-interview-context-db-detailed-design.md
--
-- 실행:  sqlite3 context.db < schema.sql
-- 주의:  SQLite FK는 커넥션마다 아래 PRAGMA로 켜야 함(파일에 영구 저장 안 됨).

PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────
-- 1) PROJECT : 최상위 분류(수동 부여). 채널은 여기에 매핑됨.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS project (
  project_id   INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  description  TEXT,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────
-- 2) SOURCE_TYPE : 소스 유형 조회 테이블
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source_type (
  source_type_id INTEGER PRIMARY KEY,
  code           TEXT NOT NULL UNIQUE,   -- messenger, web_doc, web_link, paper, server_info, file, note
  label          TEXT NOT NULL,
  -- 휘발성은 소스 개별이 아니라 소스 '유형'이 결정한다(3NF: source 에 두면 이행 종속).
  is_ephemeral   INTEGER NOT NULL DEFAULT 0 CHECK (is_ephemeral IN (0,1))
);

-- ─────────────────────────────────────────────────────────────
-- 3) SOURCE : 맥락 출처. 채널 폴더 1개 = messenger source 1개.
--    자연키는 (source_type_id, name) — project_id는 정체성이 아닌 가변 속성(리뷰 Issue 1).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source (
  source_id      INTEGER PRIMARY KEY,
  project_id     INTEGER NOT NULL REFERENCES project(project_id)
                   ON UPDATE CASCADE ON DELETE RESTRICT,   -- 가변: 재매핑은 UPDATE
  source_type_id INTEGER NOT NULL REFERENCES source_type(source_type_id)
                   ON UPDATE CASCADE ON DELETE RESTRICT,
  name           TEXT NOT NULL,          -- 채널명/문서명/파일저장소명
  uri            TEXT,                   -- 폴더 절대경로/웹 문서 URL/파일 경로
  -- is_ephemeral 은 source_type 으로 이관됨(유형이 결정하는 값).
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source_type_id, name)
);
CREATE INDEX IF NOT EXISTS ix_source_project ON source(project_id);

-- ─────────────────────────────────────────────────────────────
-- 4) ITEM_TYPE : 맥락 항목 유형 조회 테이블.
--    source_type 과 같은 성격의 enum 이므로 같은 방식(룩업 테이블)으로 모델링한다.
--    id 는 시드에서 명시적으로 못박는다 — context_item.item_type_id 의 DEFAULT 가
--    특정 id 를 가리키므로 auto rowid 배정에 기대면 깨진다.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS item_type (
  item_type_id INTEGER PRIMARY KEY,
  code         TEXT NOT NULL UNIQUE,   -- message, note, excerpt, system, file
  label        TEXT NOT NULL
);

-- ─────────────────────────────────────────────────────────────
-- 5) PERSON : 발화자. MVP는 display_name 동일 = 동일인으로 취급.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS person (
  person_id    INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL UNIQUE,
  role         TEXT
);

-- ─────────────────────────────────────────────────────────────
-- 6) CONTEXT_ITEM : 맥락 최소 단위(대화 1건·메모·발췌). 전문검색 대상.
--    external_id = dedup 자연키(sha1). UNIQUE(source_id, external_id)로 멱등 재적재.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS context_item (
  context_item_id INTEGER PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES source(source_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
  person_id       INTEGER REFERENCES person(person_id)
                    ON UPDATE CASCADE ON DELETE SET NULL,
  -- DEFAULT 1(=message)을 반드시 유지한다. 빼면 item_type_id 누락 시 FK 가 아니라
  -- NOT NULL 이 먼저 걸려서, FK 를 검증하던 테스트가 조용히 NOT NULL 테스트로 바뀐다.
  item_type_id    INTEGER NOT NULL DEFAULT 1 REFERENCES item_type(item_type_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
  event_ts        DATETIME,
  content         TEXT NOT NULL,
  thread_key      TEXT,
  -- NOT NULL 필수: nullable 이면 SQLite가 NULL을 서로 distinct 로 취급해
  -- 아래 UNIQUE 가 무력화되고 dedup 이 조용히 무너진다.
  external_id     TEXT NOT NULL,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS ix_item_source ON context_item(source_id);
CREATE INDEX IF NOT EXISTS ix_item_ts     ON context_item(event_ts);
CREATE INDEX IF NOT EXISTS ix_item_person ON context_item(person_id);

-- ─────────────────────────────────────────────────────────────
-- 7) LINK : 외부 자원 URL/파일. 맥락 항목 또는 소스 단독에 부착.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS link (
  link_id         INTEGER PRIMARY KEY,
  context_item_id INTEGER REFERENCES context_item(context_item_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
  source_id       INTEGER REFERENCES source(source_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
  url             TEXT NOT NULL,         -- URL 또는 파일 절대경로
  title           TEXT,
  last_checked_at DATETIME,
  -- 배타적 아크: 맥락 항목 또는 소스 중 정확히 한쪽에만 부착된다.
  -- (기존의 OR 조건은 양쪽 동시 부착을 허용했으나 그런 호출부는 없었다)
  CHECK ((context_item_id IS NULL) <> (source_id IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_link_item   ON link(context_item_id);
CREATE INDEX IF NOT EXISTS ix_link_source ON link(source_id);

-- 아크별 부분 유니크 인덱스. 배타적 아크는 단일 UNIQUE 로 표현할 수 없어
-- 그동안 Python 에서 IFNULL(...,-1) 로 흉내내던 것을 DB 제약으로 되돌린다.
CREATE UNIQUE INDEX IF NOT EXISTS ux_link_item
  ON link(context_item_id, url) WHERE context_item_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_link_source
  ON link(source_id, url)       WHERE source_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- 8) TAG / 9) CONTEXT_ITEM_TAG : 태그 M:N
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tag (
  tag_id INTEGER PRIMARY KEY,
  name   TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS context_item_tag (
  context_item_id INTEGER NOT NULL REFERENCES context_item(context_item_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
  tag_id          INTEGER NOT NULL REFERENCES tag(tag_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
  PRIMARY KEY (context_item_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_cit_tag ON context_item_tag(tag_id);

-- ─────────────────────────────────────────────────────────────
-- 전문검색(FTS5) : 외부 콘텐츠 테이블 방식, rowid = context_item_id
-- ─────────────────────────────────────────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(
  content,
  content='context_item',
  content_rowid='context_item_id'
);

-- FTS 동기화 트리거 (external content FTS 표준 패턴)
CREATE TRIGGER IF NOT EXISTS trg_ci_ai AFTER INSERT ON context_item BEGIN
  INSERT INTO context_fts(rowid, content) VALUES (new.context_item_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_ci_ad AFTER DELETE ON context_item BEGIN
  INSERT INTO context_fts(context_fts, rowid, content)
  VALUES ('delete', old.context_item_id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_ci_au AFTER UPDATE ON context_item BEGIN
  INSERT INTO context_fts(context_fts, rowid, content)
  VALUES ('delete', old.context_item_id, old.content);
  INSERT INTO context_fts(rowid, content) VALUES (new.context_item_id, new.content);
END;

-- ─────────────────────────────────────────────────────────────
-- 뷰
-- ─────────────────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS v_project_sources AS
SELECT pr.name AS project, st.label AS type, s.name AS source, s.uri
FROM source s
JOIN project pr     ON pr.project_id = s.project_id
JOIN source_type st ON st.source_type_id = s.source_type_id;

CREATE VIEW IF NOT EXISTS v_recent_context AS
SELECT pr.name AS project, s.name AS source, ci.event_ts,
       p.display_name AS author, ci.content
FROM context_item ci
JOIN source s      ON s.source_id = ci.source_id
JOIN project pr    ON pr.project_id = s.project_id
LEFT JOIN person p ON p.person_id = ci.person_id
ORDER BY ci.event_ts DESC;

CREATE VIEW IF NOT EXISTS v_tag_links AS
SELECT g.name AS tag, ci.content, l.url
FROM tag g
JOIN context_item_tag t ON t.tag_id = g.tag_id
JOIN context_item ci    ON ci.context_item_id = t.context_item_id
LEFT JOIN link l        ON l.context_item_id = ci.context_item_id;

-- ─────────────────────────────────────────────────────────────
-- 시드 데이터
-- ─────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO project (name, description) VALUES ('미분류', '프로젝트 미지정 기본 버킷');

-- messenger 는 메신저 채팅 일반을 의미하며, 현재 적재는 하이웍스 채팅 저장 포맷만 지원한다.
-- item_type: id 를 명시한다. context_item.item_type_id 의 DEFAULT 1 이 message 를
-- 가리키므로 auto rowid 배정에 의존하면 안 된다.
INSERT OR IGNORE INTO item_type (item_type_id, code, label) VALUES
  (1, 'message', '메시지'),
  (2, 'note',    '메모'),
  (3, 'excerpt', '발췌'),
  (4, 'system',  '시스템'),
  (5, 'file',    '파일');

-- is_ephemeral: 메신저 대화만 휘발성(원본이 시간이 지나면 사라짐). 나머지는 영속.
INSERT OR IGNORE INTO source_type (code, label, is_ephemeral) VALUES
  ('messenger',   '메신저',    1),
  ('web_doc',     '웹 문서',   0),
  ('web_link',    '웹 링크',   0),
  ('paper',       '논문',      0),
  ('server_info', '서버 정보', 0),
  ('file',        '파일',      0),
  ('note',        '메모',      0);

-- ─────────────────────────────────────────────────────────────
-- 스키마 버전 — 맨 마지막에 찍는다(앞 DDL이 실패하면 도장이 남지 않도록).
-- src/db.py 의 SCHEMA_VERSION 과 반드시 함께 올린다.
-- ─────────────────────────────────────────────────────────────
PRAGMA user_version = 6;
