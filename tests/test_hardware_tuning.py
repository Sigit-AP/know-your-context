"""Tests for hardware.tuning + the additive StorageTuning in store.py (W15).

Verdict plan §5.2/§5.3: mmap_size = min(db_size, ½ MemAvailable), page-size
hint per disk type, synchronous=NORMAL only when the operator opts in AND the
device is non-rotational. Everything behind knobs + a kill switch; the
historical recipe (no mmap, engine page size, FULL) is one switch away.

The RSS drill proves the mmap promise: random point queries against a 100MB+
store must not raise RSS by a meaningful fraction of the database size.
"""

from __future__ import annotations

import gc
import hashlib
import os
import random
import sqlite3
import sys
from pathlib import Path

import pytest

from know_your_context.hardware.probe import DiskKind, DiskProbe, MemoryProbe, probe_disk_kind
from know_your_context.hardware.tuning import (
    DISK_PROFILES,
    DiskProfile,
    TuningError,
    plan_storage_tuning,
    recommended_mmap_size,
    validate_page_size,
)
from know_your_context.ops.switches import load_knobs
from know_your_context.store import EventStore, StorageTuning, StorageTuningError

_SSD = DiskProbe(DiskKind.SSD, "sda", False, "test")
_HDD = DiskProbe(DiskKind.HDD, "sda", True, "test")
_UNKNOWN = DiskProbe(DiskKind.UNKNOWN, None, None, "test")
_MEM_4G = MemoryProbe(4 * 1024**3, 3 * 1024**3, "test")
_MEM_UNKNOWN = MemoryProbe(0, 0, "test")

GiB = 1024**3
MiB = 1024**2


class TestRecommendedMmapSize:
    def test_min_of_db_half_mem_cap(self) -> None:
        assert recommended_mmap_size(db_size_bytes=100, mem_available_bytes=1000, cap_bytes=10**9) == 100
        assert recommended_mmap_size(db_size_bytes=10**9, mem_available_bytes=200, cap_bytes=10**9) == 100
        assert recommended_mmap_size(db_size_bytes=10**9, mem_available_bytes=10**9, cap_bytes=64) == 64

    def test_zero_db_maps_up_to_memory_bound(self) -> None:
        """A fresh db (size 0 at open) gets the memory-bound hint, not 0."""
        assert recommended_mmap_size(db_size_bytes=0, mem_available_bytes=2 * GiB, cap_bytes=GiB) == GiB

    def test_zero_available_disables_mmap(self) -> None:
        assert recommended_mmap_size(db_size_bytes=GiB, mem_available_bytes=0, cap_bytes=GiB) == 0
        assert recommended_mmap_size(db_size_bytes=0, mem_available_bytes=0, cap_bytes=GiB) == 0

    def test_verdict_host_numbers(self) -> None:
        """Host: 2.7 GB available → half is ~1.35 GB; a 350 MB db maps fully."""
        avail = 2_700_000 * 1024
        assert recommended_mmap_size(db_size_bytes=350 * MiB, mem_available_bytes=avail, cap_bytes=GiB) == 350 * MiB

    def test_negative_inputs_raise_typed(self) -> None:
        with pytest.raises(TuningError) as excinfo:
            recommended_mmap_size(db_size_bytes=-1, mem_available_bytes=1, cap_bytes=1)
        assert excinfo.value.code == "INVALID_INPUT"
        with pytest.raises(TuningError):
            recommended_mmap_size(db_size_bytes=1, mem_available_bytes=-1, cap_bytes=1)
        with pytest.raises(TuningError):
            recommended_mmap_size(db_size_bytes=1, mem_available_bytes=1, cap_bytes=-1)


