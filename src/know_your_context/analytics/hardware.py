"""Hardware integration for the analytics batch kernels (SUB-135).

One place where centrality/communities read the single HardwarePlan:

* :func:`analytics_workers` — worker count for independent batch compute
  (per-domain suites, per-community cohesion), following the plan's
  cores/RAM advice. Kill switch ``KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER``
  or a failed probe → 1 (the historical sequential path).
* :func:`consult_gpu_offload` — every batch's offload decision goes through
  the measured verdict formula (:func:`hardware.gpu_gate.decide_gpu_offload`)
  with a MEASURED CPU time and an honest CSR transfer estimate. There is no
  GPU kernel (GPU-VERDICT); the decision is telemetry for the optional
  observer, and the CPU path always stands unless a kernel lands.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from know_your_context.hardware.gpu_gate import GpuOffloadDecision, decide_gpu_offload

__all__ = ["analytics_workers", "consult_gpu_offload", "estimate_csr_bytes"]


def analytics_workers() -> int:
    """Workers for independent analytics batch compute, from the plan.

    Fail-open shape follows the operator direction: detection failure or the
    kill switch means the HISTORICAL sequential path (1) — analytics is
    correctness-critical, so an unknown machine gets the proven path.
    """
    try:
        from know_your_context.hardware.profile import hardware_plan
        from know_your_context.ops.switches import load_switches

        if load_switches().hw_autoscaler_disabled():
            return 1
        return max(1, hardware_plan(None).base_workers())
    except Exception:  # noqa: BLE001 - analytics must never break on detection
        return 1


def estimate_csr_bytes(nodes: int, edges: int) -> int:
    """Honest CSR transfer estimate: int64 index + source arrays."""
    if nodes < 0 or edges < 0:
        raise ValueError(f"nodes/edges must be >= 0, got {nodes}/{edges}")
    return 8 * (nodes + 1 + 2 * edges)


def consult_gpu_offload(
    *,
    nodes: int,
    edges: int,
    started: float,
    observer: Callable[[GpuOffloadDecision], None] | None,
) -> GpuOffloadDecision | None:
    """Evaluate the verdict gate for the batch that just ran on CPU.

    ``started`` is the ``time.perf_counter()`` taken before the CPU compute —
    the gate's T_cpu is MEASURED, never assumed. The decision is reported to
    ``observer`` (tests/telemetry); nothing is offloaded without a kernel.
    A gate failure is swallowed honestly (returns None) — analytics output
    must never depend on the gate.
    """
    try:
        decision = decide_gpu_offload(
            edges=edges,
            t_cpu_s=time.perf_counter() - started,
            bytes_to_transfer=estimate_csr_bytes(nodes, edges),
        )
    except Exception:  # noqa: BLE001 - the gate is telemetry, not a failure source
        return None
    if observer is not None:
        observer(decision)
    return decision
