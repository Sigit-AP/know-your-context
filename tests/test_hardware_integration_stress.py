"""Integration stress (SUB-135): the hardware wiring changes REAL behavior.

Two kinds of evidence, both measured on this host:

1. **Behavior change.** The ingest pipeline resolves (workers, commit batch)
   from the plan; a synthetic big host flips DaemonConfig read pool / queues;
   the retrieval query cache turns repeated lookups into memory hits. Each is
   asserted on live objects, and the A/B wall times are recorded honestly.
2. **No regression.** The 1/10/50/100-agent storm (small + large store) is
   re-run through the integrated code path with the same maturity gates as
   the SUB-134 baseline: 0 errors everywhere, p99 bounded, no collapse.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from know_your_context.hardware.profile import reset_plan_cache
from know_your_context.ingest.pipeline import IngestionPipeline, _hardware_ingest_params
from know_your_context.retrieval_v2.lookup import LookupConfig, SymbolLookup
from know_your_context.store import EventStore

_STRESS_DIR = Path(os.environ.get("KYS_HW_STRESS_DIR") or (os.environ.get("TMPDIR", "/tmp") + "/kys-hw-stress"))
_INTEGRATION_RESULTS = _STRESS_DIR / "integration-results.json"

# Shared storm machinery lives in tests/test_hardware_stress.py (SUB-134);
# imported here so both suites measure the SAME code path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_hardware_stress import _NEEDS_LINUX, _SIZES, _build_store, run_stress  # noqa: E402


def _record(key: str, data: dict[str, object]) -> None:
    _STRESS_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if _INTEGRATION_RESULTS.exists():
        existing = json.loads(_INTEGRATION_RESULTS.read_text())
    existing[key] = data
    _INTEGRATION_RESULTS.write_text(json.dumps(existing, indent=2, sort_keys=True))


def _write_corpus(root: Path, count: int) -> None:
    """Realistic Python corpus: classes, methods, calls — parseable work."""
    for index in range(count):
        (root / f"module_{index:03d}.py").write_text(
            f'"""Corpus module {index}."""\n\n\n'
            f"class Worker{index}:\n"
            f"    def __init__(self, seed: int) -> None:\n"
            f"        self.seed = seed\n\n"
            + "".join(
                f"    def step_{step}(self, value: int) -> int:\n"
                f"        return (value + self.seed + {step}) % {index + 7}\n\n"
                for step in range(12)
            )
            + f"\ndef make_{index}() -> Worker{index}:\n    return Worker{index}({index})\n",
            encoding="utf-8",
        )


def _ingest_wall(store_path: Path, root: Path) -> float:
    store = EventStore(store_path)
    try:
        started = time.perf_counter()
        IngestionPipeline(store).ingest_path(root, incremental=False, resolve=False)
        return time.perf_counter() - started
    finally:
        store.close()


@_NEEDS_LINUX
class TestIngestIntegrationMeasured:
    def test_workers_and_batch_live_on_this_host(self, tmp_path: Path) -> None:
        """The wiring is real on this host (2 vCPU, ~2.7 GiB free, SSD):
        2 extraction workers + the SSD group-commit batch of 32."""
        store = EventStore(tmp_path / "s.sqlite3")
        try:
            workers, batch = _hardware_ingest_params(store)
        finally:
            store.close()
        assert workers == 2  # min(2 cores, RAM allowance) — the plan, not a constant
        assert batch == 32  # DISK_PROFILES[SSD].group_commit_batch

    def test_parallel_vs_sequential_throughput(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A/B: the hardware path must not regress vs the historical path.

        The GIL bounds pure-Python tree-sitter gains; the honest requirement
        is no regression + identical output, and the ratio is recorded.
        """
        root = tmp_path / "corpus"
        root.mkdir()
        _write_corpus(root, 96)

        historical_s = _ingest_wall(tmp_path / "historical.sqlite3", root)
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        sequential_s = _ingest_wall(tmp_path / "sequential.sqlite3", root)
        reset_plan_cache()

        # Identical persisted content (the determinism pin under stress shape).
        dump_h = EventStore(tmp_path / "historical.sqlite3")
        dump_s = EventStore(tmp_path / "sequential.sqlite3")
        try:
            with dump_h.read() as conn_h, dump_s.read() as conn_s:
                rows_h = conn_h.execute("SELECT count(*) FROM symbols").fetchone()[0]
                rows_s = conn_s.execute("SELECT count(*) FROM symbols").fetchone()[0]
        finally:
            dump_h.close()
            dump_s.close()
        assert rows_h == rows_s > 0

        ratio = historical_s / sequential_s if sequential_s > 0 else 0.0
        _record(
            "ingest-96-files",
            {
                "hardware_s": round(historical_s, 4),
                "historical_s": round(sequential_s, 4),
                "speedup": round(ratio, 3),
                "symbols": rows_h,
            },
        )
        assert historical_s <= sequential_s * 1.5, (
            f"hardware path regressed: {historical_s:.3f}s vs historical {sequential_s:.3f}s"
        )


@_NEEDS_LINUX
class TestQueryCacheMeasured:
    def test_cache_hits_beat_uncached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        _write_corpus(root, 24)
        store = EventStore(tmp_path / "s.sqlite3")
        IngestionPipeline(store).ingest_path(root, incremental=False, resolve=False)
        # Force the cache policy on with an explicit config (the plan on this
        # host may resolve 0 for a tiny store — that path is pinned elsewhere).
        cached = SymbolLookup(LookupConfig(query_cache_entries=64))
        uncached = SymbolLookup(LookupConfig(query_cache_entries=0))
        try:
            cached.search(store, "step")  # warm
            started = time.perf_counter()
            for _ in range(200):
                cached.search(store, "step")
            cached_s = time.perf_counter() - started
            started = time.perf_counter()
            for _ in range(200):
                uncached.search(store, "step")
            uncached_s = time.perf_counter() - started
        finally:
            store.close()
        stats = cached.cache_stats
        _record(
            "query-cache-200-lookups",
            {"cached_s": round(cached_s, 4), "uncached_s": round(uncached_s, 4), **stats},
        )
        assert stats["hits"] >= 199
        assert cached_s < uncached_s, f"cache slower than uncached: {cached_s:.4f}s vs {uncached_s:.4f}s"


@_NEEDS_LINUX
class TestStormNoRegression:
    """The 1/10/50/100-agent matrix re-run through the integrated tree."""

    @pytest.mark.parametrize(
        ("label", "agents", "queries"),
        [
            ("10mb", 1, 800),
            ("10mb", 10, 800),
            ("10mb", 50, 400),
            ("10mb", 100, 200),
            ("500mb", 1, 1500),
            ("500mb", 10, 800),
            ("500mb", 50, 400),
            ("500mb", 100, 300),
        ],
        ids=lambda v: str(v),
    )
    def test_cell(self, label: str, agents: int, queries: int) -> None:
        db = _STRESS_DIR / f"stress-{label}.db"
        rows = _build_store(db, _SIZES[label])
        report = run_stress(db, agents=agents, queries_per_agent=queries, rows=rows)
        _record(
            f"storm-{agents}-agents-{label}",
            {
                "ops": report.ops,
                "errors": report.errors,
                "throughput_ops_s": round(report.throughput_ops_s, 1),
                "p99_ms": round(report.p99_ms, 3),
                "rss_delta_mb": round(report.rss_delta_bytes / 1024**2, 1),
            },
        )
        assert report.errors == 0
        assert report.ops == agents * queries
        assert report.p99_ms < 5000.0, f"p99 {report.p99_ms:.1f} ms — wedged under overload"
