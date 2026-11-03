"""The single hardware source of truth (operator direction, W15).

One cached :class:`HardwarePlan` per store path, consumed by every
subsystem: the store pragma recipe, the autoscaler's parallelism advice, the
GPU gate, and operator tooling all read from here — no subsystem probes or
computes hardware on its own. Load is the only dynamic input (the autoscaler
re-reads it per advice); everything structural (cores, memory, disk kind,
GPU) is probed once and cached.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from know_your_context.hardware.probe import HardwareProfile, probe_hardware, reset_probe_cache
from know_your_context.hardware.tuning import TuningPlan, plan_storage_tuning
from know_your_context.ops.switches import load_knobs, load_switches

__all__ = ["HISTORICAL_IO_BUFFER_BYTES", "HardwarePlan", "hardware_plan", "io_buffer_bytes", "reset_plan_cache"]


@dataclass(frozen=True)
class HardwarePlan:
    """Everything a subsystem needs, derived once from one probe."""

    profile: HardwareProfile
    storage: TuningPlan
    cores: int
    mem_available_bytes: int
    mem_per_worker_bytes: int
    min_workers: int
    autoscale_enabled: bool

    def base_workers(self) -> int:
        """Worker count following cores, bounded by the RAM allowance."""
        from know_your_context.hardware.autoscaler import recommended_workers

        return recommended_workers(
            cores=self.cores,
            mem_available_bytes=self.mem_available_bytes,
            mem_per_worker_bytes=self.mem_per_worker_bytes,
        )

    def queue_scale(self) -> int:
        """RAM-derived multiplier for bounded queues (SUB-135 integration).

        One step per started 4 GiB of MemAvailable, capped at 16; unknown RAM
        (non-Linux procfs) or small hosts return 1, so bounded queues keep
        their historical sizes exactly — scale-UP only, never a reduction.
        """
        if self.mem_available_bytes <= 0:
            return 1
        return max(1, min(16, -(-self.mem_available_bytes // (4 * 1024**3))))

    def io_buffer_bytes(self) -> int:
        """Streaming I/O buffer for backup/projection/ops, from the disk profile."""
        return self.storage.profile.io_buffer_bytes

    def query_cache_entries(self) -> int:
        """retrieval_v2 query-cache capacity derived from the cache policy.

        Follows ``storage.cache_kib`` (itself ⅛ of MemAvailable, capped):
        0 (tuning off / unknown RAM / small store) means the cache stays OFF
        and lookups keep the historical uncached behavior byte-identical.
        """
        cache_kib = self.storage.cache_kib
        if cache_kib <= 0:
            return 0
        return min(16384, max(128, cache_kib // 16))


_PLAN_CACHE: dict[str | None, HardwarePlan] = {}

#: Historical streaming I/O buffer (``1 << 20``) — the fallback when the
#: plan is unavailable or hw tuning is switched off.
HISTORICAL_IO_BUFFER_BYTES = 1024 * 1024


def io_buffer_bytes(path: str | Path | None = None) -> int:
    """Streaming I/O buffer for backup/projection/ops, from the single plan.

    SSD and UNKNOWN keep the historical 1 MiB; rotational devices scale up to
    4 MiB (fewer syscalls, seeks amortized) — scale-UP only. Kill switch
    ``KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING`` or any detection failure returns
    the historical buffer.
    """
    try:
        if load_switches().hw_tuning_disabled():
            return HISTORICAL_IO_BUFFER_BYTES
        return max(HISTORICAL_IO_BUFFER_BYTES, hardware_plan(path).io_buffer_bytes())
    except Exception:  # noqa: BLE001 - a buffer hint must never break I/O
        return HISTORICAL_IO_BUFFER_BYTES


def hardware_plan(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    refresh: bool = False,
) -> HardwarePlan:
    """The cached plan for the host as seen from *path* (the store's location).

    ``env=None`` (the production path) resolves knobs from the process
    environment and caches per path; an explicit env bypasses the cache so
    tests and one-off operator runs see exactly what they set.
    """
    key: str | None = None if path is None else str(Path(path).resolve())
    if env is None and not refresh and key in _PLAN_CACHE:
        return _PLAN_CACHE[key]

    profile = probe_hardware(path, refresh=refresh)
    knob_board = load_knobs(env)
    switch_board = load_switches(env)
    try:
        db_size = os.path.getsize(key) if key is not None else 0
    except OSError:
        db_size = 0
    storage = plan_storage_tuning(
        db_size_bytes=db_size,
        disk=profile.disk,
        memory=profile.memory,
        knobs=knob_board,
        hw_tuning_disabled=switch_board.hw_tuning_disabled(),
    )
    plan = HardwarePlan(
        profile=profile,
        storage=storage,
        cores=profile.cpu_count,
        mem_available_bytes=profile.memory.available_bytes,
        mem_per_worker_bytes=int(knob_board.value("hw.mem_per_worker_mib")) * 1024**2,
        min_workers=int(knob_board.value("hw.min_workers")),
        autoscale_enabled=bool(knob_board.value("hw.autoscale_enabled")) and not switch_board.hw_autoscaler_disabled(),
    )
    if env is None:
        _PLAN_CACHE[key] = plan
    return plan


def reset_plan_cache() -> None:
    """Drop cached plans AND probe results (tests; operator refresh flows)."""
    _PLAN_CACHE.clear()
    reset_probe_cache()
