"""Schema v6 (symbols.name_tail + symbols_watch + journal index) at the store level (TASK2 WS-B, SUB-160).

v6 is purely additive: a materialized ``symbols.name_tail`` column (the
resolver tail key: segment after the last ``.``/``::``), its index, the
``symbols_watch`` change counter (trigger-maintained so EVERY write path —
helpers, pipeline raw SQL, future code — moves the freshness token), and the
``invocation_journal.effective_idempotency_key`` conflict-lookup index (S3).
The explicit rollback-capable path is covered in tests/test_schema_migrations.py.
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


def _objects(path: Path, kind: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}
    finally:
        conn.close()


def _columns(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608
    finally:
        conn.close()


def _legacy_v5_store_with_symbols(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine v5 store carrying symbols inserted with the v5-era column list."""
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 5)
    legacy = EventStore(path)
    legacy.close()
    monkeypatch.undo()
    conn = sqlite3.connect(path)
    try:
        conn.executemany(
            "INSERT INTO symbols (node_id, name, kind, path, line, col) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("function:1:0", "pkg.mod.func", "function", "a.py", 1, 0),
                ("function:2:0", "other.func", "function", "b.py", 2, 0),
                ("function:3:0", "a::b", "function", "c.py", 3, 0),
                ("function:4:0", "plain", "function", "d.py", 4, 0),
                ("function:5:0", "a:b", "function", "e.py", 5, 0),
                ("function:6:0", "x.README.md", "variable", "f.py", 6, 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class TestFreshStoreV6:
    def test_fresh_store_records_v6_and_objects(self, store_path: Path) -> None:
        assert _versions(store_path) == [1, 2, 3, 4, 5, 6]
        assert "name_tail" in _columns(store_path, "symbols")
        indexes = _objects(store_path, "index")
        assert "idx_symbols_name_tail" in indexes
        assert "idx_invocation_journal_effective_key" in indexes
        assert "symbols_watch" in _objects(store_path, "table")
        triggers = _objects(store_path, "trigger")
        assert {"symbols_watch_ins", "symbols_watch_upd", "symbols_watch_del"} <= triggers

    def test_upsert_symbol_populates_name_tail(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            store.upsert_symbol(node_id="function:1:0", name="pkg.mod.func", kind="function", path="a.py", line=1)
            store.upsert_symbol(node_id="function:2:0", name="a::b", kind="function", path="b.py", line=1)
            store.upsert_symbol(node_id="function:3:0", name="plain", kind="function", path="c.py", line=1)
            store.upsert_symbol(node_id="function:4:0", name="a:b", kind="function", path="d.py", line=1)
            rows = store.symbols_with_tail("func")
            assert [row.name for row in rows] == ["pkg.mod.func"]
            assert [row.name for row in store.symbols_with_tail("b")] == ["a::b"]
            assert [row.name for row in store.symbols_with_tail("plain")] == ["plain"]
            # A single colon is NOT a separator (extractor _name_tail semantics).
            assert [row.name for row in store.symbols_with_tail("a:b")] == ["a:b"]
        finally:
            store.close()


class TestUpgradeV5ToV6:
    def test_upgrade_backfills_name_tail(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "s.sqlite3"
        _legacy_v5_store_with_symbols(path, monkeypatch)
        store = EventStore(path)
        try:
            assert _versions(path) == [1, 2, 3, 4, 5, 6]
            with store.read() as conn:
                rows = conn.execute("SELECT name, name_tail FROM symbols ORDER BY node_id").fetchall()
            assert [(str(r[0]), str(r[1])) for r in rows] == [
                ("pkg.mod.func", "func"),
                ("other.func", "func"),
                ("a::b", "b"),
                ("plain", "plain"),
                ("a:b", "a:b"),
                ("x.README.md", "md"),
            ]
        finally:
            store.close()

    def test_upgrade_is_transactional_and_rolls_back_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "s.sqlite3"
        monkeypatch.setattr(store_module, "SCHEMA_VERSION", 5)
        legacy = EventStore(path)
        legacy.close()
        monkeypatch.undo()
        broken = tuple(store_module._SCHEMA_V6_STATEMENTS) + ("CREATE TABLE broken (",)
        monkeypatch.setattr(store_module, "_SCHEMA_V6_STATEMENTS", broken)
        with pytest.raises(MigrationError):
            EventStore(path)
        assert _versions(path) == [1, 2, 3, 4, 5]
        assert "name_tail" not in _columns(path, "symbols")
        assert "symbols_watch" not in _objects(path, "table")

    def test_upgrade_reopen_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "s.sqlite3"
        _legacy_v5_store_with_symbols(path, monkeypatch)
        first = EventStore(path)
        first.close()
        second = EventStore(path)
        try:
            assert _versions(path).count(6) == 1
            assert second.verify_chain().ok
        finally:
            second.close()


class TestFailClosed:
    def test_v6_record_without_column_fails_closed(self, store_path: Path) -> None:
        conn = sqlite3.connect(store_path)
        try:
            conn.execute("DROP INDEX idx_symbols_name_tail")
            conn.execute("ALTER TABLE symbols DROP COLUMN name_tail")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(SchemaStateError, match="name_tail"):
            EventStore(store_path)

    def test_column_without_v6_record_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "s.sqlite3"
        monkeypatch.setattr(store_module, "SCHEMA_VERSION", 5)
        legacy = EventStore(path)
        legacy.close()
        monkeypatch.undo()
        conn = sqlite3.connect(path)
        try:
            conn.execute("ALTER TABLE symbols ADD COLUMN name_tail TEXT NOT NULL DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(SchemaStateError, match="name_tail"):
            EventStore(path)

    def test_watch_table_without_v6_record_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "s.sqlite3"
        monkeypatch.setattr(store_module, "SCHEMA_VERSION", 5)
        legacy = EventStore(path)
        legacy.close()
        monkeypatch.undo()
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE symbols_watch (id INTEGER PRIMARY KEY CHECK (id = 1), seq INTEGER NOT NULL)")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(SchemaStateError, match="symbols_watch"):
            EventStore(path)

    def test_too_new_schema_fails_closed(self, store_path: Path) -> None:
        conn = sqlite3.connect(store_path)
        try:
            conn.execute("INSERT INTO schema_versions (version, applied_at, description) VALUES (7, 'x', 'future')")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(SchemaVersionTooNew):
            EventStore(store_path)


class TestSymbolsWatch:
    def _watch_seq(self, store: EventStore) -> int:
        with store.read() as conn:
            return int(conn.execute("SELECT seq FROM symbols_watch WHERE id = 1").fetchone()[0])

    def test_helper_insert_moves_watch(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            before = self._watch_seq(store)
            store.upsert_symbol(node_id="function:1:0", name="alpha", kind="function", path="a.py", line=1)
            assert self._watch_seq(store) > before
        finally:
            store.close()

    def test_raw_sql_bypass_still_moves_watch(self, tmp_path: Path) -> None:
        """The trigger catches write paths that bypass the store helpers
        (the ingest pipeline's raw batch SQL, future code)."""
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            before = self._watch_seq(store)
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO symbols (node_id, name, kind, path, line, col, name_tail) "
                    "VALUES ('function:9:0', 'raw.fn', 'function', 'r.py', 1, 0, 'fn')"
                )
            assert self._watch_seq(store) > before
        finally:
            store.close()

    def test_delete_moves_watch(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            store.upsert_symbol(node_id="function:1:0", name="alpha", kind="function", path="a.py", line=1)
            before = self._watch_seq(store)
            with store.transaction() as conn:
                conn.execute("DELETE FROM symbols WHERE node_id = 'function:1:0'")
            assert self._watch_seq(store) > before
        finally:
            store.close()

    def test_unrelated_writes_do_not_move_watch(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            before = self._watch_seq(store)
            store.upsert_file(
                path="unrelated.py",
                sha256="ab" * 32,
                size=10,
                mtime=1,
                language="python",
                generated_hint=False,
                extraction_version=1,
            )
            assert self._watch_seq(store) == before
        finally:
            store.close()
