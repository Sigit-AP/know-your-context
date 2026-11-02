"""Tests for ``know_your_context.ops.schema_migrations`` (W13 SUB-125).

Explicit, rollback-capable schema migrations v3/v4: canonical SQL artifacts
under ``migrations/versions/``, a typed registry, data-loss guards, and a
post-rollback verifier. Round trip v4→v3→v4 is proven on a real store.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from know_your_context.enums import MigrationDirection
from know_your_context.ops.schema_migrations import (
    MIGRATIONS,
    VERSIONS_DIR,
    Migration,
    RollbackReport,
    SchemaMigrationError,
    plan_rollback,
    recorded_schema_version,
    rollback_to,
    verify_schema,
)
from know_your_context.store import EventStore

SEEN = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "mig.sqlite3"
    store = EventStore(path)
    store.close()
    return path


def _seed_ref_with_reason(path: Path) -> None:
    store = EventStore(path)
    store.upsert_file(
        path="a.py",
        sha256=hashlib.sha256(b"a.py").hexdigest(),
        size=10,
        mtime=1,
        language="python",
        generated_hint=False,
        extraction_version=1,
    )
    store.upsert_symbol(node_id="function:aaa", name="alpha_fn", kind="function", path="a.py", line=1)
    ref_id = store.insert_unresolved_ref(source_node_id="function:aaa", target_name="zzz", kind="calls")
    store.record_resolution_attempt(ref_id, outcome="failed", reason="no_match")
    store.close()


class TestRegistry:
    def test_two_migrations_in_order(self) -> None:
        assert [migration.version for migration in MIGRATIONS] == [3, 4]
        for migration in MIGRATIONS:
            assert isinstance(migration, Migration)

    def test_sql_files_exist(self) -> None:
        for migration in MIGRATIONS:
            assert (VERSIONS_DIR / migration.up_file).is_file()
            assert (VERSIONS_DIR / migration.down_file).is_file()

    def test_up_sql_matches_store_constants(self) -> None:
        """The canonical SQL artifacts and the store's in-code migrations
        cannot drift (the file is the artifact; the store is the applier)."""
        from know_your_context import store as store_module
        from know_your_context.ops.schema_migrations import _normalize

        expected = {3: store_module._SCHEMA_V3_STATEMENTS, 4: store_module._SCHEMA_V4_STATEMENTS}
        for migration in MIGRATIONS:
            file_stmts = migration.up_sql()
            store_stmts = expected[migration.version]
            assert len(file_stmts) == len(store_stmts)
            assert {_normalize(s) for s in file_stmts} == {_normalize(s) for s in store_stmts}

    def test_down_sql_mentions_owned_objects(self) -> None:
        v4 = next(m for m in MIGRATIONS if m.version == 4)
        assert "failure_reason" in " ".join(v4.down_sql())
        v3 = next(m for m in MIGRATIONS if m.version == 3)
        down = " ".join(v3.down_sql())
        for table in ("files", "symbols", "graph_edges", "unresolved_refs"):
            assert table in down

    def test_versions_dir_is_below_migrations(self) -> None:
        assert VERSIONS_DIR.parent.name == "migrations"

    def test_store_migration_loop_does_not_pick_versions_subdir(self, tmp_path: Path) -> None:
        # The store's file-migration loop is non-recursive: our versions/ files
        # must never be applied as file migrations.
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            assert store.applied_migrations() == [1, 2]
        finally:
            store.close()


class TestVerifySchema:
    def test_fresh_store_is_consistent_v4(self, store_path: Path) -> None:
        report = verify_schema(store_path)
        assert report["recorded_version"] == 4
        assert report["consistent"] is True
        assert report["problems"] == []

    def test_missing_store_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaMigrationError):
            verify_schema(tmp_path / "absent.sqlite3")

    def test_ledger_object_disagreement_detected(self, store_path: Path) -> None:
        conn = sqlite3.connect(store_path)
        try:
            conn.execute("ALTER TABLE unresolved_refs DROP COLUMN failure_reason")
            conn.commit()
        finally:
            conn.close()
        report = verify_schema(store_path)
        assert report["consistent"] is False
        assert any("failure_reason" in problem for problem in report["problems"])


class TestRecordedVersion:
    def test_recorded_is_four(self, store_path: Path) -> None:
        assert recorded_schema_version(store_path) == 4


class TestPlanRollback:
    def test_plan_is_read_only(self, store_path: Path) -> None:
        before = verify_schema(store_path)
        plan = plan_rollback(store_path, 3)
        after = verify_schema(store_path)
        assert before == after
        assert [step["version"] for step in plan] == [4]

    def test_plan_to_v2_has_two_steps(self, store_path: Path) -> None:
        plan = plan_rollback(store_path, 2)
        assert [step["version"] for step in plan] == [4, 3]
        assert all(step["direction"] == MigrationDirection.DOWN for step in plan)

    def test_plan_rejects_target_at_or_above_recorded(self, store_path: Path) -> None:
        with pytest.raises(SchemaMigrationError):
            plan_rollback(store_path, 4)

    def test_plan_rejects_target_below_v2(self, store_path: Path) -> None:
        with pytest.raises(SchemaMigrationError):
            plan_rollback(store_path, 1)


class TestRollback:
    def test_rollback_v4_to_v3(self, store_path: Path) -> None:
        report = rollback_to(store_path, 3)
        assert isinstance(report, RollbackReport)
        assert report.from_version == 4
        assert report.to_version == 3
        assert report.direction == MigrationDirection.DOWN
        assert report.verified is True
        after = verify_schema(store_path)
        assert after["recorded_version"] == 3
        assert after["consistent"] is True

    def test_round_trip_v4_v3_v4(self, store_path: Path) -> None:
        rollback_to(store_path, 3)
        # Reopening re-applies v4 transactionally — the store self-heals.
        store = EventStore(store_path)
        try:
            assert recorded_schema_version(store_path) == 4
            with store.read() as conn:
                columns = [row["name"] for row in conn.execute("PRAGMA table_info(unresolved_refs)")]
            assert "failure_reason" in columns
        finally:
            store.close()

    def test_rollback_refuses_data_loss_by_default(self, tmp_path: Path) -> None:
        path = tmp_path / "data.sqlite3"
        _seed_ref_with_reason(path)
        with pytest.raises(SchemaMigrationError) as info:
            rollback_to(path, 3)
        assert info.value.code == "DATA_LOSS_GUARD"

    def test_rollback_with_allow_data_loss(self, tmp_path: Path) -> None:
        path = tmp_path / "mig.sqlite3"
        _seed_ref_with_reason(path)
        report = rollback_to(path, 3, allow_data_loss=True)
        assert report.verified is True
        conn = sqlite3.connect(path)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(unresolved_refs)")]
        finally:
            conn.close()
        assert "failure_reason" not in columns

    def test_rollback_to_v2_drops_v3_surface(self, store_path: Path) -> None:
        report = rollback_to(store_path, 2)
        assert report.verified is True
        conn = sqlite3.connect(path := store_path)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert not {"files", "symbols", "graph_edges", "unresolved_refs", "schema_versions"} & tables
        # The v1/v2 file-migration ledger survives — the pre-v3 era is intact.
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("SELECT version FROM schema_migration ORDER BY version").fetchall()
        finally:
            conn.close()
        assert [row[0] for row in rows] == [1, 2]

    def test_rollback_to_v2_guards_v3_data(self, tmp_path: Path) -> None:
        path = tmp_path / "v3data.sqlite3"
        store = EventStore(path)
        store.upsert_file(
            path="a.py",
            sha256=hashlib.sha256(b"a.py").hexdigest(),
            size=10,
            mtime=1,
            language="python",
            generated_hint=False,
            extraction_version=1,
        )
        store.close()
        with pytest.raises(SchemaMigrationError) as info:
            rollback_to(path, 2)
        assert info.value.code == "DATA_LOSS_GUARD"

    def test_rollback_rejects_target_at_or_above_recorded(self, store_path: Path) -> None:
        with pytest.raises(SchemaMigrationError):
            rollback_to(store_path, 4)

    def test_rollback_missing_store_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaMigrationError):
            rollback_to(tmp_path / "absent.sqlite3", 3)

    def test_rollback_is_atomic_on_object_missing(self, store_path: Path) -> None:
        # Corrupt the ledger state: v4 recorded but the column is gone. The
        # rollback must fail closed and leave the store untouched.
        conn = sqlite3.connect(store_path)
        try:
            conn.execute("ALTER TABLE unresolved_refs DROP COLUMN failure_reason")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(SchemaMigrationError):
            rollback_to(store_path, 3)
        # Ledger unchanged: v4 is still recorded (the rollback never half-applied).
        conn = sqlite3.connect(store_path)
        try:
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_versions")]
        finally:
            conn.close()
        assert 4 in versions

    def test_old_sqlite_refused(self, store_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))
        with pytest.raises(SchemaMigrationError) as info:
            rollback_to(store_path, 3)
        assert info.value.code == "SQLITE_TOO_OLD"
