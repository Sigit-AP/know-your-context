"""Tests for the async lane of ingest/watcher.py — AsyncIngestionWatcher (W13).

Real asyncio: cancellation is proven with task.cancel(), not asserted from
structure. All sleeps are tiny or faked; the watcher logic is deterministic
given the fixture filesystem.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from know_your_context.ingest.watcher import (
    AsyncIngestionWatcher,
    WatcherConfig,
    WatcherRootError,
)
from know_your_context.serve.flow_retry import RetryPolicy


class _FakePipeline:
    """Counts ingest calls; can be scripted to fail or block per path."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.oserror_budget: int = 0
        self.block_gate: threading.Event | None = None
        self.started = threading.Event()
        self._lock = threading.Lock()

    def ingest_path(self, path: Any, *, incremental: bool, recursive: bool, resolve: bool) -> Any:
        with self._lock:
            self.calls.append(str(path))
        self.started.set()
        if self.block_gate is not None:
            self.block_gate.wait(30.0)
        with self._lock:
            if self.oserror_budget > 0:
                self.oserror_budget -= 1
                raise OSError("transient store contention")
        stats = {"proposed": 1, "skipped": 0, "failed": 0, "deleted": 0}
        totals = {"nodes": 1, "edges": 1, "refs_parked": 0}
        return type("Report", (), {"stats": stats, "totals": totals, "resolution": None})()

    def call_count(self) -> int:
        with self._lock:
            return len(self.calls)


def _watcher(
    root: Path,
    pipeline: _FakePipeline,
    *,
    config: WatcherConfig | None = None,
    retry_policy: RetryPolicy | None = None,
    cycle_timeout_ms: int = 30_000,
) -> AsyncIngestionWatcher:
    return AsyncIngestionWatcher(
        root,
        pipeline,
        config=config
        or WatcherConfig(
            interval_seconds=0.05,
            debounce_seconds=0.0,
            quick_quiet_seconds=0.0,
            max_consecutive_failures=3,
        ),
        retry_policy=retry_policy or RetryPolicy(max_attempts=1, jitter_ratio=0.0),
        cycle_timeout_ms=cycle_timeout_ms,
    )


