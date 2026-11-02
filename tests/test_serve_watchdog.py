"""Tests for serve/watchdog.py — heartbeat + tiered worker supervision (W13).

Scripted clocks everywhere; the few real-thread integration tests use short
but load-tolerant intervals.
"""

from __future__ import annotations

import threading
import time

import pytest

from know_your_context.serve.watchdog import (
    DrainReport,
    Heartbeat,
    SupervisedWorker,
    Watchdog,
    WatchdogPolicy,
    WorkerState,
)


class _ScriptedClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _noop_target(heartbeat: Heartbeat, stop: threading.Event) -> None:
    while not stop.wait(0.01):
        heartbeat.beat()


# ------------------------------------------------------------------ policy


class TestWatchdogPolicy:
    def test_defaults(self) -> None:
        policy = WatchdogPolicy()
        assert policy.suspect_seconds > 0
        assert policy.stall_seconds >= policy.suspect_seconds
        assert policy.max_restarts >= 1
        assert policy.restart_window_seconds > 0
        assert policy.check_interval_seconds > 0

    def test_frozen(self) -> None:
        with pytest.raises(AttributeError):
            WatchdogPolicy().max_restarts = 9  # type: ignore[misc]

    @pytest.mark.parametrize("value", [0, -1, "x", True, None])
    def test_suspect_seconds_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="suspect_seconds"):
            WatchdogPolicy(suspect_seconds=value)  # type: ignore[arg-type]

    def test_stall_below_suspect_rejected(self) -> None:
        with pytest.raises(ValueError, match="stall_seconds"):
            WatchdogPolicy(suspect_seconds=10.0, stall_seconds=5.0)

    @pytest.mark.parametrize("value", [0, -2, 1.5, True])
    def test_max_restarts_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="max_restarts"):
            WatchdogPolicy(max_restarts=value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [0, -1, "x"])
    def test_restart_window_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="restart_window_seconds"):
            WatchdogPolicy(restart_window_seconds=value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [0, -0.5, "x", True])
    def test_check_interval_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="check_interval_seconds"):
            WatchdogPolicy(check_interval_seconds=value)  # type: ignore[arg-type]


# ---------------------------------------------------------------- heartbeat


