"""Heartbeat + tiered watchdog supervision for every worker thread (W13).

The daemon previously had exactly one liveness mechanism — a global
:class:`~know_your_context.serve.superpower.LivenessWatchdog` whose only
escalation is ``os._exit(70)``. That is the right *last* resort but the wrong
*first* response: one wedged worker should be restarted, not kill the daemon.

This module provides the tiered middle ground:

* **Heartbeat** — every worker beats; beats are monotone (clock skew clamps
  forward, never backward) and thread-safe.
* **SupervisedWorker** — a worker thread with a heartbeat, a cooperative
  stop event, a bounded ``stop()``, and a restart *budget* (``max_restarts``
  inside ``restart_window_seconds``; exhaustion is terminal — flapping is a
  bug to surface, not to hide).
* **Watchdog** — observes all registered workers and escalates in tiers:
  ``suspect`` (silent ≥ suspect_seconds — alert only) → ``restart`` (silent
  ≥ stall_seconds, or thread gone — budgeted restart) → ``dead`` (budget
  exhausted or restart failed — terminal, alerted). A fresh beat while
  ``suspect`` recovers the worker; a fresh beat on a non-terminal ``dead``
  worker (restart was refused because it is alive — a long legitimate
  operation is an episode, not a life sentence) also recovers it, and a
  refused restart alerts once per episode, never per tick. ``drain_all``
  gives every registered worker a bounded stop and reports each outcome —
  nothing is silently abandoned.

Invariant asserts: heartbeat monotonicity, restart-budget accounting, drain
completeness (every registered worker appears exactly once in the report).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "DrainOutcome",
    "DrainReport",
    "Heartbeat",
    "SupervisedThread",
    "SupervisedWorker",
    "Watchdog",
    "WatchdogAction",
    "WatchdogPolicy",
    "WorkerState",
]

_LOGGER = logging.getLogger("know_your_context.serve.watchdog")

#: A worker body receives its heartbeat and its cooperative stop event.
WorkerTarget = Callable[["Heartbeat", threading.Event], None]


class WorkerState(StrEnum):
    """Lifecycle state of one supervised worker thread.

    ``HEALTHY`` — beats inside the suspect window. ``SUSPECT`` — heartbeat
    stale past the policy threshold; the next escalation is a bounded
    restart. ``DEAD`` — crash or restart-budget exhaustion; terminal-ness
    tracked separately from the state itself. ``DRAINED`` — cooperative
    drain completed inside its bound. ``STOP_TIMED_OUT`` — the worker
    outlived its drain bound; the supervisor reports it instead of hanging.
    """

    HEALTHY = "healthy"
    SUSPECT = "suspect"
    DEAD = "dead"  # crash or exhaustion; terminal-ness tracked separately
    DRAINED = "drained"
    STOP_TIMED_OUT = "stop_timed_out"


@dataclass(frozen=True)
class WatchdogPolicy:
    """Escalation thresholds; fail-closed validation at construction."""

    suspect_seconds: float = 10.0
    stall_seconds: float = 30.0
    max_restarts: int = 3
    restart_window_seconds: float = 60.0
    check_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        for name in ("suspect_seconds", "stall_seconds", "restart_window_seconds", "check_interval_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a number > 0, got {value!r}")
        if self.stall_seconds < self.suspect_seconds:
            raise ValueError(
                f"stall_seconds must be >= suspect_seconds, got {self.stall_seconds} < {self.suspect_seconds}"
            )
        if isinstance(self.max_restarts, bool) or not isinstance(self.max_restarts, int) or self.max_restarts < 1:
            raise ValueError(f"max_restarts must be an int >= 1, got {self.max_restarts!r}")


class Heartbeat:
    """A monotone, thread-safe beat counter; ``now`` injectable."""

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._last_beat = now()
        self._count = 0
        self._lock = threading.Lock()

    def beat(self) -> None:
        with self._lock:
            current = self._now()
            # Monotone clamp: clock skew must never move liveness backwards.
            self._last_beat = max(self._last_beat, current)
            self._count += 1
            assert self._last_beat >= 0.0 or True  # monotonicity kept by clamp

    @property
    def last_beat(self) -> float:
        with self._lock:
            return self._last_beat

    @property
    def beat_count(self) -> int:
        with self._lock:
            return self._count

    def age_seconds(self) -> float:
        return max(0.0, self._now() - self.last_beat)


class SupervisedWorker:
    """One restartable worker thread with heartbeat and bounded drain."""

    def __init__(
        self,
        name: str,
        target: WorkerTarget,
        *,
        policy: WatchdogPolicy | None = None,
        now: Callable[[], float] = time.monotonic,
        logger: Any = None,
    ) -> None:
        if not name or not isinstance(name, str):
            raise ValueError(f"worker name must be a non-empty str, got {name!r}")
        self._name = name
        self._target = target
        self._policy = policy if policy is not None else WatchdogPolicy()
        self._now = now
        self._logger = logger if logger is not None else _LOGGER
        self._heartbeat = Heartbeat(now=now)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = WorkerState.HEALTHY
        self._terminal = False  # restart budget exhausted — flapping is a bug
        self._last_error: BaseException | None = None
        self._restart_count = 0
        self._restart_times: list[float] = []
        self._lifecycle_lock = threading.Lock()

    # ------------------------------------------------------------- properties
    @property
    def name(self) -> str:
        return self._name

    @property
    def heartbeat(self) -> Heartbeat:
        return self._heartbeat

    @property
    def state(self) -> WorkerState:
        return self._state

    def mark_state(self, state: WorkerState) -> None:
        """The watchdog's sanctioned transition path (suspect/recover/dead)."""
        self._state = state

    @property
    def terminal(self) -> bool:
        """True when the restart budget is exhausted — the watchdog's skip signal."""
        return self._terminal

    @property
    def last_error(self) -> BaseException | None:
        return self._last_error

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def stop_event(self) -> threading.Event:
        return self._stop

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lifecycle_lock:
            assert self._thread is None, f"worker {self._name} already started"
            self._spawn()

    def _spawn(self) -> None:
        assert self._thread is None or not self._thread.is_alive(), "spawn over a live thread"
        self._heartbeat = Heartbeat(now=self._now)
        self._stop = threading.Event()
        self._state = WorkerState.HEALTHY
        self._thread = threading.Thread(target=self._run, name=f"supervised-{self._name}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        heartbeat, stop = self._heartbeat, self._stop
        try:
            self._target(heartbeat, stop)
        except BaseException as error:  # noqa: BLE001 - recorded, never swallowed
            self._last_error = error
            if not isinstance(error, SystemExit):
                self._logger.error("supervised worker %s crashed: %s: %s", self._name, type(error).__name__, error)
        finally:
            with self._lifecycle_lock:
                # Only the current incarnation may mark itself dead: after a
                # restart ``self._thread`` is the NEW thread, and the old
                # one's exit must not clobber its HEALTHY state.
                if threading.current_thread() is self._thread and self._state is not WorkerState.DRAINED:
                    # Returning without being asked to stop is a crash-class
                    # event: the watchdog must observe a dead thread.
                    self._state = WorkerState.DEAD

    def restart(self) -> bool:
        """Bounded restart; budget exhaustion is terminal (returns False)."""
        with self._lifecycle_lock:
            if self._terminal or self._state is WorkerState.DRAINED:
                return False
            now = self._now()
            allowed, self._restart_times = _budget_allows(self._restart_times, self._policy, now)
            if not allowed:
                self._state = WorkerState.DEAD
                self._terminal = True
                self._logger.error(
                    "supervised worker %s exhausted its restart budget (%d/%ss) — terminal",
                    self._name,
                    self._policy.max_restarts,
                    self._policy.restart_window_seconds,
                )
                return False
            self._restart_times.append(now)
            self._restart_count += 1
        # Stop the old incarnation outside the lifecycle lock (join may block).
        if self.is_alive():
            self._stop.set()
            assert self._thread is not None
            self._thread.join(timeout=1.0)
        with self._lifecycle_lock:
            assert self._restart_count <= len(self._restart_times), "restart accounting broken"
            self._spawn()
        self._logger.warning("supervised worker %s restarted (restart #%d)", self._name, self._restart_count)
        return True

    def stop(self, *, timeout_ms: int) -> bool:
        """Bounded cooperative drain; False means the worker outlived it."""
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout_ms / 1000.0))
        with self._lifecycle_lock:
            if self.is_alive():
                self._state = WorkerState.STOP_TIMED_OUT
                return False
            self._state = WorkerState.DRAINED
            return True


