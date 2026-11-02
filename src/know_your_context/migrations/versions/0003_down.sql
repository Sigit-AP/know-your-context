-- Schema v3 down: remove the whole universal-graph surface (rollback to the
-- v1/v2 file-migration era). Indexes drop with their tables. The
-- schema_versions ledger itself did not exist before v3, so it goes too.
-- Executed only by ops.schema_migrations inside one BEGIN IMMEDIATE, after the
-- data-loss guard passes.

DROP TABLE IF EXISTS files;

DROP TABLE IF EXISTS symbols;

DROP TABLE IF EXISTS graph_edges;

DROP TABLE IF EXISTS unresolved_refs;

DROP TABLE IF EXISTS schema_versions;
