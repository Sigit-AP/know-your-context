"""Hardware stress suite (operator direction, W15): maturity under load.

Real measurements on real stores (10 MB / 500 MB / 2 GB) with 1 / 10 / 50 /
100 concurrent agents. Every agent opens its own EventStore (WAL multi-
reader), runs point reads with a 1% write mix, and we record throughput,
p50/p95/p99 latency, RSS delta, and I/O bytes. Maturity gates are honest and
host-relative: correctness always, scale-UP from 1→10 agents, no collapse at
100 agents, p99 bounded, RSS within the configured map bound. Numbers land
in docs/recovery/w15-hardware.md.
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from know_your_context.store import EventStore

_STRESS_DIR = Path(os.environ.get("KYS_HW_STRESS_DIR") or (os.environ.get("TMPDIR", "/tmp") + "/kys-hw-stress"))
_RESULTS_PATH = _STRESS_DIR / "stress-results.json"
_CHUNK = 65536  # 64 KiB chunks: ~16 pages per point read — a realistic heavy read
_WRITE_MODULO = 100  # 1% write mix


def _build_store(path: Path, target_bytes: int) -> int:
    """Deterministic chunked store (seeded sha256; no urandom).

    Returns the ACTUAL row count; file size drifts once EventStore migrations
    add their tables, so sizing rows from the file would over-read the table.
    """
    marker = path.with_suffix(".rows")
    if path.exists() and marker.exists() and path.stat().st_size >= target_bytes * 9 // 10:
        return int(marker.read_text().strip())
    rows = target_bytes // _CHUNK
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("DROP TABLE IF EXISTS chunks")
    conn.execute("DROP TABLE IF EXISTS stress_kv")
    conn.execute("CREATE TABLE chunks (row_k INTEGER PRIMARY KEY, data BLOB NOT NULL)")
    conn.execute("CREATE TABLE stress_kv (k INTEGER PRIMARY KEY, v INTEGER NOT NULL)")
    hashes = _CHUNK // 32
    for k in range(rows):
        payload = b"".join(hashlib.sha256(f"{k}:{b}".encode()).digest() for b in range(hashes))
        conn.execute("INSERT INTO chunks VALUES (?, ?)", (k, payload))
        if k % 512 == 0:
            conn.commit()
    conn.commit()
    conn.close()
    marker.write_text(str(rows))
    return rows


@dataclass(frozen=True)
class StressReport:
    agents: int
    store_bytes: int
    ops: int
    errors: int
    wall_s: float
    throughput_ops_s: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    rss_delta_bytes: int
    io_read_bytes: int


def _rss_bytes() -> int:
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def _io_read_bytes() -> int:
    for line in Path("/proc/self/io").read_text().splitlines():
        if line.startswith("read_bytes:"):
            return int(line.split()[1])
    return 0


def run_stress(db: Path, agents: int, queries_per_agent: int, rows: int) -> StressReport:
    """N agent threads, each with its own EventStore, mixed read/write load."""
    gc.collect()
    rss_before = _rss_bytes()
    io_before = _io_read_bytes()
    latencies_ns: list[int] = []
    lat_lock = threading.Lock()
    errors = 0
    error_lock = threading.Lock()
    rng = random.Random(42)

    def _agent(seed: int) -> None:
        nonlocal errors
        local_rng = random.Random(seed)
        store = EventStore(db)
        try:
            for i in range(queries_per_agent):
                start = time.perf_counter_ns()
                try:
                    if i % _WRITE_MODULO == _WRITE_MODULO - 1:
                        with store.transaction() as conn:
                            conn.execute("INSERT OR REPLACE INTO stress_kv VALUES (?, ?)", (seed % 1024, i))
                    else:
                        row = store.execute_raw(
                            "SELECT length(data) FROM chunks WHERE row_k = ?", (local_rng.randrange(rows),)
                        ).fetchone()
                        assert row[0] == _CHUNK
                except Exception:
                    with error_lock:
                        errors += 1
                finally:
                    with lat_lock:
                        latencies_ns.append(time.perf_counter_ns() - start)
        finally:
            store.close()

    threads = [threading.Thread(target=_agent, args=(rng.randrange(2**31),)) for _ in range(agents)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - started

    gc.collect()
    latencies_ms = sorted(value / 1e6 for value in latencies_ns)
    ops = len(latencies_ms)

    def _pct(q: float) -> float:
        return latencies_ms[min(ops - 1, max(0, math.ceil(q * ops) - 1))]

    return StressReport(
        agents=agents,
        store_bytes=db.stat().st_size,
        ops=ops,
        errors=errors,
        wall_s=wall,
        throughput_ops_s=ops / wall if wall > 0 else 0.0,
        p50_ms=_pct(0.50),
        p95_ms=_pct(0.95),
        p99_ms=_pct(0.99),
        rss_delta_bytes=_rss_bytes() - rss_before,
        io_read_bytes=_io_read_bytes() - io_before,
    )


def _record(key: str, report: StressReport) -> None:
    _STRESS_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    if _RESULTS_PATH.exists():
        data = json.loads(_RESULTS_PATH.read_text())
    data[key] = {
        "agents": report.agents,
        "store_mb": round(report.store_bytes / 1024**2, 1),
        "ops": report.ops,
        "errors": report.errors,
        "wall_s": round(report.wall_s, 3),
        "throughput_ops_s": round(report.throughput_ops_s, 1),
        "p50_ms": round(report.p50_ms, 3),
        "p95_ms": round(report.p95_ms, 3),
        "p99_ms": round(report.p99_ms, 3),
        "rss_delta_mb": round(report.rss_delta_bytes / 1024**2, 1),
        "io_read_mb": round(report.io_read_bytes / 1024**2, 1),
    }
    _RESULTS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True))


def _results() -> dict[str, dict[str, float]]:
    if not _RESULTS_PATH.exists():
        return {}
    return json.loads(_RESULTS_PATH.read_text())


@pytest.fixture(scope="module")
def stress_stores() -> dict[str, Path]:
    _STRESS_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "10mb": _STRESS_DIR / "stress-10mb.db",
        "500mb": _STRESS_DIR / "stress-500mb.db",
        "2gb": _STRESS_DIR / "stress-2gb.db",
    }


_NEEDS_LINUX = pytest.mark.skipif(
    sys.platform != "linux" or not Path("/proc/self/statm").exists(),
    reason="hardware stress needs Linux /proc",
)


_SIZES = {"10mb": 10 * 1024**2, "500mb": 500 * 1024**2, "2gb": 2 * 1024**3}


def _cell(stores: dict[str, Path], label: str, agents: int, queries: int, key: str) -> dict[str, float]:
    """One measured cell; recomputed when absent (order-independent)."""
    existing = _results()
    if key in existing:
        return existing[key]
    db = stores[label]
    rows = _build_store(db, _SIZES[label])
    report = run_stress(db, agents=agents, queries_per_agent=queries, rows=rows)
    _record(key, report)
    return _results()[key]


@_NEEDS_LINUX
class TestHardwareStress:
    def test_low_load_single_agent(self, stress_stores: dict[str, Path]) -> None:
        report = _cell(stress_stores, "500mb", 1, 2000, "low-1-agent-500mb")
        assert report["errors"] == 0
        assert report["ops"] == 2000
        assert report["throughput_ops_s"] > 0

    def test_medium_load_10_agents(self, stress_stores: dict[str, Path]) -> None:
        report = _cell(stress_stores, "500mb", 10, 1000, "medium-10-agents-500mb")
        assert report["errors"] == 0

    def test_heavy_load_50_agents(self, stress_stores: dict[str, Path]) -> None:
        report = _cell(stress_stores, "500mb", 50, 600, "heavy-50-agents-500mb")
        assert report["errors"] == 0

    def test_very_heavy_load_100_agents(self, stress_stores: dict[str, Path]) -> None:
        """Overload: 100 agents on 2 vCPU. Graceful = all complete, no wedge,
        no error storm, p99 bounded — fairness/backpressure, not shrinkage."""
        report = _cell(stress_stores, "500mb", 100, 400, "very-heavy-100-agents-500mb")
        assert report["errors"] == 0
        assert report["ops"] == 40000
        assert report["p99_ms"] < 5000.0, f"p99 {report['p99_ms']:.1f} ms — wedged under overload"

    def test_small_store_10mb_heavy(self, stress_stores: dict[str, Path]) -> None:
        report = _cell(stress_stores, "10mb", 50, 400, "heavy-50-agents-10mb")
        assert report["errors"] == 0

    def test_huge_store_2gb(self, stress_stores: dict[str, Path]) -> None:
        for agents, queries, key in ((10, 800, "medium-10-agents-2gb"), (100, 200, "very-heavy-100-agents-2gb")):
            report = _cell(stress_stores, "2gb", agents, queries, key)
            assert report["errors"] == 0

    def test_scaling_up_1_to_10_agents(self, stress_stores: dict[str, Path]) -> None:
        """Scale-UP maturity: 10 agents must beat a single agent on the same store."""
        low = _cell(stress_stores, "500mb", 1, 2000, "low-1-agent-500mb")
        medium = _cell(stress_stores, "500mb", 10, 1000, "medium-10-agents-500mb")
        assert medium["throughput_ops_s"] >= low["throughput_ops_s"] * 1.1, (
            f"no scale-up: 1 agent {low['throughput_ops_s']}/s vs 10 agents {medium['throughput_ops_s']}/s"
        )

    def test_no_collapse_under_overload(self, stress_stores: dict[str, Path]) -> None:
        """100 agents must not collapse vs 10 agents (typed fairness, not meltdown)."""
        medium = _cell(stress_stores, "500mb", 10, 1000, "medium-10-agents-500mb")
        heavy = _cell(stress_stores, "500mb", 100, 400, "very-heavy-100-agents-500mb")
        assert heavy["throughput_ops_s"] >= medium["throughput_ops_s"] * 0.3, (
            f"collapse: 10 agents {medium['throughput_ops_s']}/s vs 100 agents {heavy['throughput_ops_s']}/s"
        )

    def test_ram_bound_holds_during_storm(self, stress_stores: dict[str, Path]) -> None:
        """RSS growth across the whole storm stays inside the configured map + slack."""
        for label in ("10mb", "500mb", "2gb"):
            store = EventStore(stress_stores[label])
            bound = store.applied_tuning.mmap_bytes
            store.close()
            for key, report in _results().items():
                if not key.endswith(label):
                    continue
                assert report["rss_delta_mb"] * 1024**2 <= bound + 256 * 1024**2, (
                    f"{key}: RSS delta {report['rss_delta_mb']} MiB exceeds map bound {bound / 1024**2:.0f} MiB"
                )
