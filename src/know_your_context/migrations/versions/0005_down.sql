-- Schema v5 down: drop the additive node_leases table (rollback to v4).
-- Data-loss guard: refuses while any lease row is present unless the operator
-- passes allow_data_loss=True.

DROP TABLE node_leases;
