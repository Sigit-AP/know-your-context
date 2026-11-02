"""Tests for ops/async_lifecycle.py — async lifecycle scheduler (W13).

The scheduler runs record_health_snapshot + prune_health_history as real
asyncio tasks with bounded per-task timeouts, deterministic retry, and
cooperative + hard cancellation.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from know_your_context.ops.async_lifecycle import (
    AsyncLifecycleScheduler,
    LifecycleSchedule,
)
from know_your_context.ops.lifecycle import (
    GENESIS_PREV_HASH,
    load_health_history,
    record_health_snapshot,
)
from know_your_context.serve.flow_retry import RetryPolicy
from know_your_context.store import EventStore


@pytest.fixture()
def store(tmp_path: Path) -> Any:
    instance = EventStore(tmp_path / "ops.sqlite3")
    yield instance
    instance.close()


def _schedule(**overrides: Any) -> LifecycleSchedule:
    base: dict[str, Any] = {
        "record_interval_seconds": 0.05,
        "prune_interval_seconds": 0.07,
        "prune_keep": 50,
        "task_timeout_ms": 5_000,
    }
    base.update(overrides)
    return LifecycleSchedule(**base)


class TestScheduleValidation:
    def test_defaults_sane(self) -> None:
        schedule = LifecycleSchedule()
        assert schedule.record_interval_seconds > 0
        assert schedule.prune_interval_seconds > 0
        assert schedule.prune_keep >= 1
        assert schedule.task_timeout_ms > 0

    def test_frozen(self) -> None:
        with pytest.raises(AttributeError):
            LifecycleSchedule().prune_keep = 3  # type: ignore[misc]

    @pytest.mark.parametrize("value", [0, -1.0, "x", True, None])
    def test_record_interval_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="record_interval_seconds"):
            _schedule(record_interval_seconds=value)

    @pytest.mark.parametrize("value", [0, -1.0, "x", True])
    def test_prune_interval_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="prune_interval_seconds"):
            _schedule(prune_interval_seconds=value)

    @pytest.mark.parametrize("value", [0, -1, 1.5, "x", True])
    def test_prune_keep_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="prune_keep"):
            _schedule(prune_keep=value)

    @pytest.mark.parametrize("value", [0, -100, 1.5, True])
    def test_task_timeout_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="task_timeout_ms"):
            _schedule(task_timeout_ms=value)


class TestSingleShots:
    def test_record_once_appends_verified_snapshot(self, store: Any, tmp_path: Path) -> None:
        async def main() -> int:
            scheduler = AsyncLifecycleScheduler(store, history_path=tmp_path / "health.jsonl", schedule=_schedule())
            snapshot = await scheduler.record_once()
            assert snapshot.prev_hash == GENESIS_PREV_HASH
            return snapshot.score

        score = asyncio.run(main())
        assert 0 <= score
        history = load_health_history(tmp_path / "health.jsonl")
        assert len(history) == 1  # load verifies the hash chain

    def test_prune_once_keeps_newest(self, store: Any, tmp_path: Path) -> None:
        path = tmp_path / "health.jsonl"
        for _ in range(4):
            record_health_snapshot(store, history_path=path)

        async def main() -> Any:
            scheduler = AsyncLifecycleScheduler(store, history_path=path, schedule=_schedule(prune_keep=2))
            return await scheduler.prune_once()

        report = asyncio.run(main())
        assert report.removed == 2
        assert report.kept == 2
        assert len(load_health_history(path)) == 2  # re-chained genesis verifies

    def test_record_once_retries_transient(self, store: Any, tmp_path: Path) -> None:
        calls = 0
        real = record_health_snapshot

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient fsync contention")
            return real(*args, **kwargs)

        async def main() -> Any:
            scheduler = AsyncLifecycleScheduler(
                store,
                history_path=tmp_path / "health.jsonl",
                schedule=_schedule(),
                retry_policy=RetryPolicy(max_attempts=3, base_delay_ms=1, max_delay_ms=5, jitter_ratio=0.0),
                record_fn=real,
            )
            scheduler._record_fn = flaky
            return await scheduler.record_once()

        snapshot = asyncio.run(main())
        assert calls == 2
        assert snapshot.hash

    def test_record_once_timeout_is_typed(self, store: Any, tmp_path: Path) -> None:
        gate = threading.Event()

        def blocking(*args: Any, **kwargs: Any) -> Any:
            gate.wait(30.0)
            raise AssertionError("unreachable")

        async def main() -> None:
            scheduler = AsyncLifecycleScheduler(
                store,
                history_path=tmp_path / "health.jsonl",
                schedule=_schedule(task_timeout_ms=80),
                retry_policy=RetryPolicy(max_attempts=1),
                record_fn=lambda *a, **k: None,
            )
            scheduler._record_fn = lambda *a, **k: gate.wait(30.0)
            from know_your_context.serve.flow_retry import RetryExhaustedError

            with pytest.raises(RetryExhaustedError):
                await scheduler.record_once()

        try:
            asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        finally:
            gate.set()


class TestRunLoop:
    def test_run_records_and_stops_cooperatively(self, store: Any, tmp_path: Path) -> None:
        async def main() -> Any:
            scheduler = AsyncLifecycleScheduler(store, history_path=tmp_path / "health.jsonl", schedule=_schedule())
            asyncio.get_running_loop().call_later(0.22, scheduler.request_stop)
            return await scheduler.run()

        report = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        assert report.status == "stopped"
        assert report.records >= 2
        history = load_health_history(tmp_path / "health.jsonl")
        assert len(history) == report.records

    def test_run_cancellation_propagates(self, store: Any, tmp_path: Path) -> None:
        async def main() -> str:
            scheduler = AsyncLifecycleScheduler(
                store, history_path=tmp_path / "health.jsonl", schedule=_schedule(record_interval_seconds=30.0)
            )
            task = asyncio.create_task(scheduler.run())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return "cancelled"

        assert asyncio.run(asyncio.wait_for(main(), timeout=5.0)) == "cancelled"

    def test_failing_task_does_not_kill_the_loop(self, store: Any, tmp_path: Path) -> None:
        def always_fails(*args: Any, **kwargs: Any) -> Any:
            raise OSError("store down")

        async def main() -> Any:
            scheduler = AsyncLifecycleScheduler(
                store,
                history_path=tmp_path / "health.jsonl",
                schedule=_schedule(),
                retry_policy=RetryPolicy(max_attempts=1, jitter_ratio=0.0),
            )
            scheduler._record_fn = always_fails
            asyncio.get_running_loop().call_later(0.25, scheduler.request_stop)
            return await scheduler.run()

        report = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        assert report.status == "stopped"
        assert report.failed_ticks >= 2  # failures recorded, loop kept going
        assert report.records == 0

    def test_tick_sequence_monotonic(self, store: Any, tmp_path: Path) -> None:
        async def main() -> Any:
            scheduler = AsyncLifecycleScheduler(store, history_path=tmp_path / "health.jsonl", schedule=_schedule())
            asyncio.get_running_loop().call_later(0.2, scheduler.request_stop)
            return await scheduler.run()

        report = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        sequences = [tick.sequence for tick in report.ticks]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)  # strictly increasing

    def test_max_ticks_bound(self, store: Any, tmp_path: Path) -> None:
        async def main() -> Any:
            scheduler = AsyncLifecycleScheduler(store, history_path=tmp_path / "health.jsonl", schedule=_schedule())
            return await scheduler.run(max_ticks=3)

        report = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        assert report.status == "completed"
        assert len(report.ticks) == 3
