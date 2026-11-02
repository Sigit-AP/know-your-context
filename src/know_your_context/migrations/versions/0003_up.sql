-- Schema v3 up: universal knowledge graph (mirrors store._SCHEMA_V3_STATEMENTS).
-- Applied transactionally by the store; kept here as the canonical artifact
-- for audit, repair and parity checks. Parity is pinned by tests.

CREATE TABLE files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    mtime INTEGER NOT NULL,
    language TEXT NOT NULL DEFAULT 'unknown',
    generated_hint INTEGER NOT NULL DEFAULT 0 CHECK (generated_hint IN (0, 1)),
    extraction_version INTEGER NOT NULL
);

CREATE INDEX idx_files_language ON files (language);

CREATE INDEX idx_files_mtime ON files (mtime);

CREATE INDEX idx_files_generated ON files (path) WHERE generated_hint = 1;

CREATE TABLE symbols (
    node_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    col INTEGER NOT NULL DEFAULT -1
);

CREATE INDEX idx_symbols_name ON symbols (name);

CREATE INDEX idx_symbols_lower_name ON symbols (lower(name));

CREATE INDEX idx_symbols_path ON symbols (path, line, col);

CREATE INDEX idx_symbols_kind ON symbols (kind);

CREATE TABLE graph_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER,
    col INTEGER,
    metadata TEXT,
    provenance TEXT
);

CREATE INDEX idx_graph_edges_source_kind ON graph_edges (source, kind);

CREATE INDEX idx_graph_edges_target_kind ON graph_edges (target, kind);

CREATE UNIQUE INDEX idx_graph_edges_identity
    ON graph_edges (source, target, kind, IFNULL(line, -1), IFNULL(col, -1));

CREATE TABLE unresolved_refs (
    ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    name_tail TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'failed')),
    first_seen TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);

CREATE INDEX idx_unresolved_source ON unresolved_refs (source_node_id);

CREATE INDEX idx_unresolved_target_name ON unresolved_refs (target_name);

CREATE INDEX idx_unresolved_pending ON unresolved_refs (first_seen, ref_id) WHERE status = 'pending';

CREATE INDEX idx_unresolved_failed_tail ON unresolved_refs (name_tail) WHERE status = 'failed';
