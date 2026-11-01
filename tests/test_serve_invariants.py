"""Tiger Style invariant + budget tests for the superpower server (SUB-118).

Two families:

* **Assertion density** — every critical function (`_FairQueue`,
  `_TokenBucket`, `_SessionToken`, `_WriteArbiter` batch/execute/resolve/
  publish, session lease, inflight pairing) verifies its own pre/post
  conditions. The *drill* tests deliberately corrupt internal state and
  prove the assert fires: an invariant that cannot be shown to catch a
  violation is not an invariant.
* **Static budgets** — every queue/buffer/cache has an explicit upper
  bound known at start; `budgets.py` is the single source of truth and
  cross-budget soundness is re-proven at daemon start.

Assert = programmer error. Typed errors (ServerBusy/ShuttingDown/
DeadlineExceeded) remain the only answer to environment conditions — no
assert here may be reachable from network input alone.
"""

from __future__ import annotations

import time
from concurrent.futures import Future
from pathlib import Path

import pytest

from _serve_helpers import make_config
from know_your_context.audit import AuditLog
from know_your_context.mcp_server import DEFAULT_RESOURCE_MAX_BYTES, _Adapter
from know_your_context.projection import GraphProjector
from know_your_context.serve.budgets import (
    ADAPTER_CACHE_CAP,
    BUCKET_CACHE_CAP,
    LATENCY_RESERVOIR_CAP,
    LISTENER_BACKLOG,
    STATS_ACTOR_CAP,
    BudgetEntry,
    assert_static_budgets,
    budget_table,
    render_markdown,
)
from know_your_context.serve.daemon_config import ConfigError
from know_your_context.serve.superpower import (
    ServerBusy,
    ShuttingDown,
    SuperpowerDaemon,
    _FairQueue,
    _Mutation,
    _SessionToken,
    _TokenBucket,
    _WriteArbiter,
)
from know_your_context.service import KnowledgeService
from know_your_context.store import EventStore


