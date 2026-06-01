-- UCW Memory schema. Two-scope (global vs project), hybrid retrieval (FTS5 + sqlite-vec).
-- M3 will expand `embeddings` to a real virtual table once sqlite-vec is loaded at runtime.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS facts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scope               TEXT NOT NULL CHECK (scope IN ('global', 'project')),
    predicate           TEXT NOT NULL,            -- e.g. "uses", "decided", "prefers"
    subject             TEXT NOT NULL,
    object              TEXT NOT NULL,
    reason              TEXT NOT NULL,            -- the "because" clause — required by quality gate
    contextual_prefix   TEXT,                     -- Haiku-generated context line
    source_session      TEXT,
    confidence          REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    created_at          INTEGER NOT NULL,         -- unix seconds
    ttl_seconds         INTEGER,                  -- nullable = never expire
    pinned              INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    promoted_to_skill   TEXT,                     -- slug of skill if promoted via /distill
    deleted             INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS facts_scope_pinned ON facts(scope, pinned) WHERE deleted = 0;
CREATE INDEX IF NOT EXISTS facts_subject ON facts(subject) WHERE deleted = 0;
CREATE INDEX IF NOT EXISTS facts_created ON facts(created_at);

-- FTS5 over the raw fact text. Triggers keep it in sync with facts.
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, predicate, object, reason, contextual_prefix,
    content='facts', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS facts_fts_ins AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, predicate, object, reason, contextual_prefix)
    VALUES (new.id, new.subject, new.predicate, new.object, new.reason, new.contextual_prefix);
END;

CREATE TRIGGER IF NOT EXISTS facts_fts_del AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, predicate, object, reason, contextual_prefix)
    VALUES ('delete', old.id, old.subject, old.predicate, old.object, old.reason, old.contextual_prefix);
END;

CREATE TRIGGER IF NOT EXISTS facts_fts_upd AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, predicate, object, reason, contextual_prefix)
    VALUES ('delete', old.id, old.subject, old.predicate, old.object, old.reason, old.contextual_prefix);
    INSERT INTO facts_fts(rowid, subject, predicate, object, reason, contextual_prefix)
    VALUES (new.id, new.subject, new.predicate, new.object, new.reason, new.contextual_prefix);
END;

-- Embeddings live alongside facts. M3 wires this to sqlite-vec virtual table.
CREATE TABLE IF NOT EXISTS embeddings (
    fact_id   INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,                      -- e.g. "voyage-3"
    vector    BLOB NOT NULL,
    dim       INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS instincts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 0.5,
    uses        INTEGER NOT NULL DEFAULT 0,
    last_used   INTEGER,
    promoted_to_skill TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    started         INTEGER NOT NULL,
    ended           INTEGER,
    goal            TEXT,
    summary         TEXT,
    gate_pass_rate  REAL,
    tokens_used     INTEGER,
    context_tokens  INTEGER                       -- injected at SessionStart
);

CREATE TABLE IF NOT EXISTS work_items (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('open', 'wip', 'blocked', 'done', 'abandoned')),
    branch             TEXT,
    pr_url             TEXT,
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS work_item_sessions (
    work_item_id  INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    PRIMARY KEY (work_item_id, session_id)
);

-- Schema version for migrations.
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);
INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (1, strftime('%s','now'));
