-- Schema v4 up (audit-final-v020 F4): persist the resolver's deterministic
-- failure reason per unresolved_refs row. Mirrors store._SCHEMA_V4_STATEMENTS;
-- parity is pinned by tests.

ALTER TABLE unresolved_refs ADD COLUMN failure_reason TEXT;
