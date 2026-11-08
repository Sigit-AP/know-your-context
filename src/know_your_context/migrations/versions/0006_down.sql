-- Schema v6 down: drop the additive retrieval objects (rollback to v5).
-- No data-loss guard: every v6 object is derived/recomputable (name_tail,
-- path_rev) or structural (indexes, triggers, counter) — no user data is lost.

DROP TRIGGER symbols_watch_ins;
DROP TRIGGER symbols_watch_upd;
DROP TRIGGER symbols_watch_del;
DROP TABLE symbols_watch;
DROP INDEX idx_symbols_name_tail;
DROP INDEX idx_files_path_rev;
DROP INDEX idx_invocation_journal_effective_key;
ALTER TABLE symbols DROP COLUMN name_tail;
ALTER TABLE files DROP COLUMN path_rev;