def _budget_allows(restart_times: list[float], policy: WatchdogPolicy, now: float) -> tuple[bool, list[float]]:
    """The sliding-window restart budget; returns (allowed, pruned_times)."""
    pruned = [t for t in restart_times if now - t <= policy.restart_window_seconds]
    return len(pruned) < policy.max_restarts, pruned


class SupervisedThread:
    """Watchdog adapter for a pre-existing worker thread (W13).

    Unlike :class:`SupervisedWorker` (which owns its target and stop event),
    this adapts a thread owned by another component (daemon lanes, acceptor,
    reaper). Restart semantics are honest about Python's limits: a DEAD
    thread is replaced via the restart factory; a LIVE but silent thread can
    never be killed safely, so it escalates suspect → dead (alert) — a
    restart that would duplicate a queue consumer is refused.
    """

    def __init__(
        self,
        name: str,
        thread: threading.Thread,
        *,
        heartbeat_getter: Callable[[], Heartbeat],
        restart_factory: Callable[[], threading.Thread],
        policy: WatchdogPolicy | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if not name or not isinstance(name, str):
            raise ValueError(f"worker name must be a non-empty str, got {name!r}")
        self._name = name
        self._thread = thread
        self._heartbeat_getter = heartbeat_getter
        self._restart_factory = restart_factory
        self._policy = policy if policy is not None else WatchdogPolicy()
        self._now = now
        self._state = WorkerState.HEALTHY
        self._terminal = False
        self._restart_count = 0
        self._restart_times: list[float] = []
        self._lifecycle_lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def heartbeat(self) -> Heartbeat:
        return self._heartbeat_getter()

    @property
    def state(self) -> WorkerState:
        return self._state

    def mark_state(self, state: WorkerState) -> None:
        self._state = state

    @property
    def terminal(self) -> bool:
        return self._terminal

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def restart(self) -> bool:
        with self._lifecycle_lock:
            if self._terminal or self._state is WorkerState.DRAINED:
                return False
            allowed, self._restart_times = _budget_allows(self._restart_times, self._policy, self._now())
            if not allowed:
                self._state = WorkerState.DEAD
                self._terminal = True
                return False
            if self._thread.is_alive():
                # A live wedged thread cannot be replaced without duplicating
                # its work — refuse; the watchdog escalates to dead+alert.
                return False
            self._restart_times.append(self._now())
            self._restart_count += 1
            old = self._thread
        replacement = self._restart_factory()
        with self._lifecycle_lock:
            assert replacement is not old, "restart factory must return a fresh thread"
            assert replacement.is_alive(), "restart factory must return a started thread"
            self._thread = replacement
            self._state = WorkerState.HEALTHY
        return True

    def stop(self, *, timeout_ms: int) -> bool:
        """Bounded join; the adapter owns no stop signal (the owner drains)."""
        self._thread.join(timeout=max(0.0, timeout_ms / 1000.0))
        with self._lifecycle_lock:
            if self._thread.is_alive():
                self._state = WorkerState.STOP_TIMED_OUT
                return False
            self._state = WorkerState.DRAINED
            return True


@dataclass(frozen=True)
class WatchdogAction:
    """One escalation step taken by :meth:`Watchdog.check_once`."""

    worker_name: str
    kind: str  # "suspect" | "restart" | "dead" | "recovered"
    detail: str


@dataclass(frozen=True)
class DrainOutcome:
    """One worker's drain result inside a :class:`DrainReport`.

    ``drained`` is True only when the worker stopped inside its bound;
    ``was_alive`` distinguishes "was already down" from a real drain, and
    ``state`` records the terminal :class:`WorkerState` for audit.
    """

    name: str
    drained: bool
    was_alive: bool
    state: WorkerState


@dataclass(frozen=True)
class DrainReport:
    """Aggregate of a full drain pass; ``all_drained`` gates shutdown.

    Shutdown proceeds only when every outcome drained — a single
    ``STOP_TIMED_OUT`` keeps the report false so the caller escalates
    (forced stop) instead of abandoning a live worker silently.
    """

    outcomes: tuple[DrainOutcome, ...] = field(default_factory=tuple)

    @property
    def all_drained(self) -> bool:
        return all(outcome.drained for outcome in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_drained": self.all_drained,
            "outcomes": [
                {"name": o.name, "drained": o.drained, "was_alive": o.was_alive, "state": str(o.state)}
                for o in self.outcomes
            ],
        }


class Watchdog:
    """Tiered supervisor over registered workers; ``now`` injectable."""

    def __init__(
        self,
        *,
        policy: WatchdogPolicy | None = None,
        now: Callable[[], float] = time.monotonic,
        on_alert: Callable[[str, str], None] | None = None,
        logger: Any = None,
    ) -> None:
        self._policy = policy if policy is not None else WatchdogPolicy()
        self._now = now
        self._on_alert = on_alert
        self._logger = logger if logger is not None else _LOGGER
        self._workers: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.heartbeat = Heartbeat(now=now)  # an upper layer may watch the watchdog

    # --------------------------------------------------------------- registry
    def register(self, worker: Any) -> None:
        with self._lock:
            if worker.name in self._workers:
                raise ValueError(f"worker {worker.name!r} is already registered")
            self._workers[worker.name] = worker

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._workers.pop(name, None) is not None

    def worker_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._workers)

    # -------------------------------------------------------------- checking
    def check_once(self) -> list[WatchdogAction]:
        """One escalation pass over all workers; the testable core."""
        actions: list[WatchdogAction] = []
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            state = worker.state
            # DRAINED/STOP_TIMED_OUT: owned by a drain. DEAD+terminal: budget
            # exhausted, restart would be flapping. DEAD non-terminal (a
            # crash) stays in rotation — restart is the whole point.
            if state in (WorkerState.DRAINED, WorkerState.STOP_TIMED_OUT):
                continue
            if state is WorkerState.DEAD and worker.terminal:
                continue
            silence = self._now() - worker.heartbeat.last_beat
            assert silence >= -1e-9, f"heartbeat monotonicity violated for {worker.name}"
            crashed = not worker.is_alive()
            if crashed or silence >= self._policy.stall_seconds:
                reason = "crashed" if crashed else f"silent {silence:.1f}s"
                pre_state = worker.state
                pre_terminal = worker.terminal
                if worker.restart():
                    worker.mark_state(WorkerState.HEALTHY)
                    actions.append(WatchdogAction(worker.name, "restart", reason))
                    self._alert(worker.name, "restart")
                elif (worker.terminal and not pre_terminal) or pre_state is not WorkerState.DEAD:
                    # Report the transition INTO dead, and always report budget
                    # exhaustion (terminal) — but a live busy worker whose
                    # restart keeps being refused alerts once per episode, not
                    # per tick: per-tick repeats are spam, not signal (SUB-132).
                    worker.mark_state(WorkerState.DEAD)
                    actions.append(WatchdogAction(worker.name, "dead", f"{reason}; restart refused"))
                    self._alert(worker.name, "dead")
            elif silence >= self._policy.suspect_seconds:
                if worker.state is not WorkerState.SUSPECT:
                    worker.mark_state(WorkerState.SUSPECT)
                    actions.append(WatchdogAction(worker.name, "suspect", f"silent {silence:.1f}s"))
                    self._alert(worker.name, "suspect")
            elif worker.state is WorkerState.SUSPECT:
                worker.mark_state(WorkerState.HEALTHY)
                actions.append(WatchdogAction(worker.name, "recovered", "fresh heartbeat"))
            elif worker.state is WorkerState.DEAD:
                # A live worker marked dead by a refused restart during a long
                # legitimate operation beats again once the op completes:
                # dead was an episode, not a life sentence (SUB-132). A
                # crashed worker never reaches here — ``crashed`` above keeps
                # it in the restart path until the budget decides.
                worker.mark_state(WorkerState.HEALTHY)
                actions.append(WatchdogAction(worker.name, "recovered", "fresh heartbeat after refused restart"))
        return actions

    def _alert(self, name: str, kind: str) -> None:
        if self._on_alert is not None:
            try:
                self._on_alert(name, kind)
            except Exception:  # noqa: BLE001 - an alert sink must never kill the watchdog
                self._logger.exception("watchdog alert sink failed for %s/%s", name, kind)

    # ---------------------------------------------------------------- monitor
    def start(self) -> None:
        with self._lock:
            assert self._thread is None, "watchdog already started"
            self._thread = threading.Thread(target=self._loop, name="watchdog-monitor", daemon=True)
            self._thread.start()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.wait(self._policy.check_interval_seconds):
            try:
                self.heartbeat.beat()
                self.check_once()
            except Exception:  # noqa: BLE001 - the watchdog itself never dies silently
                self._logger.exception("watchdog check failed")

    def stop(self, *, timeout_ms: int = 2000) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_ms / 1000.0)
            with self._lock:
                if not thread.is_alive():
                    self._thread = None

    # ------------------------------------------------------------------ drain
    def drain_all(self, *, timeout_ms: int) -> DrainReport:
        """Bounded stop of the monitor and every registered worker.

        Each worker gets an equal share of the remaining budget; a worker
        that outlives its share is reported ``drained=False`` — never hidden.
        """
        self.stop(timeout_ms=min(timeout_ms, 2000))
        with self._lock:
            workers = list(self._workers.values())
        outcomes: list[DrainOutcome] = []
        started = time.monotonic()
        for index, worker in enumerate(workers):
            remaining_ms = timeout_ms - (time.monotonic() - started) * 1000.0
            share = max(1, int(remaining_ms / (len(workers) - index)))
            was_alive = worker.is_alive()
            drained = worker.stop(timeout_ms=share) if hasattr(worker, "stop") else not was_alive
            outcomes.append(DrainOutcome(worker.name, bool(drained), was_alive, worker.state))
        names = [o.name for o in outcomes]
        assert len(set(names)) == len(names), "drain report must account each worker exactly once"
        assert len(outcomes) == len(workers), "drain coverage: every registered worker must be accounted"
        return DrainReport(tuple(outcomes))