class TestDiskProfiles:
    def test_every_disk_kind_has_a_profile(self) -> None:
        assert set(DISK_PROFILES) == {DiskKind.SSD, DiskKind.HDD, DiskKind.UNKNOWN}

    def test_hdd_batches_bigger_and_flushes_less_often_than_ssd(self) -> None:
        ssd, hdd = DISK_PROFILES[DiskKind.SSD], DISK_PROFILES[DiskKind.HDD]
        assert hdd.group_commit_batch > ssd.group_commit_batch
        assert hdd.flush_cadence_ms > ssd.flush_cadence_ms

    def test_hdd_page_hint_larger_than_ssd(self) -> None:
        assert DISK_PROFILES[DiskKind.HDD].page_size_hint > DISK_PROFILES[DiskKind.SSD].page_size_hint

    def test_unknown_profile_is_the_conservative_middle(self) -> None:
        unknown = DISK_PROFILES[DiskKind.UNKNOWN]
        ssd, hdd = DISK_PROFILES[DiskKind.SSD], DISK_PROFILES[DiskKind.HDD]
        assert ssd.group_commit_batch <= unknown.group_commit_batch <= hdd.group_commit_batch
        assert unknown.page_size_hint == 4096  # engine default: no change without evidence

    def test_profiles_are_frozen_and_positive(self) -> None:
        for profile in DISK_PROFILES.values():
            assert isinstance(profile, DiskProfile)
            assert profile.flush_cadence_ms > 0
            assert profile.group_commit_batch > 0


class TestValidatePageSize:
    def test_zero_means_engine_default(self) -> None:
        assert validate_page_size(0) is None

    def test_powers_of_two_accepted(self) -> None:
        for value in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536):
            assert validate_page_size(value) == value

    def test_non_power_of_two_rejected(self) -> None:
        for value in (1000, 4095, 4097, 12288 + 1, 96):
            with pytest.raises(TuningError) as excinfo:
                validate_page_size(value)
            assert excinfo.value.code == "INVALID_PAGE_SIZE"

    def test_out_of_range_rejected(self) -> None:
        for value in (256, 131072, -4096):
            with pytest.raises(TuningError):
                validate_page_size(value)


