"""Hardware integration for resolve/synthesis (SUB-135).

The pass insert flush follows the disk profile's group-commit batch
(executemany chunks inside the SAME transaction — rowids and counts
unchanged), and the per-candidate ``_edge_exists`` N+1 becomes one
prefetched key set paginated by the same batch. Kill switch
KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING restores the historical per-candidate
path; a failed probe falls back to it. Results are byte-identical either
way (pinned against the kill-switched run).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from know_your_context.hardware.profile import reset_plan_cache
from know_your_context.ingest.pipeline import IngestionPipeline
from know_your_context.resolve.synthesis import _synthesis_hardware, run_synthesis
from know_your_context.store import EventStore


def _seed(root: Path) -> None:
    root.mkdir(exist_ok=True)
    (root / "a.py").write_text(
        "class Base:\n    pass\n\n\nclass Child(Base):\n    def run(self):\n        return 1\n",
        encoding="utf-8",
    )
    (root / "b.py").write_text(
        "from a import Base\n\n\ndef make() -> Base:\n    return Base()\n",
        encoding="utf-8",
    )


def _edge_dump(store: EventStore) -> list[tuple]:
    with store.read() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT source, target, kind, line, col, metadata, provenance FROM graph_edges ORDER BY 1, 2, 3, 4, 5"
            )
        ]


class TestSynthesisHardwareParams:
    def test_defaults_follow_plan(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            batch, prefetch = _synthesis_hardware(store)
        finally:
            store.close()
        assert batch >= 1
        assert prefetch is True

    def test_kill_switch_restores_historical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            assert _synthesis_hardware(store) == (0, False)
        finally:
            store.close()
        reset_plan_cache()

    def test_probe_failure_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = EventStore(tmp_path / "s.sqlite3")

        def _boom(path=None, *, refresh=False):
            raise RuntimeError("synthetic probe failure")

        monkeypatch.setattr("know_your_context.hardware.profile.probe_hardware", _boom)
        reset_plan_cache()
        try:
            assert _synthesis_hardware(store) == (0, False)
        finally:
            store.close()
        reset_plan_cache()

    def test_memory_store_still_gets_a_plan(self) -> None:
        store = EventStore(":memory:")
        try:
            batch, _prefetch = _synthesis_hardware(store)
        finally:
            store.close()
        assert batch >= 1  # UNKNOWN disk profile (64) — fail-open, never zero


class TestByteIdenticalResults:
    def test_synthesis_output_matches_historical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "tree"
        _seed(root)

        store_h = EventStore(tmp_path / "h.sqlite3")
        IngestionPipeline(store_h).ingest_path(root, incremental=False, resolve=False)
        report_h = run_synthesis(store_h)
        edges_h = _edge_dump(store_h)
        store_h.close()

        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        store_s = EventStore(tmp_path / "s.sqlite3")
        IngestionPipeline(store_s).ingest_path(root, incremental=False, resolve=False)
        report_s = run_synthesis(store_s)
        edges_s = _edge_dump(store_s)
        store_s.close()
        reset_plan_cache()

        assert edges_h == edges_s
        assert report_h.emitted == report_s.emitted
        assert report_h.inserted == report_s.inserted
        assert [
            (p.name, p.candidates, p.emitted, p.inserted, p.skipped_existing, p.skipped_uncertain)
            for p in report_h.passes
        ] == [
            (p.name, p.candidates, p.emitted, p.inserted, p.skipped_existing, p.skipped_uncertain)
            for p in report_s.passes
        ]

    def test_second_run_is_a_noop_identically(self, tmp_path: Path) -> None:
        """Idempotence: re-running synthesis on the same store emits nothing new."""
        root = tmp_path / "tree"
        _seed(root)
        store = EventStore(tmp_path / "s.sqlite3")
        IngestionPipeline(store).ingest_path(root, incremental=False, resolve=False)
        first = run_synthesis(store)
        second = run_synthesis(store)  # prefetch path must see pass-1 inserts
        store.close()
        assert first.inserted >= 0
        assert second.inserted == 0
