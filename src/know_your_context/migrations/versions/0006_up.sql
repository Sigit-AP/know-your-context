-- Schema v6 up (TASK2 WS-B SUB-160): retrieval-scale indexes, purely additive.
-- symbols.name_tail   : materialized resolver tail key (segment after the last
--                       '.' then the last '::'; byte-equivalent to
--                       ingest.extractor._name_tail — equivalence pinned by tests).
-- files.path_rev      : reversed path; the module-file suffix match becomes a
--                       GLOB prefix on lower(path_rev) (SQLite lower() folds
--                       ASCII only — exactly LIKE's case semantics).
-- symbols_watch       : one-row change counter driven by triggers on symbols so
--                       EVERY write path moves the FTS freshness token (O(1)).
-- idx_invocation_journal_effective_key : conflict-lookup index (cluster A, S3).
-- Mirrors store._SCHEMA_V6_STATEMENTS; parity is pinned by tests.

ALTER TABLE symbols ADD COLUMN name_tail TEXT NOT NULL DEFAULT '';

UPDATE symbols SET name_tail = substr(replace(replace(substr(name, length(rtrim(name, replace(name, '.', ''))) + 1), '::', char(31)), char(31) || ':', char(31)), length(rtrim(replace(replace(substr(name, length(rtrim(name, replace(name, '.', ''))) + 1), '::', char(31)), char(31) || ':', char(31)), replace(replace(replace(substr(name, length(rtrim(name, replace(name, '.', ''))) + 1), '::', char(31)), char(31) || ':', char(31)), char(31), ''))) + 1);

ALTER TABLE files ADD COLUMN path_rev TEXT NOT NULL DEFAULT '';

UPDATE files SET path_rev = (WITH RECURSIVE _rev (p, acc, i) AS (SELECT files.path, '', length(files.path) UNION ALL SELECT p, acc || substr(p, i, 1), i - 1 FROM _rev WHERE i > 0) SELECT acc FROM _rev WHERE i = 0);

CREATE TABLE symbols_watch (    id INTEGER PRIMARY KEY CHECK (id = 1),    seq INTEGER NOT NULL DEFAULT 0);

INSERT INTO symbols_watch (id, seq) VALUES (1, 0);

CREATE TRIGGER symbols_watch_ins AFTER INSERT ON symbols BEGIN UPDATE symbols_watch SET seq = seq + 1 WHERE id = 1; END;

CREATE TRIGGER symbols_watch_upd AFTER UPDATE ON symbols BEGIN UPDATE symbols_watch SET seq = seq + 1 WHERE id = 1; END;

CREATE TRIGGER symbols_watch_del AFTER DELETE ON symbols BEGIN UPDATE symbols_watch SET seq = seq + 1 WHERE id = 1; END;

CREATE INDEX idx_symbols_name_tail ON symbols (name_tail);

CREATE INDEX idx_files_path_rev ON files (lower(path_rev));

CREATE INDEX idx_invocation_journal_effective_key ON invocation_journal (effective_idempotency_key);