class TestHeartbeat:
    def test_beat_advances_last_beat(self) -> None:
        clock = _ScriptedClock()
        hb = Heartbeat(now=clock)
        assert hb.last_beat == 1000.0
        clock.advance(5.0)
        hb.beat()
        assert hb.last_beat == 1005.0
        assert hb.beat_count == 1

    def test_beat_never_goes_backwards(self) -> None:
        clock = _ScriptedClock()
        hb = Heartbeat(now=clock)
        clock.advance(10.0)
        hb.beat()
        clock.now = 999.0  # clock skew backwards
        hb.beat()
        assert hb.last_beat == 1010.0  # monotone clamp

    def test_concurrent_beats_all_counted(self) -> None:
        hb = Heartbeat()
        threads = [threading.Thread(target=lambda: [hb.beat() for _ in range(50)]) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert hb.beat_count == 200

    def test_age_seconds(self) -> None:
        clock = _ScriptedClock()
        hb = Heartbeat(now=clock)
        clock.advance(3.5)
        assert hb.age_seconds() == pytest.approx(3.5)


# ---------------------------------------------------------- supervised worker


class TestSupervisedWorker:
    def test_run_to_drain(self) -> None:
        worker = SupervisedWorker("w1", _noop_target, policy=WatchdogPolicy())
        worker.start()
        assert worker.is_alive()
        assert worker.state is WorkerState.HEALTHY
        deadline = time.monotonic() + 2.0
        while worker.heartbeat.beat_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.heartbeat.beat_count > 0
        assert worker.stop(timeout_ms=2000) is True
        assert worker.state is WorkerState.DRAINED
        assert not worker.is_alive()

    def test_double_start_rejected(self) -> None:
        worker = SupervisedWorker("w1", _noop_target, policy=WatchdogPolicy())
        worker.start()
        try:
            with pytest.raises(AssertionError):
                worker.start()
        finally:
            worker.stop(timeout_ms=2000)

    def test_crash_records_typed_state(self) -> None:
        def boom(heartbeat: Heartbeat, stop: threading.Event) -> None:
            raise RuntimeError("worker exploded")

        worker = SupervisedWorker("w1", boom, policy=WatchdogPolicy())
        worker.start()
        deadline = time.monotonic() + 2.0
        while worker.state is not WorkerState.DEAD and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.state is WorkerState.DEAD
        assert isinstance(worker.last_error, RuntimeError)
        assert not worker.is_alive()

    def test_stubborn_worker_stop_times_out(self) -> None:
        def stubborn(heartbeat: Heartbeat, stop: threading.Event) -> None:
            while True:  # ignores the stop event entirely
                heartbeat.beat()
                time.sleep(0.01)

        worker = SupervisedWorker("w1", stubborn, policy=WatchdogPolicy())
        worker.start()
        assert worker.stop(timeout_ms=50) is False
        assert worker.state is WorkerState.STOP_TIMED_OUT
        assert worker.is_alive()  # documented: still running, caller decides

    def test_restart_respawns_with_fresh_stop_event(self) -> None:
        runs: list[int] = []

        def target(heartbeat: Heartbeat, stop: threading.Event) -> None:
            runs.append(1)
            while not stop.wait(0.01):
                heartbeat.beat()

        policy = WatchdogPolicy(max_restarts=3, restart_window_seconds=60.0)
        worker = SupervisedWorker("w1", target, policy=policy)
        worker.start()
        assert worker.restart_count == 0
        assert worker.restart() is True
        assert worker.restart_count == 1
        deadline = time.monotonic() + 2.0
        while len(runs) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(runs) == 2  # the new incarnation really ran
        assert worker.stop(timeout_ms=2000) is True

    def test_restart_budget_enforced(self) -> None:
        policy = WatchdogPolicy(max_restarts=2, restart_window_seconds=60.0)
        worker = SupervisedWorker("w1", _noop_target, policy=policy)
        worker.start()
        assert worker.restart() is True
        assert worker.restart() is True
        assert worker.restart() is False  # budget exhausted inside the window
        assert worker.restart_count == 2
        assert worker.state is WorkerState.DEAD
        worker.stop(timeout_ms=2000)

    def test_restart_window_slides(self) -> None:
        clock = _ScriptedClock()
        policy = WatchdogPolicy(max_restarts=1, restart_window_seconds=30.0)
        worker = SupervisedWorker("w1", _noop_target, policy=policy, now=clock)
        worker.start()
        assert worker.restart() is True
        assert worker.restart() is False  # still inside the window
        clock.advance(31.0)
        # Outside the window the budget refills — but the worker went DEAD on
        # exhaustion, which is terminal by policy.
        assert worker.state is WorkerState.DEAD
        worker.stop(timeout_ms=2000)

    def test_restart_of_dead_from_crash(self) -> None:
        attempts = 0

        def flaky(heartbeat: Heartbeat, stop: threading.Event) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("first incarnation dies")
            while not stop.wait(0.01):
                heartbeat.beat()

        worker = SupervisedWorker("w1", flaky, policy=WatchdogPolicy(max_restarts=2))
        worker.start()
        deadline = time.monotonic() + 2.0
        while worker.state is not WorkerState.DEAD and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.restart() is True
        deadline = time.monotonic() + 2.0
        while attempts < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert attempts == 2
        assert worker.state is WorkerState.HEALTHY
        worker.stop(timeout_ms=2000)


# ----------------------------------------------------------------- watchdog


class _FakeWorker:
    """Scripted stand-in for check_once unit tests (no real threads).

    Enforces the same restart-budget semantics as SupervisedWorker: the
    watchdog must observe exhaustion through ``restart() -> False``.
    """

    def __init__(
        self, name: str, clock: _ScriptedClock, *, alive: bool = True, policy: WatchdogPolicy | None = None
    ) -> None:
        self.name = name
        self.heartbeat = Heartbeat(now=clock)
        self.state = WorkerState.HEALTHY
        self._alive = alive
        self._clock = clock
        self._policy = policy if policy is not None else WatchdogPolicy()
        self._restart_times: list[float] = []
        self.terminal = False
        self.restart_calls = 0
        self.restart_result = True

    def is_alive(self) -> bool:
        return self._alive

    def mark_state(self, state: WorkerState) -> None:
        self.state = state

    def restart(self) -> bool:
        self.restart_calls += 1
        if self.terminal:
            return False
        now = self._clock()
        self._restart_times = [t for t in self._restart_times if now - t <= self._policy.restart_window_seconds]
        if len(self._restart_times) >= self._policy.max_restarts:
            self.terminal = True
            self.state = WorkerState.DEAD
            return False
        self._restart_times.append(now)
        if self.restart_result:
            self.heartbeat.beat()
            self.state = WorkerState.HEALTHY
            self._alive = True
        else:
            self.state = WorkerState.DEAD
        return self.restart_result


class TestWatchdogCheckOnce:
    def test_healthy_worker_no_action(self) -> None:
        clock = _ScriptedClock()
        dog = Watchdog(policy=WatchdogPolicy(), now=clock)
        worker = _FakeWorker("w", clock)
        worker.heartbeat.beat()
        dog.register(worker)
        clock.advance(1.0)
        assert dog.check_once() == []
        assert worker.state is WorkerState.HEALTHY

    def test_silence_escalates_suspect_then_restart(self) -> None:
        clock = _ScriptedClock()
        policy = WatchdogPolicy(suspect_seconds=5.0, stall_seconds=10.0, max_restarts=2)
        alerts: list[tuple[str, str]] = []
        dog = Watchdog(policy=policy, now=clock, on_alert=lambda name, kind: alerts.append((name, kind)))
        worker = _FakeWorker("w", clock)
        worker.heartbeat.beat()
        dog.register(worker)
        clock.advance(6.0)
        actions = dog.check_once()
        assert [a.kind for a in actions] == ["suspect"]
        assert worker.state is WorkerState.SUSPECT
        assert alerts == [("w", "suspect")]
        clock.advance(5.0)  # 11s silent ≥ stall
        actions = dog.check_once()
        assert [a.kind for a in actions] == ["restart"]
        assert worker.restart_calls == 1
        assert worker.state is WorkerState.HEALTHY  # restart succeeded

    def test_restart_budget_exhaustion_marks_dead(self) -> None:
        clock = _ScriptedClock()
        policy = WatchdogPolicy(suspect_seconds=5.0, stall_seconds=10.0, max_restarts=1, restart_window_seconds=60.0)
        alerts: list[tuple[str, str]] = []
        dog = Watchdog(policy=policy, now=clock, on_alert=lambda name, kind: alerts.append((name, kind)))
        worker = _FakeWorker("w", clock, policy=policy)
        worker.heartbeat.beat()
        dog.register(worker)
        clock.advance(11.0)
        dog.check_once()  # restart #1
        clock.advance(11.0)
        actions = dog.check_once()  # budget exhausted → dead
        assert [a.kind for a in actions] == ["dead"]
        assert worker.state is WorkerState.DEAD
        assert worker.terminal
        assert ("w", "dead") in alerts
        clock.advance(30.0)
        assert dog.check_once() == []  # terminal DEAD is never re-acted

    def test_crashed_thread_detected_without_silence(self) -> None:
        clock = _ScriptedClock()
        dog = Watchdog(policy=WatchdogPolicy(suspect_seconds=50.0, stall_seconds=90.0), now=clock)
        worker = _FakeWorker("w", clock, alive=False)
        worker.heartbeat.beat()
        dog.register(worker)
        clock.advance(1.0)  # fresh beat but the thread is gone
        actions = dog.check_once()
        assert [a.kind for a in actions] == ["restart"]
        assert worker.restart_calls == 1

    def test_failed_restart_marks_dead(self) -> None:
        clock = _ScriptedClock()
        policy = WatchdogPolicy(suspect_seconds=5.0, stall_seconds=10.0)
        dog = Watchdog(policy=policy, now=clock)
        worker = _FakeWorker("w", clock, alive=False)
        worker.restart_result = False
        worker.heartbeat.beat()
        dog.register(worker)
        clock.advance(1.0)
        actions = dog.check_once()
        assert [a.kind for a in actions] == ["dead"]

    def test_drained_and_dead_workers_skipped(self) -> None:
        clock = _ScriptedClock()
        dog = Watchdog(policy=WatchdogPolicy(), now=clock)
        drained = _FakeWorker("d", clock)
        drained.state = WorkerState.DRAINED
        dead = _FakeWorker("x", clock)
        dead.state = WorkerState.DEAD
        dead.terminal = True  # budget-exhausted: out of rotation for good
        dog.register(drained)
        dog.register(dead)
        clock.advance(9999.0)
        assert dog.check_once() == []

    def test_suspect_recovers_on_fresh_beat(self) -> None:
        clock = _ScriptedClock()
        policy = WatchdogPolicy(suspect_seconds=5.0, stall_seconds=10.0)
        dog = Watchdog(policy=policy, now=clock)
        worker = _FakeWorker("w", clock)
        worker.heartbeat.beat()
        dog.register(worker)
        clock.advance(6.0)
        dog.check_once()
        assert worker.state is WorkerState.SUSPECT
        worker.heartbeat.beat()
        clock.advance(0.5)
        actions = dog.check_once()
        assert [a.kind for a in actions] == ["recovered"]
        assert worker.state is WorkerState.HEALTHY

    def test_duplicate_registration_rejected(self) -> None:
        clock = _ScriptedClock()
        dog = Watchdog(policy=WatchdogPolicy(), now=clock)
        dog.register(_FakeWorker("w", clock))
        with pytest.raises(ValueError, match="w"):
            dog.register(_FakeWorker("w", clock))

    def test_unregister(self) -> None:
        clock = _ScriptedClock()
        dog = Watchdog(policy=WatchdogPolicy(), now=clock)
        dog.register(_FakeWorker("w", clock))
        assert dog.unregister("w") is True
        assert dog.unregister("w") is False
        clock.advance(9999.0)
        assert dog.check_once() == []

    def test_registry_snapshot(self) -> None:
        clock = _ScriptedClock()
        dog = Watchdog(policy=WatchdogPolicy(), now=clock)
        dog.register(_FakeWorker("a", clock))
        dog.register(_FakeWorker("b", clock))
        assert dog.worker_names() == ("a", "b")


# ------------------------------------------------------------- drain coverage


class TestDrainAll:
    def test_drains_every_registered_worker(self) -> None:
        dog = Watchdog(policy=WatchdogPolicy())
        workers = [SupervisedWorker(f"w{i}", _noop_target, policy=WatchdogPolicy()) for i in range(3)]
        for worker in workers:
            worker.start()
            dog.register(worker)
        report = dog.drain_all(timeout_ms=5000)
        assert isinstance(report, DrainReport)
        assert report.all_drained
        assert len(report.outcomes) == 3
        assert all(o.drained for o in report.outcomes)
        assert [o.name for o in report.outcomes] == ["w0", "w1", "w2"]
        assert all(not w.is_alive() for w in workers)

    def test_stubborn_worker_reported_not_hidden(self) -> None:
        def stubborn(heartbeat: Heartbeat, stop: threading.Event) -> None:
            while True:
                heartbeat.beat()
                time.sleep(0.01)

        dog = Watchdog(policy=WatchdogPolicy())
        good = SupervisedWorker("good", _noop_target, policy=WatchdogPolicy())
        bad = SupervisedWorker("bad", stubborn, policy=WatchdogPolicy())
        good.start()
        bad.start()
        dog.register(good)
        dog.register(bad)
        report = dog.drain_all(timeout_ms=200)
        assert not report.all_drained
        by_name = {o.name: o for o in report.outcomes}
        assert by_name["good"].drained is True
        assert by_name["bad"].drained is False
        assert by_name["bad"].state is WorkerState.STOP_TIMED_OUT

    def test_drain_all_on_empty_registry(self) -> None:
        report = Watchdog(policy=WatchdogPolicy()).drain_all(timeout_ms=100)
        assert report.all_drained
        assert report.outcomes == ()

    def test_drain_all_stops_monitor_thread(self) -> None:
        dog = Watchdog(policy=WatchdogPolicy(check_interval_seconds=0.05))
        worker = SupervisedWorker("w", _noop_target, policy=WatchdogPolicy())
        worker.start()
        dog.register(worker)
        dog.start()
        assert dog.is_running
        dog.drain_all(timeout_ms=2000)
        assert not dog.is_running


# ------------------------------------------------- real-thread integration


class TestWatchdogIntegration:
    def test_monitor_restarts_silent_worker(self) -> None:
        gate = threading.Event()

        def target(heartbeat: Heartbeat, stop: threading.Event) -> None:
            while not stop.wait(0.01):
                if not gate.is_set():
                    heartbeat.beat()

        policy = WatchdogPolicy(
            suspect_seconds=0.15,
            stall_seconds=0.4,
            max_restarts=2,
            restart_window_seconds=30.0,
            check_interval_seconds=0.05,
        )
        alerts: list[tuple[str, str]] = []
        dog = Watchdog(policy=policy, on_alert=lambda n, k: alerts.append((n, k)))
        worker = SupervisedWorker("silent", target, policy=policy)
        worker.start()
        dog.register(worker)
        dog.start()
        try:
            gate.set()  # worker keeps looping but goes silent
            deadline = time.monotonic() + 5.0
            while worker.restart_count == 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert worker.restart_count >= 1
            kinds = [k for _, k in alerts]
            assert "suspect" in kinds  # escalation passed through the warn tier
        finally:
            gate.clear()
            dog.drain_all(timeout_ms=3000)

    def test_monitor_detects_crash_and_restarts(self) -> None:
        incarnations = 0
        gate = threading.Event()

        def target(heartbeat: Heartbeat, stop: threading.Event) -> None:
            nonlocal incarnations
            incarnations += 1
            if incarnations == 1:
                gate.wait(2.0)  # let the monitor observe the crash
                raise RuntimeError("boom")
            while not stop.wait(0.01):
                heartbeat.beat()

        policy = WatchdogPolicy(
            suspect_seconds=5.0,
            stall_seconds=30.0,
            max_restarts=2,
            restart_window_seconds=30.0,
            check_interval_seconds=0.05,
        )
        dog = Watchdog(policy=policy)
        worker = SupervisedWorker("crasher", target, policy=policy)
        worker.start()
        dog.register(worker)
        dog.start()
        try:
            deadline = time.monotonic() + 6.0
            while incarnations < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert incarnations >= 2  # crash → restart, no heartbeat-silence needed
        finally:
            dog.drain_all(timeout_ms=3000)
