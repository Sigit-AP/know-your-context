"""Tests for hardware.gpu_gate: the machine-executable offload formula.

Verdict (GPU-VERDICT.md §4): offload batch analytics to a GPU only if
``edges > 5_000_000`` AND ``T_cpu > 3 × (T_transfer + 20µs launch)`` AND a
compute GPU is actually detected. Default is NO-GPU; the env gate
``KYS_GPU_OFFLOAD`` must be explicitly ``on``.
"""

from __future__ import annotations

import pytest

from know_your_context.hardware.gpu_gate import (
    DEFAULT_PCIE_BANDWIDTH_BPS,
    ENV_GPU_OFFLOAD,
    GPU_LAUNCH_OVERHEAD_S,
    GPU_OFFLOAD_MIN_EDGES,
    GpuGateError,
    GpuOffloadDecision,
    decide_gpu_offload,
    estimate_transfer_s,
)
from know_your_context.hardware.probe import GpuKind, GpuProbe
from know_your_context.ops.switches import SwitchError

_GPU_COMPUTE = GpuProbe(GpuKind.COMPUTE, ("tool:nvidia-smi(/usr/bin/nvidia-smi)",), ())
_GPU_NONE = GpuProbe(GpuKind.NONE, (), ("no compute GPU detected",))
_GPU_VIRTUAL = GpuProbe(GpuKind.VIRTUAL_DISPLAY, ("drm:renderD128(driver=vmwgfx,display-only)",), ())

# Verdict §3.3: 4.4 MB CSR over PCIe 3.0 x16 @ 12 GB/s.
_CSR_BYTES = 4_400_000


class TestTransferEstimate:
    def test_verdict_number_4_4mb_pcie3(self) -> None:
        t = estimate_transfer_s(_CSR_BYTES, DEFAULT_PCIE_BANDWIDTH_BPS)
        assert t == pytest.approx(0.000367, rel=0.01)

    def test_zero_bytes_is_zero_seconds(self) -> None:
        assert estimate_transfer_s(0, DEFAULT_PCIE_BANDWIDTH_BPS) == 0.0

    def test_invalid_inputs_raise_typed(self) -> None:
        with pytest.raises(GpuGateError):
            estimate_transfer_s(-1, DEFAULT_PCIE_BANDWIDTH_BPS)
        with pytest.raises(GpuGateError):
            estimate_transfer_s(1024, 0.0)
        with pytest.raises(GpuGateError):
            estimate_transfer_s(1024, -5.0)


