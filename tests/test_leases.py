"""Tests for the node lease engine (W17 SUB-138).

Leases are short-lived exclusive claims on a node: while agent A holds the
lease on node X, agent B's write to that node fails typed (NODE_IN_USE)
instead of silently overwriting. Every transition (acquire/release/conflict/
expiry) is recorded in the hash-chained event log (kind="lease"), so the
activity log and ``verify_chain`` stay honest.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from know_your_context.leases import (
    DEFAULT_LEASE_TTL_SECONDS,
    LEASE_KIND,
    LeaseError,
    LeaseRegistry,
    NodeLease,
    NodeLeaseConflict,
)
from know_your_context.store import EventStore, StoreError

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def store(tmp_path: Path) -> Any:
    store = EventStore(tmp_path / "leases.sqlite3")
    yield store
    store.close()


@pytest.fixture
def registry(store: EventStore, clock: _Clock) -> LeaseRegistry:
    return LeaseRegistry(store, now=clock)


class TestAcquire:
    def test_acquire_returns_lease_with_expiry(self, registry: LeaseRegistry) -> None:
        lease = registry.acquire("n-1", "agent-1", ttl_seconds=60.0)
        assert isinstance(lease, NodeLease)
        assert lease.node_id == "n-1"
        assert lease.actor == "agent-1"
        assert lease.acquired_at == T0.isoformat()
        assert lease.expires_at == (T0 + timedelta(seconds=60.0)).isoformat()

    def test_default_ttl(self, registry: LeaseRegistry) -> None:
        lease = registry.acquire("n-1", "agent-1")
        expected = (T0 + timedelta(seconds=DEFAULT_LEASE_TTL_SECONDS)).isoformat()
        assert lease.expires_at == expected

    def test_same_actor_renews(self, registry: LeaseRegistry, clock: _Clock) -> None:
        first = registry.acquire("n-1", "agent-1", ttl_seconds=60.0)
        clock.advance(30)
        renewed = registry.acquire("n-1", "agent-1", ttl_seconds=60.0)
        assert renewed.actor == "agent-1"
        assert renewed.expires_at > first.expires_at

    def test_other_actor_conflict_raises_typed(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=60.0)
        with pytest.raises(NodeLeaseConflict) as info:
            registry.acquire("n-1", "agent-2", ttl_seconds=60.0)
        error = info.value
        assert error.code == "NODE_IN_USE"
        assert error.node_id == "n-1"
        assert error.holder == "agent-1"
        assert "agent-1" in str(error)
        assert isinstance(error, StoreError)

    def test_conflict_carries_expiry(self, registry: LeaseRegistry) -> None:
        lease = registry.acquire("n-1", "agent-1", ttl_seconds=60.0)
        with pytest.raises(NodeLeaseConflict) as info:
            registry.acquire("n-1", "agent-2")
        assert info.value.expires_at == lease.expires_at

    def test_expired_lease_is_free(self, registry: LeaseRegistry, clock: _Clock) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=10.0)
        clock.advance(11)
        lease = registry.acquire("n-1", "agent-2", ttl_seconds=10.0)
        assert lease.actor == "agent-2"

    def test_distinct_nodes_do_not_conflict(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-1", "agent-1")
        lease = registry.acquire("n-2", "agent-2")
        assert lease.node_id == "n-2"

    @pytest.mark.parametrize("node_id", ["", "   "])
    def test_empty_node_id_rejected(self, registry: LeaseRegistry, node_id: str) -> None:
        with pytest.raises(LeaseError):
            registry.acquire(node_id, "agent-1")

    @pytest.mark.parametrize("actor", ["", "   "])
    def test_empty_actor_rejected(self, registry: LeaseRegistry, actor: str) -> None:
        with pytest.raises(LeaseError):
            registry.acquire("n-1", actor)

    @pytest.mark.parametrize("ttl", [0.0, -1.0, float("inf"), float("nan")])
    def test_invalid_ttl_rejected(self, registry: LeaseRegistry, ttl: float) -> None:
        with pytest.raises(LeaseError):
            registry.acquire("n-1", "agent-1", ttl_seconds=ttl)


class TestAcquireMany:
    def test_atomic_success(self, registry: LeaseRegistry) -> None:
        leases = registry.acquire_many(["n-1", "n-2"], "agent-1", ttl_seconds=60.0)
        assert [lease.node_id for lease in leases] == ["n-1", "n-2"]
        assert registry.holder_of("n-1") is not None
        assert registry.holder_of("n-2") is not None

    def test_atomic_conflict_acquires_nothing(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-2", "agent-1", ttl_seconds=60.0)
        with pytest.raises(NodeLeaseConflict):
            registry.acquire_many(["n-1", "n-2"], "agent-2")
        # All-or-nothing: n-1 was never claimed by agent-2.
        assert registry.holder_of("n-1") is None
        assert registry.holder_of("n-2").actor == "agent-1"  # type: ignore[union-attr]

    def test_check_many_passes_when_free(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-1", "agent-1")
        registry.check_many(["n-1", "n-2"], "agent-1")  # holder re-checks: fine

    def test_check_many_raises_on_conflict(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-1", "agent-1")
        with pytest.raises(NodeLeaseConflict) as info:
            registry.check_many(["n-2", "n-1"], "agent-2")
        assert info.value.node_id == "n-1"


class TestRelease:
    def test_holder_release_frees_node(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-1", "agent-1")
        assert registry.release("n-1", "agent-1") is True
        assert registry.holder_of("n-1") is None

    def test_release_by_other_actor_refused(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-1", "agent-1")
        assert registry.release("n-1", "agent-2") is False
        assert registry.holder_of("n-1").actor == "agent-1"  # type: ignore[union-attr]

    def test_release_unheld_is_false(self, registry: LeaseRegistry) -> None:
        assert registry.release("n-absent", "agent-1") is False

    def test_release_expired_is_false_and_reaped(self, registry: LeaseRegistry, clock: _Clock) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=5.0)
        clock.advance(6)
        assert registry.release("n-1", "agent-1") is False
        assert registry.holder_of("n-1") is None

    def test_force_release_by_operator(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-1", "agent-1")
        assert registry.force_release("n-1", actor="operator") is True
        assert registry.holder_of("n-1") is None

    def test_force_release_unheld_is_false(self, registry: LeaseRegistry) -> None:
        assert registry.force_release("n-absent", actor="operator") is False


class TestListing:
    def test_active_leases_sorted(self, registry: LeaseRegistry) -> None:
        registry.acquire("n-b", "agent-1")
        registry.acquire("n-a", "agent-2")
        active = registry.active_leases()
        assert [lease.node_id for lease in active] == ["n-a", "n-b"]
        assert len(active) == 2

    def test_active_leases_excludes_expired(self, registry: LeaseRegistry, clock: _Clock) -> None:
        registry.acquire("n-old", "agent-1", ttl_seconds=5.0)
        clock.advance(6)
        registry.acquire("n-new", "agent-2", ttl_seconds=60.0)
        active = registry.active_leases()
        assert [lease.node_id for lease in active] == ["n-new"]

    def test_holder_of_expired_is_none(self, registry: LeaseRegistry, clock: _Clock) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=5.0)
        clock.advance(6)
        assert registry.holder_of("n-1") is None

    def test_reap_expired_returns_count(self, registry: LeaseRegistry, clock: _Clock) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=5.0)
        registry.acquire("n-2", "agent-2", ttl_seconds=5.0)
        registry.acquire("n-3", "agent-3", ttl_seconds=600.0)
        clock.advance(6)
        assert registry.reap_expired() == 2
        assert [lease.node_id for lease in registry.active_leases()] == ["n-3"]


class TestAuditTrail:
    def _lease_events(self, store: EventStore) -> list[sqlite3.Row]:
        with store.read() as conn:
            return conn.execute(
                "SELECT event_type, actor, node_id, payload_json FROM event_log "
                "WHERE event_type LIKE 'lease.%' ORDER BY event_seq"
            ).fetchall()

    def test_acquire_records_event(self, registry: LeaseRegistry, store: EventStore) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=60.0)
        rows = self._lease_events(store)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "lease.acquired"
        assert rows[0]["actor"] == "agent-1"
        assert rows[0]["node_id"] == "n-1"

    def test_conflict_records_event(self, registry: LeaseRegistry, store: EventStore) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=60.0)
        with pytest.raises(NodeLeaseConflict):
            registry.acquire("n-1", "agent-2")
        rows = self._lease_events(store)
        conflict = [row for row in rows if row["event_type"] == "lease.conflict"]
        assert len(conflict) == 1
        assert conflict[0]["actor"] == "agent-2"
        assert conflict[0]["node_id"] == "n-1"

    def test_release_records_event(self, registry: LeaseRegistry, store: EventStore) -> None:
        registry.acquire("n-1", "agent-1")
        registry.release("n-1", "agent-1")
        rows = self._lease_events(store)
        assert [row["event_type"] for row in rows] == ["lease.acquired", "lease.released"]

    def test_expiry_records_event(self, registry: LeaseRegistry, store: EventStore, clock: _Clock) -> None:
        registry.acquire("n-1", "agent-1", ttl_seconds=5.0)
        clock.advance(6)
        registry.acquire("n-1", "agent-2", ttl_seconds=5.0)
        rows = self._lease_events(store)
        types = [row["event_type"] for row in rows]
        assert "lease.expired" in types

    def test_lease_events_keep_chain_valid(self, registry: LeaseRegistry, store: EventStore) -> None:
        registry.acquire("n-1", "agent-1")
        with pytest.raises(NodeLeaseConflict):
            registry.acquire("n-1", "agent-2")
        registry.release("n-1", "agent-1")
        report = store.verify_chain()
        assert report.ok is True, report

    def test_lease_events_are_not_knowledge_affecting(self, registry: LeaseRegistry, store: EventStore) -> None:
        registry.acquire("n-1", "agent-1")
        with store.read() as conn:
            row = conn.execute(
                "SELECT kind, knowledge_affecting FROM event_log WHERE event_type LIKE 'lease.%'"
            ).fetchone()
        assert row is not None
        assert str(row["kind"]) == LEASE_KIND
        assert int(row["knowledge_affecting"]) == 0
