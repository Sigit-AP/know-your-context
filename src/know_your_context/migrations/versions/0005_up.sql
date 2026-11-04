-- Schema v5 up (W17 SUB-138): node leases for multi-agent write coordination.
-- One additive table; lease transitions are recorded in the hash-chained
-- event_log (kind='lease') by know_your_context.leases.LeaseRegistry.
-- Mirrors store._SCHEMA_V5_STATEMENTS; parity is pinned by tests.

CREATE TABLE node_leases (
    node_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX node_leases_expiry ON node_leases (expires_at);
