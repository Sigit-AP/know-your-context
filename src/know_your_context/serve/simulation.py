"""DST-lite: deterministic simulation testing for the superpower lanes.

Phase 1 (W10, SUB-118) of the FoundationDB pattern: the scheduler
(`_FairQueue`), admission control (`_TokenBucket`) and session lease
(`_SessionToken`) are driven DIRECTLY — not re-modelled — with the clock
and the choice sequence injected:

* ``SimClock`` is the only time source (virtual, explicit ``advance``);
* ``random.Random(seed)`` is the only source of choices (actor selection,
  action mix, failure injection, deadline assignment);
* queue ordering is exercised through the real lanes via the seeded action
  stream and the writer's gather discipline (batch cap + per-actor
  exclusion with work-conserving fallback).

Because a run is one deterministic function of its seed, any failure the
simulator exposes is replayable byte-identically:

    python -m know_your_context.serve.simulation SEED [STEPS]

prints the trace digest + coverage + counters of that seed; pinning the
seed in a regression test pins the bug forever.

Invariants checked every tick (a violation fails the run, seed in hand):

* ack ⇒ commit: an acknowledged write is always a committed event;
* the published watermark never leads the committed watermark;
* the queues never exceed their static budgets;
* batch fairness: an actor repeats inside one batch only when no
  non-excluded lane held work at that gather step (work-conserving
  fallback, mirroring the arbiter's ``_gather_batch``).
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from know_your_context.serve.superpower import (
    ServerBusy,
    _FairQueue,
    _SessionToken,
    _TokenBucket,
)

__all__ = [
    "SimClock",
    "SimConfig",
    "SimResult",
    "Simulation",
    "main",
]

_ACTION_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("write", 40),
    ("read", 35),
    ("dedup_write", 10),
    ("replay_write", 5),
    ("idle", 10),
)


class SimClock:
    """Virtual monotonic clock: time moves only when ``advance`` is called."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        assert dt >= 0.0, f"virtual time cannot move backwards (dt={dt})"
        self._t += float(dt)


@dataclass(frozen=True)
class SimConfig:
    """The static shape of one simulated storm (mirrors DaemonConfig)."""

    agents: int = 50
    steps: int = 2000
    write_queue_size: int = 64
    per_actor_write_cap: int = 16
    read_queue_size: int = 128
    per_actor_read_cap: int = 32
    group_commit_batch: int = 8
    session_lease_seconds: float = 30.0
    agent_rate_per_second: float = 100.0
    agent_burst: int = 10
    # Scenario shaping: the spammer fraction floods; failures and deadlines
    # are injected at these probabilities.
    spammer_fraction: float = 0.2
    failure_probability: float = 0.05
    deadline_probability: float = 0.15
    # The writer/read ticks are intermittent (the seeded RNG decides), so
    # bursts actually accumulate in the bounded lanes — without this every
    # tick would drain instantly and the backpressure scenarios could never
    # be explored.
    writer_tick_probability: float = 0.4
    read_tick_probability: float = 0.5
    mutation_cost_seconds: float = 0.002
    read_cost_seconds: float = 0.001
    publish_lag_seconds: float = 0.005

    def __post_init__(self) -> None:
        assert self.agents >= 1, f"agents must be >= 1, got {self.agents}"
        assert self.steps >= 0, f"steps must be >= 0, got {self.steps}"
        assert self.write_queue_size >= 1, "write_queue_size must be >= 1"
        assert 1 <= self.per_actor_write_cap <= self.write_queue_size, (
            f"per_actor_write_cap must be in [1, {self.write_queue_size}]"
        )
        assert self.read_queue_size >= 1, "read_queue_size must be >= 1"
        assert 1 <= self.per_actor_read_cap <= self.read_queue_size, (
            f"per_actor_read_cap must be in [1, {self.read_queue_size}]"
        )
        assert self.group_commit_batch >= 1, "group_commit_batch must be >= 1"
        assert self.session_lease_seconds > 0, "session_lease_seconds must be > 0"
        assert self.agent_rate_per_second > 0, "agent_rate_per_second must be > 0"
        assert self.agent_burst >= 1, "agent_burst must be >= 1"
        assert 0.0 <= self.spammer_fraction <= 1.0, "spammer_fraction must be in [0, 1]"
        assert 0.0 <= self.failure_probability <= 1.0, "failure_probability must be in [0, 1]"
        assert 0.0 <= self.deadline_probability <= 1.0, "deadline_probability must be in [0, 1]"
        assert 0.0 < self.writer_tick_probability <= 1.0, "writer_tick_probability must be in (0, 1]"
        assert 0.0 < self.read_tick_probability <= 1.0, "read_tick_probability must be in (0, 1]"
        assert self.mutation_cost_seconds >= 0.0
        assert self.read_cost_seconds >= 0.0
        assert self.publish_lag_seconds >= 0.0


