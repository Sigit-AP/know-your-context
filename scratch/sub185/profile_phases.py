"""Phase profiler for the ingestion pipeline (SUB-185).

Traces tracemalloc current/peak + /proc RSS at every phase boundary of a real
ingest (walk, phase A, phase C, resolve, primary-suggestions, report) using a
subclass that snapshots around each phase call. Deterministic corpus, one
worker profile: set KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING=1 to keep SQLite
policy pages out of the traced-allocation picture (RSS is still reported).

Usage:
    KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING=1 KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER=1 \\
    .venv/bin/python scratch/sub185/profile_phases.py <corpus_dir> [tag]
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from know_your_context.ingest.pipeline import IngestionPipeline, canonical_ingestion_dump
from know_your_context.store import EventStore

# ruff: noqa: E402


def _rss_kb() -> int:
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


class _ProfiledPipeline(IngestionPipeline):
    """Snapshot tracemalloc+RSS around each phase boundary."""

    def __init__(self, *args, profiler: "_Profiler", **kwargs):
        super().__init__(*args, **kwargs)
        self._profiler = profiler

    def _run_phase_a(self, *args, **kwargs):
        self._profiler.checkpoint("A-preflight")
        result = super()._run_phase_a(*args, **kwargs)
        self._profiler.checkpoint("A-end")
        return result

    def _run_phase_c(self, *args, **kwargs):
        self._profiler.checkpoint("C-start")
        result = super()._run_phase_c(*args, **kwargs)
        self._profiler.checkpoint("C-end")
        return result

    def _retry_failed_refs(self, *args, **kwargs):
        self._profiler.checkpoint("resolve-pre")
        result = super()._retry_failed_refs(*args, **kwargs)
        self._profiler.checkpoint("resolve-pre-end")
        return result

    def _run_resolution(self, *args, **kwargs):
        self._profiler.checkpoint("resolve-start")
        result = super()._run_resolution(*args, **kwargs)
        self._profiler.checkpoint("resolve-end")
        return result

    def _primary_suggestions(self, *args, **kwargs):
        self._profiler.checkpoint("sugg-start")
        result = super()._primary_suggestions(*args, **kwargs)
        self._profiler.checkpoint("sugg-end")
        return result

    def _assemble_report(self, *args, **kwargs):
        self._profiler.checkpoint("report-start")
        result = super()._assemble_report(*args, **kwargs)
        self._profiler.checkpoint("done")
        return result


class _Profiler:
    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, int, int, int, float]] = []

    def checkpoint(self, label: str) -> None:
        cur, peak = tracemalloc.get_traced_memory()
        self.checkpoints.append((label, cur, peak, _rss_kb(), time.monotonic()))

    def report(self) -> str:
        lines = ["phase-boundary    tm_cur_MiB  tm_peak_MiB  rss_MiB  t_sec"]
        base_t = self.checkpoints[0][4] if self.checkpoints else 0.0
        for label, cur, peak, rss_kb, t in self.checkpoints:
            lines.append(
                f"{label:<17} {cur / 1048576:>10.1f} {peak / 1048576:>12.1f} "
                f"{rss_kb / 1024:>8.1f} {t - base_t:>6.1f}"
            )
        return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: profile_phases.py <corpus_dir> [tag]", file=sys.stderr)
        return 2
    corpus = Path(sys.argv[1])
    tag = sys.argv[2] if len(sys.argv) > 2 else "run"
    profiler = _Profiler()
    store = EventStore(":memory:")
    store.migrate()
    pipeline = _ProfiledPipeline(store, profiler=profiler)
    tracemalloc.start()
    started = time.monotonic()
    try:
        report = pipeline.ingest_path(corpus, incremental=True, recursive=True, resolve=True)
    finally:
        tracemalloc.stop()
    wall = time.monotonic() - started
    dump = canonical_ingestion_dump(store)
    digest = hashlib.sha256(dump.encode("utf-8")).hexdigest()
    print(profiler.report())
    print(f"tag={tag} wall={wall:.2f}s dump_sha256={digest} dump_bytes={len(dump)}")
    print(
        f"stats={dict(report.stats)} totals={dict(report.totals)} "
        f"resolution={report.resolution} resolution_status={report.resolution_status} "
        f"suggestions={len(report.primary_suggestions)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())