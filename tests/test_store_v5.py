"""Schema v5 (node_leases) migration behavior at the store level (W17 SUB-138).

The explicit rollback-capable path is covered by tests/test_schema_migrations.py;
these tests pin the live EventStore migration: fail-closed ledger/object
checks, transactional rollback, and the too-new guard.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import know_your_context.store as store_module
from know_your_context.store import (
    EventStore,
    MigrationError,
    SchemaStateError,
    SchemaVersionTooNew,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "s.sqlite3"
    store = EventStore(path)
    store.close()
    return path


def _versions(path: Path) -> list[int]:
    conn = sqlite3.connect(path)
    try:
        return sorted(int(row[0]) for row in conn.execute("SELECT version FROM schema_versions"))
    finally:
        conn.close()


def test_fresh_store_has_node_leases_and_v5_record(store_path: Path) -> None:
    conn = sqlite3.connect(store_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "node_leases" in tables
    assert 5 in _versions(store_path)


def test_v5_record_without_table_fails_closed(store_path: Path) -> None:
    conn = sqlite3.connect(store_path)
    try:
        conn.execute("DROP TABLE node_leases")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SchemaStateError, match="node_leases"):
        EventStore(store_path)


def test_table_without_v5_record_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "s.sqlite3"
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 4)
    legacy = EventStore(path)  # a genuine capped v4 store
    legacy.close()
    monkeypatch.undo()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE node_leases (node_id TEXT PRIMARY KEY, actor TEXT NOT NULL, "
            "acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SchemaStateError, match="node_leases"):
        EventStore(path)


def test_v5_migration_is_transactional_and_rolls_back_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "s.sqlite3"
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 4)
    legacy = EventStore(path)
    legacy.close()
    monkeypatch.undo()
    broken = tuple(store_module._SCHEMA_V5_STATEMENTS) + ("CREATE TABLE broken (",)
    monkeypatch.setattr(store_module, "_SCHEMA_V5_STATEMENTS", broken)
    with pytest.raises(MigrationError):
        EventStore(path)
    # The failed v5 attempt left no trace: still a clean v4 store.
    assert _versions(path) == [1, 2, 3, 4]
    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "node_leases" not in tables


def test_too_new_schema_fails_closed(store_path: Path) -> None:
    conn = sqlite3.connect(store_path)
    try:
        conn.execute("INSERT INTO schema_versions (version, applied_at, description) VALUES (6, 'x', 'future')")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SchemaVersionTooNew):
        EventStore(store_path)


def test_v5_reopen_is_idempotent(store_path: Path) -> None:
    first = EventStore(store_path)
    first.close()
    second = EventStore(store_path)
    try:
        assert _versions(store_path).count(5) == 1
        assert second.verify_chain().ok
    finally:
        second.close()
