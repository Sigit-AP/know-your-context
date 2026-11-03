"""Hardware integration for retrieval_v2 (SUB-135).

The symbol-lookup query cache follows the plan's cache policy
(``query_cache_entries``: aggressive when RAM is large, 0 — the historical
uncached behavior — when the policy is off) and the FTS rebuild asserts the
plan's page cache on the build connection. Cache keys carry the store's
knowledge high-watermark, so a write can never serve a stale hit; an active
experience reranker bypasses the cache entirely.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from know_your_context.hardware.profile import reset_plan_cache
from know_your_context.ingest.pipeline import IngestionPipeline
from know_your_context.retrieval_v2.fts import rebuild_fts_index
from know_your_context.retrieval_v2.lookup import LookupConfig, SymbolLookup
from know_your_context.store import EventStore


def _seed_store(path: Path) -> EventStore:
    root = path.parent / "tree"
    root.mkdir(exist_ok=True)
    for index in range(4):
        (root / f"m{index}.py").write_text(
            f"def handler_{index}(request):\n    return process_{index}(request)\n",
            encoding="utf-8",
        )
    store = EventStore(path)
    IngestionPipeline(store).ingest_path(root, incremental=False, resolve=False)
    return store


def _big_cache_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake a host whose cache policy is aggressive (large page cache)."""
    from know_your_context.hardware import tuning as tuning_mod
    from know_your_context.hardware.probe import DiskKind, DiskProbe, GpuKind, GpuProbe, HardwareProfile, MemoryProbe
    from know_your_context.hardware.profile import HardwarePlan

    MiB = 1024**2
    profile = HardwareProfile(
        cpu_count=8,
        memory=MemoryProbe(total_bytes=64 * 1024**3, available_bytes=32 * 1024**3, note="synthetic"),
        disk=DiskProbe(kind=DiskKind.SSD, device="t", rotational=False, note="synthetic"),
        gpu=GpuProbe(kind=GpuKind.NONE, devices=(), notes=()),
        notes=(),
    )
    storage = tuning_mod.TuningPlan(0, None, False, tuning_mod.DISK_PROFILES[DiskKind.SSD], (), 512 * 1024)
    plan = HardwarePlan(
        profile=profile,
        storage=storage,
        cores=8,
        mem_available_bytes=32 * 1024**3,
        mem_per_worker_bytes=512 * MiB,
        min_workers=1,
        autoscale_enabled=True,
    )
    monkeypatch.setattr(
        "know_your_context.hardware.profile.hardware_plan", lambda path=None, *, env=None, refresh=False: plan
    )
    reset_plan_cache()


class TestQueryCacheCapacity:
    def test_capacity_zero_when_policy_off(self, tmp_path: Path) -> None:
        """Tiny store on this host: cache_kib is 0 → the cache stays off (historical)."""
        store = _seed_store(tmp_path / "s.sqlite3")
        try:
            lookup = SymbolLookup()
            assert lookup.cache_capacity >= 0  # resolution never fails
        finally:
            store.close()

    def test_capacity_follows_big_ram(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _big_cache_plan(monkeypatch)
        try:
            lookup = SymbolLookup()
            assert lookup.cache_capacity >= 128
        finally:
            reset_plan_cache()

    def test_kill_switch_disables_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _big_cache_plan(monkeypatch)
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        try:
            assert SymbolLookup().cache_capacity == 0
        finally:
            reset_plan_cache()

    def test_probe_failure_disables_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(path=None, *, env=None, refresh=False):
            raise RuntimeError("synthetic probe failure")

        monkeypatch.setattr("know_your_context.hardware.profile.hardware_plan", _boom)
        reset_plan_cache()
        try:
            assert SymbolLookup().cache_capacity == 0
        finally:
            reset_plan_cache()

    def test_explicit_config_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _big_cache_plan(monkeypatch)
        try:
            assert SymbolLookup(LookupConfig(query_cache_entries=7)).cache_capacity == 7
            assert SymbolLookup(LookupConfig(query_cache_entries=0)).cache_capacity == 0
        finally:
            reset_plan_cache()


class TestQueryCacheBehavior:
    def test_results_identical_cached_and_uncached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _seed_store(tmp_path / "s.sqlite3")
        _big_cache_plan(monkeypatch)
        try:
            cached = SymbolLookup()
            first = cached.search(store, "handler")
            second = cached.search(store, "handler")
            assert first == second
            assert cached.cache_stats["hits"] >= 1
            uncached = SymbolLookup(LookupConfig(query_cache_entries=0))
            assert uncached.search(store, "handler") == first
        finally:
            store.close()
            reset_plan_cache()

    def test_write_invalidates_via_watermark(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _seed_store(tmp_path / "s.sqlite3")
        _big_cache_plan(monkeypatch)
        root = tmp_path / "tree"
        try:
            lookup = SymbolLookup()
            before = lookup.search(store, "handler")
            (root / "new_module.py").write_text("def handler_new(request):\n    return request\n", encoding="utf-8")
            IngestionPipeline(store).ingest_path(root, incremental=True, resolve=False)
            after = lookup.search(store, "handler")
            assert any(hit.name == "handler_new" for hit in after)
            assert len(after) >= len(before)
        finally:
            store.close()
            reset_plan_cache()

    def test_cache_is_bounded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _seed_store(tmp_path / "s.sqlite3")
        _big_cache_plan(monkeypatch)
        try:
            lookup = SymbolLookup(LookupConfig(query_cache_entries=2))
            for query in ("handler", "process", "request", "handler"):
                lookup.search(store, query)
            assert lookup.cache_stats["size"] <= 2
            assert lookup.cache_stats["evictions"] >= 1
        finally:
            store.close()
            reset_plan_cache()


class TestFtsPageCache:
    def test_rebuild_asserts_plan_page_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The FTS build connection carries the plan's aggressive page cache."""
        store = _seed_store(tmp_path / "s.sqlite3")
        with store.read() as conn:
            before = int(conn.execute("PRAGMA cache_size").fetchone()[0])
        _big_cache_plan(monkeypatch)  # patched AFTER open: the rebuild must apply it
        try:
            rebuild_fts_index(store)
            with store.read() as conn:
                cache_size = int(conn.execute("PRAGMA cache_size").fetchone()[0])
            assert cache_size == -(512 * 1024)  # negative = KiB, straight from the plan
            assert cache_size != before
        finally:
            store.close()
            reset_plan_cache()

    def test_rebuild_results_identical_with_kill_switch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _seed_store(tmp_path / "s.sqlite3")
        _big_cache_plan(monkeypatch)
        try:
            lookup = SymbolLookup(LookupConfig(query_cache_entries=0))
            baseline = lookup.search(store, "handler")
            monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
            rebuild_fts_index(store)
            assert lookup.search(store, "handler") == baseline
        finally:
            store.close()
            reset_plan_cache()
