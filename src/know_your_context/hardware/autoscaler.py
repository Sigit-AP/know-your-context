"""Adaptive parallelism advisor: workers follow demand, up to full hardware.

Operator direction (W15): the product must *use* the machine fully —
"gunakan seperlunya sesuai beban, skala NAIK saat padat". The advisor maps
the 1-minute load average (runnable tasks ≈ demand) onto a worker count:

    workers = clamp(ceil(load1), min_workers, min(cores, RAM allowance))

* sepi → floor (``min_workers``), no idle workers burning nothing;
* padat → FULL core count, never less: overload is absorbed by typed
  backpressure and fairness at the edges, never by shrinking the pool;
* RAM allowance (MemAvailable ÷ per-worker) bounds the cap so the host
  survives — a guardrail, not a courtesy;
* unknown load or memory → fail-open to FULL parallelism, honestly noted;
* nothing is niced, reniced, or deprioritized — ever.

Kill switch: ``KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER`` → static workers.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from know_your_context.hardware.probe import probe_cpu_count, probe_memory
from know_your_context.ops.switches import (
    ENV_DISABLE_HW_AUTOSCALER,
    ENV_HW_AUTOSCALE_ENABLED,
    SwitchError,
    load_knobs,
    parse_switch_bool,
)

__all__ = [
    "DEFAULT_MEM_PER_WORKER_BYTES",
    "ENV_DISABLE_HW_AUTOSCALER",
    "Autoscaler",
    "ParallelismAdvice",
    "autoscaler_disabled",
    "recommended_workers",
]

DEFAULT_MEM_PER_WORKER_BYTES = 512 * 1024**2


@dataclass(frozen=True)
class ParallelismAdvice:
    """One scale decision; pure data — applying it is the caller's job."""

    workers: int
    cores: int
    mem_available_bytes: int
    load1: float | None
    saturated: bool
    notes: tuple[str, ...]


def recommended_workers(*, cores: int, mem_available_bytes: int, mem_per_worker_bytes: int) -> int:
    """Core cap bounded by the RAM allowance; floor 1.

    ``mem_available_bytes == 0`` means "unknown" (non-Linux, restricted
    procfs): fail open to the core count — an unknown budget must not cage
    the machine.
    """
    if cores < 1:
        raise SwitchError("INVALID_SPEC", f"cores must be >= 1, got {cores}")
    if mem_available_bytes < 0:
        raise SwitchError("INVALID_SPEC", f"mem_available_bytes must be >= 0, got {mem_available_bytes}")
    if mem_per_worker_bytes <= 0:
        raise SwitchError("INVALID_SPEC", f"mem_per_worker_bytes must be > 0, got {mem_per_worker_bytes}")
    if mem_available_bytes == 0:
        return cores
    allowance = max(1, mem_available_bytes // mem_per_worker_bytes)
    return max(1, min(cores, allowance))


def autoscaler_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Kill switch: strict parse of ``KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER``."""
    environ: Mapping[str, str] = os.environ if env is None else env
    raw = environ.get(ENV_DISABLE_HW_AUTOSCALER)
    if raw is None:
        return False
    return parse_switch_bool(ENV_DISABLE_HW_AUTOSCALER, raw)


class Autoscaler:
    """Advises the active worker count from demand; scale-up only."""

    def __init__(
        self,
        *,
        cores: int | None = None,
        mem_available_bytes: int | None = None,
        load_reader: Callable[[], tuple[float, float, float]] = os.getloadavg,
        mem_per_worker_bytes: int = DEFAULT_MEM_PER_WORKER_BYTES,
        min_workers: int = 1,
        enabled: bool = True,
        static_workers: int = 1,
    ) -> None:
        if mem_per_worker_bytes <= 0:
            raise SwitchError("INVALID_SPEC", f"mem_per_worker_bytes must be > 0, got {mem_per_worker_bytes}")
        if min_workers < 1:
            raise SwitchError("INVALID_SPEC", f"min_workers must be >= 1, got {min_workers}")
        if static_workers < 1:
            raise SwitchError("INVALID_SPEC", f"static_workers must be >= 1, got {static_workers}")
        self.cores = cores if cores is not None else probe_cpu_count()
        self.mem_available_bytes = (
            mem_available_bytes if mem_available_bytes is not None else probe_memory().available_bytes
        )
        self._load_reader = load_reader
        self.mem_per_worker_bytes = mem_per_worker_bytes
        self.min_workers = min_workers
        self.enabled = enabled
        self.static_workers = static_workers

    @classmethod
    def from_env(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        cores: int | None = None,
        mem_available_bytes: int | None = None,
        load_reader: Callable[[], tuple[float, float, float]] = os.getloadavg,
        static_workers: int = 1,
    ) -> Autoscaler:
        """Resolve config from knobs + kill switch (fail-closed on bad values)."""
        knobs = load_knobs(env)
        enabled = bool(knobs.value("hw.autoscale_enabled")) and not autoscaler_disabled(env)
        return cls(
            cores=cores,
            mem_available_bytes=mem_available_bytes,
            load_reader=load_reader,
            mem_per_worker_bytes=int(knobs.value("hw.mem_per_worker_mib")) * 1024**2,
            min_workers=int(knobs.value("hw.min_workers")),
            enabled=enabled,
            static_workers=static_workers,
        )

    def _load1(self) -> float | None:
        try:
            return float(self._load_reader()[0])
        except OSError, AttributeError:
            return None

    def advise(self) -> ParallelismAdvice:
        """One advisory: demand-driven workers, capped at the core/RAM limit."""
        notes: list[str] = []
        if not self.enabled:
            notes.append(f"autoscaler disabled ({ENV_HW_AUTOSCALE_ENABLED}=off or {ENV_DISABLE_HW_AUTOSCALER}=1)")
            return ParallelismAdvice(
                self.static_workers, self.cores, self.mem_available_bytes, None, False, tuple(notes)
            )

        cap = recommended_workers(
            cores=self.cores,
            mem_available_bytes=self.mem_available_bytes,
            mem_per_worker_bytes=self.mem_per_worker_bytes,
        )
        if self.mem_available_bytes == 0:
            notes.append("MemAvailable unknown; cap follows cores (fail-open to full parallelism)")

        load1 = self._load1()
        if load1 is None:
            notes.append("load unknown on this platform; advising full parallelism (fail-open)")
            return ParallelismAdvice(cap, self.cores, self.mem_available_bytes, None, False, tuple(notes))

        demand = max(self.min_workers, math.ceil(load1))
        workers = min(cap, demand)
        saturated = load1 >= self.cores
        if saturated:
            notes.append(
                f"load1 {load1:.2f} >= {self.cores} cores: saturated — full parallelism ({workers} workers); "
                "overload is absorbed by typed backpressure + fairness at the edges, never by shrinking the pool"
            )
        else:
            notes.append(f"load1 {load1:.2f} on {self.cores} cores: demand {demand} workers (cap {cap})")
        return ParallelismAdvice(workers, self.cores, self.mem_available_bytes, load1, saturated, tuple(notes))
