"""Event-store scaling guards (TASK2 WS-A: findings S1 + S2).

S1: ``insert_event`` must not run ``SELECT COUNT(*) FROM event_log`` on every
insert — that is O(N) per write and O(N^2) over the store's lifetime (verdict
cluster-a §1.2: lane A throughput 645 -> 290 ops/s from 5k to 160k events).
The chain anchor in ``system_state`` is read and incremented in the same
transaction instead, so the insert path is O(1).

S2: ``verify_chain`` must stream the event log, not ``fetchall()`` it —
@160k events the whole-table read peaked at 951 MiB RSS (verdict §1.2), which
OOMs small hosts at ~1M events. The batched iterator keeps memory constant in
chain length.
"""

from __future__ import annotations

import tracemalloc
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain
from know_your_context.store import EventStore

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

# S2 memory budget: verify_chain over _VERIFY_EVENTS events with ~4 KiB
# summaries. The old fetchall() implementation materializes every row at once
# (~12k rows x ~8 KiB Python-side ~= 100 MiB traced); a batched iterator holds
# at most one batch. The budget sits between the two with >2x margin.
_VERIFY_EVENTS = 12000
_VERIFY_PEAK_BUDGET_BYTES = 64 * 1024 * 1024


def _node(node_id: str, summary_size: int = 16) -> Node:
    return Node.model_validate(
        {
            "node_id": node_id,
            "kind": NodeKind.LEAF,
            "domain": PrimaryDomain.P1,
            "source_version_id": "sv-1",
            "payload": {"fact": node_id},
            "summary": f"node {node_id} " + "x" * summary_size,
            "provenance": ("soul/01-identity-compliance.md",),
            "context_tags": ("identity",),
            "scope": "global",
            "created_at": NOW,
            "updated_at": NOW,
            "professional_level": 1,
            "version": 1,
        }
    )


def _node_event(node: Node) -> Event:
    return Event.model_validate(
        {
            "kind": EventKind.NODE_UPSERT,
            "node_id": node.node_id,
            "domain": node.domain,
            "node": node,
            "source_version_id": node.source_version_id,
            "occurred_at": NOW,
        }
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    instance = EventStore(tmp_path / "store.sqlite3")
    yield instance
    instance.close()


def _bulk_events(store: EventStore, count: int, *, summary_size: int = 16, start: int = 0) -> None:
    """Append ``count`` node events in ONE transaction (group-commit shape)."""
    with store.transaction() as conn:
        for i in range(start, start + count):
            node = _node(f"n-{i}", summary_size=summary_size)
            event = _node_event(node)
            store.insert_event(
                conn,
                event_id=event.event_id,
                kind="domain",
                event_type=event.kind.value,
                knowledge_affecting=event.knowledge_affecting,
                actor="test",
                occurred_at=event.occurred_at.isoformat(),
                payload=event.model_dump(mode="python"),
                node_id=event.node_id,
                domain=event.domain.value,
                source_version_id=event.source_version_id,
                node_version=1,
                idempotency_key=f"idem-{i}",
            )


def _anchor(store: EventStore, key: str) -> str | None:
    with store.read() as conn:
        row = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


# ----------------------------------------------------------------------- S1


def test_insert_event_does_not_count_event_log_rows(store: EventStore) -> None:
    """The per-insert anchor update must be O(1): read the anchor, add one.

    A ``COUNT(*)`` over ``event_log`` in the insert path is the S1 regression:
    it turns every write into a full index scan.
    """
    statements: list[str] = []
    with store.transaction() as conn:
        conn.set_trace_callback(statements.append)
    for i in range(5):
        store.append_domain(_node_event(_node(f"n-{i}")), idempotency_key=f"idem-{i}")
    offenders = [sql for sql in statements if "COUNT(*)" in sql.upper() and "event_log" in sql.lower()]
    assert not offenders, f"insert path ran an O(N) count over event_log: {offenders[:1]}"


def test_chain_anchor_tracks_appends(store: EventStore) -> None:
    """Pin: the anchor written by the insert path equals the real chain head."""
    for i in range(7):
        store.append_domain(_node_event(_node(f"n-{i}")), idempotency_key=f"idem-{i}")
    assert _anchor(store, "chain_event_count") == "7"
    with store.read() as conn:
        head = conn.execute("SELECT event_hash FROM event_log ORDER BY event_seq DESC LIMIT 1").fetchone()
    assert _anchor(store, "chain_head_hash") == str(head["event_hash"])
    assert store.verify_chain().ok


# ----------------------------------------------------------------------- S2


def test_verify_chain_streams_the_event_log(store: EventStore) -> None:
    """Peak Python memory during verify_chain stays bounded (no fetchall)."""
    _bulk_events(store, _VERIFY_EVENTS, summary_size=4096)
    tracemalloc.start()
    try:
        report = store.verify_chain()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert report.ok
    assert report.checked == _VERIFY_EVENTS
    assert peak < _VERIFY_PEAK_BUDGET_BYTES, (
        f"verify_chain peaked at {peak / 1024 / 1024:.1f} MiB for {_VERIFY_EVENTS} events; "
        f"budget {_VERIFY_PEAK_BUDGET_BYTES / 1024 / 1024:.0f} MiB — the event log must be streamed"
    )