class TestPlanStorageTuning:
    def test_kill_switch_returns_legacy_plan(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=10 * MiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={}),
            hw_tuning_disabled=True,
        )
        assert plan.mmap_bytes == 0
        assert plan.page_size is None
        assert plan.synchronous_normal is False
        assert any("kill switch" in note for note in plan.notes)

    def test_default_plan_maps_db_and_keeps_full_sync(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=10 * MiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={}),
            hw_tuning_disabled=False,
        )
        assert plan.mmap_bytes == min(10 * MiB, _MEM_4G.available_bytes // 2, GiB)
        assert plan.synchronous_normal is False  # knob default off = historical FULL
        assert plan.page_size is None  # SSD default = engine 4096

    def test_mmap_knob_off_disables_mmap(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=10 * MiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_MMAP_ENABLED": "off"}),
            hw_tuning_disabled=False,
        )
        assert plan.mmap_bytes == 0
        assert any("mmap" in note for note in plan.notes)

    def test_mmap_cap_knob_bounds_the_map(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=10 * GiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_MMAP_MAX_BYTES": "1048576"}),
            hw_tuning_disabled=False,
        )
        assert plan.mmap_bytes == MiB

    def test_unknown_memory_falls_back_to_cap_with_note(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=10 * MiB,
            disk=_SSD,
            memory=_MEM_UNKNOWN,
            knobs=load_knobs(env={}),
            hw_tuning_disabled=False,
        )
        assert plan.mmap_bytes == 10 * MiB
        assert any("MemAvailable" in note for note in plan.notes)

    def test_synchronous_normal_only_on_ssd_with_opt_in(self) -> None:
        env = {"KNOW_YOUR_CONTEXT_STORAGE_SYNCHRONOUS_NORMAL": "1"}
        ssd_plan = plan_storage_tuning(
            db_size_bytes=MiB, disk=_SSD, memory=_MEM_4G, knobs=load_knobs(env=env), hw_tuning_disabled=False
        )
        assert ssd_plan.synchronous_normal is True
        hdd_plan = plan_storage_tuning(
            db_size_bytes=MiB, disk=_HDD, memory=_MEM_4G, knobs=load_knobs(env=env), hw_tuning_disabled=False
        )
        assert hdd_plan.synchronous_normal is False
        assert any("rotational" in note for note in hdd_plan.notes)
        unknown_plan = plan_storage_tuning(
            db_size_bytes=MiB, disk=_UNKNOWN, memory=_MEM_4G, knobs=load_knobs(env=env), hw_tuning_disabled=False
        )
        assert unknown_plan.synchronous_normal is False

    def test_hdd_gets_larger_page_hint_on_fresh_dbs(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=0, disk=_HDD, memory=_MEM_4G, knobs=load_knobs(env={}), hw_tuning_disabled=False
        )
        assert plan.page_size == DISK_PROFILES[DiskKind.HDD].page_size_hint

    def test_page_size_knob_overrides_profile(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=0,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_PAGE_SIZE": "16384"}),
            hw_tuning_disabled=False,
        )
        assert plan.page_size == 16384

    def test_page_size_knob_rejects_non_power_of_two(self) -> None:
        with pytest.raises(TuningError):
            plan_storage_tuning(
                db_size_bytes=0,
                disk=_SSD,
                memory=_MEM_4G,
                knobs=load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_PAGE_SIZE": "1000"}),
                hw_tuning_disabled=False,
            )


class TestAggressiveCache:
    """Operator direction: big cache when RAM is available — a measured
    bound (⅛ of MemAvailable, capped), not a cage."""

    def test_aggressive_cache_default_on(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=200 * MiB,
            disk=_SSD,
            memory=_MEM_4G,  # 3 GiB available → ⅛ = 384 MiB; db caps at 200 MiB
            knobs=load_knobs(env={}),
            hw_tuning_disabled=False,
        )
        assert plan.cache_kib == 200 * 1024

    def test_cache_bounded_by_eighth_of_available(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=10 * GiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={}),
            hw_tuning_disabled=False,
        )
        assert plan.cache_kib == 3 * GiB // 8 // 1024

    def test_cache_cap_knob_bounds(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=10 * GiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_CACHE_MAX_KIB": "65536"}),
            hw_tuning_disabled=False,
        )
        assert plan.cache_kib == 65536

    def test_aggressive_cache_off_leaves_engine_default(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=200 * MiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_AGGRESSIVE_CACHE": "off"}),
            hw_tuning_disabled=False,
        )
        assert plan.cache_kib == 0

    def test_unknown_memory_leaves_engine_default_with_note(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=200 * MiB,
            disk=_SSD,
            memory=_MEM_UNKNOWN,
            knobs=load_knobs(env={}),
            hw_tuning_disabled=False,
        )
        assert plan.cache_kib == 0
        assert any("cache" in note for note in plan.notes)

    def test_kill_switch_leaves_engine_default(self) -> None:
        plan = plan_storage_tuning(
            db_size_bytes=200 * MiB,
            disk=_SSD,
            memory=_MEM_4G,
            knobs=load_knobs(env={}),
            hw_tuning_disabled=True,
        )
        assert plan.cache_kib == 0

    def test_small_cache_falls_back_to_engine_default(self) -> None:
        """Below ~2 MiB there is nothing to gain over the engine default."""
        plan = plan_storage_tuning(
            db_size_bytes=1024,
            disk=_SSD,
            memory=MemoryProbe(512 * MiB, 8 * MiB, "test"),  # ⅛ of 8 MiB = 1 MiB
            knobs=load_knobs(env={}),
            hw_tuning_disabled=False,
        )
        assert plan.cache_kib == 0


class TestStorageTuningSpec:
    def test_legacy_is_the_historical_recipe(self) -> None:
        legacy = StorageTuning.legacy()
        assert legacy.mmap_bytes == 0
        assert legacy.page_size is None
        assert legacy.synchronous_normal is False
        assert legacy.source == "legacy"

    def test_invalid_spec_fails_closed(self) -> None:
        with pytest.raises(StorageTuningError):
            StorageTuning(mmap_bytes=-1)
        with pytest.raises(StorageTuningError):
            StorageTuning(page_size=1000)
        with pytest.raises(StorageTuningError):
            StorageTuning(page_size=256)
        with pytest.raises(StorageTuningError):
            StorageTuning(cache_kib=-1)


