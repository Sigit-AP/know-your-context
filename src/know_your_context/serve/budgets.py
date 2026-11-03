"""Static resource budgets of the superpower daemon (Tiger Style §budgets).

Every queue, buffer, cache and map the daemon can grow has an explicit
upper bound that is known at start time — there is no structure in the
process whose worst-case size must be discovered by reading the source
under incident pressure. This module is the single source of truth:

* :func:`budget_table` derives every bound from the validated
  :class:`~know_your_context.serve.daemon_config.DaemonConfig` (plus the
  module constants below, which superpower.py imports — the arrows point
  one way, so a budget can never drift from its enforcement);
* :func:`assert_static_budgets` re-proves the cross-budget soundness rules
  at daemon start (assert = programmer error; the user-facing validation
  of the same rules lives in ``DaemonConfig._validate`` as typed
  ``ConfigError``);
* :func:`render_markdown` renders the table for the operator docs, so the
  documented budget can never disagree with the enforced one (a test pins
  the render against the table).
"""

from __future__ import annotations

from dataclasses import dataclass

from know_your_context.serve.daemon_config import DaemonConfig

__all__ = [
    "ADAPTER_CACHE_CAP",
    "BUCKET_CACHE_CAP",
    "LATENCY_RESERVOIR_CAP",
    "LISTENER_BACKLOG",
    "STATS_ACTOR_CAP",
    "BudgetEntry",
    "assert_static_budgets",
    "budget_table",
    "render_markdown",
]

# Per-actor adapter caches (writer lane, each read worker, heavy lane):
# rebinding an adapter is cheap, so the policy on overflow is
# clear-and-rebuild.
ADAPTER_CACHE_CAP = 256
# Per-agent admission token buckets: same clear-and-rebuild policy.
BUCKET_CACHE_CAP = 512
# Stats maps (per-actor request/error counts, per-actor latency lanes):
# observability only — beyond the cap NEW actors go untracked while the
# already-tracked ones are never evicted (an actor-spam flood cannot wipe
# the honest majority's counters).
STATS_ACTOR_CAP = 1024
# Latency reservoir per (actor, lane): newest samples replace the oldest.
LATENCY_RESERVOIR_CAP = 256
# listen() backlog on the AF_UNIX rendezvous socket.
LISTENER_BACKLOG = 128


@dataclass(frozen=True)
class BudgetEntry:
    """One statically bounded structure of the daemon."""

    name: str
    bound: int
    unit: str  # "items" | "entries" | "bytes" | "samples" | "sessions" | "millis" | ...
    enforced_at: str
    overflow: str  # the policy when the bound is reached