class TestConstruction:
    def test_bad_root_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(WatcherRootError):
            AsyncIngestionWatcher(tmp_path / "missing", _FakePipeline())

    def test_cycle_timeout_validated(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cycle_timeout_ms"):
            _watcher(tmp_path, _FakePipeline(), cycle_timeout_ms=0)

    def test_config_exposed(self, tmp_path: Path) -> None:
        watcher = _watcher(tmp_path, _FakePipeline())
        assert watcher.config.interval_seconds == pytest.approx(0.05)
        assert not watcher.degraded


class TestRunLoop:
    def test_idle_cycles(self, tmp_path: Path) -> None:
        async def main() -> Any:
            watcher = _watcher(tmp_path, _FakePipeline())
            return await watcher.run_async(max_cycles=2)

        report = asyncio.run(main())
        assert report.status == "completed"
        assert len(report.cycles) == 2
        assert all(c.mode == "idle" for c in report.cycles)

    def test_detects_added_file_quick_path(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
        pipeline = _FakePipeline()

        async def main() -> Any:
            return await _watcher(tmp_path, pipeline).run_async(max_cycles=1)

        report = asyncio.run(main())
        assert report.cycles[0].mode == "quick"
        assert report.cycles[0].files_proposed == 1
        assert pipeline.call_count() == 1

    def test_removal_triggers_full_scan(self, tmp_path: Path) -> None:
        pipeline = _FakePipeline()

        async def main() -> Any:
            watcher = _watcher(tmp_path, pipeline)
            await watcher.run_async(max_cycles=1)  # baseline cycle
            target = tmp_path / "gone.py"
            target.write_text("x = 1\n", encoding="utf-8")
            await watcher.run_async(max_cycles=1)  # sees the add
            target.unlink()
            return await watcher.run_async(max_cycles=1)  # sees the removal

        report = asyncio.run(main())
        assert report.cycles[0].mode == "full-scan"
        assert pipeline.calls[-1] == str(tmp_path.resolve())

    def test_request_stop_is_cooperative(self, tmp_path: Path) -> None:
        async def main() -> Any:
            watcher = _watcher(tmp_path, _FakePipeline())
            asyncio.get_running_loop().call_later(0.12, watcher.request_stop)
            return await watcher.run_async()

        report = asyncio.run(asyncio.wait_for(main(), timeout=5.0))
        assert report.status == "cancelled"
        assert 1 <= len(report.cycles) <= 4

    def test_external_cancellation_propagates(self, tmp_path: Path) -> None:
        async def main() -> str:
            watcher = _watcher(tmp_path, _FakePipeline())
            task = asyncio.create_task(watcher.run_async())
            await asyncio.sleep(0.08)  # mid interval sleep
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return "cancelled"

        assert asyncio.run(asyncio.wait_for(main(), timeout=5.0)) == "cancelled"

    def test_cancellation_mid_batch_skips_remaining_files(self, tmp_path: Path) -> None:
        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
        pipeline = _FakePipeline()

        async def main() -> int:
            watcher = _watcher(tmp_path, pipeline)
            task = asyncio.create_task(watcher.run_async(max_cycles=1))
            await asyncio.to_thread(pipeline.started.wait, 5.0)  # first ingest submitted
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return pipeline.call_count()

        count = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        assert count == 1  # the cancel point between files held: b.py/c.py never submitted


class TestFailureModes:
    def test_cycle_timeout_marks_cycle_failed_not_degraded(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        pipeline = _FakePipeline()
        pipeline.block_gate = threading.Event()  # never set until teardown

        async def main() -> Any:
            watcher = _watcher(tmp_path, pipeline, cycle_timeout_ms=80)
            return await watcher.run_async(max_cycles=1)

        try:
            report = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
            assert report.cycles[0].mode == "failed"
            assert "timeout" in (report.cycles[0].error or "").lower()
            assert report.status == "completed"
            assert pipeline.call_count() == 1
        finally:
            pipeline.block_gate.set()

    def test_transient_oserror_retried(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        pipeline = _FakePipeline()
        pipeline.oserror_budget = 2

        async def main() -> Any:
            policy = RetryPolicy(max_attempts=4, base_delay_ms=1, max_delay_ms=5, jitter_ratio=0.0, seed=7)
            return await _watcher(tmp_path, pipeline, retry_policy=policy).run_async(max_cycles=1)

        report = asyncio.run(main())
        assert report.cycles[0].mode == "quick"
        assert pipeline.call_count() == 3  # 2 transient failures + 1 success

    def test_permanent_error_not_retried(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        class _PermanentPipeline(_FakePipeline):
            def ingest_path(self, path: Any, *, incremental: bool, recursive: bool, resolve: bool) -> Any:
                with self._lock:
                    self.calls.append(str(path))
                raise ValueError("permanent contract violation")

        pipeline = _PermanentPipeline()

        async def main() -> Any:
            policy = RetryPolicy(max_attempts=4, base_delay_ms=1, max_delay_ms=5, jitter_ratio=0.0)
            return await _watcher(tmp_path, pipeline, retry_policy=policy).run_async(max_cycles=1)

        report = asyncio.run(main())
        assert report.cycles[0].mode == "failed"
        assert pipeline.call_count() == 1

    def test_consecutive_failures_degrade(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        class _AlwaysFails(_FakePipeline):
            def ingest_path(self, path: Any, *, incremental: bool, recursive: bool, resolve: bool) -> Any:
                raise OSError("store gone")

        async def main() -> Any:
            watcher = _watcher(tmp_path, _AlwaysFails(), retry_policy=RetryPolicy(max_attempts=1))
            return await watcher.run_async()

        report = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        assert report.status == "degraded"
        assert report.consecutive_failures == 3
        assert len(report.cycles) == 3

    def test_degraded_watcher_stays_put(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        class _AlwaysFails(_FakePipeline):
            def ingest_path(self, path: Any, *, incremental: bool, recursive: bool, resolve: bool) -> Any:
                raise OSError("store gone")

        async def main() -> Any:
            watcher = _watcher(tmp_path, _AlwaysFails(), retry_policy=RetryPolicy(max_attempts=1, jitter_ratio=0.0))
            first = await watcher.run_async()
            assert first.status == "degraded"
            second = await watcher.run_async(max_cycles=1)
            assert second.status == "degraded"
            assert len(second.cycles) == 0
            return watcher

        watcher = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        assert watcher.degraded


class TestDeterminism:
    def test_same_fixture_same_report_modes(self, tmp_path: Path) -> None:
        async def run_once() -> list[tuple[str, int]]:
            root = tmp_path / "case"
            root.mkdir(exist_ok=True)
            for child in root.glob("*"):
                child.unlink()
            (root / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
            watcher = _watcher(root, _FakePipeline())
            report = await watcher.run_async(max_cycles=1)
            return [(c.mode, c.files_proposed) for c in report.cycles]

        first = asyncio.run(run_once())
        second = asyncio.run(run_once())
        assert first == second == [("quick", 1)]
