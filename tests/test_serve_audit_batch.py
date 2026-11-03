"""W16 (SUB-137): audit-lane drain batching pins.

Every daemon operation — reads included — journals ``begin``/``complete``
through the single writer thread, and each journal call committed its own
SQLite transaction: a 50-agent storm pays ~1600 serialized commits on the
writer, each holding the store lock while read workers block on their
receipts. ``EventStore.transaction`` is reentrant (savepoints), so a drain
can join every pending audit item into ONE outer transaction: fewer commits,
same ordering, same per-item failure isolation (a failing item rolls back
its own savepoint; the batch survives), and receipts still resolve only
after the COMMIT lands (ack ⇒ durable). Kill switch:
``KNOW_YOUR_CONTEXT_NO_AUDIT_BATCH=1`` restores per-item transactions.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any, cast

import pytest

from _serve_helpers import make_config
from know_your_context.projection import GraphProjector
from know_your_context.serve import superpower as sp
from know_your_context.store import EventStore


def _counting_transaction(store: EventStore, counts: dict[str, int]) -> None:
    """Count OUTER transactions (a savepoint inside an open one is free)."""
    original = store.transaction

    @contextlib.contextmanager
    def counting() -> Iterator[object]:
        if not store._conn.in_transaction:  # noqa: SLF001 - test instrumentation
            counts["outer"] += 1
        with original() as conn:
            yield conn

    store.transaction = counting  # type: ignore[method-assign]


def _arbiter(tmp_path: Path, store: EventStore) -> sp._WriteArbiter:  # noqa: SLF001
    config = make_config(tmp_path)
    projector = GraphProjector(store, output_root=tmp_path / "graph")
    return sp._WriteArbiter(  # noqa: SLF001
        config,
        store=store,
        projector=projector,
        adapter_factory=lambda actor: cast(Any, None),  # never called: no mutations here
    )


def _journal_fn(store: EventStore, key: str, value: str):
    def fn() -> int:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, "2026-11-03T00:00:00+00:00"),
            )
        return 1

    return fn


def _enqueue(arbiter: sp._WriteArbiter, fn) -> Future:  # noqa: SLF001
    future: Future = Future()
    arbiter._audit_q.put((fn, future))  # noqa: SLF001
    return future


class TestAuditDrainBatching:
    def test_burst_drains_in_one_outer_transaction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KNOW_YOUR_CONTEXT_NO_AUDIT_BATCH", raising=False)
        store = EventStore(tmp_path / "soul.sqlite3")
        counts = {"outer": 0}
        _counting_transaction(store, counts)
        arbiter = _arbiter(tmp_path, store)
        futures = [_enqueue(arbiter, _journal_fn(store, f"k-{index}", str(index))) for index in range(6)]
        arbiter._drain_audit()  # noqa: SLF001
        assert all(future.done() for future in futures)
        assert counts["outer"] == 1, f"6 audit items took {counts['outer']} outer transactions"
        with store.read() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM system_state WHERE key LIKE 'k-%'").fetchone()
        assert rows is not None and int(rows[0]) == 6
        store.close()

    def test_failing_item_rolls_back_only_itself(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KNOW_YOUR_CONTEXT_NO_AUDIT_BATCH", raising=False)
        store = EventStore(tmp_path / "soul.sqlite3")
        arbiter = _arbiter(tmp_path, store)

        def boom() -> None:
            with store.transaction() as conn:
                conn.execute("INSERT INTO system_state (key, value, updated_at) VALUES ('poison', 'x', 't')")
                raise RuntimeError("simulated journal failure")

        f_ok1 = _enqueue(arbiter, _journal_fn(store, "k-ok-1", "1"))
        f_bad = _enqueue(arbiter, boom)
        f_ok2 = _enqueue(arbiter, _journal_fn(store, "k-ok-2", "2"))
        arbiter._drain_audit()  # noqa: SLF001
        assert f_ok1.result() == 1 and f_ok2.result() == 1
        with pytest.raises(RuntimeError, match="simulated journal"):
            f_bad.result()
        with store.read() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM system_state WHERE key LIKE 'k-ok-%'").fetchone()
            poison = conn.execute("SELECT COUNT(*) FROM system_state WHERE key = 'poison'").fetchone()
        assert rows is not None and int(rows[0]) == 2, "the good items must survive a sibling's failure"
        assert poison is not None and int(poison[0]) == 0, "the failing item's writes must roll back"
        store.close()

    def test_kill_switch_restores_per_item_transactions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_NO_AUDIT_BATCH", "1")
        store = EventStore(tmp_path / "soul.sqlite3")
        counts = {"outer": 0}
        _counting_transaction(store, counts)
        arbiter = _arbiter(tmp_path, store)
        for index in range(4):
            _enqueue(arbiter, _journal_fn(store, f"k-{index}", str(index)))
        arbiter._drain_audit()  # noqa: SLF001
        assert counts["outer"] == 4, "the kill switch must restore per-item transactions"
        store.close()

    def test_mid_batch_drain_defers_receipts_until_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An audit item drained inside an open group-commit batch rides the
        batch's savepoints, and its receipt must NOT resolve before the batch
        commits (ack ⇒ durability, the SUB-104 invariant)."""
        monkeypatch.delenv("KNOW_YOUR_CONTEXT_NO_AUDIT_BATCH", raising=False)
        store = EventStore(tmp_path / "soul.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        future = _enqueue(arbiter, _journal_fn(store, "k-mid", "1"))
        arbiter._batch_transaction_open = True  # noqa: SLF001 - simulate an open batch
        with store.transaction():
            arbiter._drain_audit()  # noqa: SLF001
            assert not future.done(), "receipt resolved before the batch committed"
        arbiter._batch_transaction_open = False  # noqa: SLF001
        arbiter._flush_pending_audit()  # noqa: SLF001
        assert future.result() == 1
        with store.read() as conn:
            row = conn.execute("SELECT value FROM system_state WHERE key = 'k-mid'").fetchone()
        assert row is not None and row[0] == "1"
        store.close()

    def test_mid_batch_receipts_fail_with_the_batch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the shared commit fails, every deferred receipt fails typed."""
        monkeypatch.delenv("KNOW_YOUR_CONTEXT_NO_AUDIT_BATCH", raising=False)
        store = EventStore(tmp_path / "soul.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        future = _enqueue(arbiter, _journal_fn(store, "k-midfail", "1"))
        arbiter._batch_transaction_open = True  # noqa: SLF001
        arbiter._drain_audit()  # noqa: SLF001
        assert not future.done()
        arbiter._batch_transaction_open = False  # noqa: SLF001
        arbiter._flush_pending_audit(error=RuntimeError("batch commit failed"))  # noqa: SLF001
        with pytest.raises(RuntimeError, match="batch commit failed"):
            future.result()
        store.close()
