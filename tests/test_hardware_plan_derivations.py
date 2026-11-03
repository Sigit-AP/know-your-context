"""Derived HardwarePlan values: the single source of truth (SUB-135).

Every subsystem consumes these derivations; no subsystem re-computes
hardware on its own. The formulas are pinned here, scale-up only: on small
hardware every derivation collapses to the historical default, so behavior
is byte-identical where the historical sizing was already right.
"""

from __future__ import annotations

from know_your_context.hardware.probe import DiskKind, DiskProbe, GpuKind, GpuProbe, HardwareProfile, MemoryProbe
from know_your_context.hardware.profile import HardwarePlan, hardware_plan, reset_plan_cache
from know_your_context.hardware.tuning import DISK_PROFILES, TuningPlan

MiB = 1024**2
GiB = 1024**3


def _profile(*, cores: int, mem_available: int, disk: DiskKind) -> HardwareProfile:
    return HardwareProfile(
        cpu_count=cores,
        memory=MemoryProbe(total_bytes=64 * GiB, available_bytes=mem_available, note="synthetic"),
        disk=DiskProbe(kind=disk, device="test", rotational=disk is DiskKind.HDD, note="synthetic"),
        gpu=GpuProbe(kind=GpuKind.NONE, devices=(), notes=()),
        notes=(),
    )


def _plan(*, cores: int, mem_available: int, disk: DiskKind, cache_kib: int = 0) -> HardwarePlan:
    profile = _profile(cores=cores, mem_available=mem_available, disk=disk)
    storage = TuningPlan(
        mmap_bytes=0,
        page_size=None,
        synchronous_normal=False,
        profile=DISK_PROFILES[disk],
        notes=(),
        cache_kib=cache_kib,
    )
    return HardwarePlan(
        profile=profile,
        storage=storage,
        cores=cores,
        mem_available_bytes=mem_available,
        mem_per_worker_bytes=512 * MiB,
        min_workers=1,
        autoscale_enabled=True,
    )


class TestQueueScale:
    def test_unknown_ram_is_historical(self) -> None:
        assert _plan(cores=8, mem_available=0, disk=DiskKind.SSD).queue_scale() == 1

    def test_small_host_is_historical(self) -> None:
        """The verdict host (2.7 GiB free) keeps the historical queue sizes."""
        assert _plan(cores=2, mem_available=2700 * MiB, disk=DiskKind.SSD).queue_scale() == 1

    def test_scale_up_only_with_ram(self) -> None:
        assert _plan(cores=16, mem_available=8 * GiB, disk=DiskKind.SSD).queue_scale() == 2
        assert _plan(cores=16, mem_available=32 * GiB, disk=DiskKind.SSD).queue_scale() == 8

    def test_scale_capped(self) -> None:
        assert _plan(cores=64, mem_available=1024 * GiB, disk=DiskKind.SSD).queue_scale() == 16


class TestIoBuffer:
    def test_ssd_keeps_historical_1mib(self) -> None:
        assert _plan(cores=4, mem_available=8 * GiB, disk=DiskKind.SSD).io_buffer_bytes() == 1 * MiB

    def test_unknown_keeps_historical_1mib(self) -> None:
        assert _plan(cores=4, mem_available=8 * GiB, disk=DiskKind.UNKNOWN).io_buffer_bytes() == 1 * MiB

    def test_hdd_scales_up_to_4mib(self) -> None:
        """Rotational: bigger sequential buffers amortize seeks."""
        assert _plan(cores=4, mem_available=8 * GiB, disk=DiskKind.HDD).io_buffer_bytes() == 4 * MiB

    def test_disk_profile_carries_the_buffer(self) -> None:
        """The value lives on the disk profile (single source), not in consumers."""
        assert DISK_PROFILES[DiskKind.SSD].io_buffer_bytes == 1 * MiB
        assert DISK_PROFILES[DiskKind.HDD].io_buffer_bytes == 4 * MiB
        assert DISK_PROFILES[DiskKind.UNKNOWN].io_buffer_bytes == 1 * MiB


class TestQueryCacheEntries:
    def test_cache_off_when_policy_off(self) -> None:
        """cache_kib == 0 (small db / tuning off / unknown RAM) means no query cache."""
        assert _plan(cores=4, mem_available=8 * GiB, disk=DiskKind.SSD, cache_kib=0).query_cache_entries() == 0

    def test_aggressive_when_ram_allows(self) -> None:
        plan = _plan(cores=4, mem_available=64 * GiB, disk=DiskKind.SSD, cache_kib=4 * MiB // 1024)
        assert plan.query_cache_entries() >= 128

    def test_capped(self) -> None:
        plan = _plan(cores=4, mem_available=64 * GiB, disk=DiskKind.SSD, cache_kib=4 * GiB // 1024)
        assert plan.query_cache_entries() == 16384


class TestSingleSource:
    def test_cached_plan_is_shared(self, tmp_path) -> None:
        """One probe, one cached plan per path; consumers never re-probe."""
        reset_plan_cache()
        first = hardware_plan(tmp_path / "a.sqlite3")
        second = hardware_plan(tmp_path / "a.sqlite3")
        assert first is second
        other = hardware_plan(tmp_path / "b.sqlite3")
        assert other is not first
        reset_plan_cache()

    def test_plan_survives_missing_db_file(self, tmp_path) -> None:
        """Fail-open: a not-yet-created store still gets a plan (probe walks ancestors)."""
        plan = hardware_plan(tmp_path / "ghost.sqlite3")
        assert plan.cores >= 1
        reset_plan_cache()

    def test_base_workers_bounded_by_ram(self) -> None:
        assert _plan(cores=16, mem_available=2700 * MiB, disk=DiskKind.SSD).base_workers() == 5
        assert _plan(cores=2, mem_available=2700 * MiB, disk=DiskKind.SSD).base_workers() == 2

    def test_queue_scale_is_pure(self) -> None:
        plan = _plan(cores=4, mem_available=12 * GiB, disk=DiskKind.SSD)
        assert plan.queue_scale() == plan.queue_scale() == 3