class VirtualClock:
    """Test clock: time moves only when told (DST seam)."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ------------------------------------------------------------------ FairQueue


class TestFairQueueConstruction:
    @pytest.mark.parametrize(
        ("total_cap", "per_actor_cap"),
        [(0, 1), (-1, 1), (4, 0), (4, -2), (4, 5), (1, 2)],
    )
    def test_constructor_rejects_bad_caps(self, total_cap: int, per_actor_cap: int) -> None:
        with pytest.raises(AssertionError):
            _FairQueue(total_cap=total_cap, per_actor_cap=per_actor_cap, retry_after_ms=1)


class TestFairQueueInvariants:
    @pytest.mark.parametrize(("total_cap", "per_actor_cap"), [(1, 1), (2, 1), (8, 3), (64, 16), (256, 64)])
    def test_put_get_roundtrip_keeps_accounting(self, total_cap: int, per_actor_cap: int) -> None:
        clock = VirtualClock()
        queue = _FairQueue(total_cap=total_cap, per_actor_cap=per_actor_cap, retry_after_ms=1, now=clock.now)
        actors = [f"a{i}" for i in range(4)]
        offered = 0
        for round_no in range(total_cap + 2):
            for actor in actors:
                try:
                    queue.put(actor, f"item-{round_no}-{actor}")
                    offered += 1
                except ServerBusy:
                    pass
        assert queue.depth == min(offered, total_cap)
        drained = 0
        while True:
            item = queue.get(timeout=0)
            if item is None:
                break
            drained += 1
        assert drained == queue.depth == 0 or drained > 0  # drained what was offered
        assert drained == min(offered, total_cap)
        assert queue.depth == 0

    def test_total_cap_overflow_is_typed_not_assert(self) -> None:
        queue = _FairQueue(total_cap=2, per_actor_cap=2, retry_after_ms=7)
        queue.put("a", 1)
        queue.put("b", 2)
        with pytest.raises(ServerBusy) as caught:
            queue.put("c", 3)
        assert caught.value.retry_after_ms == 7

    def test_per_actor_cap_overflow_is_typed_not_assert(self) -> None:
        queue = _FairQueue(total_cap=10, per_actor_cap=2, retry_after_ms=3)
        queue.put("a", 1)
        queue.put("a", 2)
        with pytest.raises(ServerBusy) as caught:
            queue.put("a", 3)
        assert caught.value.retry_after_ms == 3
        # Other actors are unaffected by the spammer's full lane.
        queue.put("b", 4)
        assert queue.actor_depth("b") == 1

    def test_rotation_is_round_robin_across_actors(self) -> None:
        queue = _FairQueue(total_cap=32, per_actor_cap=8, retry_after_ms=1)
        for round_no in range(3):
            for actor in ("a", "b", "c"):
                queue.put(actor, f"{actor}{round_no}")
        seen = [queue.get(timeout=0) for _ in range(9)]
        assert seen == ["a0", "b0", "c0", "a1", "b1", "c1", "a2", "b2", "c2"]

    def test_excluded_lane_is_skipped_while_others_hold_work(self) -> None:
        queue = _FairQueue(total_cap=8, per_actor_cap=4, retry_after_ms=1)
        queue.put("a", "a-item")
        queue.put("b", "b-item")
        assert queue.get(timeout=0, exclude=frozenset({"a"})) == "b-item"
        assert queue.get(timeout=0, exclude=frozenset({"a"})) == "a-item"  # only lane left

    def test_excluded_sole_lane_is_work_conserving_fallback(self) -> None:
        queue = _FairQueue(total_cap=8, per_actor_cap=4, retry_after_ms=1)
        queue.put("a", "a-1")
        queue.put("a", "a-2")
        # 'a' is excluded but the only lane holding work: the queue never idles.
        assert queue.get(timeout=0, exclude=frozenset({"a"})) == "a-1"
        assert queue.get(timeout=0, exclude=frozenset({"a"})) == "a-2"

    def test_get_on_empty_returns_none_with_virtual_clock(self) -> None:
        clock = VirtualClock()
        queue = _FairQueue(total_cap=4, per_actor_cap=2, retry_after_ms=1, now=clock.now)
        assert queue.get(timeout=0) is None
        assert queue.get(timeout=0, exclude=frozenset({"a"})) is None

    def test_drain_returns_everything_and_resets(self) -> None:
        queue = _FairQueue(total_cap=8, per_actor_cap=4, retry_after_ms=1)
        queue.put("a", 1)
        queue.put("b", 2)
        queue.put("a", 3)
        items = queue.drain()
        assert sorted(items) == [1, 2, 3]
        assert queue.depth == 0
        assert queue.actor_depth("a") == 0
        assert queue.get(timeout=0) is None
        # The queue is reusable after a drain.
        queue.put("c", 4)
        assert queue.get(timeout=0) == 4

    def test_busy_put_leaves_no_orphan_lane(self) -> None:
        """Regression: a refused put must not leave an empty lane behind."""
        queue = _FairQueue(total_cap=2, per_actor_cap=1, retry_after_ms=1)
        queue.put("a", 1)
        with pytest.raises(ServerBusy):
            queue.put("a", 2)  # refused on the per-actor cap
        assert queue.actor_depth("a") == 1
        assert queue.depth == 1
        # A refused put for a NEW actor must not leave an empty lane either.
        queue.put("b", 3)
        with pytest.raises(ServerBusy):
            queue.put("c", 4)  # refused on the total cap
        assert queue.actor_depth("c") == 0
        assert queue.depth == 2
        # The rotation is unpoisoned: drains round-robin over a and b only.
        assert queue.get(timeout=0) == 1
        assert queue.get(timeout=0) == 3
        assert queue.get(timeout=0) is None


class TestFairQueueDrills:
    """Invariant drills: corrupt the state, prove the assert catches it."""

    def _queue(self) -> _FairQueue:
        queue = _FairQueue(total_cap=8, per_actor_cap=4, retry_after_ms=1)
        queue.put("a", "x")
        queue.put("b", "y")
        return queue

    def test_drill_total_counter_drift_is_caught(self) -> None:
        queue = self._queue()
        queue._total += 1  # sabotage: counter no longer matches the lanes
        with pytest.raises(AssertionError):
            queue.put("c", "z")

    def test_drill_total_counter_drift_caught_on_get(self) -> None:
        queue = self._queue()
        queue._total = 0  # sabotage: lanes hold items the counter denies
        with pytest.raises(AssertionError):
            queue.get(timeout=0)

    def test_drill_duplicate_rotation_entry_is_caught(self) -> None:
        queue = self._queue()
        queue._rotation.append("a")  # sabotage: actor twice in the rotation
        with pytest.raises(AssertionError):
            queue.get(timeout=0)

    def test_drill_orphan_empty_lane_is_caught(self) -> None:
        import collections

        queue = self._queue()
        queue._lanes["ghost"] = collections.deque()  # sabotage: lane outside the rotation
        with pytest.raises(AssertionError):
            queue.put("c", "z")

    def test_drill_lane_over_cap_is_caught(self) -> None:
        queue = self._queue()
        for _ in range(10):
            queue._lanes["a"].append("flood")  # sabotage: lane past the per-actor cap
        with pytest.raises(AssertionError):
            queue.get(timeout=0)

    def test_drill_total_beyond_cap_is_caught(self) -> None:
        queue = self._queue()
        queue._lanes["b"].extend(["1", "2", "3"])  # lanes hold more than the counter admits
        with pytest.raises(AssertionError):
            queue.put("c", "z")


# ----------------------------------------------------------------- TokenBucket


class TestTokenBucket:
    def test_burst_then_typed_retry(self) -> None:
        clock = VirtualClock()
        bucket = _TokenBucket(rate_per_second=10.0, burst=3, now=clock.now)
        assert bucket.retry_after_seconds() is None
        assert bucket.retry_after_seconds() is None
        assert bucket.retry_after_seconds() is None
        retry = bucket.retry_after_seconds()
        assert retry is not None and retry > 0

    def test_virtual_time_refills(self) -> None:
        clock = VirtualClock()
        bucket = _TokenBucket(rate_per_second=10.0, burst=1, now=clock.now)
        assert bucket.retry_after_seconds() is None
        assert bucket.retry_after_seconds() is not None
        clock.advance(0.2)  # two tokens' worth, capped at burst
        assert bucket.retry_after_seconds() is None
        assert bucket.retry_after_seconds() is not None  # burst was 1: no hoarding

    def test_retry_after_math_is_exact(self) -> None:
        clock = VirtualClock()
        bucket = _TokenBucket(rate_per_second=10.0, burst=1, now=clock.now)
        assert bucket.retry_after_seconds() is None
        clock.advance(0.05)  # half a token
        retry = bucket.retry_after_seconds()
        assert retry == pytest.approx(0.05)

    @pytest.mark.parametrize("seed", [1, 7, 42, 1000, 99999])
    def test_tokens_stay_within_capacity_under_random_load(self, seed: int) -> None:
        import random

        rng = random.Random(seed)
        clock = VirtualClock()
        bucket = _TokenBucket(rate_per_second=50.0, burst=5, now=clock.now)
        admitted = 0
        for _ in range(500):
            clock.advance(rng.choice([0.0, 0.001, 0.01, 0.1]))
            if bucket.retry_after_seconds() is None:
                admitted += 1
        assert admitted >= 1  # made progress; internal asserts guarded the bounds

    @pytest.mark.parametrize(("rate", "burst"), [(0.0, 1), (-1.0, 1), (1.0, 0), (1.0, -3)])
    def test_constructor_rejects_bad_shape(self, rate: float, burst: int) -> None:
        with pytest.raises(AssertionError):
            _TokenBucket(rate_per_second=rate, burst=burst)


# ---------------------------------------------------------------- SessionToken


class TestSessionToken:
    def test_beat_renews_lease_on_virtual_clock(self) -> None:
        clock = VirtualClock()
        token = _SessionToken("s-1", now=clock.now)
        clock.advance(10.0)
        assert token.last_seen == 0.0  # no implicit renewal: only beat() renews
        token.beat()
        assert token.last_seen == 10.0
        clock.advance(5.0)
        token.beat()
        assert token.last_seen == 15.0
        assert token.alive

    def test_constructor_rejects_empty_session_id(self) -> None:
        with pytest.raises(AssertionError):
            _SessionToken("")

    def test_default_clock_is_monotonic(self) -> None:
        token = _SessionToken("s-2")
        before = time.monotonic()
        assert token.last_seen <= time.monotonic()
        assert token.last_seen >= before - 1.0


# --------------------------------------------------------------- WriteArbiter


def _node(node_id: str) -> dict:
    return {
        "node_id": node_id,
        "kind": "leaf",
        "domain": "P1",
        "summary": "tiger node",
        "provenance": ["context://test"],
        "source_version_id": "sv-test",
    }


def _make_arbiter(tmp_path: Path, **overrides):
    config = make_config(tmp_path, **overrides)
    store = EventStore(config.db_path)
    projector = GraphProjector(store, output_root=config.graph_root)
    service = KnowledgeService(store, projector, max_token_budget=config.max_token_budget)
    audit = AuditLog(store)
    adapters: dict[str, _Adapter] = {}

    def factory(actor: str) -> _Adapter:
        adapter = adapters.get(actor)
        if adapter is None:
            adapter = _Adapter(
                service,
                graph_root=config.graph_root,
                audit_log=audit,
                actor=actor,
                max_token_budget=config.max_token_budget,
                resource_max_bytes=DEFAULT_RESOURCE_MAX_BYTES,
            )
            adapters[actor] = adapter
        return adapter

    arbiter = _WriteArbiter(config, store=store, projector=projector, adapter_factory=factory)
    return arbiter, store


class TestWriteArbiterExecuteResolve:
    def test_execute_captures_outcome_only_on_success(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        try:
            item = _Mutation("knowledge_log_event", {"node": _node("n-tiger")}, "actor", Future(), None)
            arbiter._execute_mutation(item)
            assert item.outcome is not None and item.error is None
        finally:
            store.close()

    def test_execute_captures_error_only_on_deadline(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        try:
            item = _Mutation(
                "knowledge_log_event",
                {"node": _node("n-late")},
                "actor",
                Future(),
                None,
                deadline=time.monotonic() - 1.0,
            )
            arbiter._execute_mutation(item)
            assert item.error is not None and item.outcome is None
            from know_your_context.serve.superpower import DeadlineExceeded

            assert isinstance(item.error, DeadlineExceeded)
        finally:
            store.close()

    def test_execute_captures_error_on_dead_session(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        try:
            token = _SessionToken("s-dead")
            token.alive = False
            item = _Mutation("knowledge_log_event", {"node": _node("n-orphan")}, "actor", Future(), None, session=token)
            arbiter._execute_mutation(item)
            assert isinstance(item.error, ShuttingDown)
            assert item.outcome is None
        finally:
            store.close()

    def test_resolve_sets_result_and_pops_dedup(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        try:
            key = ("knowledge_log_event", "actor", "k-1")
            future: Future = Future()
            arbiter._in_flight[key] = future
            item = _Mutation("knowledge_log_event", {}, "actor", future, key)
            item.outcome = {"ok": True}
            arbiter._resolve_mutation(item)
            assert future.done()
            assert future.result() == {"ok": True}
            assert key not in arbiter._in_flight
        finally:
            store.close()

    def test_resolve_sets_exception_and_pops_dedup(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        try:
            key = ("knowledge_log_event", "actor", "k-2")
            future: Future = Future()
            arbiter._in_flight[key] = future
            item = _Mutation("knowledge_log_event", {}, "actor", future, key)
            item.error = ShuttingDown("gone")
            arbiter._resolve_mutation(item)
            assert future.done()
            with pytest.raises(ShuttingDown):
                future.result()
            assert key not in arbiter._in_flight
        finally:
            store.close()

    def test_drill_resolve_inside_group_commit_transaction_is_caught(self, tmp_path: Path) -> None:
        """The SUB-104 regression guard: an ack must never fire pre-COMMIT."""
        arbiter, store = _make_arbiter(tmp_path)
        try:
            item = _Mutation("knowledge_log_event", {}, "actor", Future(), None)
            item.outcome = {"ok": True}
            arbiter._batch_transaction_open = True  # sabotage: resolve mid-transaction
            with pytest.raises(AssertionError):
                arbiter._resolve_mutation(item)
        finally:
            store.close()

    def test_drill_publish_watermark_regression_is_caught(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        try:
            arbiter._published_watermark = store.knowledge_high_watermark() + 10_000  # sabotage
            with pytest.raises(AssertionError):
                arbiter._publish_now()
        finally:
            store.close()

    def test_drill_in_flight_beyond_static_bound_is_caught(self, tmp_path: Path) -> None:
        config_overrides = {"write_queue_size": 4, "per_actor_write_cap": 2, "group_commit_batch": 2}
        arbiter, store = _make_arbiter(tmp_path, **config_overrides)
        try:
            # Sabotage: pretend the dedup map already holds queue+batch entries.
            for index in range(4 + 2):
                arbiter._in_flight[("verb", "actor", f"k-{index}")] = Future()
            with pytest.raises(AssertionError):
                arbiter.submit_mutation(
                    "knowledge_log_event",
                    {"node": _node("n-bound"), "idempotency_key": "k-new"},
                    actor="actor",
                )
        finally:
            store.close()


class TestWriteArbiterBatchGather:
    @pytest.mark.parametrize("batch_cap", [1, 2, 8])
    def test_batch_never_exceeds_group_commit_cap(self, tmp_path: Path, batch_cap: int) -> None:
        arbiter, store = _make_arbiter(tmp_path, group_commit_batch=batch_cap, group_commit_window_seconds=5.0)
        try:
            for index in range(20):
                arbiter._write_q.put(f"a{index}", _Mutation("v", {}, f"a{index}", Future(), None))
            first = arbiter._write_q.get(timeout=0)
            assert first is not None
            total = 1 + len(arbiter._gather_batch(first))  # gather returns items AFTER first
            assert 1 <= total <= batch_cap
        finally:
            store.close()

    def test_batch_has_at_most_one_item_per_actor_while_others_wait(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path, group_commit_batch=8, group_commit_window_seconds=5.0)
        try:
            for _ in range(4):
                for actor in ("a", "b", "c"):
                    arbiter._write_q.put(actor, _Mutation("v", {}, actor, Future(), None))
            first = arbiter._write_q.get(timeout=0)
            assert first is not None
            batch = [first, *arbiter._gather_batch(first)]
            # Fairness: every live lane is represented before ANY actor gets
            # a second slot (repeats afterwards are the documented
            # work-conserving fallback once all lanes are excluded).
            assert len(batch) == 8
            assert set(member.actor for member in batch[:3]) == {"a", "b", "c"}
            assert len({member.actor for member in batch[:3]}) == 3
        finally:
            store.close()

    def test_batch_fills_from_a_single_actor_as_work_conserving_fallback(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path, group_commit_batch=4, group_commit_window_seconds=5.0)
        try:
            for _ in range(10):
                arbiter._write_q.put("solo", _Mutation("v", {}, "a", Future(), None))
            first = arbiter._write_q.get(timeout=0)
            assert first is not None
            batch = [first, *arbiter._gather_batch(first)]
            assert len(batch) == 4
            assert {member.actor for member in batch} == {"a"}
        finally:
            store.close()

    def test_zero_window_yields_single_item_batch(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path, group_commit_batch=8, group_commit_window_seconds=0.0)
        try:
            for index in range(4):
                arbiter._write_q.put(f"a{index}", _Mutation("v", {}, f"a{index}", Future(), None))
            first = arbiter._write_q.get(timeout=0)
            assert first is not None
            assert arbiter._gather_batch(first) == []
        finally:
            store.close()


class TestWriteArbiterLifecycle:
    def test_full_write_path_through_the_real_thread(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        arbiter.start()
        try:
            result = arbiter.submit_mutation("knowledge_log_event", {"node": _node("n-live")}, actor="actor")
            assert isinstance(result, dict)
            assert store.knowledge_high_watermark() >= arbiter.published_watermark
        finally:
            arbiter.stop()
            store.close()

    def test_stop_fails_queued_items_typed_and_pops_dedup(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        key = ("v", "actor", "k-stop")
        future: Future = Future()
        arbiter._in_flight[key] = future
        arbiter._write_q.put("actor", _Mutation("v", {}, "actor", future, key))
        arbiter.stop()  # never started: the drain path still answers queued items
        assert future.done()
        with pytest.raises(ShuttingDown):
            future.result()
        assert key not in arbiter._in_flight

    def test_run_audit_after_stop_is_typed_not_hanging(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        arbiter._stop.set()
        try:
            with pytest.raises(ShuttingDown):
                arbiter.run_audit(lambda: None)
        finally:
            store.close()

    def test_audit_lane_is_bounded_by_config(self, tmp_path: Path) -> None:
        arbiter, store = _make_arbiter(tmp_path)
        try:
            assert arbiter._audit_q.maxsize == arbiter._config.audit_queue_size
            assert arbiter._audit_q.maxsize >= 1
        finally:
            store.close()


# -------------------------------------------------------------------- budgets


class TestBudgetTable:
    EXPECTED = (
        "sessions_active",
        "session_socket_map",
        "write_queue_total",
        "write_queue_per_actor",
        "read_queue_total",
        "read_queue_per_actor",
        "heavy_queue_total",
        "heavy_queue_per_actor",
        "audit_lane",
        "dedup_in_flight",
        "group_commit_batch",
        "admission_buckets",
        "writer_adapter_cache",
        "read_adapter_cache",
        "heavy_adapter_cache",
        "stats_actor_map",
        "latency_reservoir",
        "frame_bytes",
        "listener_backlog",
        "session_lease",
        "drain_timeout",
        "read_publish_wait",
    )

    @pytest.mark.parametrize("name", EXPECTED)
    def test_every_bounded_structure_is_in_the_table(self, tmp_path: Path, name: str) -> None:
        config = make_config(tmp_path)
        names = {entry.name for entry in budget_table(config)}
        assert name in names

    def test_table_is_deterministic(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        assert budget_table(config) == budget_table(config)

    def test_entries_are_budget_entries_with_positive_bounds(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        for entry in budget_table(config):
            assert isinstance(entry, BudgetEntry)
            assert entry.bound >= 1, entry.name
            assert entry.unit and entry.enforced_at and entry.overflow

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("write_queue_total", 32),
            ("read_queue_per_actor", 6),
            ("heavy_queue_total", 9),
            ("dedup_in_flight", 32 + 3),
            ("admission_buckets", BUCKET_CACHE_CAP),
            ("writer_adapter_cache", ADAPTER_CACHE_CAP),
            ("latency_reservoir", LATENCY_RESERVOIR_CAP),
            ("listener_backlog", LISTENER_BACKLOG),
        ],
    )
    def test_bounds_derive_from_config(self, tmp_path: Path, name: str, expected: int) -> None:
        config = make_config(
            tmp_path,
            write_queue_size=32,
            per_actor_write_cap=8,
            read_queue_size=24,
            per_actor_read_cap=6,
            heavy_queue_size=9,
            per_actor_heavy_cap=3,
            group_commit_batch=3,
        )
        bounds = {entry.name: entry.bound for entry in budget_table(config)}
        assert bounds[name] == expected

    def test_render_markdown_lists_every_entry(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        entries = budget_table(config)
        rendered = render_markdown(entries)
        assert rendered.startswith("| Budget |")
        for entry in entries:
            assert entry.name in rendered
        # one header + separator + one row per entry
        assert len(rendered.strip().splitlines()) == len(entries) + 2

    def test_render_is_deterministic(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        entries = budget_table(config)
        assert render_markdown(entries) == render_markdown(entries)

    def test_assert_static_budgets_accepts_default_config(self, tmp_path: Path) -> None:
        assert_static_budgets(make_config(tmp_path))

    def test_audit_lane_must_absorb_the_read_pool(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            make_config(tmp_path, read_pool_size=8, audit_queue_size=4)


# ------------------------------------------------------------- daemon internals


class TestDaemonStatsBudgets:
    def test_latency_map_is_bounded(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path))
        for index in range(STATS_ACTOR_CAP + 50):
            daemon._record_latency(f"actor-{index}", "read", time.monotonic())
        assert len(daemon._latencies) <= STATS_ACTOR_CAP

    def test_latency_map_keeps_the_first_tracked_actors(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path))
        daemon._record_latency("actor-first", "read", time.monotonic())
        for index in range(STATS_ACTOR_CAP + 50):
            daemon._record_latency(f"actor-{index}", "read", time.monotonic())
        assert "actor-first" in daemon._latencies

    def test_latency_reservoir_is_bounded(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path))
        for _ in range(LATENCY_RESERVOIR_CAP + 100):
            daemon._record_latency("actor", "read", time.monotonic())
        assert len(daemon._latencies["actor"]["read"]) <= LATENCY_RESERVOIR_CAP

    def test_per_actor_map_is_bounded(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path))
        for index in range(STATS_ACTOR_CAP + 50):
            daemon._note_actor_request(f"actor-{index}")
        assert len(daemon._per_actor) <= STATS_ACTOR_CAP

    def test_error_note_on_untracked_actor_is_a_no_op(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path))
        for index in range(STATS_ACTOR_CAP + 10):
            daemon._note_actor_request(f"actor-{index}")
        daemon._note_actor_error("actor-never-tracked")
        assert "actor-never-tracked" not in daemon._per_actor


class _FakeSocket:
    def __init__(self) -> None:
        self.closed = False

    def shutdown(self, _how) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class TestSessionLeaseAndInflight:
    def test_reap_marks_expired_sessions_dead(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path, session_lease_seconds=30.0))
        expired = _SessionToken("s-old")
        expired.last_seen = time.monotonic() - 60.0
        fresh = _SessionToken("s-new")
        daemon._session_sockets[_FakeSocket()] = expired  # type: ignore[dict-item]
        daemon._session_sockets[_FakeSocket()] = fresh  # type: ignore[dict-item]
        daemon._reap_expired_sessions()
        assert not expired.alive
        assert fresh.alive

    def test_drill_expired_session_surviving_reap_is_caught(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path, session_lease_seconds=30.0))
        stale = _SessionToken("s-stale")
        stale.last_seen = time.monotonic() - 3600.0
        daemon._session_sockets[_FakeSocket()] = stale  # type: ignore[dict-item]
        with pytest.raises(AssertionError):
            daemon._assert_no_expired_sessions_locked(time.monotonic())

    def test_inflight_end_without_begin_is_caught(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path))
        with pytest.raises(AssertionError):
            daemon._inflight_end()

    def test_inflight_begin_end_pairs_return_to_zero(self, tmp_path: Path) -> None:
        daemon = SuperpowerDaemon(make_config(tmp_path))
        for _ in range(5):
            daemon._inflight_begin()
        for _ in range(5):
            daemon._inflight_end()
        assert daemon._inflight == 0

    def test_drill_session_capacity_overflow_is_caught(self, tmp_path: Path) -> None:
        import threading

        daemon = SuperpowerDaemon(make_config(tmp_path, max_agents=2))
        daemon._sessions = {threading.Thread() for _ in range(3)}  # sabotage
        with pytest.raises(AssertionError):
            daemon._assert_session_capacity_locked()