class TestEventStoreTuningIntegration:
    def test_default_store_applies_mmap_on_file_db(self, tmp_path: Path) -> None:
        db = tmp_path / "store.db"
        store = EventStore(db)
        try:
            mmap = store.execute_raw("PRAGMA mmap_size").fetchone()[0]
            assert mmap > 0
            sync = store.execute_raw("PRAGMA synchronous").fetchone()[0]
            assert sync == 2  # FULL: the knob defaults off (historical durability)
            assert store.applied_tuning.source == "probe"
        finally:
            store.close()

    def test_memory_db_skips_mmap_and_stays_legacy(self) -> None:
        store = EventStore(":memory:")
        try:
            assert store.applied_tuning.mmap_bytes == 0
            sync = store.execute_raw("PRAGMA synchronous").fetchone()[0]
            assert sync == 2
        finally:
            store.close()

    def test_explicit_legacy_tuning_disables_mmap(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "store.db", storage_tuning=StorageTuning.legacy())
        try:
            assert store.execute_raw("PRAGMA mmap_size").fetchone()[0] == 0
        finally:
            store.close()

    def test_explicit_mmap_tuning_applies(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "store.db", storage_tuning=StorageTuning(mmap_bytes=8 * MiB))
        try:
            assert store.execute_raw("PRAGMA mmap_size").fetchone()[0] == 8 * MiB
        finally:
            store.close()

    def test_synchronous_normal_applies_when_tuned(self, tmp_path: Path) -> None:
        store = EventStore(
            tmp_path / "store.db",
            storage_tuning=StorageTuning(mmap_bytes=0, synchronous_normal=True),
        )
        try:
            assert store.execute_raw("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        finally:
            store.close()

    def test_page_size_hint_applies_to_fresh_db(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "store.db", storage_tuning=StorageTuning(page_size=8192))
        try:
            assert store.execute_raw("PRAGMA page_size").fetchone()[0] == 8192
        finally:
            store.close()

    def test_page_size_hint_does_not_rewrite_existing_db(self, tmp_path: Path) -> None:
        db = tmp_path / "store.db"
        store = EventStore(db, storage_tuning=StorageTuning.legacy())
        store.close()
        store2 = EventStore(db, storage_tuning=StorageTuning(page_size=16384))
        try:
            assert store2.execute_raw("PRAGMA page_size").fetchone()[0] == 4096  # untouched
        finally:
            store2.close()

    def test_kill_switch_restores_legacy_recipe(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_STORAGE_SYNCHRONOUS_NORMAL", "1")
        store = EventStore(tmp_path / "store.db")
        try:
            assert store.applied_tuning.mmap_bytes == 0
            assert store.applied_tuning.synchronous_normal is False
            assert store.applied_tuning.source == "legacy"
            assert store.execute_raw("PRAGMA synchronous").fetchone()[0] == 2
        finally:
            store.close()

    def test_env_knob_cap_applies(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_STORAGE_MMAP_MAX_BYTES", "65536")
        store = EventStore(tmp_path / "store.db")
        try:
            assert store.execute_raw("PRAGMA mmap_size").fetchone()[0] == 65536
        finally:
            store.close()

    def test_env_knob_sync_normal_reflects_disk_honestly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_STORAGE_SYNCHRONOUS_NORMAL", "1")
        db = tmp_path / "store.db"
        store = EventStore(db)
        try:
            expected = 1 if probe_disk_kind(db).kind is DiskKind.SSD else 2
            assert store.execute_raw("PRAGMA synchronous").fetchone()[0] == expected
        finally:
            store.close()

    def test_reopen_in_place_preserves_tuning(self, tmp_path: Path) -> None:
        db = tmp_path / "store.db"
        tuning = StorageTuning(mmap_bytes=4 * MiB, synchronous_normal=True)
        store = EventStore(db, storage_tuning=tuning)
        try:
            with store._lock:
                store.reopen_in_place()
            assert store.applied_tuning is tuning
            assert store.execute_raw("PRAGMA mmap_size").fetchone()[0] == 4 * MiB
            assert store.execute_raw("PRAGMA synchronous").fetchone()[0] == 1
        finally:
            store.close()

    def test_explicit_cache_kib_applies_as_kib_units(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "store.db", storage_tuning=StorageTuning(cache_kib=65536))
        try:
            # Negative cache_size = KiB units (SQLite contract).
            assert store.execute_raw("PRAGMA cache_size").fetchone()[0] == -65536
        finally:
            store.close()

    def test_legacy_tuning_leaves_engine_cache_default(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "store.db", storage_tuning=StorageTuning.legacy())
        try:
            assert store.execute_raw("PRAGMA cache_size").fetchone()[0] == -2000
        finally:
            store.close()

    def test_tuned_store_serves_queries_identically(self, tmp_path: Path) -> None:
        """mmap/NORMAL change access mechanics, never results."""
        results: list[list[tuple[int, bytes]]] = []
        for tuning in (StorageTuning.legacy(), StorageTuning(mmap_bytes=64 * MiB, synchronous_normal=True)):
            store = EventStore(tmp_path / "store.db", storage_tuning=tuning)
            try:
                with store.transaction() as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS t_probe (k INTEGER PRIMARY KEY, v BLOB)")
                    for i in range(50):
                        digest = hashlib.sha256(str(i).encode()).digest()
                        conn.execute("INSERT OR REPLACE INTO t_probe VALUES (?, ?)", (i, digest))
                rows = store.execute_raw("SELECT k, v FROM t_probe ORDER BY k").fetchall()
                results.append([(row["k"], row["v"]) for row in rows])
            finally:
                store.close()
        assert results[0] == results[1]
        assert len(results[0]) == 50


def _build_big_db(path: Path, rows: int = 1500, chunks_per_row: int = 25, chunk_bytes: int = 4096) -> int:
    """Deterministic ~150 MB database of 4 KiB chunks (seeded sha256, no urandom).

    Chunks of one page each model the interactive access pattern honestly:
    a point lookup touches a handful of pages, never a whole object — unlike
    ``substr`` on a big blob, which SQLite materializes in full first.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute(
        "CREATE TABLE chunks ("
        "row_k INTEGER NOT NULL, seq INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY (row_k, seq)"
        ")"
    )
    hashes_per_chunk = chunk_bytes // 32
    for k in range(rows):
        conn.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?)",
            (
                (
                    k,
                    seq,
                    b"".join(hashlib.sha256(f"{k}:{seq}:{b}".encode()).digest() for b in range(hashes_per_chunk)),
                )
                for seq in range(chunks_per_row)
            ),
        )
    conn.commit()
    conn.close()
    return path.stat().st_size


def _rss_bytes() -> int:
    statm = Path("/proc/self/statm").read_text().split()
    return int(statm[1]) * os.sysconf("SC_PAGE_SIZE")


@pytest.mark.skipif(
    sys.platform != "linux" or not Path("/proc/self/statm").exists(),
    reason="RSS drill requires Linux /proc/self/statm",
)
def test_mmap_random_reads_respect_the_map_bound(tmp_path: Path) -> None:
    """Drill: 3000 random point reads on a 100MB+ mmap'd store.

    Measured honesty (this host, 2-vCPU VM): mmap does NOT lower RSS versus
    the read() path — kernel readahead faults neighboring pages into RSS
    (full-map run: 144 MiB of a 165 MiB store; mmap=0 run: 2.6 MiB). What
    the sizing formula guarantees, and this drill proves, is the *bound*:
    RSS growth never exceeds the configured map (here db_size // 4), so
    min(db, ½ × MemAvailable, cap) caps worst-case RSS growth at half of
    available memory. The mmap win is no-copy access and reclaimable pages,
    not a smaller resident set — recorded in docs/recovery/w15-hardware.md.
    """
    db = tmp_path / "big.db"
    db_size = _build_big_db(db)
    assert db_size >= 100 * MiB

    map_bound = db_size // 4
    tuning = StorageTuning(mmap_bytes=map_bound)
    store = EventStore(db, storage_tuning=tuning)
    try:
        assert store.execute_raw("PRAGMA mmap_size").fetchone()[0] >= map_bound
        gc.collect()
        rss_before = _rss_bytes()
        rng = random.Random(42)
        rows, chunks_per_row = 1500, 25
        total_len = 0
        for _ in range(3000):
            row = store.execute_raw(
                "SELECT length(data) FROM chunks WHERE row_k = ? AND seq = ?",
                (rng.randrange(rows), rng.randrange(chunks_per_row)),
            ).fetchone()
            total_len += row[0]
        gc.collect()
        rss_after = _rss_bytes()
        assert total_len == 3000 * 4096  # every query hit a real 4 KiB chunk
        delta = rss_after - rss_before
        # The map bound holds with slack for interpreter noise, and the
        # resident set stays far below the whole database.
        assert delta <= map_bound + 8 * MiB, f"RSS grew {delta / MiB:.1f} MiB beyond the {map_bound / MiB:.1f} MiB map"
        assert delta < db_size // 3, f"RSS grew {delta / MiB:.1f} MiB over a {db_size / MiB:.1f} MiB store"
    finally:
        store.close()