class TestFormulaThresholds:
    def test_edges_below_threshold_never_offload(self) -> None:
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES,  # exactly 5M: NOT above the threshold
            t_cpu_s=10.0,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert decision.offload is False
        assert decision.would_offload is False
        assert decision.reason == "EDGES_BELOW_THRESHOLD"

    def test_edges_just_above_threshold_passes_edge_gate(self) -> None:
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=10.0,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert decision.offload is True
        assert decision.reason == "OFFLOAD"

    def test_cpu_time_at_exact_boundary_does_not_offload(self) -> None:
        transfer = estimate_transfer_s(_CSR_BYTES, DEFAULT_PCIE_BANDWIDTH_BPS)
        boundary = 3.0 * (transfer + GPU_LAUNCH_OVERHEAD_S)
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=boundary,  # strictly-greater is required
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert decision.offload is False
        assert decision.reason == "CPU_TIME_BELOW_THRESHOLD"
        assert decision.cpu_threshold_s == pytest.approx(boundary)

    def test_interactive_query_scale_never_offloads(self) -> None:
        """FTS5 at 45-132 µs is below one H2D transfer — the verdict's core."""
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=132e-6,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert decision.offload is False
        assert decision.reason == "CPU_TIME_BELOW_THRESHOLD"

    def test_pagerank_1m_edge_scale_stays_cpu(self) -> None:
        """Verdict §4: 1M edges at 124 ms total CPU is below the edge gate."""
        decision = decide_gpu_offload(
            edges=1_000_000,
            t_cpu_s=0.124,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert decision.offload is False
        assert decision.reason == "EDGES_BELOW_THRESHOLD"


class TestGpuPresenceGate:
    def test_no_gpu_means_no_offload_even_when_formula_passes(self) -> None:
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=60.0,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_NONE,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert decision.offload is False
        assert decision.reason == "GPU_ABSENT"

    def test_virtual_display_is_not_compute(self) -> None:
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=60.0,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_VIRTUAL,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert decision.offload is False
        assert decision.reason == "GPU_ABSENT"
        assert decision.gpu_kind is GpuKind.VIRTUAL_DISPLAY


class TestEnvGate:
    def _formula_passing(self, env: dict[str, str]) -> GpuOffloadDecision:
        """One call where the formula passes; only the env gate varies."""
        return decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=60.0,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env=env,
        )

    def test_env_unset_defaults_to_no_gpu(self) -> None:
        decision = self._formula_passing(env={})
        assert decision.offload is False
        assert decision.would_offload is True
        assert decision.reason == "ENV_DISABLED"

    def test_env_off_explicit(self) -> None:
        decision = self._formula_passing(env={ENV_GPU_OFFLOAD: "off"})
        assert decision.offload is False
        assert decision.would_offload is True
        assert decision.reason == "ENV_DISABLED"

    def test_env_on_allows_formula_to_decide(self) -> None:
        decision = self._formula_passing(env={ENV_GPU_OFFLOAD: "on"})
        assert decision.offload is True
        assert decision.reason == "OFFLOAD"

    def test_env_invalid_fails_closed(self) -> None:
        with pytest.raises(SwitchError):
            self._formula_passing(env={ENV_GPU_OFFLOAD: "maybe"})

    def test_env_gate_checked_after_formula_for_observability(self) -> None:
        """A disabled gate on a formula-failing input reports the formula, not env."""
        decision = decide_gpu_offload(
            edges=10,
            t_cpu_s=60.0,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env={},
        )
        assert decision.offload is False
        assert decision.would_offload is False
        assert decision.reason == "EDGES_BELOW_THRESHOLD"


class TestInputValidation:
    def test_negative_edges_raise_typed(self) -> None:
        with pytest.raises(GpuGateError) as excinfo:
            decide_gpu_offload(edges=-1, t_cpu_s=1.0, bytes_to_transfer=1, gpu=_GPU_COMPUTE, env={})
        assert excinfo.value.code == "INVALID_INPUT"

    def test_negative_cpu_time_raises_typed(self) -> None:
        with pytest.raises(GpuGateError):
            decide_gpu_offload(edges=1, t_cpu_s=-0.5, bytes_to_transfer=1, gpu=_GPU_COMPUTE, env={})

    def test_non_finite_cpu_time_raises_typed(self) -> None:
        with pytest.raises(GpuGateError):
            decide_gpu_offload(edges=1, t_cpu_s=float("nan"), bytes_to_transfer=1, gpu=_GPU_COMPUTE, env={})

    def test_negative_overhead_raises_typed(self) -> None:
        with pytest.raises(GpuGateError):
            decide_gpu_offload(
                edges=1,
                t_cpu_s=1.0,
                bytes_to_transfer=1,
                launch_overhead_s=-1e-6,
                gpu=_GPU_COMPUTE,
                env={},
            )


class TestDecisionShape:
    def test_decision_carries_measured_inputs(self) -> None:
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=1.0,
            bytes_to_transfer=_CSR_BYTES,
            gpu=_GPU_COMPUTE,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        assert isinstance(decision, GpuOffloadDecision)
        assert decision.edges == GPU_OFFLOAD_MIN_EDGES + 1
        assert decision.t_transfer_s == pytest.approx(0.000367, rel=0.01)
        assert decision.launch_overhead_s == GPU_LAUNCH_OVERHEAD_S
        assert decision.gpu_kind is GpuKind.COMPUTE
        assert isinstance(decision.notes, tuple)

    def test_real_host_gpu_probe_defaults_to_no_offload(self) -> None:
        """On this host (no GPGPU) the gate must say NO even when asked."""
        decision = decide_gpu_offload(
            edges=GPU_OFFLOAD_MIN_EDGES + 1,
            t_cpu_s=60.0,
            bytes_to_transfer=_CSR_BYTES,
            env={ENV_GPU_OFFLOAD: "on"},
        )
        if decision.gpu_kind is not GpuKind.COMPUTE:
            assert decision.offload is False
            assert decision.reason == "GPU_ABSENT"
