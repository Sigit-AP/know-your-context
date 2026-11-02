"""W13 flow integration: SupervisedThread drills, daemon watchdog wiring,
proxy backoff determinism, staged-pipeline determinism (SUB-124).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from _ingest_fakes import ToyExtractor, ToyResolver, write_fixture
from _serve_helpers import make_config
from know_your_context.ingest.pipeline import IngestionPipeline
from know_your_context.serve.daemon_config import ConfigError
from know_your_context.serve.flow_retry import RetryPolicy
from know_your_context.serve.pipeline_stages import (
    CheckpointStore,
    StagedPipelineRunner,
    build_ingestion_stages,
)
from know_your_context.serve.superpower import SuperpowerDaemon
from know_your_context.serve.watchdog import (
    Heartbeat,
    SupervisedThread,
    Watchdog,
    WatchdogPolicy,
    WorkerState,
)
from know_your_context.store import EventStore


def _looping_thread(stop: threading.Event, *, beats: Heartbeat | None = None) -> threading.Thread:
    def loop() -> None:
        while not stop.wait(0.01):
            if beats is not None:
                beats.beat()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


class TestSupervisedThread:
    def test_restart_dead_thread_via_factory(self) -> None:
        stop_old = threading.Event()
        beats = Heartbeat()
        thread = _looping_thread(stop_old, beats=beats)
        stop_old.set()
        thread.join(timeout=2.0)
        stop_new = threading.Event()
        replacement = _looping_thread(stop_new, beats=beats)
        adapter = SupervisedThread(
            "t",
            thread,
            heartbeat_getter=lambda: beats,
            restart_factory=lambda: replacement,
            policy=WatchdogPolicy(max_restarts=2),
        )
        assert adapter.restart() is True
        assert adapter.thread is replacement
        assert adapter.state is WorkerState.HEALTHY
        assert adapter.restart_count == 1
        stop_new.set()
        assert adapter.stop(timeout_ms=2000) is True
        assert adapter.state is WorkerState.DRAINED

    def test_restart_live_thread_refused(self) -> None:
        stop = threading.Event()
        thread = _looping_thread(stop)
        adapter = SupervisedThread(
            "t",
            thread,
            heartbeat_getter=Heartbeat,
            restart_factory=lambda: thread,
            policy=WatchdogPolicy(),
        )
        assert adapter.restart() is False  # never duplicate a live worker
        assert adapter.restart_count == 0
        assert adapter.state is WorkerState.HEALTHY
        stop.set()
        thread.join(timeout=2.0)

    def test_restart_budget_exhaustion_terminal(self) -> None:
        clock = lambda: 1000.0  # noqa: E731 - fixed scripted clock: window never slides
        policy = WatchdogPolicy(max_restarts=1, restart_window_seconds=60.0)
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join(timeout=2.0)
        stop_new = threading.Event()
        adapter = SupervisedThread(
            "t",
            dead,
            heartbeat_getter=Heartbeat,
            restart_factory=lambda: _looping_thread(stop_new),
            policy=policy,
            now=clock,
        )
        assert adapter.restart() is True
        # Second restart is within the window and over budget → terminal.
        assert adapter.restart() is False
        assert adapter.terminal
        assert adapter.state is WorkerState.DEAD
        stop_new.set()
        adapter.thread.join(timeout=2.0)

    def test_restart_factory_must_return_fresh_started_thread(self) -> None:
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join(timeout=2.0)
        adapter = SupervisedThread(
            "t", dead, heartbeat_getter=Heartbeat, restart_factory=lambda: dead, policy=WatchdogPolicy()
        )
        with pytest.raises(AssertionError):
            adapter.restart()

    def test_stop_drains_and_times_out(self) -> None:
        quick = threading.Thread(target=lambda: None)
        quick.start()
        quick.join(timeout=2.0)
        adapter = SupervisedThread(
            "quick", quick, heartbeat_getter=Heartbeat, restart_factory=lambda: quick, policy=WatchdogPolicy()
        )
        assert adapter.stop(timeout_ms=500) is True
        assert adapter.state is WorkerState.DRAINED

        stop = threading.Event()  # never set
        stubborn = _looping_thread(stop)
        adapter2 = SupervisedThread(
            "stubborn", stubborn, heartbeat_getter=Heartbeat, restart_factory=lambda: stubborn, policy=WatchdogPolicy()
        )
        assert adapter2.stop(timeout_ms=30) is False
        assert adapter2.state is WorkerState.STOP_TIMED_OUT
        stop.set()
        stubborn.join(timeout=2.0)

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="name"):
            SupervisedThread(
                "",
                threading.Thread(target=lambda: None),
                heartbeat_getter=Heartbeat,
                restart_factory=lambda: threading.Thread(target=lambda: None),
            )

    def test_watchdog_drives_thread_restart(self) -> None:
        beats = Heartbeat()
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join(timeout=2.0)
        stop_new = threading.Event()
        alive = _looping_thread(stop_new)
        dog = Watchdog(policy=WatchdogPolicy(suspect_seconds=0.1, stall_seconds=0.2, check_interval_seconds=0.05))
        adapter = SupervisedThread(
            "t", dead, heartbeat_getter=lambda: beats, restart_factory=lambda: alive, policy=WatchdogPolicy()
        )
        dog.register(adapter)
        actions = dog.check_once()
        assert [a.kind for a in actions] == ["restart"]
        assert adapter.thread is alive
        stop_new.set()
        alive.join(timeout=2.0)


# ------------------------------------------------------- daemon wiring (real)


@pytest.fixture()
def daemon(tmp_path: Path) -> Any:
    config = make_config(tmp_path, idle_timeout_seconds=120.0)
    instance = SuperpowerDaemon(config)
    instance.start()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, config
    instance.shutdown("test-teardown")
    thread.join(timeout=15)


class TestDaemonWorkerWatchdog:
    def test_stats_section_covers_every_worker(self, daemon: Any) -> None:
        instance, config = daemon
        stats = instance.stats()
        section = stats["worker_watchdog"]
        assert section["enabled"] is True
        names = set(section["workers"])
        expected = {f"read-{i}" for i in range(config.read_pool_size)} | {"heavy", "acceptor", "reaper"}
        assert names == expected
        assert all(w["alive"] for w in section["workers"].values())

    def test_heartbeats_beat_during_idle(self, daemon: Any) -> None:
        instance, _ = daemon
        first = {n: w["beats"] for n, w in instance.stats()["worker_watchdog"]["workers"].items()}
        time.sleep(1.6)  # the reaper's natural cadence is min(1.0, idle/2)
        second = {n: w["beats"] for n, w in instance.stats()["worker_watchdog"]["workers"].items()}
        for name in first:
            assert second[name] > first[name], f"{name} did not beat while idle"

    def test_no_spurious_restarts_when_healthy(self, daemon: Any) -> None:
        instance, _ = daemon
        time.sleep(0.4)
        workers = instance.stats()["worker_watchdog"]["workers"]
        assert all(w["restarts"] == 0 for w in workers.values())
        assert instance.stats()["worker_watchdog"]["alerts"] == []

    def test_shutdown_drains_all_workers(self, daemon: Any) -> None:
        instance, _ = daemon
        instance.shutdown("drain-test")
        deadline = time.monotonic() + 20.0
        while instance._worker_drain_report is None and time.monotonic() < deadline:
            time.sleep(0.05)
        report = instance._worker_drain_report
        assert report is not None
        assert report.all_drained
        assert len(report.outcomes) >= 4  # read lanes + heavy + acceptor + reaper

    def test_kill_switch_disables(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, idle_timeout_seconds=120.0, worker_watchdog=False)
        instance = SuperpowerDaemon(config)
        instance.start()
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            assert instance._worker_watchdog is None
            assert instance.stats()["worker_watchdog"]["enabled"] is False
        finally:
            instance.shutdown("test-teardown")
            thread.join(timeout=15)

    def test_respawn_guards_refuse_live_workers(self, daemon: Any) -> None:
        instance, _ = daemon
        with pytest.raises(AssertionError):
            instance._read_pool._respawn_worker(0)  # noqa: SLF001 - drill targets the guard
        with pytest.raises(AssertionError):
            instance._heavy._respawn()  # noqa: SLF001
        with pytest.raises(AssertionError):
            instance._respawn_acceptor()
        with pytest.raises(AssertionError):
            instance._respawn_reaper()


class TestDaemonConfigFlowFields:
    def test_defaults_present(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        assert config.worker_watchdog is True
        assert config.watchdog_stall_seconds >= config.watchdog_suspect_seconds
        assert config.proxy_io_timeout_seconds >= config.proxy_connect_timeout_seconds
        assert config.proxy_retry_seed is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("watchdog_suspect_seconds", 0.0),
            ("watchdog_stall_seconds", 5.0),  # below default suspect (15)
            ("watchdog_stall_seconds", 20.0),  # below default read_publish_wait (30): stall inside a bounded read wait
            ("watchdog_max_restarts", 0),
            ("watchdog_restart_window_seconds", 0.0),
            ("watchdog_check_interval_seconds", 0.0),
            ("proxy_connect_timeout_seconds", 0.0),
            ("proxy_io_timeout_seconds", 5.0),  # below default connect (10)
        ],
    )
    def test_invalid_values_rejected(self, tmp_path: Path, field: str, value: Any) -> None:
        with pytest.raises(ConfigError):
            make_config(tmp_path, **{field: value})

    def test_retry_seed_bool_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            make_config(tmp_path, proxy_retry_seed=True)

    def test_watchdog_flag_bool_only(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            make_config(tmp_path, worker_watchdog="yes")


# ---------------------------------------------------- proxy backoff (unit)


class TestProxyBackoffDeterminism:
    def test_seeded_backoff_stream_reproducible(self, tmp_path: Path) -> None:
        from know_your_context.serve.flow_retry import compute_delay_ms

        config = make_config(tmp_path, proxy_retry_seed=42)
        from know_your_context.serve.flow_retry import RetryPolicy

        def stream() -> list[int]:
            policy = RetryPolicy(
                max_attempts=1,
                base_delay_ms=100,
                max_delay_ms=1_000,
                backoff_multiplier=2.0,
                jitter_ratio=0.2,
                seed=config.proxy_retry_seed,
            )
            rng = policy.rng()
            return [compute_delay_ms(a, policy, rng=rng) for a in range(1, 8)]

        assert stream() == stream()
        assert len(set(stream())) > 1  # jittered, not constant


# ------------------------------------------- staged pipeline determinism


class TestStagedPipelineDeterminism:
    def _run_once(self, base: Path) -> dict[str, str]:
        root = base / "repo"
        write_fixture(root, {"a.py": "def alpha():\n    pass\n", "b.py": "def beta():\n    -> alpha\n"})
        store = EventStore(base / "store.sqlite3")
        pipeline = IngestionPipeline(store, extractor=ToyExtractor(), resolver=ToyResolver())
        checkpoints = CheckpointStore(base / "flow")
        stages = build_ingestion_stages(
            pipeline, store, root, checkpoints=checkpoints, seal_path=base / "flow" / "seal.jsonl"
        )
        report = StagedPipelineRunner(
            stages,
            checkpoints=checkpoints,
            input_payload={"root": str(root), "mode": "full"},
            retry_policy=RetryPolicy(max_attempts=1, jitter_ratio=0.0),
            sleep=lambda _: None,
        ).run()
        store.close()
        return {r.stage: (r.output_digest or "") for r in report.stages}

    def test_identical_inputs_identical_digests(self, tmp_path: Path) -> None:
        first = self._run_once(tmp_path / "a")
        second = self._run_once(tmp_path / "b")
        # resolve/synthesize/index/seal are pure over the extracted graph;
        # extract embeds the absolute input path digest, so compare the rest.
        assert first["resolve"] == second["resolve"]
        assert first["synthesize"] == second["synthesize"]
        assert first["index"] == second["index"]
