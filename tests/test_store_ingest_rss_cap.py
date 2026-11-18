"""SUB-209: the ingest kill switch must reach the SQLite pragmas.

``INGEST_DISABLE_HW_TUNING=1`` historically reached pipeline fan-out only
(workers/commit batch = 1, in ingest/pipeline.py); the store still opened
with the probe recipe — mmap ≈ db size + an aggressive page cache — whose
first scan query costs tens of MiB of RSS (measured +59 MiB on the real
313 MB L4 store for one ``COUNT(*)`` vs +3 MiB with the legacy recipe,
SUB-206 baseline repro). These tests pin:

- the deterministic recipe change: with the env set, ``PRAGMA mmap_size`` is
  0 and the page cache stays at the engine default (no huge mapped region);
- the measured RSS cost per query in ingest mode under a hard threshold
  (Linux-gated; the 313 MB probe is not a unit-test fixture);
- that the default read path (env unset) keeps the probe recipe — the read
  webview/serve path is a separate process and is untouched;
- the pure ``plan_for_ingest_rss`` cap function.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from know_your_context.hardware.probe import DiskKind, DiskProbe, MemoryProbe
from know_your_context.hardware.tuning import (
    DISK_PROFILES,
    INGEST_DISABLE_HW_TUNING_ENV,
    TuningPlan,
    plan_for_ingest_rss,
)
from know_your_context.store import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]
CHILD = REPO_ROOT / "tests" / "_store_rss_cap_child.py"
SRC = REPO_ROOT / "src"

#: Hard RSS budget for the ingest-mode query phase (MiB). The capped recipe
#: sits at a few MiB (engine-default page cache, no mmap); the uncapped probe
#: recipe costs ~db size (~70 MiB in these tests, +59 MiB on the 313 MB L4
#: store) — 16 MiB is a >3x margin over the capped signal and a fraction of
#: the uncapped one.
RSS_DELTA_MB = 16.0
#: Must be big enough that the uncapped recipe blows the budget: several
#: windows wider than RSS_DELTA_MB.
WIDE_ROWS = 135000
WIDE_ROW_BYTES = 516


def _build_store(path: Path, rows: int) -> Path:
    """A real file-backed store with ``rows`` wide rows (WAL checkpointed)."""
    store = EventStore(str(path))
    try:
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE wide(id INTEGER PRIMARY KEY, blob TEXT)")
            conn.execute(
                "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < ?) "
                "INSERT INTO wide(blob) SELECT printf('%-512d-END', x) FROM c",
                (rows,),
            )
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    finally:
        store.close()
    return path


def _build_big_store(path: Path) -> Path:
    """The ~70 MB store for the subprocess probes (uncapped recipe must blow the budget)."""
    path = _build_store(path, WIDE_ROWS)
    assert os.path.getsize(path) > 64 * 1024 * 1024, (
        f"test db too small for a meaningful probe: {os.path.getsize(path)} bytes"
    )
    return path


@pytest.fixture(scope="session")
def big_store(tmp_path_factory) -> Path:
    """One ~70 MB store shared by every subprocess probe in the session."""
    return _build_big_store(tmp_path_factory.mktemp("sub209") / "wide.db")


def _run_child(mode: str, db_path: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [sys.executable, str(CHILD), mode, str(db_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"child {mode} failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"
        )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert "error" not in payload, payload
    return payload


# ---------------------------------------------------------------------------
# Full-stack pragma + RSS behavior on a real file-backed store (~70 MB).
# ---------------------------------------------------------------------------


def test_ingest_rss_mode_caps_mmap_and_cache_pragmas(big_store) -> None:
    """The env reaches the store connection: no mmap, engine-default cache."""
    payload = _run_child("capped", big_store)
    assert payload["applied_mmap_bytes"] == 0
    assert payload["applied_cache_kib"] == 0
    assert payload["mmap_size"] == 0, f"PRAGMA mmap_size not capped: {payload}"
    # Negative cache_size means KiB units (SQLite contract); -2000 pages is
    # the engine default. Anything below (huge) is the aggressive recipe.
    assert payload["cache_size"] >= -2000, f"page cache not capped: {payload}"


def test_default_read_path_keeps_probe_recipe(big_store) -> None:
    """Env unset: the read path keeps the probe recipe (mmap sized to db)."""
    payload = _run_child("default", big_store)
    assert payload["mmap_size"] > 0, f"probe mmap missing on the default path: {payload}"
    assert payload["mmap_size"] <= payload["db_bytes"], (
        f"mmap {payload['mmap_size']} exceeds db size {payload['db_bytes']}: {payload}"
    )


@pytest.mark.skipif(
    sys.platform != "linux" or not Path("/proc/self/status").exists(),
    reason="RSS deltas are read from /proc/self/status (VmRSS)",
)
def test_ingest_rss_mode_bounds_rss_delta_per_query(big_store) -> None:
    """100 full-scan COUNT(*) queries in ingest mode stay under RSS_DELTA_MB."""
    payload = _run_child("capped", big_store)
    assert payload["delta_1q_mb"] <= RSS_DELTA_MB, (
        f"one COUNT(*) cost {payload['delta_1q_mb']} MiB RSS in ingest mode "
        f"(budget {RSS_DELTA_MB} MiB, SUB-206 uncapped signal was +59 MiB): {payload}"
    )
    assert payload["delta_100q_mb"] <= RSS_DELTA_MB, (
        f"100 COUNT(*) cost {payload['delta_100q_mb']} MiB RSS in ingest mode (budget {RSS_DELTA_MB} MiB): {payload}"
    )


# ---------------------------------------------------------------------------
# Store-level resolution: the env is read at open time, in-process.
# ---------------------------------------------------------------------------


def test_ingest_env_reaches_resolved_store_tuning(tmp_path, monkeypatch) -> None:
    db = _build_store(tmp_path / "store.db", rows=6000)  # ~3 MB: >2 MiB so the probe cache bound engages

    monkeypatch.setenv(INGEST_DISABLE_HW_TUNING_ENV, "1")
    capped = EventStore(str(db))
    try:
        assert capped.applied_tuning.mmap_bytes == 0
        assert capped.applied_tuning.cache_kib == 0
    finally:
        capped.close()

    monkeypatch.delenv(INGEST_DISABLE_HW_TUNING_ENV, raising=False)
    default = EventStore(str(db))
    try:
        assert default.applied_tuning.mmap_bytes > 0
        assert default.applied_tuning.cache_kib > 0
    finally:
        default.close()


def test_explicit_storage_tuning_pin_wins_over_env(tmp_path, monkeypatch) -> None:
    """An explicit recipe is a pin, not a suggestion; the env never overrides it."""
    from know_your_context.store import StorageTuning

    db = _build_store(tmp_path / "store.db", rows=6000)

    monkeypatch.setenv(INGEST_DISABLE_HW_TUNING_ENV, "1")
    store = EventStore(str(db), storage_tuning=StorageTuning.legacy())
    try:
        assert store.applied_tuning.mmap_bytes == 0
        assert store.applied_tuning.source == "legacy"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Pure function: plan_for_ingest_rss.
# ---------------------------------------------------------------------------

_SSD = DiskProbe(DiskKind.SSD, "sda", False, "test")
_MEM = MemoryProbe(8 * 1024**3, 6 * 1024**3, "test")


def _tuned_plan(mmap_bytes: int = 256 * 1024**2, cache_kib: int = 256 * 1024) -> TuningPlan:
    return TuningPlan(
        mmap_bytes,
        4096,
        True,
        DISK_PROFILES[DiskKind.SSD],
        ("probe",),
        cache_kib,
    )


def test_plan_for_ingest_rss_caps_mmap_and_cache() -> None:
    plan = plan_for_ingest_rss(_tuned_plan())
    assert plan.mmap_bytes == 0
    assert plan.cache_kib == 0
    assert plan is not _tuned_plan()


def test_plan_for_ingest_rss_preserves_access_mechanics() -> None:
    cap = plan_for_ingest_rss(_tuned_plan())
    assert cap.page_size == 4096
    assert cap.synchronous_normal is True
    assert cap.profile is DISK_PROFILES[DiskKind.SSD]
    assert any(INGEST_DISABLE_HW_TUNING_ENV in note for note in cap.notes)


def test_plan_for_ingest_rss_passthrough_when_already_capped() -> None:
    already = _tuned_plan(mmap_bytes=0, cache_kib=0)
    assert plan_for_ingest_rss(already) is already


def test_plan_for_ingest_rss_does_not_mutate_input() -> None:
    original = _tuned_plan()
    plan_for_ingest_rss(original)
    assert original.mmap_bytes == 256 * 1024**2
    assert original.cache_kib == 256 * 1024
    assert original.notes == ("probe",)
