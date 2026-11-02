-- Schema v4 down: drop the additive failure_reason column (rollback to v3).
-- Requires SQLite >= 3.35 (ALTER TABLE DROP COLUMN); the runner checks.
-- Data-loss guard: refuses while any row carries a non-NULL failure_reason
-- unless the operator passes allow_data_loss=True.

ALTER TABLE unresolved_refs DROP COLUMN failure_reason;