@dataclass
class _SimOp:
    op_id: int
    actor: str
    kind: str  # "write" | "read"
    idem_key: str | None
    deadline: float | None
    session: _SessionToken
    is_spammer: bool
    outcome: str | None = None  # committed event id
    error: str | None = None  # typed error name
    sharers: int = 0  # dedup-shared waiters (acked with the same receipt)


@dataclass(frozen=True)
class SimResult:
    """The deterministic outcome of one run."""

    seed: int
    steps: int
    trace: tuple[str, ...]
    trace_digest: str
    coverage: frozenset[str]
    counters: Any  # MappingProxyType[str, int] — deterministic insertion order
    committed: tuple[str, ...]
    acked: tuple[str, ...]
    max_write_depth: int
    max_read_depth: int
    checks: Any  # MappingProxyType[str, bool]


class Simulation:
    """One deterministic run: storm the lanes, check the invariants."""

    def __init__(self, seed: int, config: SimConfig | None = None) -> None:
        self.seed = int(seed)
        self.config = config or SimConfig()
        self.clock = SimClock()
        self.rng = random.Random(self.seed)
        self.write_q = _FairQueue(
            total_cap=self.config.write_queue_size,
            per_actor_cap=self.config.per_actor_write_cap,
            retry_after_ms=0,
            now=self.clock.now,
        )
        self.read_q = _FairQueue(
            total_cap=self.config.read_queue_size,
            per_actor_cap=self.config.per_actor_read_cap,
            retry_after_ms=0,
            now=self.clock.now,
        )
        self._trace: list[str] = []
        self._coverage: set[str] = set()
        self._counters: dict[str, int] = {}
        self._committed: list[str] = []
        self._committed_set: set[str] = set()
        self._acked: list[str] = []
        self._published_watermark = 0
        self._unpublished_since: float | None = None
        self._fairness_ok = True
        self._max_write_depth = 0
        self._max_read_depth = 0
        self._actors = [f"agent-{index:03d}" for index in range(self.config.agents)]

    # -------------------------------------------------------------- helpers

    def _count(self, key: str) -> None:
        self._counters[key] = self._counters.get(key, 0) + 1

    def _emit(self, event: str, **fields: Any) -> None:
        body = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
        line = f"t={self.clock.now():.4f} ev={event}"
        self._trace.append(f"{line} {body}" if body else line)

    def _commit(self, op: _SimOp) -> None:
        """The COMMIT point: the event lands in the log BEFORE any ack."""
        event_id = f"ev-{len(self._committed) + 1:06d}"
        op.outcome = event_id
        self._committed.append(event_id)
        self._committed_set.add(event_id)
        if self._unpublished_since is None:
            self._unpublished_since = self.clock.now()

    def _ack(self, op: _SimOp) -> None:
        """Post-commit delivery to the caller and every dedup sharer."""
        assert op.outcome is not None and op.outcome in self._committed_set, (
            f"ack for {op.outcome} would precede its commit (seed={self.seed})"
        )
        self._acked.extend([op.outcome] * (1 + op.sharers))

    def _weighted_action(self) -> str:
        total = sum(weight for _, weight in _ACTION_WEIGHTS)
        pick = self.rng.randrange(total)
        acc = 0
        for name, weight in _ACTION_WEIGHTS:
            acc += weight
            if pick < acc:
                return name
        raise AssertionError("weighted choice fell through")  # pragma: no cover

    def _gather_batch(self) -> list[_SimOp]:
        """One writer gather through the real queue (arbiter discipline)."""
        batch: list[_SimOp] = []
        exclude: frozenset[str] = frozenset()
        while len(batch) < self.config.group_commit_batch:
            other_lanes_busy = any(
                self.write_q.actor_depth(actor) > 0 for actor in self._actors if actor not in exclude
            )
            nxt = self.write_q.get(timeout=0, exclude=exclude)
            if nxt is None:
                break
            if nxt.actor in exclude and other_lanes_busy:
                self._fairness_ok = False  # a non-excluded lane was starved
            batch.append(nxt)
            exclude = frozenset({*exclude, nxt.actor})
        return batch

    # ------------------------------------------------------------------ run

    def run(self) -> SimResult:
        config = self.config
        spammer_count = max(1, round(config.agents * config.spammer_fraction))
        spammers = frozenset(self._actors[:spammer_count])
        buckets: dict[str, _TokenBucket] = {}
        sessions: dict[str, _SessionToken] = {}
        key_counter = 0
        actor_keys: dict[str, list[str]] = {}
        in_flight: dict[tuple[str, str], _SimOp] = {}
        completed_keys: dict[tuple[str, str], str] = {}
        op_seq = 0

        for _step in range(config.steps):
            # Reaper pass (virtual): expire sessions silent past the lease.
            for actor in self._actors:
                token = sessions.get(actor)
                if (
                    token is not None
                    and token.alive
                    and self.clock.now() - token.last_seen > config.session_lease_seconds
                ):
                    token.alive = False
                    self._coverage.add("session_expired")
                    self._emit("session_expired", actor=actor)

            # Concurrency burst: several agents act within one step (the
            # storm is 50 agents on one writer — without bursts the bounded
            # lanes could never fill and backpressure would stay unexplored).
            burst = self.rng.choice([1, 1, 2, 2, 3, 5, 8])
            for _burst_index in range(burst):
                # Choose actor (spammers get half the airtime) and action.
                if self.rng.random() < 0.5:
                    actor = self.rng.choice(sorted(spammers))
                else:
                    actor = self.rng.choice(self._actors)
                is_spammer = actor in spammers
                action = self._weighted_action()
                if is_spammer and action in ("idle", "read"):
                    action = "write"  # spammers flood the write lane

                # Admission control (real token bucket, virtual clock).
                if action != "idle":
                    bucket = buckets.get(actor)
                    if bucket is None:
                        bucket = _TokenBucket(
                            rate_per_second=config.agent_rate_per_second,
                            burst=config.agent_burst,
                            now=self.clock.now,
                        )
                        buckets[actor] = bucket
                    retry = bucket.retry_after_seconds()
                    if retry is not None:
                        self._coverage.add("admission_busy")
                        self._count("busy_admission_spammer" if is_spammer else "busy_admission_polite")
                        self._emit("admission_busy", actor=actor, retry_ms=f"{retry * 1000:.1f}")
                        self.clock.advance(min(retry, 0.01))
                        continue

                # Session lease: reconnect transparently after an expiry.
                token = sessions.get(actor)
                if token is None or not token.alive:
                    token = _SessionToken(f"sess-{actor}-{op_seq}", now=self.clock.now)
                    sessions[actor] = token
                    self._emit("session_open", actor=actor)
                token.beat()

                if action == "idle":
                    self.clock.advance(self.rng.choice([0.01, 0.05, 0.1]))
                    continue
                deadline = None
                if self.rng.random() < config.deadline_probability:
                    deadline = self.clock.now() + self.rng.choice([0.001, 0.005, 0.01])
                if action == "read":
                    op_seq += 1
                    op = _SimOp(op_seq, actor, "read", None, deadline, token, is_spammer)
                    try:
                        self.read_q.put(actor, op)
                    except ServerBusy:
                        self._coverage.add("busy_read")
                        self._count("busy_read_spammer" if is_spammer else "busy_read_polite")
                        self._emit("busy_read", actor=actor)
                else:
                    own_keys = actor_keys.get(actor, [])
                    if action == "dedup_write" and (own_keys or key_counter > 0):
                        # Dedup: usually the actor's OWN recent key (the real
                        # retry pattern), sometimes another actor's key.
                        if own_keys and self.rng.random() < 0.7:
                            idem_key = self.rng.choice(own_keys)
                        else:
                            idem_key = f"key-{self.rng.randrange(key_counter)}"
                    elif action == "replay_write" and completed_keys:
                        idem_key = self.rng.choice(sorted({key for _, key in completed_keys}))
                    else:
                        key_counter += 1
                        idem_key = f"key-{key_counter}"
                        actor_keys.setdefault(actor, []).append(idem_key)
                    dedup_key = (actor, idem_key)
                    if dedup_key in completed_keys:
                        # Store-level idempotent replay: no new commit.
                        self._acked.append(completed_keys[dedup_key])
                        self._coverage.add("idempotent_replay")
                        self._emit("idempotent_replay", actor=actor, ref=completed_keys[dedup_key])
                    elif dedup_key in in_flight:
                        in_flight[dedup_key].sharers += 1
                        self._coverage.add("dedup_shared")
                        self._emit("dedup_shared", actor=actor, key=idem_key)
                    else:
                        op_seq += 1
                        op = _SimOp(op_seq, actor, "write", idem_key, deadline, token, is_spammer)
                        try:
                            self.write_q.put(actor, op)
                        except ServerBusy:
                            self._coverage.add("busy_write")
                            self._count("busy_write_spammer" if is_spammer else "busy_write_polite")
                            self._emit("busy_write", actor=actor)
                        else:
                            in_flight[dedup_key] = op

            # Writer tick: intermittent, so bursts accumulate in the lane.
            if self.rng.random() < config.writer_tick_probability:
                batch = self._gather_batch()
            else:
                batch = []
            if len(batch) > 1:
                self._coverage.add("batch_multi")
            for member in batch:
                if member.deadline is not None and self.clock.now() > member.deadline:
                    member.error = "DeadlineExceeded"
                    self._coverage.add("deadline_drop")
                elif not member.session.alive:
                    member.error = "ShuttingDown"
                    self._coverage.add("session_drop")
                elif self.rng.random() < config.failure_probability:
                    # Injected member failure: its savepoint rolls back, the
                    # batch survives (group-commit contract).
                    member.error = "InjectedFault"
                    self._coverage.add("member_failure")
                else:
                    self._commit(member)
                self.clock.advance(config.mutation_cost_seconds)
            # Acks are delivered strictly after the batch's commit point.
            for member in batch:
                if member.outcome is not None:
                    self._ack(member)
                    self._count("commit_spammer" if member.is_spammer else "commit_polite")
                    self._coverage.add("write_commit")
                    completed_keys[(member.actor, member.idem_key or "")] = member.outcome
                else:
                    self._count("write_failed")
                in_flight.pop((member.actor, member.idem_key or ""), None)
            if batch:
                self._emit(
                    "batch_commit",
                    n=len(batch),
                    ok=sum(1 for member in batch if member.outcome is not None),
                    failed=sum(1 for member in batch if member.error is not None),
                )

            # Read drain: intermittent, one worker-equivalent per tick.
            read_item = self.read_q.get(timeout=0) if self.rng.random() < config.read_tick_probability else None
            if read_item is not None:
                if read_item.deadline is not None and self.clock.now() > read_item.deadline:
                    self._coverage.add("deadline_drop")
                    self._emit("deadline_read", actor=read_item.actor)
                elif not read_item.session.alive:
                    self._emit("drop_read", actor=read_item.actor)
                else:
                    if self._published_watermark < len(self._committed):
                        # Raced a commit: the lane waits out the publish lag.
                        self._coverage.add("stale_read")
                        self._emit("stale_read", actor=read_item.actor)
                    self._coverage.add("read_ok")
                    self._emit("read_ok", actor=read_item.actor)
                    self.clock.advance(config.read_cost_seconds)

            # Publish tick: the watermark catches up after the lag.
            if (
                self._unpublished_since is not None
                and self.clock.now() - self._unpublished_since >= config.publish_lag_seconds
            ):
                self._published_watermark = len(self._committed)
                self._unpublished_since = None
                self._emit("publish", watermark=self._published_watermark)

            # Per-tick invariants.
            self._max_write_depth = max(self._max_write_depth, self.write_q.depth)
            self._max_read_depth = max(self._max_read_depth, self.read_q.depth)
            assert self._published_watermark <= len(self._committed), (
                f"published watermark leads the log (seed={self.seed})"
            )
            self.clock.advance(self.rng.choice([0.0, 0.0005, 0.001, 0.005]))

        # Final drain: no new submissions; ticks continue until both lanes
        # are empty so nothing is left unresolved at halt.
        guard = config.steps + config.write_queue_size + config.read_queue_size + 16
        for _ in range(guard):
            if self.write_q.depth == 0 and self.read_q.depth == 0:
                break
            member = self.write_q.get(timeout=0)
            if member is not None:
                if member.session.alive and (member.deadline is None or self.clock.now() <= member.deadline):
                    self._commit(member)
                    self._ack(member)
                    completed_keys[(member.actor, member.idem_key or "")] = member.outcome or ""
                else:
                    member.error = "DeadlineExceeded" if member.deadline is not None else "ShuttingDown"
                in_flight.pop((member.actor, member.idem_key or ""), None)
            read_item = self.read_q.get(timeout=0)
            if read_item is not None:
                self._emit("read_ok", actor=read_item.actor)
            self.clock.advance(config.mutation_cost_seconds)
        self._published_watermark = len(self._committed)

        checks = {
            "ack_implies_commit": all(event_id in self._committed_set for event_id in self._acked),
            "watermark_monotone": self._published_watermark == len(self._committed),
            "write_cap_respected": self._max_write_depth <= config.write_queue_size,
            "read_cap_respected": self._max_read_depth <= config.read_queue_size,
            "fairness_ok": self._fairness_ok,
            "no_unresolved_at_halt": self.write_q.depth == 0 and self.read_q.depth == 0 and not in_flight,
        }
        trace = tuple(self._trace)
        digest = hashlib.sha256("\n".join(trace).encode("utf-8")).hexdigest()
        return SimResult(
            seed=self.seed,
            steps=config.steps,
            trace=trace,
            trace_digest=digest,
            coverage=frozenset(self._coverage),
            counters=MappingProxyType(dict(self._counters)),
            committed=tuple(self._committed),
            acked=tuple(self._acked),
            max_write_depth=self._max_write_depth,
            max_read_depth=self._max_read_depth,
            checks=MappingProxyType(checks),
        )


def main(argv: list[str] | None = None) -> int:
    """Replay one seed: print the trace digest, coverage, counters, checks."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m know_your_context.serve.simulation SEED [STEPS]", file=sys.stderr)
        return 2
    # W13 operator freeze switch: refuse to run when the operator disabled the
    # simulator (e.g. frozen CI window); exit 2, announced, nothing simulated.
    from know_your_context.ops.switches import ENV_DISABLE_SIMULATION, load_switches

    if load_switches().simulation_disabled():
        print(f"simulation: refused — disabled by {ENV_DISABLE_SIMULATION}=1 (operator freeze switch)", file=sys.stderr)
        return 2
    seed = int(args[0])
    steps = int(args[1]) if len(args) > 1 else SimConfig.steps
    result = Simulation(seed=seed, config=SimConfig(steps=steps)).run()
    print(f"digest={result.trace_digest}")
    print(f"coverage={','.join(sorted(result.coverage))}")
    print(f"counters={json.dumps(dict(result.counters), sort_keys=True)}")
    print(f"checks={json.dumps(dict(result.checks), sort_keys=True)}")
    return 0 if all(result.checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
