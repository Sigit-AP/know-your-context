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

__all__ = ["HardwarePlan", "hardware_plan", "reset_plan_cache"]


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


_PLAN_CACHE: dict[str | None, HardwarePlan] = {}


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
