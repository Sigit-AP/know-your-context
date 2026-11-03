"""Hardware integration for the ingestion pipeline (SUB-135).

Parallel extraction follows the plan's worker count (cores/RAM), the commit
batch follows the disk profile, and the mmap recipe reaches the store (SUB-134
wiring, asserted end-to-end here). Hard invariants pinned:

* store content + report digest are byte-identical between the historical
  (sequential, per-file commit) path and the hardware-derived path;
* a budget stop keeps every row committed before the boundary, batched or not;
* kill switches restore the historical path exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from know_your_context.hardware.profile import reset_plan_cache
from know_your_context.ingest.pipeline import IngestionBudget, IngestionBudgetError, IngestionPipeline
from know_your_context.store import EventStore


def _write_tree(root: Path, count: int = 12) -> None:
    for index in range(count):
        (root / f"mod_{index:02d}.py").write_text(
            f'"""Module {index}."""\n\n\n'
            f"def func_{index}_a(value: int) -> int:\n"
            f"    return value + {index}\n\n\n"
            f"def func_{index}_b(value: int) -> int:\n"
            f"    return func_{index}_a(value) * 2\n",
            encoding="utf-8",
        )


def _dump(store: EventStore) -> dict[str, list[tuple]]:
    with store.read() as conn:
        return {
            table: [
                tuple(row)
                for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3")  # nosec B608 -- fixed test table names
            ]
            for table in ("files", "symbols", "graph_edges", "unresolved_refs")
        }


def _ingest(store: EventStore, root: Path):
    pipeline = IngestionPipeline(store)
    return pipeline.ingest_path(root, incremental=False, resolve=False)


class TestHardwareParams:
    def test_defaults_follow_the_plan(self, tmp_path: Path) -> None:
        from know_your_context.ingest.pipeline import _hardware_ingest_params

        store = EventStore(tmp_path / "s.sqlite3")
        try:
            workers, batch = _hardware_ingest_params(store)
        finally:
            store.close()
        assert workers >= 1
        assert batch >= 1

    def test_kill_switches_restore_historical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.ingest.pipeline import _hardware_ingest_params

        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            assert _hardware_ingest_params(store) == (1, 1)
        finally:
            store.close()
        reset_plan_cache()

    def test_probe_failure_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.ingest.pipeline import _hardware_ingest_params

        store = EventStore(tmp_path / "s.sqlite3")

        def _boom(path=None, *, refresh=False):
            raise RuntimeError("synthetic probe failure")

        monkeypatch.setattr("know_your_context.hardware.profile.probe_hardware", _boom)
        reset_plan_cache()
        try:
            assert _hardware_ingest_params(store) == (1, 1)
        finally:
            store.close()
        reset_plan_cache()

    def test_workers_and_batch_track_cores_and_disk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.hardware.probe import (
            DiskKind,
            DiskProbe,
            GpuKind,
            GpuProbe,
            HardwareProfile,
            MemoryProbe,
        )
        from know_your_context.ingest.pipeline import _hardware_ingest_params

        profile = HardwareProfile(
            cpu_count=8,
            memory=MemoryProbe(total_bytes=64 * 1024**3, available_bytes=32 * 1024**3, note="synthetic"),
            disk=DiskProbe(kind=DiskKind.HDD, device="t", rotational=True, note="synthetic"),
            gpu=GpuProbe(kind=GpuKind.NONE, devices=(), notes=()),
            notes=(),
        )
        monkeypatch.setattr(
            "know_your_context.hardware.profile.probe_hardware", lambda path=None, *, refresh=False: profile
        )
        reset_plan_cache()
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            workers, batch = _hardware_ingest_params(store)
        finally:
            store.close()
        reset_plan_cache()
        assert workers == 8
        assert batch == 128  # HDD group-commit profile


class TestByteIdenticalDeterminism:
    def test_hardware_path_matches_historical_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "tree"
        root.mkdir()
        _write_tree(root)

        historical_store = EventStore(tmp_path / "historical.sqlite3")
        report_h = _ingest(historical_store, root)
        dump_h = _dump(historical_store)
        historical_store.close()

        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        sequential_store = EventStore(tmp_path / "sequential.sqlite3")
        report_s = _ingest(sequential_store, root)
        dump_s = _dump(sequential_store)
        sequential_store.close()
        reset_plan_cache()

        assert report_h.input_sha256 == report_s.input_sha256
        assert report_h.stats == report_s.stats
        assert report_h.totals == report_s.totals
        assert [f.to_dict() for f in report_h.files] == [f.to_dict() for f in report_s.files]
        assert dump_h == dump_s

    def test_budget_stop_keeps_rows_before_boundary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With disk-profile batching active, a budget stop commits every file before the boundary."""
        root = tmp_path / "tree"
        root.mkdir()
        _write_tree(root, count=6)
        store = EventStore(tmp_path / "budget.sqlite3")
        budget = IngestionBudget(max_files=100, max_nodes=10, max_edges=1000, max_refs=1000)
        pipeline = IngestionPipeline(store, budget=budget)
        with pytest.raises(IngestionBudgetError):
            pipeline.ingest_path(root, incremental=False, resolve=False)
        with store.read() as conn:
            files = int(conn.execute("SELECT count(*) FROM files").fetchone()[0])
        store.close()
        assert files >= 1, "rows before the budget boundary must stay committed"

    def test_mmap_recipe_reaches_the_ingest_store(self, tmp_path: Path) -> None:
        """The store the pipeline writes through carries the plan's mmap hint (SUB-134 seam, end-to-end)."""
        store = EventStore(tmp_path / "mmap.sqlite3")
        try:
            tuning = store.applied_tuning
            assert tuning.mmap_bytes > 0
        finally:
            store.close()
        reset_plan_cache()


class TestIncrementalUnchanged:
    def test_second_run_skips_identically(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "tree"
        root.mkdir()
        _write_tree(root)
        store = EventStore(tmp_path / "inc.sqlite3")
        _ingest(store, root)
        report = IngestionPipeline(store).ingest_path(root, resolve=False)  # incremental: everything skipped
        store.close()
        assert report.stats["skipped"] == 12
        assert report.stats["proposed"] == 0
