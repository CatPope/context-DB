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
  label          TEXT NOT NULL
);

-- ─────────────────────────────────────────────────────────────
-- 3) SOURCE : 맥락 출처. 채널 폴더 1개 = messenger source 1개.
--    자연키는 (source_type_id, name) — project_id는 정체성이 아닌 가변 속성(리뷰 Issue 1).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source (
  source_id      INTEGER PRIMARY KEY,
  project_id     INTEGER NOT NULL REFERENCES project(project_id),  -- 가변: 재매핑은 UPDATE
  source_type_id INTEGER NOT NULL REFERENCES source_type(source_type_id),
  name           TEXT NOT NULL,          -- 채널명/문서명/파일저장소명
  uri            TEXT,                   -- 폴더 절대경로/웹 문서 URL/파일 경로
  is_ephemeral   INTEGER NOT NULL DEFAULT 0 CHECK (is_ephemeral IN (0,1)),
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source_type_id, name)
);
CREATE INDEX IF NOT EXISTS ix_source_project ON source(project_id);

-- ─────────────────────────────────────────────────────────────
-- 4) PERSON : 발화자. MVP는 display_name 동일 = 동일인으로 취급.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS person (
  person_id    INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL UNIQUE,
  role         TEXT
);

-- ─────────────────────────────────────────────────────────────
-- 5) CONTEXT_ITEM : 맥락 최소 단위(대화 1건·메모·발췌). 전문검색 대상.
--    external_id = dedup 자연키(sha1). UNIQUE(source_id, external_id)로 멱등 재적재.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS context_item (
  context_item_id INTEGER PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES source(source_id),
  person_id       INTEGER REFERENCES person(person_id),
  item_type       TEXT NOT NULL DEFAULT 'message'
                    CHECK (item_type IN ('message','note','excerpt','system','file')),
  event_ts        DATETIME,
  content         TEXT NOT NULL,
  thread_key      TEXT,
  external_id     TEXT,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS ix_item_source ON context_item(source_id);
CREATE INDEX IF NOT EXISTS ix_item_ts     ON context_item(event_ts);
CREATE INDEX IF NOT EXISTS ix_item_person ON context_item(person_id);

-- ─────────────────────────────────────────────────────────────
-- 6) LINK : 외부 자원 URL/파일. 맥락 항목 또는 소스 단독에 부착.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS link (
  link_id         INTEGER PRIMARY KEY,
  context_item_id INTEGER REFERENCES context_item(context_item_id),
  source_id       INTEGER REFERENCES source(source_id),
  url             TEXT NOT NULL,         -- URL 또는 파일 절대경로
  title           TEXT,
  last_checked_at DATETIME,
  CHECK (context_item_id IS NOT NULL OR source_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_link_item   ON link(context_item_id);
CREATE INDEX IF NOT EXISTS ix_link_source ON link(source_id);

-- ─────────────────────────────────────────────────────────────
-- 7) TAG / 8) CONTEXT_ITEM_TAG : 태그 M:N
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tag (
  tag_id INTEGER PRIMARY KEY,
  name   TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS context_item_tag (
  context_item_id INTEGER NOT NULL REFERENCES context_item(context_item_id),
  tag_id          INTEGER NOT NULL REFERENCES tag(tag_id),
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
INSERT OR IGNORE INTO source_type (code, label) VALUES
  ('messenger',   '메신저'),
  ('web_doc',     '웹 문서'),
  ('web_link',    '웹 링크'),
  ('paper',       '논문'),
  ('server_info', '서버 정보'),
  ('file',        '파일'),
  ('note',        '메모');
