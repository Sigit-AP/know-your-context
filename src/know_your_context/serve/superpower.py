"""The superpower daemon: one detached multi-agent server per store.

This is the Wave-7 answer to the codegraph daemon (RESEARCH-ARCH section
1b), with the operator's improvements built in:

* **Explicit capacity with typed backpressure.** At most ``max_agents``
  (default 50) concurrent sessions; overflow connections and full work
  queues are answered with a typed ``SERVER_BUSY`` carrying
  ``retry_after_ms`` — never a crash, never an unbounded queue.
* **Read pool vs single writer.** ``read_pool_size`` (default 8) read-only
  SQLite connections (:class:`ReadOnlyEventStore`, WAL ``mode=ro``) serve
  the read verbs; the mutation verbs are serialized through one
  writer thread. Query execution on the pool never blocks the writer and
  the writer never blocks the pool (WAL).
* **Write arbitration with idempotency dedup.** Every mutation flows
  through the single-writer queue; identical in-flight
  ``(verb, actor, idempotency_key)`` submissions share one execution, and
  the store's own idempotency replay closes the race for completed ones.
* **Per-agent audit.** Every ``tools/call`` must carry
  ``params._meta.actor``; rejections and executions are journaled exactly
  once through the same audit contract as the MCP stdio surface.
* **Scope enforcement.** The dispatch allowlist is the frozen fourteen ops
  plus the two contract-v3 ingestion verbs plus the one contract-v4
  extraction verb and nothing else; the envelope carries no path-like escape
  hatch beyond the confined amendments, and ingestion/extraction paths stay
  confined to the startup-fixed graph root.
* **Supervision.** Atomic ``O_EXCL`` lockfile + pidfile, double-fork/setsid
  detached spawn, refcount + idle-timeout reaping, a liveness watchdog that
  kills a starved daemon, and a proxy-side PPID watchdog (see
  :mod:`know_your_context.serve.proxy`).

Determinism and fail-closed typing follow the house rules: every refusal is
a typed JSON-RPC error or a typed ``isError`` tool result.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import queue
import secrets
import signal
import socket
import sqlite3
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from know_your_context.audit import AuditLog
from know_your_context.mcp_server import (
    DEFAULT_RESOURCE_MAX_BYTES,
    EXPECTED_TOOLS,
    _Adapter,  # noqa: SLF001 - same-package surface reuse
)
from know_your_context.projection import GraphProjector, ProjectorBusy
from know_your_context.security import resolve_within_root
from know_your_context.security.capabilities import (
    CapabilityActorMismatchError,
    CapabilityError,
    CapabilityExpiredError,
    CapabilityForgedError,
    CapabilityMalformedError,
    CapabilityRevokedError,
    CapabilityStore,
    CapabilityStoreError,
    decode_token,
)
from know_your_context.serve import rpc
from know_your_context.serve.budgets import (
    ADAPTER_CACHE_CAP,
    BUCKET_CACHE_CAP,
    LATENCY_RESERVOIR_CAP,
    STATS_ACTOR_CAP,
    assert_static_budgets,
)
from know_your_context.serve.daemon_config import DaemonConfig, DaemonPaths
from know_your_context.serve.watchdog import DrainReport, Heartbeat, SupervisedThread, Watchdog, WatchdogPolicy
from know_your_context.service import KnowledgeService
from know_your_context.store import EventStore, StoreError

__all__ = [
    "READ_TOOLS",
    "WRITE_TOOLS",
    "ActorRequired",
    "CapabilityDenied",
    "DaemonUnavailable",
    "LivenessWatchdog",
    "ReadOnlyEventStore",
    "ReadOnlyStoreError",
    "ScopeDenied",
    "ServeError",
    "ServerBusy",
    "SpawnResult",
    "SuperpowerDaemon",
    "ShuttingDown",
    "UnknownVerb",
    "classify_tool",
    "daemon_status",
    "ensure_daemon",
    "run_daemon",
    "spawn_daemon",
    "stop_daemon",
]

# ------------------------------------------------------------------- verbs

# Lane assignment is by WRITE SEMANTICS, not by name: ``evaluate`` persists a
# score-materialization event when scores move, and ``review`` persists its
# decision record on every call — both must run on the writer (the read pool's
# connections are machine-enforced read-only and would refuse their commits).
READ_TOOLS: frozenset[str] = frozenset(
    {
        "knowledge_query",
        "knowledge_get_node",
        "knowledge_get_subtree",
        "knowledge_path",
        "knowledge_explain",
        "knowledge_affected",
        "knowledge_god_nodes",
    }
)
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "knowledge_log_event",
        "knowledge_record_outcome",
        "knowledge_evaluate",
        "knowledge_review",
        "knowledge_propose_improvement",
        "knowledge_apply_improvement",
        "knowledge_rollback",
        # Contract v4 (extraction-contract-v4.md): the external-agent
        # extraction verb is a write-lane tool; the per-agent admission token
        # bucket applies unchanged.
        "knowledge_submit_extraction",
    }
)
HEAVY_TOOLS: frozenset[str] = frozenset(
    {
        "knowledge_ingest_file",
        "knowledge_ingest_directory",
    }
)

_INTERNAL_ACTOR = "daemon-meta"
_STALE_CODE = "STALE_PROJECTION"
# Admin verbs answered by the daemon itself (local socket, same-uid peers).
_ADMIN_PREFIX = "daemon."
# Kill switches (W7 attention-to-detail: every optimization is switchable).
_NO_GROUP_COMMIT_ENV = "KNOW_YOUR_CONTEXT_NO_GROUP_COMMIT"
# W16 (SUB-137): audit-drain batching kill switch (see _drain_audit).
_NO_AUDIT_BATCH_ENV = "KNOW_YOUR_CONTEXT_NO_AUDIT_BATCH"

_LOGGER = logging.getLogger("know_your_context.serve.superpower")


class ServeError(RuntimeError):
    """A typed daemon failure; ``rpc_code`` is the JSON-RPC application code."""

    rpc_code = rpc.INTERNAL_ERROR

    def __init__(self, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.data = dict(data) if data is not None else None


class ServerBusy(ServeError):
    """Bounded capacity is exhausted; the client should retry after the hint."""

    rpc_code = rpc.SERVER_BUSY

    def __init__(self, message: str, *, retry_after_ms: int) -> None:
        super().__init__(message, data={"retry_after_ms": int(retry_after_ms)})
        self.retry_after_ms = int(retry_after_ms)


class ScopeDenied(ServeError):
    """The session's scope does not include the requested verb.

    Raised at dispatch when e.g. a read-scoped agent calls a mutation verb.
    Fail closed: the call is journaled as a denial and never reaches the
    store; retrying without a scope upgrade always fails the same way.
    """

    rpc_code = rpc.SCOPE_DENIED


class UnknownVerb(ServeError):
    """The requested method is not in the daemon's fixed verb table.

    Unknown methods are rejected at the router — never proxied, never
    defaulted — keeping the daemon's surface identical to the audited
    verb list.
    """

    rpc_code = rpc.UNKNOWN_VERB


class ActorRequired(ServeError):
    """The request carries no ``params._meta.actor``.

    Every ``tools/call`` must name its agent so per-agent audit, quotas and
    fair-queue lanes can attribute the work; anonymous calls are refused
    before dispatch and before any capability check.
    """

    rpc_code = rpc.ACTOR_REQUIRED


class DaemonUnavailable(ServeError):
    """The daemon cannot be reached (socket missing or connection refused).

    Raised by client-side helpers (spawn, stop, status); it carries no
    server state, so the caller should run ``daemon status`` instead of
    retrying blindly.
    """

    rpc_code = rpc.DAEMON_UNAVAILABLE


class ShuttingDown(ServeError):
    """The daemon is draining and rejects new work.

    Raised once shutdown starts: in-flight requests run to their deadline,
    new submissions fail typed instead of queueing behind a closing writer.
    """

    rpc_code = rpc.SHUTTING_DOWN


class DeadlineExceeded(ServeError):
    """The request's deadline elapsed while queued or executing.

    The daemon never delivers a late result: the lane slot is released, the
    audit record closes with this code, and the caller may retry under a
    fresh deadline.
    """

    rpc_code = rpc.DEADLINE_EXCEEDED


class CapabilityDenied(ServeError):
    """W10 (SUB-117): the request's capability token is missing (on a
    non-public verb), malformed, forged, expired, revoked, actor-mismatched,
    or outside its scope/verb allowlist. Every denial is audit-chained."""

    rpc_code = rpc.CAPABILITY_DENIED


def classify_tool(name: str) -> str:
    """Route one tool name to its lane; unknown names fail closed."""
    if name in READ_TOOLS:
        return "read"
    if name in WRITE_TOOLS:
        return "write"
    if name in HEAVY_TOOLS:
        return "heavy"
    raise UnknownVerb(f"unknown tool {name!r}")


def _ensure_verb_sets_frozen() -> None:
    """Guard rail: the lanes must partition exactly the frozen 16-tool surface."""
    if READ_TOOLS & WRITE_TOOLS or READ_TOOLS & HEAVY_TOOLS or WRITE_TOOLS & HEAVY_TOOLS:  # pragma: no cover
        raise AssertionError("lanes overlap")
    if READ_TOOLS | WRITE_TOOLS | HEAVY_TOOLS != frozenset(EXPECTED_TOOLS):  # pragma: no cover
        raise AssertionError("lanes must partition the frozen tool surface")


_ensure_verb_sets_frozen()


# ------------------------------------------------------------ read-only store


class ReadOnlyStoreError(StoreError):
    """A read-pool connection that cannot honor the read-only contract."""


class ReadOnlyEventStore(EventStore):
    """An EventStore view over a ``mode=ro`` WAL connection.

    The pool connections are opened read-only at the SQLite level, so the
    guarantee "reads never write" is enforced by the engine, not by
    discipline. Migrations are never run here: the schema must already match
    the live writer ``reference`` store exactly, or construction fails
    closed. The handle is kept out of the live-store registry (it is not a
    writer a restore could coordinate with).
    """

    def __init__(self, db_path: str | Path, *, reference: EventStore | None, busy_timeout_ms: int = 15000) -> None:
        path = Path(db_path)
        if str(db_path) == ":memory:":
            raise ReadOnlyStoreError("read-pool connections require a file-backed store")
        if reference is None:
            raise ReadOnlyStoreError("a read-pool connection requires the live writer reference")
        if not path.exists():
            raise ReadOnlyStoreError(f"store file {path} does not exist")
        self.db_path = str(path)
        self._migrations_dir = reference._migrations_dir  # noqa: SLF001 - deliberate recipe reuse
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._lock = threading.RLock()
        self._adjacency_sql_cache: dict[tuple[str, tuple[str, ...]], str] = {}
        self._savepoint_counter = 0
        uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro"
        try:
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
        except sqlite3.OperationalError as error:
            raise ReadOnlyStoreError(f"cannot open {path} read-only: {error}") from error
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._assert_schema_matches(reference)

    def _assert_schema_matches(self, reference: EventStore) -> None:
        def versions(conn: sqlite3.Connection) -> tuple[set[int], set[int]]:
            base = {int(row[0]) for row in conn.execute("SELECT version FROM schema_migration")}
            try:
                versioned = {int(row[0]) for row in conn.execute("SELECT version FROM schema_versions")}
            except sqlite3.OperationalError:
                versioned = set()
            return base, versioned

        try:
            mine = versions(self._conn)
            with reference.read() as conn:
                theirs = versions(conn)
        except sqlite3.OperationalError as error:
            raise ReadOnlyStoreError(f"schema introspection failed: {error}") from error
        if mine != theirs:
            raise ReadOnlyStoreError(
                f"schema version mismatch: pool sees {sorted(mine[0])}/{sorted(mine[1])} "
                f"but the writer carries {sorted(theirs[0])}/{sorted(theirs[1])}"
            )


# ----------------------------------------------------------- liveness watchdog


class LivenessWatchdog:
    """Kill the daemon when its heartbeats starve (codegraph #850 policy).

    ``on_stall`` defaults to a hard ``os._exit(70)``: a wedged daemon must
    not hold the lockfile. The policy is fully injectable for tests.
    """

    def __init__(
        self,
        *,
        stall_seconds: float,
        interval_seconds: float,
        on_stall: Callable[[], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stall = float(stall_seconds)
        self._interval = float(interval_seconds)
        self._on_stall = on_stall if on_stall is not None else lambda: os._exit(70)
        self._now = now
        self._last_beat = self._now()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        self._last_beat = self._now()

    def check_once(self) -> bool:
        """One liveness check; fires ``on_stall`` when starved."""
        if self._now() - self._last_beat > self._stall:
            self._on_stall()
            return True
        return False

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self._interval):
                if self.check_once():
                    return

        self._thread = threading.Thread(target=loop, name="superpower-liveness", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


# ------------------------------------------------------------ projector guard


class _ReadOnlyProjector:
    """A projector facade for read lanes: reads delegate, repairs fail busy.

    The writer lane is the only publisher; a read lane that finds a stale
    projection nudges the writer and waits instead of rebuilding (no repair
    stampede, no writes from a read-only store connection). ``ProjectorBusy``
    is the exact seam ``KnowledgeService._repair_projection_once`` already
    treats as "a concurrent writer is repairing; the re-check decides".
    """

    def __init__(self, delegate: GraphProjector) -> None:
        self._delegate = delegate

    def repair_if_stale(self) -> Any:
        raise ProjectorBusy("read lanes never repair; the single writer publishes")

    def rebuild(self, *args: Any, **kwargs: Any) -> Any:
        raise ProjectorBusy("read lanes never rebuild; the single writer publishes")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


# --------------------------------------------------------------- audit bridge


class _ForwardingAuditLog:
    """Route read-lane journal writes through the single writer thread.

    The hash-chained event log has exactly one appender; read lanes enqueue
    their ``begin``/``complete`` onto the writer's internal audit lane and
    block for the receipt, so audit ordering and the chain stay
    single-writer consistent while query execution stays on the pool.
    """

    def __init__(self, arbiter: _WriteArbiter, delegate: AuditLog) -> None:
        self._arbiter = arbiter
        self._delegate = delegate

    def begin(self, invocation: Any) -> Any:
        return self._arbiter.run_audit(lambda: self._delegate.begin(invocation))

    def complete(self, invocation_id: str, outcome: Any) -> Any:
        return self._arbiter.run_audit(lambda: self._delegate.complete(invocation_id, outcome))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


# ------------------------------------------------------------- lane plumbing


class _FairQueue:
    """Bounded per-actor FIFO lanes with round-robin drain (anti-starvation).

    One spamming agent can fill only its own lane — at ``per_actor_cap`` it
    receives typed ``SERVER_BUSY`` while every other actor's items still
    schedule. The consumer rotates over non-empty lanes, so no actor waits
    behind another actor's backlog longer than one item per live actor.

    Tiger Style (W10): the accounting invariants — lane total == counter,
    rotation mirrors exactly the non-empty lanes, no lane past its cap —
    are asserted on every mutation (``_check_invariants_locked``). A
    violation is a programmer error and crashes loudly in dev/test instead
    of silently breaking the capacity contract. ``now`` is the DST seam:
    production passes the default ``time.monotonic``; the simulation
    harness injects virtual time.
    """

    def __init__(
        self,
        *,
        total_cap: int,
        per_actor_cap: int,
        retry_after_ms: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        assert int(total_cap) >= 1, f"total_cap must be >= 1, got {total_cap}"
        assert 1 <= int(per_actor_cap) <= int(total_cap), (
            f"per_actor_cap must be in [1, total_cap={total_cap}], got {per_actor_cap}"
        )
        assert int(retry_after_ms) >= 0, f"retry_after_ms must be >= 0, got {retry_after_ms}"
        self._total_cap = int(total_cap)
        self._per_actor_cap = int(per_actor_cap)
        self._retry_after_ms = int(retry_after_ms)
        self._now = now
        self._cond = threading.Condition()
        self._lanes: dict[str, collections.deque[Any]] = {}
        self._rotation: collections.deque[str] = collections.deque()
        self._total = 0

    def _check_invariants_locked(self) -> None:
        """Re-prove the queue's accounting (caller holds ``self._cond``)."""
        lane_total = sum(len(lane) for lane in self._lanes.values())
        assert lane_total == self._total, f"queue accounting drift: lanes hold {lane_total}, counter says {self._total}"
        assert self._total <= self._total_cap, f"queue holds {self._total} beyond its static budget {self._total_cap}"
        non_empty = {actor for actor, lane in self._lanes.items() if lane}
        assert set(self._rotation) == non_empty, (
            f"rotation {sorted(self._rotation)} does not mirror the non-empty lanes {sorted(non_empty)}"
        )
        assert len(self._rotation) == len(set(self._rotation)), "an actor sits twice in the rotation"
        for actor, lane in self._lanes.items():
            assert 0 < len(lane) <= self._per_actor_cap, (
                f"lane {actor!r} holds {len(lane)} items (cap {self._per_actor_cap})"
            )

    def put(self, actor: str, item: Any) -> None:
        with self._cond:
            lane = self._lanes.get(actor)
            lane_len = len(lane) if lane is not None else 0
            if lane_len >= self._per_actor_cap:
                raise ServerBusy(
                    f"per-agent quota exhausted for {actor!r} ({self._per_actor_cap} queued); retry after the hint",
                    retry_after_ms=self._retry_after_ms,
                )
            if self._total >= self._total_cap:
                raise ServerBusy(
                    "the queue is full; retry after the hint",
                    retry_after_ms=self._retry_after_ms,
                )
            # Create the lane only after every refusal: a refused put must
            # leave no empty orphan lane behind (W10 finding — the orphan
            # poisoned the rotation-mirror invariant and leaked one deque
            # per capped actor).
            if lane is None:
                lane = collections.deque()
                self._lanes[actor] = lane
            lane.append(item)
            self._total += 1
            if lane_len == 0:
                self._rotation.append(actor)
            self._check_invariants_locked()
            self._cond.notify()

    def get(self, timeout: float, *, exclude: frozenset[str] = frozenset()) -> Any | None:
        deadline = self._now() + timeout
        with self._cond:
            while not self._rotation:
                remaining = deadline - self._now()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            # Batch-gather fairness: skip lanes already represented in the
            # caller's batch while any other lane holds work, so phased
            # arrivals (all clients of a batch resubmitting at once after the
            # commit) cannot hand one spammer several slots of a single
            # batch. Falls back to an excluded lane when it is the only one
            # holding work — the queue is work-conserving, never idle. After
            # the skip loop the chosen lane is at the front of the rotation.
            for _ in range(len(self._rotation)):
                if self._rotation[0] in exclude:
                    self._rotation.rotate(-1)
                else:
                    break
            actor = self._rotation[0]
            assert actor not in exclude or all(member in exclude for member in self._rotation), (
                f"fairness breach: excluded lane {actor!r} chosen while a non-excluded lane holds work"
            )
            self._rotation.popleft()
            lane = self._lanes[actor]
            item = lane.popleft()
            self._total -= 1
            if lane:
                self._rotation.append(actor)  # still has items: back of the rotation
            else:
                del self._lanes[actor]
            self._check_invariants_locked()
            return item

    @property
    def depth(self) -> int:
        with self._cond:
            return self._total

    def actor_depth(self, actor: str) -> int:
        with self._cond:
            lane = self._lanes.get(actor)
            return len(lane) if lane is not None else 0

    def drain(self) -> list[Any]:
        """Remove and return every pending item (shutdown path)."""
        with self._cond:
            items: list[Any] = []
            for lane in self._lanes.values():
                items.extend(lane)
            self._lanes.clear()
            self._rotation.clear()
            self._total = 0
            self._check_invariants_locked()
            return items


def _await(future: Future) -> Any:
    return future.result()


def _wire_dump(model: Any) -> dict[str, Any]:
    """SDK model → legacy-era wire dict (modern envelope keys stripped)."""
    dumped = model.model_dump(by_alias=True, exclude_none=True)
    for modern_key in ("cacheScope", "resultType", "ttlMs"):
        dumped.pop(modern_key, None)
    return dumped  # type: ignore[no-any-return]


def _call_tool(adapter: _Adapter, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one adapter call on this thread (the adapter body is synchronous)."""
    result = asyncio.run(adapter.call_tool(tool_name, arguments))
    return _wire_dump(result)


def _is_stale_result(result: Mapping[str, Any]) -> bool:
    if not result.get("isError"):
        return False
    try:
        content: Any = result.get("content") or ()
        text = str(content[0]["text"])
        return str(json.loads(text)["error"]["code"]) == _STALE_CODE
    except KeyError, IndexError, TypeError, json.JSONDecodeError:
        return False


def _list_resources(adapter: _Adapter) -> dict[str, Any]:
    return _wire_dump(adapter.list_resources())


async def _read_resource(adapter: _Adapter, uri: str) -> dict[str, Any]:
    from mcp.shared.exceptions import MCPError

    try:
        result = await adapter.read_resource(uri)
    except MCPError as error:
        raise rpc.ProtocolError(rpc.INVALID_PARAMS, str(error)) from None
    return _wire_dump(result)


class _TokenBucket:
    """Per-agent admission bucket: sustained rate + burst, typed refusal.

    Tiger Style (W10): the token level is asserted inside its static
    envelope ``[0, burst]`` on every call; ``now`` is the DST seam
    (production: ``time.monotonic``; simulation: virtual time).
    """

    def __init__(self, *, rate_per_second: float, burst: int, now: Callable[[], float] = time.monotonic) -> None:
        assert float(rate_per_second) > 0.0, f"rate_per_second must be > 0, got {rate_per_second}"
        assert int(burst) >= 1, f"burst must be >= 1, got {burst}"
        self._rate = float(rate_per_second)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._now = now
        self._updated = self._now()

    def retry_after_seconds(self) -> float | None:
        """Consume one token; None when admitted, else seconds until one refills."""
        now = self._now()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        assert 0.0 <= self._tokens <= self._capacity, (
            f"token level {self._tokens} escaped its static envelope [0, {self._capacity}]"
        )
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return None
        return (1.0 - self._tokens) / self._rate


class _SessionToken:
    """Liveness handle shared by a session thread and its queued work.

    ``now`` is the DST seam (production: ``time.monotonic``; simulation:
    virtual time), so the lease can be replayed deterministically.

    ``in_flight`` counts requests the daemon is actively answering for this
    session. The session lease measures *idleness*: a session whose silence
    is the daemon itself working on its request is not abandoned, so the
    reaper must never close it mid-request (SUB-132 — the W15 storm flake:
    a >lease response latency produced a bare EOF, 'daemon closed the
    connection'). A dead client with in-flight work frees its slot anyway:
    the response write fails and the session thread exits.
    """

    __slots__ = ("_inflight", "_inflight_lock", "_now", "alive", "last_seen", "session_id")

    def __init__(self, session_id: str, *, now: Callable[[], float] = time.monotonic) -> None:
        assert str(session_id), "session_id must be a non-empty string"
        self.alive = True
        self._now = now
        self.last_seen = self._now()
        self.session_id = session_id
        self._inflight = 0
        self._inflight_lock = threading.Lock()

    def beat(self) -> None:
        self.last_seen = self._now()

    def begin_request(self) -> None:
        with self._inflight_lock:
            self._inflight += 1

    def end_request(self) -> None:
        with self._inflight_lock:
            assert self._inflight > 0, "session request ended without a matching begin"
            self._inflight -= 1
        # Answering is also activity: idleness is measured from the last
        # observed traffic in either direction, so a just-answered session is
        # not instantly reapable while the client turns around.
        self.beat()

    @property
    def in_flight(self) -> int:
        with self._inflight_lock:
            return self._inflight


@dataclass
class _Mutation:
    tool_name: str
    arguments: dict[str, Any]
    actor: str
    future: Future
    dedup_key: tuple[str, str, str] | None
    deadline: float | None = None
    session: _SessionToken | None = None
    # Captured outcome; delivered by _resolve_mutation only AFTER the
    # surrounding transaction commits, so an acknowledged write is durable.
    outcome: dict[str, Any] | None = None
    error: BaseException | None = None


@dataclass
class _ReadItem:
    tool_name: str
    arguments: dict[str, Any]
    actor: str
    future: Future
    deadline: float | None = None
    session: _SessionToken | None = None


# ------------------------------------------------------------- write arbiter


class _WriteArbiter:
    """The single-writer queue: mutations, audit appends, projection publish.

    Loop discipline: drain the internal audit lane first (read lanes block on
    it), then one client mutation, then publish the projection if any write
    moved the watermark or a read lane nudged. One thread touches the writer
    store, ever.
    """

    def __init__(
        self,
        config: DaemonConfig,
        *,
        store: EventStore,
        projector: GraphProjector,
        adapter_factory: Callable[[str], _Adapter],
    ) -> None:
        self._config = config
        self._store = store
        self._projector = projector
        self._adapter_factory = adapter_factory
        self._write_q = _FairQueue(
            total_cap=config.write_queue_size,
            per_actor_cap=config.per_actor_write_cap,
            retry_after_ms=config.retry_after_ms,
        )
        # Bounded audit lane (Tiger Style static budget): overflow is
        # blocking backpressure on the read lanes — never an unbounded
        # queue. Deadlock-free because this writer drains the audit lane
        # FIRST on every loop iteration.
        self._audit_q: queue.Queue[tuple[Callable[[], Any], Future] | None] = queue.Queue(
            maxsize=config.audit_queue_size
        )
        self._in_flight: dict[tuple[str, str, str], Future] = {}
        self._in_flight_lock = threading.Lock()
        # Static bound: queued deduped writes (≤ write_queue_size) plus the
        # executing batch (≤ group_commit_batch); asserted on submit.
        self._in_flight_bound = int(config.write_queue_size) + int(config.group_commit_batch)
        self._publish = threading.Condition()
        self._publish_requested = False
        self._dirty = False
        self._published_watermark = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="superpower-writer", daemon=True)
        self._started = False
        self._executing = 0  # batch members in flight right now (drain watches this)
        # SUB-104 regression guard (Tiger Style): True exactly while the
        # outer group-commit transaction is open; _resolve_mutation asserts
        # against it so a client ack can never be delivered pre-COMMIT.
        self._batch_transaction_open = False
        self._group_commit_enabled = bool(config.group_commit) and os.environ.get(_NO_GROUP_COMMIT_ENV) != "1"
        self._audit_batch_enabled = os.environ.get(_NO_AUDIT_BATCH_ENV) != "1"
        # W16 (SUB-137): receipts of audit items drained INSIDE an open
        # group-commit batch wait here until the batch commits.
        self._pending_audit: list[tuple[Future, Any]] = []

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._published_watermark = self._store.knowledge_high_watermark()
        self._thread.start()
        self._started = True

    def stop(self) -> None:
        self._stop.set()
        if self._started:
            self._thread.join(timeout=15.0)
        self._flush_pending_audit(error=ShuttingDown("the daemon is shutting down"))
        for item in self._write_q.drain():
            if not item.future.done():
                item.future.set_exception(ShuttingDown("the daemon is shutting down"))
            # The drained item leaves the dedup map too: a stopped arbiter
            # must not hold entries for work that will never execute.
            if item.dedup_key is not None:
                with self._in_flight_lock:
                    self._in_flight.pop(item.dedup_key, None)
        while True:
            try:
                audit_item = self._audit_q.get_nowait()
            except queue.Empty:
                break
            if audit_item is None:
                continue
            _, future = audit_item
            if not future.done():
                future.set_exception(ShuttingDown("the daemon is shutting down"))

    # ------------------------------------------------------------- submission

    def submit_mutation(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        actor: str,
        deadline: float | None = None,
        session: _SessionToken | None = None,
    ) -> dict[str, Any]:
        """Enqueue one mutation; identical in-flight keys share one execution."""
        idempotency_key = arguments.get("idempotency_key")
        dedup_key = (tool_name, actor, str(idempotency_key)) if idempotency_key is not None else None
        if dedup_key is not None:
            with self._in_flight_lock:
                existing = self._in_flight.get(dedup_key)
                if existing is not None:
                    return _await(existing)
        future: Future = Future()
        if dedup_key is not None:
            with self._in_flight_lock:
                existing = self._in_flight.get(dedup_key)
                if existing is not None:
                    return _await(existing)
                self._in_flight[dedup_key] = future
                assert len(self._in_flight) <= self._in_flight_bound, (
                    f"dedup map holds {len(self._in_flight)} entries beyond its static bound "
                    f"{self._in_flight_bound} (queued + executing batch)"
                )
        try:
            self._write_q.put(actor, _Mutation(tool_name, arguments, actor, future, dedup_key, deadline, session))
        except ServerBusy:
            if dedup_key is not None:
                with self._in_flight_lock:
                    self._in_flight.pop(dedup_key, None)
            raise
        return _await(future)

    def run_audit(self, fn: Callable[[], Any]) -> Any:
        """Run one journal append on the writer thread, blocking for it.

        The lane is bounded: a full lane applies blocking backpressure to
        the calling read worker (the writer drains audit first every loop,
        so the wait is one writer iteration at most). During shutdown the
        put retries short-circuit into a typed ``ShuttingDown`` instead of
        ever hanging the caller.
        """
        if self._stop.is_set():
            raise ShuttingDown("the daemon is shutting down")
        future: Future = Future()
        while True:
            try:
                self._audit_q.put((fn, future), timeout=0.05)
                break
            except queue.Full:
                if self._stop.is_set():
                    raise ShuttingDown("the daemon is shutting down") from None
        return _await(future)

    def request_publish(self) -> None:
        with self._publish:
            self._publish_requested = True
            self._publish.notify_all()

    def wait_for_publish(self, since: int, timeout: float) -> int:
        """Block until the published watermark moves past ``since`` (or timeout)."""
        deadline = time.monotonic() + timeout
        with self._publish:
            while self._published_watermark <= since:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._publish.wait(timeout=remaining)
            return self._published_watermark

    @property
    def write_queue_depth(self) -> int:
        return self._write_q.depth

    @property
    def executing(self) -> int:
        """Batch members executing right now; the drain waits for these too."""
        return self._executing

    @property
    def audit_queue_depth(self) -> int:
        return self._audit_q.qsize()

    @property
    def published_watermark(self) -> int:
        with self._publish:
            return self._published_watermark

    # ------------------------------------------------------------------- loop

    def _run(self) -> None:
        while not self._stop.is_set():
            self._drain_audit()
            item = self._write_q.get(timeout=0.05)
            if item is not None:
                batch = [item, *self._gather_batch(item)]
                if self._group_commit_enabled and len(batch) > 1:
                    # One outer transaction; every per-mutation journal,
                    # domain append and audit joins it via savepoints
                    # (EventStore.transaction reentrancy). A failing member
                    # rolls back only its own savepoint — the batch survives.
                    # W16: the audit lane drains BETWEEN members so read lanes
                    # wait at most one member (~one rebuild) for their journal
                    # receipts; those receipts stay deferred until the COMMIT.
                    assert not self._batch_transaction_open
                    assert self._executing == 0
                    self._executing = len(batch)
                    self._batch_transaction_open = True
                    batch_error: BaseException | None = None
                    try:
                        with self._store.transaction():
                            for member in batch:
                                self._execute_mutation(member)
                                self._drain_audit()
                    except BaseException as error:  # noqa: BLE001 - the outer commit failed
                        # Nothing in the batch committed; every member fails.
                        batch_error = error
                        for member in batch:
                            if member.error is None:
                                member.error = error
                    finally:
                        self._batch_transaction_open = False
                        self._executing = 0
                    # Resolve only after COMMIT: an ack implies durability.
                    self._flush_pending_audit(error=batch_error)
                    for member in batch:
                        self._resolve_mutation(member)
                else:
                    assert self._executing == 0
                    self._executing = len(batch)
                    try:
                        for member in batch:
                            self._execute_mutation(member)
                            self._resolve_mutation(member)
                    finally:
                        self._executing = 0
                self._dirty = True
                # W16 (SUB-137): read lanes block on their journal receipts;
                # draining again between the batch and the publish halves
                # their worst-case wait (instrumented storm: audit round
                # trips p50 ~52ms when the writer drained once per loop).
                self._drain_audit()
            if self._dirty or self._publish_requested:
                self._publish_now()
        self._drain_audit()

    def _gather_batch(self, first: _Mutation) -> list[_Mutation]:
        """Pull the rest of a group-commit burst from the write queue.

        Batch cap or window, whichever first; at most one item per actor
        while any other lane holds work (the queue enforces the fallback).
        Returns the items AFTER ``first``.
        """
        if not self._group_commit_enabled:
            return []
        batch: list[_Mutation] = []
        deadline = time.monotonic() + self._config.group_commit_window_seconds
        exclude: frozenset[str] = frozenset({first.actor})
        while len(batch) + 1 < self._config.group_commit_batch:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            nxt = self._write_q.get(timeout=remaining, exclude=exclude)
            if nxt is None:
                break
            batch.append(nxt)
            exclude = frozenset({*exclude, nxt.actor})
        assert len(batch) + 1 <= self._config.group_commit_batch, (
            f"batch of {len(batch) + 1} exceeds the static group-commit budget {self._config.group_commit_batch}"
        )
        return batch

    def _execute_mutation(self, item: _Mutation) -> None:
        """Run one mutation, capturing the outcome — never delivering it.

        Delivery happens in :meth:`_resolve_mutation`, which the batch path
        calls only after the outer group-commit transaction has committed:
        a client acknowledgement implies durability, even under kill -9.
        """
        try:
            if item.deadline is not None and time.monotonic() > item.deadline:
                raise DeadlineExceeded("the request expired in the write queue")
            if item.session is not None and not item.session.alive:
                raise ShuttingDown("the owning session is gone")
            item.outcome = _call_tool(self._adapter_factory(item.actor), item.tool_name, item.arguments)
        except BaseException as error:  # noqa: BLE001 - captured, delivered post-commit
            item.error = error
        if self._config.mutation_delay_seconds > 0:
            # Documented backpressure test hook: per-mutation delay, so the
            # bounded write queue provably fills under a flood.
            time.sleep(self._config.mutation_delay_seconds)
        assert (item.outcome is not None) != (item.error is not None), (
            f"mutation {item.tool_name} must capture exactly one of outcome/error "
            f"(outcome={'set' if item.outcome is not None else 'unset'}, "
            f"error={'set' if item.error is not None else 'unset'})"
        )

    def _resolve_mutation(self, item: _Mutation) -> None:
        """Deliver the captured outcome to the waiting caller (post-commit).

        The future resolves WITHOUT the dedup lock: sharers block on
        ``future.result()`` while holding it, so taking the lock first would
        deadlock writer and sharers. The dedup entry is popped afterwards —
        a same-key submitter arriving in that gap simply lands a store-level
        idempotent replay of the committed write.
        """
        # SUB-104 regression guard: an acknowledged write is a durable
        # write. Resolving while the group-commit transaction is open would
        # ack data that kill -9 can still erase — programmer error, crash.
        assert not self._batch_transaction_open, (
            f"ack for {item.tool_name} would be delivered inside the group-commit transaction"
        )
        if not item.future.done():
            if item.error is not None:
                item.future.set_exception(item.error)
            else:
                item.future.set_result(item.outcome)
        assert item.future.done(), f"mutation {item.tool_name} left resolve without a verdict"
        if item.dedup_key is not None:
            with self._in_flight_lock:
                self._in_flight.pop(item.dedup_key, None)
                assert item.dedup_key not in self._in_flight

    def mark_dirty(self) -> None:
        """The heavy lane moved the store watermark: publish on this thread."""
        self._dirty = True
        self.request_publish()

    def _drain_audit(self) -> None:
        items: list[tuple[Callable[[], Any], Future]] = []
        while True:
            try:
                audit_item = self._audit_q.get_nowait()
            except queue.Empty:
                break
            if audit_item is None:
                continue
            items.append(audit_item)
        if not items:
            return
        if self._batch_transaction_open:
            # Inside an open group-commit batch the items join via savepoints
            # and their receipts DEFER to the batch commit (ack ⇒ durable).
            for fn, future in items:
                try:
                    result = fn()
                except BaseException as error:  # noqa: BLE001 - its savepoint rolled back
                    if not future.done():
                        future.set_exception(error)
                else:
                    self._pending_audit.append((future, result))
            return
        if not self._audit_batch_enabled or len(items) == 1:
            for fn, future in items:
                self._run_audit_item(fn, future)
            return
        # W16 (SUB-137): join the whole drain into ONE outer transaction.
        # Audit callables use ``EventStore.transaction`` (reentrant: savepoints
        # inside the outer scope), so ordering is preserved, a failing item
        # rolls back only its own savepoint, and receipts resolve only after
        # the shared COMMIT lands — an audit receipt still implies durability.
        # Contract: audit callables must scope their writes in
        # ``store.transaction`` (every AuditLog method does); a callable that
        # wrote unscoped would leak partial writes into the shared commit.
        succeeded: list[tuple[Future, Any]] = []
        try:
            with self._store.transaction():
                for fn, future in items:
                    try:
                        result = fn()
                    except BaseException as error:  # noqa: BLE001 - its savepoint rolled back
                        if not future.done():
                            future.set_exception(error)
                    else:
                        succeeded.append((future, result))
        except BaseException as error:  # noqa: BLE001 - the shared commit failed: nothing is durable
            for future, _result in succeeded:
                if not future.done():
                    future.set_exception(error)
            return
        for future, result in succeeded:
            if not future.done():
                future.set_result(result)

    def _flush_pending_audit(self, error: BaseException | None = None) -> None:
        """Resolve the deferred mid-batch receipts after the batch COMMIT.

        On a commit failure every deferred receipt fails with the batch's
        error: nothing was durably written, so nothing may be acknowledged.
        """
        pending, self._pending_audit = self._pending_audit, []
        for future, result in pending:
            if future.done():
                continue
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

    def _run_audit_item(self, fn: Callable[[], Any], future: Future) -> None:
        try:
            result = fn()
        except BaseException as error:  # noqa: BLE001 - surfaced to the waiting lane
            if not future.done():
                future.set_exception(error)
        else:
            if not future.done():
                future.set_result(result)

    def _publish_now(self) -> None:
        try:
            self._projector.repair_if_stale()
        except ProjectorBusy:
            pass  # a concurrent repair already ran; the watermark re-check decides
        with self._publish:
            watermark = self._store.knowledge_high_watermark()
            # The event log is append-only: the published watermark can move
            # forward or hold, never backwards (a regression would let the
            # read lanes observe the past — programmer error, crash).
            assert watermark >= self._published_watermark, (
                f"published watermark would regress {self._published_watermark} -> {watermark}"
            )
            self._dirty = False
            self._publish_requested = False
            self._published_watermark = watermark
            self._publish.notify_all()


# ------------------------------------------------------------------ read pool


class _ReadPool:
    """N workers, each with its own read-only store connection and service."""

    def __init__(
        self,
        config: DaemonConfig,
        *,
        arbiter: _WriteArbiter,
        writer_store: EventStore,
        writer_audit: AuditLog,
        graph_root: Path,
    ) -> None:
        self._config = config
        self._arbiter = arbiter
        self._writer_store = writer_store
        self._writer_audit = writer_audit
        self._graph_root = graph_root
        self._queue = _FairQueue(
            total_cap=config.read_queue_size,
            per_actor_cap=config.per_actor_read_cap,
            retry_after_ms=config.retry_after_ms,
        )
        self._workers: list[threading.Thread] = []
        # W13 (SUB-124): one heartbeat per worker; the daemon's worker
        # watchdog supervises these via SupervisedThread adapters.
        self._heartbeats: dict[int, Heartbeat] = {}
        self._stop = threading.Event()

    def start(self) -> None:
        assert not self._workers, "read pool already started"
        for index in range(self._config.read_pool_size):
            self._workers.append(self._spawn_worker(index))

    def _spawn_worker(self, index: int) -> threading.Thread:
        """Create and start read worker ``index`` with a fresh heartbeat."""
        self._heartbeats[index] = Heartbeat()
        worker = threading.Thread(target=self._worker_main, args=(index,), name=f"superpower-read-{index}", daemon=True)
        worker.start()
        return worker

    def _respawn_worker(self, index: int) -> threading.Thread:
        """Watchdog restart path: replace a DEAD worker (never a live one)."""
        existing = self._workers[index] if index < len(self._workers) else None
        assert existing is None or not existing.is_alive(), (
            f"read worker {index} is alive — restarting would duplicate a queue consumer"
        )
        worker = self._spawn_worker(index)
        if index < len(self._workers):
            self._workers[index] = worker
        else:
            self._workers.append(worker)
        return worker

    def stop(self) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.join(timeout=15.0)
        for item in self._queue.drain():
            if not item.future.done():
                item.future.set_exception(ShuttingDown("the daemon is shutting down"))

    @property
    def queue_depth(self) -> int:
        return self._queue.depth

    def submit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        actor: str,
        deadline: float | None = None,
        session: _SessionToken | None = None,
    ) -> dict[str, Any]:
        future: Future = Future()
        self._queue.put(actor, _ReadItem(tool_name, arguments, actor, future, deadline, session))
        return _await(future)

    # --------------------------------------------------------------- workers

    def _worker_main(self, index: int) -> None:
        store = ReadOnlyEventStore(self._config.db_path, reference=self._writer_store)
        forwarding = _ForwardingAuditLog(self._arbiter, self._writer_audit)
        projector = _ReadOnlyProjector(GraphProjector(store, output_root=self._graph_root))
        service = KnowledgeService(
            store,  # type: ignore[arg-type]
            projector,  # type: ignore[arg-type]
            audit_log=forwarding,  # type: ignore[arg-type]
            max_token_budget=self._config.max_token_budget,
        )
        adapters: dict[str, _Adapter] = {}
        heartbeat = self._heartbeats[index]
        try:
            while not self._stop.is_set():
                heartbeat.beat()  # W13: one beat per wakeup, idle included
                item = self._queue.get(timeout=0.2)
                if item is None:
                    continue
                try:
                    if item.deadline is not None and time.monotonic() > item.deadline:
                        raise DeadlineExceeded("the request expired in the read queue")
                    if item.session is not None and not item.session.alive:
                        raise ShuttingDown("the owning session is gone")
                    result = self._execute_with_freshness(service, forwarding, adapters, item)
                except BaseException as error:  # noqa: BLE001 - surfaced to the caller
                    if not item.future.done():
                        item.future.set_exception(error)
                else:
                    if not item.future.done():
                        item.future.set_result(result)
        finally:
            store.close()

    def _adapter_for(
        self,
        adapters: dict[str, _Adapter],
        actor: str,
        service: KnowledgeService,
        audit: _ForwardingAuditLog,
    ) -> _Adapter:
        adapter = adapters.get(actor)
        if adapter is None:
            if len(adapters) >= ADAPTER_CACHE_CAP:
                adapters.clear()  # bounded cache; actors are cheap to rebind
            adapter = _Adapter(
                service,
                graph_root=self._graph_root,
                audit_log=audit,  # type: ignore[arg-type] - duck-typed forwarding bridge (begin/complete)
                actor=actor,
                max_token_budget=self._config.max_token_budget,
                resource_max_bytes=DEFAULT_RESOURCE_MAX_BYTES,
            )
            adapters[actor] = adapter
        return adapter

    def _execute_with_freshness(
        self,
        service: KnowledgeService,
        forwarding: _ForwardingAuditLog,
        adapters: dict[str, _Adapter],
        item: _ReadItem,
    ) -> dict[str, Any]:
        """Execute one read, waiting out transient staleness on the writer.

        A read that races a commit finds the projection one watermark behind;
        instead of repairing (impossible on a read-only connection) or
        failing, the lane nudges the writer to publish and retries within a
        bounded budget, then surfaces the typed STALE_PROJECTION result.
        """
        deadline = time.monotonic() + self._config.read_publish_wait_seconds
        adapter = self._adapter_for(adapters, item.actor, service, forwarding)
        while True:
            result = self._execute_item(adapter, item)
            if not _is_stale_result(result):
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result
            since = self._arbiter.published_watermark
            self._arbiter.request_publish()
            self._arbiter.wait_for_publish(since=since, timeout=min(remaining, 5.0))

    def _execute_item(self, adapter: _Adapter, item: _ReadItem) -> dict[str, Any]:
        if item.tool_name == "__resources_list__":
            return _list_resources(adapter)
        if item.tool_name == "__resources_read__":
            uri = str(item.arguments.get("uri", ""))
            return asyncio.run(_read_resource(adapter, uri))
        return _call_tool(adapter, item.tool_name, item.arguments)


# ------------------------------------------------------------------ heavy lane


class _HeavyLane:
    """The heavy lane (ingest v3): one preemptible worker, small fair queue.

    Ingest jobs run on the writer store through the store's own lock (the
    pipeline is batched per file, so no long transaction starves the writer
    lane); preemption points are the deadline/session gates between items.
    After each job the writer lane is nudged to publish.
    """

    def __init__(
        self,
        config: DaemonConfig,
        *,
        service: KnowledgeService,
        audit: AuditLog,
        arbiter: _WriteArbiter,
        graph_root: Path,
    ) -> None:
        self._config = config
        self._service = service
        self._audit = audit
        self._arbiter = arbiter
        self._graph_root = graph_root
        self._queue = _FairQueue(
            total_cap=config.heavy_queue_size,
            per_actor_cap=config.per_actor_heavy_cap,
            retry_after_ms=config.retry_after_ms,
        )
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._main, name="superpower-heavy", daemon=True)
        # W13 (SUB-124): the heavy lane beats once per loop wakeup.
        self._heartbeat = Heartbeat()
        self._started = False

    def start(self) -> None:
        self._worker.start()
        self._started = True

    def _respawn(self) -> threading.Thread:
        """Watchdog restart path: replace a DEAD heavy worker."""
        assert not self._worker.is_alive(), "the heavy lane is alive — restart would duplicate the single worker"
        self._heartbeat = Heartbeat()
        self._worker = threading.Thread(target=self._main, name="superpower-heavy", daemon=True)
        self._worker.start()
        return self._worker

    def stop(self) -> None:
        self._stop.set()
        if self._started:
            self._worker.join(timeout=15.0)
        for item in self._queue.drain():
            if not item.future.done():
                item.future.set_exception(ShuttingDown("the daemon is shutting down"))

    @property
    def queue_depth(self) -> int:
        return self._queue.depth

    def submit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        actor: str,
        deadline: float | None = None,
        session: _SessionToken | None = None,
    ) -> dict[str, Any]:
        future: Future = Future()
        self._queue.put(actor, _ReadItem(tool_name, arguments, actor, future, deadline, session))
        return _await(future)

    def _main(self) -> None:
        adapters: dict[str, _Adapter] = {}
        heartbeat = self._heartbeat
        while not self._stop.is_set():
            heartbeat.beat()  # W13: one beat per wakeup, idle included
            item = self._queue.get(timeout=0.2)
            if item is None:
                continue
            try:
                if item.deadline is not None and time.monotonic() > item.deadline:
                    raise DeadlineExceeded("the request expired in the heavy queue")
                if item.session is not None and not item.session.alive:
                    raise ShuttingDown("the owning session is gone")
                adapter = adapters.get(item.actor)
                if adapter is None:
                    if len(adapters) >= ADAPTER_CACHE_CAP:
                        adapters.clear()
                    adapter = _Adapter(
                        self._service,
                        graph_root=self._graph_root,
                        audit_log=self._audit,
                        actor=item.actor,
                        max_token_budget=self._config.max_token_budget,
                        resource_max_bytes=DEFAULT_RESOURCE_MAX_BYTES,
                    )
                    adapters[item.actor] = adapter
                result = _call_tool(adapter, item.tool_name, item.arguments)
                self._arbiter.mark_dirty()  # ingest moved the store: publish via the writer
            except BaseException as error:  # noqa: BLE001 - surfaced to the caller
                if not item.future.done():
                    item.future.set_exception(error)
            else:
                if not item.future.done():
                    item.future.set_result(result)


# ------------------------------------------------------------------- daemon


@dataclass(frozen=True)
class SpawnResult:
    """Outcome of an idempotent daemon spawn.

    ``already_running`` is True when a healthy daemon already answered the
    handshake — in that case no new process was created and ``pid`` names
    the existing daemon. Frozen by contract: spawn state is a fact, callers
    must not mutate it after the fact.
    """

    pid: int
    socket_path: Path
    already_running: bool


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SuperpowerDaemon:
    """One threaded daemon: acceptor, session threads, writer, read pool."""

    def __init__(self, config: DaemonConfig) -> None:
        self._config = config
        self._paths = config.paths
        self._nonce = secrets.token_hex(8)
        self._started_at = 0.0
        self._lock_fd: int | None = None
        self._listener: socket.socket | None = None
        self._stop = threading.Event()
        self._shutdown_reason: str | None = None
        self._sessions: set[threading.Thread] = set()
        self._session_sockets: dict[socket.socket, _SessionToken] = {}
        self._sessions_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._draining = False
        self._stats_lock = threading.Lock()
        self._requests_total = 0
        self._busy_rejections = 0
        self._per_actor: dict[str, dict[str, int]] = {}
        self._per_method: dict[str, int] = {}
        # Per-agent latency observability (W7): bounded reservoirs per actor
        # per lane; stats() renders p50/p95/p99. No payload content, ever.
        self._latencies: dict[str, dict[str, collections.deque[float]]] = {}
        self._buckets: dict[str, _TokenBucket] = {}
        self._buckets_lock = threading.Lock()
        self._sessions_high_water = 0
        self._acceptor: threading.Thread | None = None
        self._reaper: threading.Thread | None = None
        self._watchdog: LivenessWatchdog | None = None
        # W13 (SUB-124): per-worker heartbeats + tiered watchdog supervision.
        self._acceptor_heartbeat = Heartbeat()
        self._reaper_heartbeat = Heartbeat()
        self._worker_watchdog: Watchdog | None = None
        self._worker_drain_report: DrainReport | None = None
        self._worker_alerts: collections.deque[dict[str, str]] = collections.deque(maxlen=256)
        # lanes (built in start)
        self._store: EventStore | None = None
        self._arbiter: _WriteArbiter | None = None
        self._read_pool: _ReadPool | None = None
        self._heavy: _HeavyLane | None = None
        self._writer_adapters: dict[str, _Adapter] = {}
        self._writer_audit: AuditLog | None = None
        self._graph_root: Path | None = None
        # Graceful-drain tracking (W7 #7): every accepted dispatch holds this
        # counter until its response is on the wire, so _finalize can wait for
        # in-flight work instead of cutting live connections mid-answer.
        self._inflight = 0
        self._inflight_cond = threading.Condition()
        self._forced = False
        # W10 capability security (SUB-117): in "enforce" mode the verifier
        # reads the operator store under the runtime dir; "off" keeps the
        # exact legacy behavior (no capability is even parsed).
        self._capability_store: CapabilityStore | None = (
            CapabilityStore(self._paths.root) if config.capability_mode == "enforce" else None
        )

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Acquire the lock, build the lanes, bind the socket, start threads."""
        # Tiger Style: re-prove the static-budget soundness rules before any
        # side effect (config validation is the typed gate; this is the
        # second lock on the door against future edits).
        assert_static_budgets(self._config)
        self._paths.ensure()
        self._acquire_lock()
        try:
            self._build_lanes()
            self._bind_socket()
        except BaseException:
            self._release_artifacts()
            raise
        self._started_at = time.monotonic()
        self._last_activity = time.monotonic()
        self._watchdog = LivenessWatchdog(
            stall_seconds=self._config.liveness_stall_seconds,
            interval_seconds=self._config.liveness_heartbeat_seconds,
        )
        self._acceptor = threading.Thread(target=self._accept_loop, name="superpower-acceptor", daemon=True)
        self._reaper = threading.Thread(target=self._reap_loop, name="superpower-reaper", daemon=True)
        self._watchdog.start()
        self._acceptor.start()
        self._reaper.start()
        if self._config.worker_watchdog:
            self._start_worker_watchdog()
        self._paths.ready.write_text(self._nonce, encoding="utf-8")

    def serve_forever(self) -> int:
        """Block until shutdown; returns the process exit code.

        A forced stop (``shutdown(..., force=True)``) exits 2 per the operator
        contract; every other reason exits 0.
        """
        if self._acceptor is None:
            raise DaemonUnavailable("daemon not started")
        while not self._stop.is_set():
            self._stop.wait(0.5)
        self._finalize()
        return 2 if self._forced else 0

    def shutdown(self, reason: str = "operator", *, force: bool = False) -> None:
        """Graceful drain (W7 #7): refuse new work, finish in-flight, then stop.

        ``force`` skips the drain window: queued items fail typed immediately.
        """
        if self._shutdown_reason is None:
            self._shutdown_reason = reason + ("-force" if force else "")
        if force:
            self._forced = True
            self._stop.set()
            return
        if self._draining:
            return
        self._draining = True  # acceptor + dispatcher now refuse new work typed

        def drain() -> None:
            deadline = time.monotonic() + self._config.drain_timeout_seconds
            while time.monotonic() < deadline:
                write_depth = self._arbiter.write_queue_depth if self._arbiter else 0
                write_executing = self._arbiter.executing if self._arbiter else 0
                read_depth = self._read_pool.queue_depth if self._read_pool else 0
                heavy_depth = self._heavy.queue_depth if self._heavy else 0
                if write_depth == 0 and write_executing == 0 and read_depth == 0 and heavy_depth == 0:
                    break
                time.sleep(0.02)
            self._stop.set()

        threading.Thread(target=drain, name="superpower-drain", daemon=True).start()

    # ------------------------------------------------------- worker watchdog

    def _start_worker_watchdog(self) -> None:
        """Register every daemon worker with the tiered watchdog (W13).

        Escalation: suspect (silent, alert only) → restart (dead threads are
        replaced through the lanes' respawn factories; a live-but-wedged
        worker is escalated to dead+alert instead — Python cannot kill a
        thread, and duplicating a queue consumer is worse) → dead (restart
        budget exhausted; terminal, alerted). Kill switch:
        ``DaemonConfig.worker_watchdog=False``.
        """
        policy = WatchdogPolicy(
            suspect_seconds=self._config.watchdog_suspect_seconds,
            stall_seconds=self._config.watchdog_stall_seconds,
            max_restarts=self._config.watchdog_max_restarts,
            restart_window_seconds=self._config.watchdog_restart_window_seconds,
            check_interval_seconds=self._config.watchdog_check_interval_seconds,
        )
        watchdog = Watchdog(policy=policy, on_alert=self._on_worker_alert)
        assert self._read_pool is not None and self._heavy is not None
        assert self._acceptor is not None and self._reaper is not None
        read_pool, heavy = self._read_pool, self._heavy
        for index, worker in enumerate(read_pool._workers):
            watchdog.register(
                SupervisedThread(
                    f"read-{index}",
                    worker,
                    heartbeat_getter=lambda i=index: read_pool._heartbeats[i],
                    restart_factory=lambda i=index: read_pool._respawn_worker(i),
                    policy=policy,
                )
            )
        watchdog.register(
            SupervisedThread(
                "heavy",
                heavy._worker,
                heartbeat_getter=lambda: heavy._heartbeat,
                restart_factory=heavy._respawn,
                policy=policy,
            )
        )
        watchdog.register(
            SupervisedThread(
                "acceptor",
                self._acceptor,
                heartbeat_getter=lambda: self._acceptor_heartbeat,
                restart_factory=self._respawn_acceptor,
                policy=policy,
            )
        )
        watchdog.register(
            SupervisedThread(
                "reaper",
                self._reaper,
                heartbeat_getter=lambda: self._reaper_heartbeat,
                restart_factory=self._respawn_reaper,
                policy=policy,
            )
        )
        watchdog.start()
        self._worker_watchdog = watchdog

    def _on_worker_alert(self, name: str, kind: str) -> None:
        entry = {"worker": name, "kind": kind, "at": _utcnow_iso()}
        self._worker_alerts.append(entry)
        _LOGGER.warning("worker watchdog: %s → %s", name, kind)

    def _respawn_acceptor(self) -> threading.Thread:
        assert self._acceptor is not None and not self._acceptor.is_alive(), (
            "the acceptor is alive — restart would double-bind the listener"
        )
        self._acceptor_heartbeat = Heartbeat()
        self._acceptor = threading.Thread(target=self._accept_loop, name="superpower-acceptor", daemon=True)
        self._acceptor.start()
        return self._acceptor

    def _respawn_reaper(self) -> threading.Thread:
        assert self._reaper is not None and not self._reaper.is_alive(), (
            "the reaper is alive — restart would double-run lease reaping"
        )
        self._reaper_heartbeat = Heartbeat()
        self._reaper = threading.Thread(target=self._reap_loop, name="superpower-reaper", daemon=True)
        self._reaper.start()
        return self._reaper

    # ------------------------------------------------------------- internals

    def _acquire_lock(self) -> None:
        try:
            fd = os.open(self._paths.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise DaemonUnavailable(
                f"daemon lock {self._paths.lock} already exists "
                f"(pid {self._lock_pid() or 'unknown'}); use spawn_daemon for stale-lock recovery"
            ) from None
        payload = json.dumps({"pid": os.getpid(), "nonce": self._nonce, "started_at": _utcnow_iso()})
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
        self._lock_fd = fd
        self._paths.pid.write_text(f"{os.getpid()} {self._nonce}\n", encoding="utf-8")

    def _lock_pid(self) -> int | None:
        try:
            content = json.loads(self._paths.lock.read_text(encoding="utf-8"))
            return int(content.get("pid", 0))
        except OSError, json.JSONDecodeError, ValueError:
            return None

    def _owns_lock(self) -> bool:
        try:
            content = json.loads(self._paths.lock.read_text(encoding="utf-8"))
            return str(content.get("nonce")) == self._nonce
        except OSError, json.JSONDecodeError:
            return False

    def _build_lanes(self) -> None:
        config = self._config
        graph_root = resolve_within_root(config.graph_root, ".")
        self._graph_root = graph_root
        store = EventStore(config.db_path)
        self._store = store
        projector = GraphProjector(store, output_root=graph_root)
        service = KnowledgeService(store, projector, max_token_budget=config.max_token_budget)
        writer_audit = AuditLog(store)
        self._writer_audit = writer_audit
        # Crash recovery first (journal reconciliation closes crashed
        # invocations per the audit contract), then start from a current
        # projection: the writer is the only publisher.
        writer_audit.recover_incomplete(projection_repair=None)
        projector.repair_if_stale()

        def writer_adapter_factory(actor: str) -> _Adapter:
            adapter = self._writer_adapters.get(actor)
            if adapter is None:
                if len(self._writer_adapters) >= ADAPTER_CACHE_CAP:
                    self._writer_adapters.clear()
                adapter = _Adapter(
                    service,
                    graph_root=graph_root,
                    audit_log=writer_audit,
                    actor=actor,
                    max_token_budget=config.max_token_budget,
                    resource_max_bytes=DEFAULT_RESOURCE_MAX_BYTES,
                )
                self._writer_adapters[actor] = adapter
            return adapter

        self._arbiter = _WriteArbiter(
            config,
            store=store,
            projector=projector,
            adapter_factory=writer_adapter_factory,
        )
        self._read_pool = _ReadPool(
            config,
            arbiter=self._arbiter,
            writer_store=store,
            writer_audit=writer_audit,
            graph_root=graph_root,
        )
        self._heavy = _HeavyLane(
            config,
            service=service,
            audit=writer_audit,
            arbiter=self._arbiter,
            graph_root=graph_root,
        )
        self._arbiter.start()
        self._read_pool.start()
        self._heavy.start()

    def _bind_socket(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.settimeout(self._config.liveness_heartbeat_seconds)
        try:
            listener.bind(str(self._paths.socket))
        except BaseException:
            listener.close()
            raise
        os.chmod(self._paths.socket, 0o600)
        listener.listen(int(self._config.listener_backlog))
        self._listener = listener

    # -------------------------------------------------------------- acceptor

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            self._acceptor_heartbeat.beat()  # W13: one beat per wakeup
            if self._watchdog is not None:
                self._watchdog.beat()
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break  # listener closed during shutdown
            with self._sessions_lock:
                active = len(self._sessions)
                overflow = self._draining or active >= self._config.max_agents
                if not overflow:
                    self._sessions_high_water = max(self._sessions_high_water, active + 1)
            session = threading.Thread(
                target=self._session_main,
                args=(connection, overflow),
                name="superpower-session",
                daemon=True,
            )
            if not overflow:
                with self._sessions_lock:
                    self._sessions.add(session)
                    self._assert_session_capacity_locked()
            session.start()

    def _assert_session_capacity_locked(self) -> None:
        """Accept invariant (caller holds ``_sessions_lock``): the session
        registry never exceeds the static ``max_agents`` budget."""
        assert len(self._sessions) <= self._config.max_agents, (
            f"{len(self._sessions)} sessions exceed the static budget {self._config.max_agents}"
        )

    def _reap_loop(self) -> None:
        while not self._stop.wait(min(1.0, self._config.idle_timeout_seconds / 2)):
            self._reaper_heartbeat.beat()  # W13: one beat per wakeup
            self._reap_expired_sessions()
            with self._sessions_lock:
                active = len(self._sessions)
            if active == 0 and time.monotonic() - self._last_activity > self._config.idle_timeout_seconds:
                self.shutdown("idle")
                return

    def _reap_expired_sessions(self) -> None:
        """Session lease: an unheartbeated session loses its slot (W7 #6).

        The lease measures *idleness*, never in-flight work (SUB-132): a
        session whose silence is the daemon itself answering its request is
        not abandoned — closing it mid-request was the W15 storm EOF flake.
        Such a session is deferred, not hidden: once the response is on the
        wire, the lease applies from that last activity as before.
        """
        now = time.monotonic()
        with self._sessions_lock:
            expired = [
                (connection, token)
                for connection, token in self._session_sockets.items()
                if now - token.last_seen > self._config.session_lease_seconds and token.in_flight == 0
            ]
        for connection, token in expired:
            token.alive = False  # queued work of the dead session is dropped at the gates
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        with self._sessions_lock:
            self._assert_no_expired_sessions_locked(now)

    def _assert_no_expired_sessions_locked(self, now: float) -> None:
        """Post-reap invariant (caller holds ``_sessions_lock``): no live
        IDLE token in the registry may be past its lease. In-flight sessions
        are exempt by construction (the daemon is their silence's cause)."""
        for token in self._session_sockets.values():
            if token.in_flight > 0:
                continue
            assert not token.alive or now - token.last_seen <= self._config.session_lease_seconds, (
                f"session {token.session_id} survived the reaper past its lease {self._config.session_lease_seconds}s"
            )

    def _unregister(self, session: threading.Thread) -> None:
        with self._sessions_lock:
            self._sessions.discard(session)

    # --------------------------------------------------------------- sessions

    def _session_main(self, connection: socket.socket, overflow: bool) -> None:
        current = threading.current_thread()
        registered = not overflow
        token = _SessionToken(session_id=secrets.token_hex(8))
        if registered:
            with self._sessions_lock:
                self._session_sockets[connection] = token
        writer: Any = None
        try:
            connection.settimeout(None)
            reader = rpc.FrameReader(connection.makefile("rb"), max_bytes=self._config.max_frame_bytes)
            writer = connection.makefile("wb")
            if overflow:
                # Overflow connections are answered with typed backpressure.
                rpc.write_message(
                    writer,
                    rpc.error_response(
                        None,
                        rpc.SERVER_BUSY if not self._draining else rpc.SHUTTING_DOWN,
                        (
                            f"agent capacity {self._config.max_agents} reached"
                            if not self._draining
                            else "the daemon is draining for shutdown"
                        ),
                        {"retry_after_ms": self._config.retry_after_ms},
                    ),
                )
                return
            while not self._stop.is_set():
                try:
                    frame = reader.read()
                except rpc.ProtocolError as error:
                    # A framing violation leaves the stream unrecoverable:
                    # answer typed, then close (fail-closed).
                    try:
                        rpc.write_message(writer, rpc.error_response(None, error.code, str(error), error.data))
                    except ConnectionError, OSError:
                        pass
                    return
                if frame is None:
                    return
                try:
                    envelope = rpc.parse_request(frame)
                except rpc.ProtocolError as error:
                    rpc.write_message(writer, rpc.error_response(None, error.code, str(error), error.data))
                    continue
                self._last_activity = time.monotonic()
                token.beat()  # any request renews the session lease
                self._inflight_begin()
                token.begin_request()  # the lease exempts in-flight work (SUB-132)
                try:
                    response = self._dispatch(envelope, token)
                    if response is not None:
                        rpc.write_message(writer, response)
                finally:
                    token.end_request()
                    self._inflight_end()
        except ConnectionError, OSError:
            return
        finally:
            token.alive = False
            if writer is not None:
                try:
                    writer.close()
                except OSError:
                    pass
            try:
                connection.close()
            except OSError:
                pass
            if registered:
                with self._sessions_lock:
                    self._session_sockets.pop(connection, None)
                self._unregister(current)

    # --------------------------------------------------------------- dispatch

    def _dispatch(self, envelope: rpc.RequestEnvelope, token: _SessionToken | None = None) -> dict[str, Any] | None:
        method = envelope.method
        if envelope.is_notification:
            # MCP lifecycle notifications are the only notifications; answered
            # notifications are a protocol violation, so all are swallowed.
            return None
        request_id = envelope.id
        started = time.monotonic()
        lane = "sys"
        with self._stats_lock:
            self._requests_total += 1
            self._per_method[method] = self._per_method.get(method, 0) + 1
        actor: str | None = None
        try:
            result = self._route(envelope, token)
            if method == "tools/call":
                name = envelope.params.get("name")
                actor = self._peek_actor(envelope.params)
                lane = (
                    classify_tool(str(name))
                    if isinstance(name, str) and str(name) in frozenset(EXPECTED_TOOLS)
                    else "sys"
                )
        except ServeError as error:
            if error.rpc_code == rpc.SERVER_BUSY:
                with self._stats_lock:
                    self._busy_rejections += 1
            self._record_latency(actor, lane, started)
            return rpc.error_response(request_id, error.rpc_code, str(error), error.data)
        except rpc.ProtocolError as error:
            self._record_latency(actor, lane, started)
            return rpc.error_response(request_id, error.code, str(error), error.data)
        except Exception:  # noqa: BLE001 - never leak raw exception text
            self._record_latency(actor, lane, started)
            return rpc.error_response(request_id, rpc.INTERNAL_ERROR, "the operation failed with an internal error")
        self._record_latency(actor, lane, started)
        return rpc.result_response(request_id, result)

    @staticmethod
    def _peek_actor(params: Mapping[str, Any]) -> str | None:
        meta = params.get("_meta")
        if isinstance(meta, Mapping):
            actor = meta.get("actor")
            if isinstance(actor, str) and actor.strip():
                return actor.strip()
        return None

    def _record_latency(self, actor: str | None, lane: str, started: float) -> None:
        if actor is None:
            return
        elapsed_ms = (time.monotonic() - started) * 1000.0
        with self._stats_lock:
            lanes = self._latencies.get(actor)
            if lanes is None:
                if len(self._latencies) >= STATS_ACTOR_CAP:
                    # Static budget: observability only — beyond the cap new
                    # actors go untracked; tracked actors are never evicted.
                    return
                lanes = {}
                self._latencies[actor] = lanes
            reservoir = lanes.setdefault(lane, collections.deque(maxlen=LATENCY_RESERVOIR_CAP))
            reservoir.append(elapsed_ms)

    def _note_actor_request(self, actor: str) -> None:
        """Count one request under the static stats budget (see budgets.py)."""
        with self._stats_lock:
            counts = self._per_actor.get(actor)
            if counts is None:
                if len(self._per_actor) >= STATS_ACTOR_CAP:
                    return  # new actors beyond the cap go untracked
                counts = {"requests": 0, "errors": 0}
                self._per_actor[actor] = counts
            counts["requests"] += 1

    def _note_actor_error(self, actor: str) -> None:
        with self._stats_lock:
            counts = self._per_actor.get(actor)
            if counts is not None:
                counts["errors"] += 1

    def _route(self, envelope: rpc.RequestEnvelope, token: _SessionToken | None) -> dict[str, Any]:
        method = envelope.method
        params = envelope.params
        # SYS lane: never queued, never throttled, always immediate.
        if method == "initialize":
            return rpc.initialize_result(params)
        if method == "ping" or method == "session.heartbeat":
            return {}
        if method == "tools/list":
            return rpc.tool_listing_result()
        if method == "tools/call":
            return self._route_tool_call(params, token)
        if method in ("resources/list", "resources/read"):
            return self._route_resource(method, params)
        if method == "daemon.status":
            return self.stats()
        if method == "daemon.shutdown":
            self._authorize_admin(params)
            force = bool(params.get("force", False))
            self.shutdown("operator", force=force)
            return {"stopping": True, "force": force}
        raise rpc.ProtocolError(rpc.METHOD_NOT_FOUND, f"method not found: {method!r}")

    # ---------------------------------------------------- capability gate (W10)

    _DENIAL_REASONS: tuple[tuple[type[CapabilityError], str], ...] = (
        (CapabilityActorMismatchError, "actor_mismatch"),
        (CapabilityRevokedError, "revoked"),
        (CapabilityExpiredError, "expired"),
        (CapabilityForgedError, "forged"),
        (CapabilityMalformedError, "malformed"),
        (CapabilityStoreError, "store_unavailable"),
    )

    @staticmethod
    def _capability_of(params: Mapping[str, Any]) -> Any:
        meta = params.get("_meta")
        if isinstance(meta, Mapping):
            return meta.get("capability")
        return None

    @staticmethod
    def _peek_nonce(token: Any) -> str | None:
        if not isinstance(token, str):
            return None
        try:
            return decode_token(token).nonce
        except CapabilityError:
            return None

    def _deny_capability(
        self,
        *,
        actor: str,
        verb: str,
        lane: str,
        reason: str,
        nonce: str | None,
    ) -> NoReturn:
        """Audit the denial, then raise the typed refusal (fail closed)."""
        assert self._capability_store is not None
        event: dict[str, Any] = {
            "event": "capability_denied",
            "actor": actor,
            "verb": verb,
            "lane": lane,
            "reason": reason,
        }
        if nonce is not None:
            event["nonce"] = nonce
        try:
            self._capability_store.audit(event)
        except CapabilityStoreError:
            # The denial still stands; the caller sees the audit failure.
            raise CapabilityDenied(
                f"capability denied ({reason}) for verb {verb!r}; the audit log is unavailable",
                data={"reason": reason, "verb": verb, "audit": "failed"},
            ) from None
        raise CapabilityDenied(
            f"capability denied ({reason}) for verb {verb!r}",
            data={"reason": reason, "verb": verb},
        )

    def _authorize_capability(self, name: str, lane: str, actor: str, params: Mapping[str, Any]) -> None:
        """Enforce mode gate: public reads need no token; everything else does."""
        store = self._capability_store
        if store is None:
            return  # "off": exact legacy behavior
        token = self._capability_of(params)
        if token is None:
            if lane == "read":
                return  # public read verbs stay open without a token
            self._deny_capability(actor=actor, verb=name, lane=lane, reason="capability_required", nonce=None)
        if not isinstance(token, str):
            self._deny_capability(actor=actor, verb=name, lane=lane, reason="malformed", nonce=None)
        try:
            capability = store.verify(token, actor=actor)
        except CapabilityError as error:
            reason = "invalid"
            for error_type, mapped in self._DENIAL_REASONS:
                if isinstance(error, error_type):
                    reason = mapped
                    break
            self._deny_capability(actor=actor, verb=name, lane=lane, reason=reason, nonce=self._peek_nonce(token))
        if not capability.allows_lane(lane):
            self._deny_capability(
                actor=actor, verb=name, lane=lane, reason="scope_insufficient", nonce=capability.nonce
            )
        if not capability.allows_verb(name):
            self._deny_capability(actor=actor, verb=name, lane=lane, reason="verb_not_allowed", nonce=capability.nonce)

    def _authorize_admin(self, params: Mapping[str, Any]) -> None:
        """daemon.shutdown in enforce mode requires an admin-scope capability."""
        store = self._capability_store
        if store is None:
            return
        meta = params.get("_meta")
        actor = "operator"
        if isinstance(meta, Mapping) and isinstance(meta.get("actor"), str) and str(meta.get("actor")).strip():
            actor = str(meta.get("actor")).strip()
        token = self._capability_of(params)
        if not isinstance(token, str):
            self._deny_capability(
                actor=actor, verb="daemon.shutdown", lane="admin", reason="capability_required", nonce=None
            )
        try:
            capability = store.verify(token, actor=actor)
        except CapabilityError as error:
            reason = "invalid"
            for error_type, mapped in self._DENIAL_REASONS:
                if isinstance(error, error_type):
                    reason = mapped
                    break
            self._deny_capability(
                actor=actor, verb="daemon.shutdown", lane="admin", reason=reason, nonce=self._peek_nonce(token)
            )
        if not capability.allows_lane("admin"):
            self._deny_capability(
                actor=actor, verb="daemon.shutdown", lane="admin", reason="scope_insufficient", nonce=capability.nonce
            )

    def _admit(self, actor: str) -> None:
        """Per-agent token bucket (W7 #1): over-rate agents get typed busy."""
        with self._buckets_lock:
            bucket = self._buckets.get(actor)
            if bucket is None:
                if len(self._buckets) >= BUCKET_CACHE_CAP:
                    self._buckets.clear()  # bounded; buckets are cheap to rebuild
                bucket = _TokenBucket(
                    rate_per_second=self._config.agent_rate_per_second,
                    burst=self._config.agent_burst,
                )
                self._buckets[actor] = bucket
            retry_after = bucket.retry_after_seconds()
        if retry_after is not None:
            raise ServerBusy(
                f"agent {actor!r} exceeded its admission rate "
                f"({self._config.agent_rate_per_second}/s, burst {self._config.agent_burst})",
                retry_after_ms=max(1, int(retry_after * 1000)),
            )

    def _route_tool_call(self, params: Mapping[str, Any], token: _SessionToken | None) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise rpc.ProtocolError(rpc.INVALID_PARAMS, "tools/call params.name must be a non-empty string")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise rpc.ProtocolError(rpc.INVALID_PARAMS, "tools/call params.arguments must be an object")
        actor = self._extract_actor(params)
        lane = classify_tool(name)  # UnknownVerb for anything outside the frozen 16
        self._authorize_capability(name, lane, actor, params)
        if self._draining or self._stop.is_set():
            raise ShuttingDown("the daemon is draining; new work is refused typed")
        self._admit(actor)
        deadline = self._extract_deadline(params)
        self._note_actor_request(actor)
        assert self._arbiter is not None and self._read_pool is not None and self._heavy is not None
        if lane == "write":
            result = self._arbiter.submit_mutation(name, dict(arguments), actor=actor, deadline=deadline, session=token)
        elif lane == "heavy":
            result = self._heavy.submit(name, dict(arguments), actor=actor, deadline=deadline, session=token)
        else:
            result = self._read_pool.submit(name, dict(arguments), actor=actor, deadline=deadline, session=token)
        if result.get("isError"):
            self._note_actor_error(actor)
        return result

    def _extract_deadline(self, params: Mapping[str, Any]) -> float | None:
        meta = params.get("_meta", {})
        if not isinstance(meta, Mapping):
            return None
        raw = meta.get("deadline_ms")
        if raw is None:
            return None
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw <= 0:
            raise rpc.ProtocolError(rpc.INVALID_PARAMS, "params._meta.deadline_ms must be a positive number")
        return time.monotonic() + float(raw) / 1000.0

    def _extract_actor(self, params: Mapping[str, Any]) -> str:
        meta = params.get("_meta", {})
        if not isinstance(meta, Mapping):
            raise ActorRequired("params._meta must be an object carrying a non-empty 'actor' string")
        actor = meta.get("actor")
        if not isinstance(actor, str) or not actor.strip():
            raise ActorRequired("every request must carry a non-empty params._meta.actor (per-agent audit)")
        if len(actor) > 128:
            raise ActorRequired("params._meta.actor exceeds 128 characters")
        return actor.strip()

    def _route_resource(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        assert self._read_pool is not None
        if method == "resources/list":
            return self._read_pool.submit("__resources_list__", {}, actor=_INTERNAL_ACTOR)
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise rpc.ProtocolError(rpc.INVALID_PARAMS, "resources/read params.uri must be a non-empty string")
        return self._read_pool.submit("__resources_read__", {"uri": uri}, actor=_INTERNAL_ACTOR)

    # ------------------------------------------------------------------ stats

    @staticmethod
    def _percentiles(reservoir: collections.deque[float]) -> dict[str, float | int]:
        values = sorted(reservoir)
        if not values:
            return {"count": 0}

        def pct(q: float) -> float:
            index = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
            return round(values[index], 3)

        return {"count": len(values), "p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99)}

    def stats(self) -> dict[str, Any]:
        with self._sessions_lock:
            active = len(self._sessions)
        with self._stats_lock:
            per_actor = {actor: dict(counts) for actor, counts in sorted(self._per_actor.items())}
            per_method = dict(sorted(self._per_method.items()))
            latency_report = {
                actor: {lane: self._percentiles(reservoir) for lane, reservoir in sorted(lanes.items())}
                for actor, lanes in sorted(self._latencies.items())
            }
            return {
                "running": True,
                "draining": self._draining,
                "pid": os.getpid(),
                "uptime_seconds": round(time.monotonic() - self._started_at, 3),
                "sessions_active": active,
                "sessions_high_water": self._sessions_high_water,
                "requests_total": self._requests_total,
                "busy_rejections": self._busy_rejections,
                "per_actor": per_actor,
                "per_method": per_method,
                "latency_ms_per_actor": latency_report,
                "write_queue_depth": self._arbiter.write_queue_depth if self._arbiter else 0,
                "read_queue_depth": self._read_pool.queue_depth if self._read_pool else 0,
                "heavy_queue_depth": self._heavy.queue_depth if self._heavy else 0,
                "published_watermark": self._arbiter.published_watermark if self._arbiter else 0,
                "group_commit": self._arbiter._group_commit_enabled if self._arbiter else False,  # noqa: SLF001
                "worker_watchdog": self._worker_watchdog_stats(),
                "fairness": {
                    "per_actor_write_cap": self._config.per_actor_write_cap,
                    "per_actor_read_cap": self._config.per_actor_read_cap,
                    "per_actor_heavy_cap": self._config.per_actor_heavy_cap,
                    "write_queue_cap": self._config.write_queue_size,
                    "read_queue_cap": self._config.read_queue_size,
                    "heavy_queue_cap": self._config.heavy_queue_size,
                    "agent_rate_per_second": self._config.agent_rate_per_second,
                    "agent_burst": self._config.agent_burst,
                },
                "db_path": str(self._config.db_path),
                "socket_path": str(self._paths.socket),
            }

    def _worker_watchdog_stats(self) -> dict[str, Any]:
        """The W13 per-worker supervision section of ``stats()``."""
        if self._worker_watchdog is None:
            return {"enabled": False, "workers": {}, "alerts": [], "drain": None}
        workers: dict[str, Any] = {}
        for name in self._worker_watchdog.worker_names():
            worker = self._worker_watchdog._workers.get(name)  # noqa: SLF001 - same module ownership
            if worker is None:
                continue
            workers[name] = {
                "state": str(worker.state),
                "alive": worker.is_alive(),
                "beats": worker.heartbeat.beat_count,
                "beat_age_seconds": round(worker.heartbeat.age_seconds(), 3),
                "restarts": worker.restart_count,
                "terminal": worker.terminal,
            }
        return {
            "enabled": True,
            "workers": workers,
            "alerts": list(self._worker_alerts),
            "drain": self._worker_drain_report.to_dict() if self._worker_drain_report is not None else None,
        }

    # --------------------------------------------------------------- shutdown

    def _inflight_begin(self) -> None:
        with self._inflight_cond:
            self._inflight += 1

    def _inflight_end(self) -> None:
        with self._inflight_cond:
            assert self._inflight > 0, "inflight dispatch ended without a matching begin"
            self._inflight -= 1
            if self._inflight <= 0:
                self._inflight_cond.notify_all()

    def _finalize(self) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
        if self._worker_watchdog is not None:
            # Stop the monitor first: workers going down below are a planned
            # drain, not stalls — the watchdog must not restart them.
            self._worker_watchdog.stop()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        if self._acceptor is not None:
            self._acceptor.join(timeout=5.0)
        # Resolve every queued or executing item BEFORE touching session
        # sockets: lane stops finish the executing item and fail queued items
        # typed, so each blocked session thread still gets a response to put
        # on the wire (graceful drain: zero requests cut without a response).
        if self._read_pool is not None:
            self._read_pool.stop()
        if self._heavy is not None:
            self._heavy.stop()
        if self._arbiter is not None:
            self._arbiter.stop()
        if self._worker_watchdog is not None:
            # Drain coverage (W13): every registered worker — read lanes,
            # heavy, acceptor, reaper — is accounted in the report, including
            # the reaper the pre-W13 finalize never joined.
            self._worker_drain_report = self._worker_watchdog.drain_all(
                timeout_ms=int(self._config.drain_timeout_seconds * 1000)
            )
        if not self._forced:
            deadline = time.monotonic() + self._config.drain_timeout_seconds
            with self._inflight_cond:
                while self._inflight > 0:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._inflight_cond.wait(timeout=remaining)
        with self._sessions_lock:
            sockets = list(self._session_sockets)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if self._store is not None:
            self._store.close()
        self._release_artifacts()

    def _release_artifacts(self) -> None:
        exit_reason = self._shutdown_reason or "error"
        for artifact in (self._paths.ready, self._paths.socket):
            try:
                artifact.unlink(missing_ok=True)
            except OSError:
                pass
        if self._owns_lock():
            try:
                self._paths.exit.write_text(
                    json.dumps({"reason": exit_reason, "pid": os.getpid(), "at": _utcnow_iso()}),
                    encoding="utf-8",
                )
            except OSError:
                pass
            for artifact in (self._paths.pid, self._paths.lock):
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    pass
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None


# ------------------------------------------------------------- spawn / control


def _socket_live(socket_path: Path) -> bool:
    if not socket_path.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(str(socket_path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _read_pid(paths: DaemonPaths) -> int | None:
    try:
        first = paths.pid.read_text(encoding="utf-8").split()[0]
        return int(first)
    except OSError, ValueError, IndexError:
        return None


def spawn_daemon(config: DaemonConfig) -> SpawnResult:
    """Spawn the daemon detached (double-fork + setsid), racing safely.

    The atomic ``O_EXCL`` lockfile inside the daemon process is the arbiter:
    concurrent spawns all succeed, but exactly one daemon survives. Returns
    once the ready marker proves the socket is serving (or raises
    :class:`DaemonUnavailable` after ``spawn_timeout_seconds``).

    Fork-only spawning runs the daemon in a forked copy of a multi-threaded
    process, so a child can very rarely wedge on an inherited lock: the
    socket file exists and accepts into the backlog, but nothing is ever
    served. Readiness therefore requires a real ``ping`` roundtrip — not
    just a connectable socket — and a wedged child is SIGKILLed, its
    artifacts recovered, and the spawn retried once.
    """
    paths = config.paths
    paths.ensure()
    if _socket_live(paths.socket):
        pid = _read_pid(paths)
        if pid is not None and _pid_alive(pid):
            return SpawnResult(pid=pid, socket_path=paths.socket, already_running=True)

    def _spawn_once() -> None:
        # fork-only, never exec: the product's static scan (runtime_guard
        # cli_scraping_violations) bans every exec/spawn vector because a fresh
        # argv cannot be statically verified — a forked child has no argv at all;
        # it IS the daemon, inheriting the caller's already-scrubbed environment.
        # The child runs run_daemon directly and os._exit's around it (no atexit,
        # no flush of inherited buffers).
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*multi-threaded.*fork.*", category=DeprecationWarning)
            first = os.fork()
        if first == 0:
            # Intermediate child: detach, fork the real daemon, exit immediately.
            try:
                os.setsid()
                second = os.fork()
                if second == 0:
                    os.environ["KNOW_YOUR_CONTEXT_DAEMON_INTERNAL"] = "1"  # no daemon-in-daemon
                    devnull = os.open(os.devnull, os.O_RDWR)
                    for fd in (0, 1, 2):
                        os.dup2(devnull, fd)
                    try:
                        run_daemon(config)
                    except BaseException:  # noqa: BLE001 - the grandchild never returns
                        pass
            finally:
                os._exit(0)
        os.waitpid(first, 0)

    def _serving() -> SpawnResult | None:
        if not paths.ready.exists() or not _socket_live(paths.socket):
            return None
        # Proof of service, not just a listening backlog: a wedged fork child
        # fails this ping even though its socket connects.
        frame = _admin_roundtrip(paths.socket, "ping", timeout=2.0)
        if frame is None or "result" not in frame:
            return None
        pid = _read_pid(paths)
        if pid is not None and _pid_alive(pid):
            return SpawnResult(pid=pid, socket_path=paths.socket, already_running=False)
        return None

    for attempt in range(2):
        _spawn_once()
        deadline = time.monotonic() + config.spawn_timeout_seconds
        while time.monotonic() < deadline:
            result = _serving()
            if result is not None:
                return result
            time.sleep(0.05)
        if attempt == 0:
            # The child wedged: kill it, reclaim the artifacts, respawn once.
            # Re-check first: in a multi-spawn race a healthy winner owned by
            # another caller must never be killed.
            result = _serving()
            if result is not None:
                return result
            pid = _read_pid(paths)
            if pid is not None and _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError, PermissionError:
                    pass
            _recover_stale_lock(paths)
            continue
    raise DaemonUnavailable(
        f"daemon did not become ready within {config.spawn_timeout_seconds}s (socket {paths.socket}, lock {paths.lock})"
    )


def ensure_daemon(config: DaemonConfig) -> SpawnResult:
    """Connect-or-spawn: the entry the proxy and CLI share."""
    paths = config.paths
    if _socket_live(paths.socket):
        pid = _read_pid(paths)
        if pid is not None:
            return SpawnResult(pid=pid, socket_path=paths.socket, already_running=True)
    return spawn_daemon(config)


def _admin_roundtrip(
    socket_path: Path, method: str, params: Mapping[str, Any] | None = None, *, timeout: float = 5.0
) -> dict[str, Any] | None:
    """One request/response against the daemon socket; None when unreachable."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            probe.connect(str(socket_path))
            message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
            if params is not None:
                message["params"] = dict(params)
            probe.sendall(rpc.encode_message(message))
            buffer = b""
            while b"\n" not in buffer and len(buffer) <= rpc.MAX_FRAME_BYTES:
                chunk = probe.recv(65536)
                if not chunk:
                    break
                buffer += chunk
    except OSError:
        return None
    if not buffer.strip():
        return None
    try:
        return rpc.decode_message(buffer.split(b"\n", 1)[0] + b"\n")
    except rpc.ProtocolError:
        return None


def daemon_status(config: DaemonConfig) -> dict[str, Any]:
    """Live stats over the socket, or a deterministic not-running report."""
    paths = config.paths
    if _socket_live(paths.socket):
        frame = _admin_roundtrip(paths.socket, "daemon.status")
        if frame is not None:
            if "result" in frame:
                return frame["result"]  # type: ignore[no-any-return]
            if "error" in frame:
                # Capacity-exhausted but alive.
                return {
                    "running": True,
                    "pid": _read_pid(paths),
                    "note": "daemon alive but at capacity",
                    "socket_path": str(paths.socket),
                    "db_path": str(config.db_path),
                }
    pid = _read_pid(paths)
    report: dict[str, Any] = {"running": False, "socket_path": str(paths.socket), "db_path": str(config.db_path)}
    if pid is not None:
        report["pid"] = pid
        report["pid_alive"] = _pid_alive(pid)
        if _pid_alive(pid):
            report["running"] = True
            report["note"] = "pid alive but socket unreachable"
    try:
        report["last_exit"] = json.loads(paths.exit.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        pass
    return report


def stop_daemon(
    config: DaemonConfig,
    *,
    timeout_seconds: float = 10.0,
    force: bool = False,
    capability: str | None = None,
) -> dict[str, Any]:
    """Ask the daemon to shut down and wait for its clean exit.

    Escalation ladder (operator stop must always work): graceful drain for the
    first half of the budget, forced shutdown for the next quarter, then a
    direct SIGKILL on the pidfile's process plus stale-artifact recovery. A
    daemon that ignores a graceful shutdown (wedged drain, race residue) is
    still stopped — never left answering the socket.

    W10: under ``capability_mode="enforce"`` the graceful/forced shutdown
    requests need an admin-scope capability; the operator CLI mints a
    short-lived one from the local secret (the root of trust) and passes it
    here. Without a token the escalation ladder still stops the daemon.
    """
    paths = config.paths
    if not _socket_live(paths.socket):
        report: dict[str, Any] = {"stopped": False, "reason": "not running", "db_path": str(config.db_path)}
        pid = _read_pid(paths)
        if pid is not None:
            report["pid"] = pid
            report["pid_alive"] = _pid_alive(pid)
        return report

    def _stopped() -> bool:
        return not paths.lock.exists() and not _socket_live(paths.socket)

    pid = _read_pid(paths)
    shutdown_params: dict[str, Any] = {"force": force}
    if capability is not None:
        shutdown_params["_meta"] = {"actor": "operator", "capability": capability}
    _admin_roundtrip(paths.socket, "daemon.shutdown", shutdown_params)
    graceful_deadline = time.monotonic() + (timeout_seconds * 0.5 if not force else 0.0)
    while time.monotonic() < graceful_deadline:
        if _stopped():
            return {"stopped": True, "pid": pid, "escalation": "graceful", "db_path": str(config.db_path)}
        time.sleep(0.05)

    if not force:
        force_params: dict[str, Any] = {"force": True}
        if capability is not None:
            force_params["_meta"] = {"actor": "operator", "capability": capability}
        _admin_roundtrip(paths.socket, "daemon.shutdown", force_params)
        force_deadline = time.monotonic() + timeout_seconds * 0.25
        while time.monotonic() < force_deadline:
            if _stopped():
                return {"stopped": True, "pid": pid, "escalation": "force", "db_path": str(config.db_path)}
            time.sleep(0.05)

    # Last resort: SIGKILL the pidfile process, then remove its stale
    # artifacts ourselves (a killed daemon cannot clean up its own lock).
    if pid is not None and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        death_deadline = time.monotonic() + 5.0
        while _pid_alive(pid) and time.monotonic() < death_deadline:
            time.sleep(0.05)
    kill_deadline = time.monotonic() + timeout_seconds * 0.25
    while time.monotonic() < kill_deadline:
        if _stopped():
            return {"stopped": True, "pid": pid, "escalation": "sigkill", "db_path": str(config.db_path)}
        if pid is not None and not _pid_alive(pid):
            # Process dead but artifacts remain (SIGKILL skips cleanup).
            for artifact in (paths.lock, paths.pid, paths.ready, paths.socket):
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    pass
        time.sleep(0.05)
    raise DaemonUnavailable(f"daemon did not stop within {timeout_seconds}s")


def _recover_stale_lock(paths: DaemonPaths) -> bool:
    """Remove stale artifacts of a dead daemon; True when recovery happened.

    A SIGKILLed holder can still be mid-death when a respawn arrives, so a
    live-looking pid is re-polled briefly before the lock is declared held.
    """
    pid: int | None = None
    try:
        content = json.loads(paths.lock.read_text(encoding="utf-8"))
        pid = int(content.get("pid", 0))
    except OSError, json.JSONDecodeError, ValueError:
        pid = None
    if pid is not None and _pid_alive(pid):
        deadline = time.monotonic() + 2.0
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _pid_alive(pid):
            return False
    for artifact in (paths.lock, paths.pid, paths.ready, paths.socket):
        try:
            artifact.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def run_daemon(config: DaemonConfig) -> int:
    """The daemon-process entry: lock with stale recovery, then serve."""
    config.paths.ensure()
    daemon: SuperpowerDaemon | None = None
    for _attempt in range(3):
        candidate = SuperpowerDaemon(config)
        try:
            candidate.start()
        except DaemonUnavailable:
            if _recover_stale_lock(config.paths):
                continue
            return 0  # a live daemon holds the lock: this spawn lost the race
        daemon = candidate
        break
    if daemon is None:
        return 2

    def _signal(reason: str) -> None:
        daemon.shutdown(reason)

    def _handler(signum: int, _frame: Any) -> None:
        _signal({int(signal.SIGTERM): "sigterm", int(signal.SIGINT): "sigint"}.get(signum, "signal"))

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    return daemon.serve_forever()
