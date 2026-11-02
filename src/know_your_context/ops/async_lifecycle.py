"""Async lifecycle scheduler: health recording + retention as tasks (W13).

``ops/lifecycle.py`` provides the synchronous primitives (verified hash-chained
health ledger, re-chaining prune). This module schedules them on an asyncio
loop with the W13 flow guarantees:

* every task runs under a bounded timeout
  (:func:`~know_your_context.serve.flow_retry.run_with_timeout_async`) and a
  deterministic :class:`RetryPolicy` — transient I/O (``OSError`` family,
  which includes timeouts) is retried with seeded backoff;
* a failed tick is *recorded* and the loop continues (fail-open: health
  observation must never take the host down); ``CancelledError`` is sacred —
  it propagates immediately and is never retried;
* ``request_stop()`` is thread-safe and cooperative: the current tick
  finishes, then ``run()`` returns with ``status="stopped"``;
* invariant asserts: tick sequence numbers are strictly increasing; the
  record count never exceeds the number of ticks.

Kill switch: constructing the scheduler is inert; nothing runs until
``run()``/``record_once()``/``prune_once()`` is awaited.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from know_your_context.ops.lifecycle import (
    PruneReport,
    history_path_for_store,
    prune_health_history,
    record_health_snapshot,
)
from know_your_context.serve.flow_retry import (
    RetryPolicy,
    retry_async,
    run_with_timeout_async,
)

__all__ = [
    "AsyncLifecycleScheduler",
    "LifecycleRunReport",
    "LifecycleSchedule",
    "LifecycleTick",
]

_LOGGER = logging.getLogger("know_your_context.ops.async_lifecycle")


def _check_positive_seconds(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a number > 0, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class LifecycleSchedule:
    """Fail-closed schedule knobs for the async lifecycle loop."""

    record_interval_seconds: float = 300.0
    prune_interval_seconds: float = 3600.0
    prune_keep: int = 500
    task_timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        _check_positive_seconds("record_interval_seconds", self.record_interval_seconds)
        _check_positive_seconds("prune_interval_seconds", self.prune_interval_seconds)
        if isinstance(self.prune_keep, bool) or not isinstance(self.prune_keep, int) or self.prune_keep < 1:
            raise ValueError(f"prune_keep must be an int >= 1, got {self.prune_keep!r}")
        if (
            isinstance(self.task_timeout_ms, bool)
            or not isinstance(self.task_timeout_ms, int)
            or self.task_timeout_ms <= 0
        ):
            raise ValueError(f"task_timeout_ms must be an int > 0, got {self.task_timeout_ms!r}")


@dataclass(frozen=True)
class LifecycleTick:
    kind: str  # "record" | "prune"
    sequence: int
    duration_ms: int
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class LifecycleRunReport:
    status: str  # "stopped" | "completed"
    ticks: tuple[LifecycleTick, ...] = field(default_factory=tuple)
    records: int = 0
    prunes: int = 0
    failed_ticks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records": self.records,
            "prunes": self.prunes,
            "failed_ticks": self.failed_ticks,
            "ticks": [
                {"kind": t.kind, "sequence": t.sequence, "duration_ms": t.duration_ms, "ok": t.ok, "error": t.error}
                for t in self.ticks
            ],
        }


class AsyncLifecycleScheduler:
    """Periodic record/prune loop with bounded tasks and real cancellation."""

    def __init__(
        self,
        store: Any,
        *,
        history_path: str | Path | None = None,
        schedule: LifecycleSchedule | None = None,
        retry_policy: RetryPolicy | None = None,
        record_fn: Callable[..., Any] = record_health_snapshot,
        prune_fn: Callable[..., Any] = prune_health_history,
        logger: Any = None,
    ) -> None:
        self._store = store
        self._history_path = Path(history_path) if history_path is not None else history_path_for_store(store)
        self._schedule = schedule if schedule is not None else LifecycleSchedule()
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._record_fn = record_fn
        self._prune_fn = prune_fn
        self._logger = logger if logger is not None else _LOGGER
        self._thread_stop = threading.Event()
        self._stop_aio: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sequence = 0

    @property
    def schedule(self) -> LifecycleSchedule:
        return self._schedule

    def request_stop(self) -> None:
        """Thread-safe cooperative stop: the in-flight tick finishes."""
        self._thread_stop.set()
        loop, event = self._loop, self._stop_aio
        if loop is not None and event is not None:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # loop already closed
                pass

    # ------------------------------------------------------------ single shots

    async def record_once(self) -> Any:
        """One bounded, retried health-snapshot append."""

        async def work() -> Any:
            return await asyncio.to_thread(self._record_fn, self._store, history_path=self._history_path)

        return await retry_async(
            lambda: run_with_timeout_async(work(), timeout_ms=self._schedule.task_timeout_ms),
            policy=self._retry_policy,
            retryable=(OSError,),
        )

    async def prune_once(self) -> PruneReport:
        """One bounded, retried retention pass."""

        async def work() -> PruneReport:
            return await asyncio.to_thread(self._prune_fn, self._history_path, keep=self._schedule.prune_keep)

        return await retry_async(
            lambda: run_with_timeout_async(work(), timeout_ms=self._schedule.task_timeout_ms),
            policy=self._retry_policy,
            retryable=(OSError,),
        )

    # -------------------------------------------------------------- the loop

    async def run(self, *, max_ticks: int | None = None) -> LifecycleRunReport:
        """The scheduler loop. CancelledError propagates; stop is cooperative."""
        if max_ticks is not None and (isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks < 1):
            raise ValueError(f"max_ticks must be an int >= 1, got {max_ticks!r}")
        self._loop = asyncio.get_running_loop()
        self._stop_aio = asyncio.Event()
        if self._thread_stop.is_set():
            self._stop_aio.set()
        ticks: list[LifecycleTick] = []
        records = 0
        prunes = 0
        failed = 0
        started = time.monotonic()
        next_record = started  # first record is due immediately
        next_prune = started + self._schedule.prune_interval_seconds
        try:
            while True:
                if self._thread_stop.is_set():
                    return self._report("stopped", ticks, records, prunes, failed)
                if max_ticks is not None and len(ticks) >= max_ticks:
                    return self._report("completed", ticks, records, prunes, failed)
                now = time.monotonic()
                due_in = max(0.0, min(next_record, next_prune) - now)
                if due_in > 0:
                    if await self._sleep_or_stopped(due_in):
                        return self._report("stopped", ticks, records, prunes, failed)
                now = time.monotonic()
                if now >= next_record and (max_ticks is None or len(ticks) < max_ticks):
                    tick = await self._tick("record")
                    ticks.append(tick)
                    if tick.ok:
                        records += 1
                    else:
                        failed += 1
                    next_record = time.monotonic() + self._schedule.record_interval_seconds
                if (
                    now >= next_prune
                    and not self._thread_stop.is_set()
                    and (max_ticks is None or len(ticks) < max_ticks)
                ):
                    tick = await self._tick("prune")
                    ticks.append(tick)
                    if tick.ok:
                        prunes += 1
                    else:
                        failed += 1
                    next_prune = time.monotonic() + self._schedule.prune_interval_seconds
        finally:
            self._loop = None
            self._stop_aio = None

    async def _tick(self, kind: str) -> LifecycleTick:
        self._sequence += 1
        sequence = self._sequence
        started = time.monotonic()
        try:
            if kind == "record":
                await self.record_once()
            else:
                await self.prune_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - recorded, loop continues (fail-open)
            self._logger.warning("lifecycle %s tick %d failed: %s: %s", kind, sequence, type(error).__name__, error)
            return LifecycleTick(
                kind=kind,
                sequence=sequence,
                duration_ms=int((time.monotonic() - started) * 1000),
                ok=False,
                error=f"{type(error).__name__}: {error}",
            )
        return LifecycleTick(
            kind=kind, sequence=sequence, duration_ms=int((time.monotonic() - started) * 1000), ok=True
        )

    def _report(
        self,
        status: str,
        ticks: list[LifecycleTick],
        records: int,
        prunes: int,
        failed: int,
    ) -> LifecycleRunReport:
        sequences = [tick.sequence for tick in ticks]
        assert sequences == sorted(sequences), "tick sequences must be monotone"
        assert len(set(sequences)) == len(sequences), "tick sequences must be unique"
        assert records + prunes + failed == len(ticks), "every tick accounted exactly once"
        return LifecycleRunReport(
            status=status, ticks=tuple(ticks), records=records, prunes=prunes, failed_ticks=failed
        )

    async def _sleep_or_stopped(self, seconds: float) -> bool:
        if seconds <= 0:
            await asyncio.sleep(0)
            return self._thread_stop.is_set()
        stop_aio = self._stop_aio
        if stop_aio is None:
            await asyncio.sleep(seconds)
            return self._thread_stop.is_set()
        try:
            await asyncio.wait_for(stop_aio.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return self._thread_stop.is_set()