def budget_table(config: DaemonConfig) -> tuple[BudgetEntry, ...]:
    """Every static budget of one daemon, derived from its config.

    Order is stable and part of the documentation contract.
    """
    return (
        BudgetEntry(
            "sessions_active",
            int(config.max_agents),
            "sessions",
            "SuperpowerDaemon._accept_loop",
            "typed SERVER_BUSY + retry_after_ms at connect",
        ),
        BudgetEntry(
            "session_socket_map",
            int(config.max_agents),
            "entries",
            "SuperpowerDaemon._session_sockets",
            "follows sessions_active (only registered sessions enter)",
        ),
        BudgetEntry(
            "write_queue_total",
            int(config.write_queue_size),
            "items",
            "_FairQueue (write lane)",
            "typed SERVER_BUSY + retry_after_ms",
        ),
        BudgetEntry(
            "write_queue_per_actor",
            int(config.per_actor_write_cap),
            "items",
            "_FairQueue lane (write)",
            "typed SERVER_BUSY; other actors unaffected",
        ),
        BudgetEntry(
            "read_queue_total",
            int(config.read_queue_size),
            "items",
            "_FairQueue (read lane)",
            "typed SERVER_BUSY + retry_after_ms",
        ),
        BudgetEntry(
            "read_queue_per_actor",
            int(config.per_actor_read_cap),
            "items",
            "_FairQueue lane (read)",
            "typed SERVER_BUSY; other actors unaffected",
        ),
        BudgetEntry(
            "heavy_queue_total",
            int(config.heavy_queue_size),
            "items",
            "_FairQueue (heavy lane)",
            "typed SERVER_BUSY + retry_after_ms",
        ),
        BudgetEntry(
            "heavy_queue_per_actor",
            int(config.per_actor_heavy_cap),
            "items",
            "_FairQueue lane (heavy)",
            "typed SERVER_BUSY; other actors unaffected",
        ),
        BudgetEntry(
            "audit_lane",
            int(config.audit_queue_size),
            "items",
            "_WriteArbiter._audit_q",
            "blocking backpressure on read lanes; typed ShuttingDown on stop",
        ),
        BudgetEntry(
            "dedup_in_flight",
            int(config.write_queue_size) + int(config.group_commit_batch),
            "entries",
            "_WriteArbiter._in_flight",
            "structural bound (queued + executing batch), asserted on submit",
        ),
        BudgetEntry(
            "group_commit_batch",
            int(config.group_commit_batch),
            "mutations",
            "_WriteArbiter._gather_batch",
            "batch cap, asserted",
        ),
        BudgetEntry(
            "admission_buckets",
            BUCKET_CACHE_CAP,
            "actors",
            "SuperpowerDaemon._admit",
            "clear-and-rebuild (buckets are cheap to recreate)",
        ),
        BudgetEntry(
            "writer_adapter_cache",
            ADAPTER_CACHE_CAP,
            "adapters",
            "writer_adapter_factory",
            "clear-and-rebind",
        ),
        BudgetEntry(
            "read_adapter_cache",
            ADAPTER_CACHE_CAP * int(config.read_pool_size),
            "adapters",
            "_ReadPool._adapter_for (per worker)",
            "clear-and-rebind (per-worker cap applies)",
        ),
        BudgetEntry(
            "heavy_adapter_cache",
            ADAPTER_CACHE_CAP,
            "adapters",
            "_HeavyLane._main",
            "clear-and-rebind",
        ),
        BudgetEntry(
            "stats_actor_map",
            STATS_ACTOR_CAP,
            "actors",
            "SuperpowerDaemon._note_actor_request / _record_latency",
            "new actors untracked; tracked actors never evicted",
        ),
        BudgetEntry(
            "latency_reservoir",
            LATENCY_RESERVOIR_CAP,
            "samples",
            "SuperpowerDaemon._record_latency (per actor+lane)",
            "oldest sample dropped (bounded deque)",
        ),
        BudgetEntry(
            "frame_bytes",
            int(config.max_frame_bytes),
            "bytes",
            "rpc.FrameReader",
            "typed PROTOCOL_ERROR, connection closed",
        ),
        BudgetEntry(
            "listener_backlog",
            int(config.listener_backlog),
            "pending connects",
            "SuperpowerDaemon._bind_socket",
            "kernel-side refusal/queueing",
        ),
        BudgetEntry(
            "session_lease",
            int(config.session_lease_seconds * 1000),
            "millis",
            "SuperpowerDaemon._reap_expired_sessions",
            "session closed, slot released, queued work dropped typed",
        ),
        BudgetEntry(
            "drain_timeout",
            int(config.drain_timeout_seconds * 1000),
            "millis",
            "SuperpowerDaemon.shutdown / _finalize",
            "remaining work fails typed ShuttingDown",
        ),
        BudgetEntry(
            "read_publish_wait",
            int(config.read_publish_wait_seconds * 1000),
            "millis",
            "_ReadPool._execute_with_freshness",
            "typed STALE_PROJECTION result",
        ),
    )


def assert_static_budgets(config: DaemonConfig) -> None:
    """Re-prove the cross-budget soundness rules (assert = programmer error).

    ``DaemonConfig._validate`` rejects the same violations with typed
    ``ConfigError`` for the operator; this second lock on the door guards
    against future edits that weaken the validation without touching the
    budgets.
    """
    assert int(config.per_actor_write_cap) <= int(config.write_queue_size), (
        f"per_actor_write_cap {config.per_actor_write_cap} exceeds write_queue_size {config.write_queue_size}"
    )
    assert int(config.per_actor_read_cap) <= int(config.read_queue_size), (
        f"per_actor_read_cap {config.per_actor_read_cap} exceeds read_queue_size {config.read_queue_size}"
    )
    assert int(config.per_actor_heavy_cap) <= int(config.heavy_queue_size), (
        f"per_actor_heavy_cap {config.per_actor_heavy_cap} exceeds heavy_queue_size {config.heavy_queue_size}"
    )
    # The audit lane must absorb a full begin+complete burst from every read
    # worker, or read lanes could block the writer's drain-first ordering.
    assert int(config.audit_queue_size) >= 2 * int(config.read_pool_size), (
        f"audit_queue_size {config.audit_queue_size} cannot absorb a begin+complete burst "
        f"from {config.read_pool_size} read workers"
    )


def render_markdown(entries: tuple[BudgetEntry, ...]) -> str:
    """Render the budget table as a deterministic markdown table."""
    lines = [
        "| Budget | Bound | Unit | Enforced at | On overflow |",
        "|---|---|---|---|---|",
    ]
    for entry in entries:
        lines.append(f"| `{entry.name}` | {entry.bound} | {entry.unit} | `{entry.enforced_at}` | {entry.overflow} |")
    return "\n".join(lines) + "\n"
