"""GPU offload gate: the machine-executable verdict formula (GPU-VERDICT §4).

Offload a batch analytics job to a GPU **only** if all of these hold:

1. ``edges > 5_000_000`` — below this, measured CPU kernels (PageRank
   6.2 ms/iter at 1M edges on a 2-vCPU VM) are already under the pain
   threshold; literature (Gunrock TOPC'17, McSherry HotOS'15) shows GPUs only
   win consistently at tens-hundreds of millions of edges;
2. ``T_cpu > 3 × (T_transfer + 20µs launch)`` — the transfer must be a
   rounding error of the compute it replaces (FTS5 at 45–132 µs never
   survives one 0.37 ms H2D transfer of a 4.4 MB CSR);
3. a *compute* GPU is actually detected (vmwgfx/virtio display adapters are
   not compute; this host has none).

Default is NO-GPU: ``KYS_GPU_OFFLOAD`` must be explicitly ``on`` for the gate
to open; every decision carries the measured inputs so telemetry can show
what the formula *would* have done (``would_offload``) without doing it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from know_your_context.hardware.probe import GpuKind, GpuProbe, probe_gpu
from know_your_context.ops.switches import parse_switch_bool

__all__ = [
    "DEFAULT_PCIE_BANDWIDTH_BPS",
    "ENV_GPU_OFFLOAD",
    "GPU_LAUNCH_OVERHEAD_S",
    "GPU_OFFLOAD_MIN_EDGES",
    "GpuGateError",
    "GpuOffloadDecision",
    "decide_gpu_offload",
    "estimate_transfer_s",
]

#: Verdict §4 edge threshold.
GPU_OFFLOAD_MIN_EDGES = 5_000_000

#: GPU kernel launch overhead (literature range 5–20 µs; we take the top).
GPU_LAUNCH_OVERHEAD_S = 20e-6

#: PCIe 3.0 x16 realistic host→device bandwidth (12 GB/s; CSUR cites 14.9 GiB/s
#: theoretical, small irregular transfers achieve far less).
DEFAULT_PCIE_BANDWIDTH_BPS = 12_000_000_000.0

#: Operator gate; default off — the product is GPU-optional, never GPU-bound.
ENV_GPU_OFFLOAD = "KYS_GPU_OFFLOAD"


class GpuGateError(ValueError):
    """Invalid gate input (typed; the gate never guesses)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class GpuOffloadDecision:
    """One measured gate decision; every field is an input or a derivation."""

    offload: bool
    would_offload: bool
    reason: str
    edges: int
    t_cpu_s: float
    t_transfer_s: float
    launch_overhead_s: float
    cpu_threshold_s: float
    gpu_kind: GpuKind
    notes: tuple[str, ...]


def estimate_transfer_s(bytes_to_transfer: int, bandwidth_bps: float) -> float:
    """Seconds to move *bytes_to_transfer* at *bandwidth_bps*."""
    if bytes_to_transfer < 0:
        raise GpuGateError("INVALID_INPUT", f"bytes_to_transfer must be >= 0, got {bytes_to_transfer}")
    if not math.isfinite(bandwidth_bps) or bandwidth_bps <= 0:
        raise GpuGateError("INVALID_INPUT", f"bandwidth_bps must be > 0 and finite, got {bandwidth_bps}")
    return bytes_to_transfer / bandwidth_bps


