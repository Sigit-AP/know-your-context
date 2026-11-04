"""Node leases: short-lived exclusive claims for multi-agent write coordination (W17).

The problem: two agents writing the same node concurrently used to silently
overwrite each other — the conflict was invisible until someone read a diff.
A lease makes ownership explicit: the first agent to touch a node holds it
(actor + expiry), and a second agent's attempt fails with the typed
:class:`NodeLeaseConflict` (``NODE_IN_USE``) instead of a silent overwrite.

Design rules:

* **Additive only.** The lease table arrives as schema v5 (one new table, no
  edits to existing tables); the frozen knowledge operations are untouched.
* **The hash chain stays the single writer.** Every lease transition is
  recorded through :meth:`EventStore.insert_event` (kind ``"lease"``,
  ``knowledge_affecting=False``) inside the same transaction as the table
  mutation, so ``verify_chain`` keeps passing and the activity log can render
  lease history without a second source of truth.
* **Fail closed on conflict, never silent.** A conflicting acquire raises AND
  records a ``lease.conflict`` event naming the holder and the expiry.
* **Time is injectable.** Tests drive expiry deterministically; production
  uses wall-clock UTC.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from know_your_context.store import EventStore, StoreError

__all__ = [
    "DEFAULT_LEASE_TTL_SECONDS",
    "LEASE_KIND",
    "LeaseError",
    "LeaseRegistry",
    "NodeLease",
    "NodeLeaseConflict",
]

#: Default lease lifetime when the caller does not name one.
DEFAULT_LEASE_TTL_SECONDS = 120.0

#: The event_log ``kind`` of every lease transition record. The v1 schema
#: pins ``kind IN ('domain', 'audit')`` by CHECK constraint — lease records
#: are operational (not knowledge-affecting), so they ride the audit kind and
#: are distinguished by the ``lease.`` event_type prefix.
LEASE_KIND = "audit"

#: The event_type prefix + vocabulary (also asserted by the activity formatter).
LEASE_EVENT_PREFIX = "lease."
EVENT_ACQUIRED = "lease.acquired"
EVENT_RELEASED = "lease.released"
EVENT_FORCE_RELEASED = "lease.force_released"
EVENT_CONFLICT = "lease.conflict"
EVENT_EXPIRED = "lease.expired"

_MAX_NODE_ID = 512
_MAX_ACTOR = 128
_MAX_TTL_SECONDS = 86400.0  # one day: a lease is a coordination hint, not a lock file


class LeaseError(StoreError):
    """A lease contract violation (typed, fail-closed)."""

    code = "LEASE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class NodeLeaseConflict(LeaseError):
    """The node is leased by another actor and the lease has not expired."""

    code = "NODE_IN_USE"

    def __init__(self, node_id: str, holder: str, expires_at: str) -> None:
        super().__init__(
            f"node {node_id!r} is leased by {holder!r} until {expires_at}; retry after expiry or release it"
        )
        self.node_id = node_id
        self.holder = holder
        self.expires_at = expires_at


@dataclass(frozen=True)
class NodeLease:
    """One active lease row."""

    node_id: str
    actor: str
    acquired_at: str
    expires_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "node_id": self.node_id,
            "actor": self.actor,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }


def _validate_node_id(node_id: str) -> str:
    if not isinstance(node_id, str) or not node_id.strip():
        raise LeaseError(f"node_id must be a non-empty string, got {node_id!r}", code="INVALID_LEASE")
    if len(node_id) > _MAX_NODE_ID:
        raise LeaseError(f"node_id exceeds {_MAX_NODE_ID} characters", code="INVALID_LEASE")
    return node_id.strip()


def _validate_actor(actor: str) -> str:
    if not isinstance(actor, str) or not actor.strip():
        raise LeaseError(f"actor must be a non-empty string, got {actor!r}", code="INVALID_LEASE")
    if len(actor) > _MAX_ACTOR:
        raise LeaseError(f"actor exceeds {_MAX_ACTOR} characters", code="INVALID_LEASE")
    return actor.strip()


def _validate_ttl(ttl_seconds: float) -> float:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
        raise LeaseError(f"ttl_seconds must be a number, got {ttl_seconds!r}", code="INVALID_LEASE")
    ttl = float(ttl_seconds)
    if not math.isfinite(ttl) or ttl <= 0 or ttl > _MAX_TTL_SECONDS:
        raise LeaseError(f"ttl_seconds must be in (0, {_MAX_TTL_SECONDS}], got {ttl_seconds!r}", code="INVALID_LEASE")
    return ttl


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


class LeaseRegistry:
    """The lease engine over one store. Thread-safe via the store's lock."""

    def __init__(self, store: EventStore, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def store(self) -> EventStore:
        return self._store

    # ------------------------------------------------------------- internals

    def _record(self, conn: Any, event_type: str, *, actor: str, node_id: str, payload: dict[str, Any]) -> None:
        self._store.insert_event(
            conn,
            event_id=f"lease-{uuid.uuid4().hex}",
            kind=LEASE_KIND,
            event_type=event_type,
            knowledge_affecting=False,
            actor=actor,
            occurred_at=self._now().isoformat(),
            node_id=node_id,
            payload=payload,
        )

    def _expired_rows(self, conn: Any, now_iso: str) -> list[Any]:
        return conn.execute(
            "SELECT node_id, actor, acquired_at, expires_at FROM node_leases WHERE expires_at <= ? ORDER BY node_id",
            (now_iso,),
        ).fetchall()

    def _reap_conn(self, conn: Any, now_iso: str) -> int:
        """Delete expired rows inside an open transaction, auditing each."""
        rows = self._expired_rows(conn, now_iso)
        for row in rows:
            conn.execute("DELETE FROM node_leases WHERE node_id = ?", (str(row["node_id"]),))
            self._record(
                conn,
                EVENT_EXPIRED,
                actor=str(row["actor"]),
                node_id=str(row["node_id"]),
                payload={
                    "node_id": str(row["node_id"]),
                    "holder": str(row["actor"]),
                    "acquired_at": str(row["acquired_at"]),
                    "expires_at": str(row["expires_at"]),
                },
            )
        return len(rows)

    def _record_conflict(self, conflict: NodeLeaseConflict, *, requester: str) -> None:
        """Persist a ``lease.conflict`` event for a refused attempt.

        The raising transaction rolls back (the refused acquire must not
        commit), so the conflict is recorded in its own committed
        transaction — a hidden conflict is exactly the failure mode this
        engine exists to kill.
        """
        with self._store.transaction() as conn:
            self._record(
                conn,
                EVENT_CONFLICT,
                actor=requester,
                node_id=conflict.node_id,
                payload={
                    "node_id": conflict.node_id,
                    "requester": requester,
                    "holder": conflict.holder,
                    "expires_at": conflict.expires_at,
                },
            )

    # ---------------------------------------------------------------- writes

    def acquire(self, node_id: str, actor: str, *, ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS) -> NodeLease:
        """Claim *node_id* for *actor*; renew if the actor already holds it.

        Raises :class:`NodeLeaseConflict` (recorded as ``lease.conflict``)
        when another actor holds an unexpired lease.
        """
        leases = self.acquire_many([node_id], actor, ttl_seconds=ttl_seconds)
        return leases[0]

    def acquire_many(
        self,
        node_ids: Iterable[str],
        actor: str,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> list[NodeLease]:
        """Atomically claim every node in *node_ids* — all or nothing."""
        actor = _validate_actor(actor)
        ttl = _validate_ttl(ttl_seconds)
        ids = [_validate_node_id(node_id) for node_id in node_ids]
        now = self._now()
        now_iso = now.isoformat()
        expires_iso = (now + timedelta(seconds=ttl)).isoformat()
        try:
            with self._store.transaction() as conn:
                self._reap_conn(conn, now_iso)
                held: dict[str, Any] = {}
                if ids:
                    placeholders = ", ".join("?" for _ in ids)
                    sql = (
                        "SELECT node_id, actor, acquired_at, expires_at FROM node_leases "
                        f"WHERE node_id IN ({placeholders})"  # nosec B608 -- generated '?' marks; values bound
                    )
                    rows = conn.execute(sql, tuple(ids)).fetchall()
                    held = {str(row["node_id"]): row for row in rows}
                for node_id in ids:
                    row = held.get(node_id)
                    if row is not None and str(row["actor"]) != actor:
                        raise NodeLeaseConflict(node_id, str(row["actor"]), str(row["expires_at"]))
                leases: list[NodeLease] = []
                for node_id in ids:
                    row = held.get(node_id)
                    acquired_iso = (
                        str(row["acquired_at"]) if row is not None and str(row["actor"]) == actor else now_iso
                    )
                    conn.execute(
                        "INSERT INTO node_leases (node_id, actor, acquired_at, expires_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT (node_id) DO UPDATE SET actor = excluded.actor, expires_at = excluded.expires_at",
                        (node_id, actor, acquired_iso, expires_iso),
                    )
                    self._record(
                        conn,
                        EVENT_ACQUIRED,
                        actor=actor,
                        node_id=node_id,
                        payload={"node_id": node_id, "actor": actor, "expires_at": expires_iso},
                    )
                    leases.append(
                        NodeLease(node_id=node_id, actor=actor, acquired_at=acquired_iso, expires_at=expires_iso)
                    )
                return leases
        except NodeLeaseConflict as conflict:
            self._record_conflict(conflict, requester=actor)
            raise

    def release(self, node_id: str, actor: str) -> bool:
        """Release *node_id* if *actor* holds it; False otherwise (no event)."""
        node_id = _validate_node_id(node_id)
        actor = _validate_actor(actor)
        now_iso = self._now().isoformat()
        with self._store.transaction() as conn:
            self._reap_conn(conn, now_iso)
            row = conn.execute("SELECT actor, expires_at FROM node_leases WHERE node_id = ?", (node_id,)).fetchone()
            if row is None or str(row["actor"]) != actor:
                return False
            conn.execute("DELETE FROM node_leases WHERE node_id = ?", (node_id,))
            self._record(
                conn,
                EVENT_RELEASED,
                actor=actor,
                node_id=node_id,
                payload={"node_id": node_id, "actor": actor},
            )
            return True

    def force_release(self, node_id: str, *, actor: str) -> bool:
        """Operator release regardless of holder; audited as forced."""
        node_id = _validate_node_id(node_id)
        actor = _validate_actor(actor)
        now_iso = self._now().isoformat()
        with self._store.transaction() as conn:
            self._reap_conn(conn, now_iso)
            row = conn.execute("SELECT actor, expires_at FROM node_leases WHERE node_id = ?", (node_id,)).fetchone()
            if row is None:
                return False
            holder = str(row["actor"])
            conn.execute("DELETE FROM node_leases WHERE node_id = ?", (node_id,))
            self._record(
                conn,
                EVENT_FORCE_RELEASED,
                actor=actor,
                node_id=node_id,
                payload={"node_id": node_id, "holder": holder, "forced_by": actor},
            )
            return True

    # ---------------------------------------------------------------- reads

    def check_many(self, node_ids: Iterable[str], actor: str) -> None:
        """Raise :class:`NodeLeaseConflict` on the first conflicting node.

        Read-only for the caller's own nodes; expired leases are reaped (and
        audited) as a side effect, which keeps the table bounded.
        """
        actor = _validate_actor(actor)
        ids = [_validate_node_id(node_id) for node_id in node_ids]
        now_iso = self._now().isoformat()
        try:
            with self._store.transaction() as conn:
                self._reap_conn(conn, now_iso)
                for node_id in ids:
                    row = conn.execute(
                        "SELECT actor, expires_at FROM node_leases WHERE node_id = ?", (node_id,)
                    ).fetchone()
                    if row is not None and str(row["actor"]) != actor:
                        raise NodeLeaseConflict(node_id, str(row["actor"]), str(row["expires_at"]))
        except NodeLeaseConflict as conflict:
            self._record_conflict(conflict, requester=actor)
            raise

    def holder_of(self, node_id: str) -> NodeLease | None:
        """The active lease on *node_id*, or None (expired leases are reaped)."""
        node_id = _validate_node_id(node_id)
        now_iso = self._now().isoformat()
        with self._store.transaction() as conn:
            self._reap_conn(conn, now_iso)
            row = conn.execute(
                "SELECT node_id, actor, acquired_at, expires_at FROM node_leases WHERE node_id = ?", (node_id,)
            ).fetchone()
        if row is None:
            return None
        return NodeLease(
            node_id=str(row["node_id"]),
            actor=str(row["actor"]),
            acquired_at=str(row["acquired_at"]),
            expires_at=str(row["expires_at"]),
        )

    def active_leases(self) -> list[NodeLease]:
        """Every unexpired lease, node_id order (expired rows reaped first)."""
        now_iso = self._now().isoformat()
        with self._store.transaction() as conn:
            self._reap_conn(conn, now_iso)
            rows = conn.execute(
                "SELECT node_id, actor, acquired_at, expires_at FROM node_leases ORDER BY node_id"
            ).fetchall()
        return [
            NodeLease(
                node_id=str(row["node_id"]),
                actor=str(row["actor"]),
                acquired_at=str(row["acquired_at"]),
                expires_at=str(row["expires_at"]),
            )
            for row in rows
        ]

    def reap_expired(self) -> int:
        """Delete (and audit) every expired lease; returns the count."""
        now_iso = self._now().isoformat()
        with self._store.transaction() as conn:
            return self._reap_conn(conn, now_iso)