def decide_gpu_offload(
    *,
    edges: int,
    t_cpu_s: float,
    bytes_to_transfer: int,
    bandwidth_bps: float = DEFAULT_PCIE_BANDWIDTH_BPS,
    launch_overhead_s: float = GPU_LAUNCH_OVERHEAD_S,
    gpu: GpuProbe | None = None,
    env: Mapping[str, str] | None = None,
) -> GpuOffloadDecision:
    """Evaluate the verdict formula against measured inputs.

    Evaluation order is chosen for observability: the formula is always
    computed, then GPU presence, then the env gate — so a disabled gate on a
    formula-failing input still reports *why* the formula failed.
    """
    if edges < 0:
        raise GpuGateError("INVALID_INPUT", f"edges must be >= 0, got {edges}")
    if not math.isfinite(t_cpu_s) or t_cpu_s < 0:
        raise GpuGateError("INVALID_INPUT", f"t_cpu_s must be >= 0 and finite, got {t_cpu_s}")
    if not math.isfinite(launch_overhead_s) or launch_overhead_s < 0:
        raise GpuGateError("INVALID_INPUT", f"launch_overhead_s must be >= 0 and finite, got {launch_overhead_s}")

    t_transfer_s = estimate_transfer_s(bytes_to_transfer, bandwidth_bps)
    cpu_threshold_s = 3.0 * (t_transfer_s + launch_overhead_s)
    gpu_probe = probe_gpu() if gpu is None else gpu
    notes: list[str] = []

    if edges <= GPU_OFFLOAD_MIN_EDGES:
        formula_reason = "EDGES_BELOW_THRESHOLD"
        formula_ok = False
        notes.append(f"edges {edges} <= {GPU_OFFLOAD_MIN_EDGES} (verdict edge gate)")
    elif t_cpu_s <= cpu_threshold_s:
        formula_reason = "CPU_TIME_BELOW_THRESHOLD"
        formula_ok = False
        notes.append(f"T_cpu {t_cpu_s:.6f}s <= 3×(T_transfer {t_transfer_s:.6f}s + launch {launch_overhead_s:.6f}s)")
    else:
        formula_reason = "FORMULA_PASSES"
        formula_ok = True
        notes.append(f"T_cpu {t_cpu_s:.6f}s > 3×(T_transfer+launch) = {cpu_threshold_s:.6f}s")

    if not formula_ok:
        return GpuOffloadDecision(
            offload=False,
            would_offload=False,
            reason=formula_reason,
            edges=edges,
            t_cpu_s=t_cpu_s,
            t_transfer_s=t_transfer_s,
            launch_overhead_s=launch_overhead_s,
            cpu_threshold_s=cpu_threshold_s,
            gpu_kind=gpu_probe.kind,
            notes=tuple(notes),
        )

    if gpu_probe.kind is not GpuKind.COMPUTE:
        notes.append(f"no compute GPU detected (kind={gpu_probe.kind}); CPU path stands")
        return GpuOffloadDecision(
            offload=False,
            would_offload=False,
            reason="GPU_ABSENT",
            edges=edges,
            t_cpu_s=t_cpu_s,
            t_transfer_s=t_transfer_s,
            launch_overhead_s=launch_overhead_s,
            cpu_threshold_s=cpu_threshold_s,
            gpu_kind=gpu_probe.kind,
            notes=tuple(notes),
        )

    environ: Mapping[str, str]
    if env is None:
        import os

        environ = os.environ
    else:
        environ = env
    raw = environ.get(ENV_GPU_OFFLOAD)
    env_on = parse_switch_bool(ENV_GPU_OFFLOAD, raw) if raw is not None else False

    if not env_on:
        notes.append(f"{ENV_GPU_OFFLOAD} is not 'on'; formula result recorded, not executed (default NO-GPU)")
        return GpuOffloadDecision(
            offload=False,
            would_offload=True,
            reason="ENV_DISABLED",
            edges=edges,
            t_cpu_s=t_cpu_s,
            t_transfer_s=t_transfer_s,
            launch_overhead_s=launch_overhead_s,
            cpu_threshold_s=cpu_threshold_s,
            gpu_kind=gpu_probe.kind,
            notes=tuple(notes),
        )

    notes.append("offload recommended: formula passes, compute GPU present, env gate open")
    return GpuOffloadDecision(
        offload=True,
        would_offload=True,
        reason="OFFLOAD",
        edges=edges,
        t_cpu_s=t_cpu_s,
        t_transfer_s=t_transfer_s,
        launch_overhead_s=launch_overhead_s,
        cpu_threshold_s=cpu_threshold_s,
        gpu_kind=gpu_probe.kind,
        notes=tuple(notes),
    )
