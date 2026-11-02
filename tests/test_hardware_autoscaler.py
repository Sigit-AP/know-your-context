"""Tests for hardware.autoscaler: adaptive scale-UP, never throttle-down.

Operator direction (W15 correction): the product must USE the hardware
fully — workers follow demand up to the core count (bounded only by a RAM
allowance so the host survives — a guardrail, not a courtesy). Under
saturation the advisor holds at FULL core count; overload is absorbed by
typed backpressure + fairness at the edges, never by shrinking the pool
below what the machine can do. Nothing is ever niced or deprioritized.
"""

from __future__ import annotations

import pytest

from know_your_context.hardware.autoscaler import (
    ENV_DISABLE_HW_AUTOSCALER,
    Autoscaler,
    ParallelismAdvice,
    autoscaler_disabled,
    recommended_workers,
)
from know_your_context.ops.switches import SwitchError

GiB = 1024**3
MiB = 1024**2


def _load(value: float):
    def _reader() -> tuple[float, float, float]:
        return (value, value, value)

    return _reader


class TestRecommendedWorkers:
    def test_workers_follow_cores_when_ram_allows(self) -> None:
        assert recommended_workers(cores=8, mem_available_bytes=16 * GiB, mem_per_worker_bytes=512 * MiB) == 8

    def test_ram_allowance_bounds_workers(self) -> None:
        """2.7 GB free / 512 MiB per worker = 5 workers even with 16 cores."""
        assert recommended_workers(cores=16, mem_available_bytes=2700 * MiB, mem_per_worker_bytes=512 * MiB) == 5

    def test_verdict_host_gets_two_workers(self) -> None:
        """Verdict §5.5: 2 vCPU / 2.7 GB → 2 workers."""
        assert recommended_workers(cores=2, mem_available_bytes=2700 * MiB, mem_per_worker_bytes=512 * MiB) == 2

    def test_never_below_one(self) -> None:
        assert recommended_workers(cores=1, mem_available_bytes=1, mem_per_worker_bytes=512 * MiB) == 1

    def test_unknown_ram_uses_cores(self) -> None:
        """MemAvailable unknown (non-Linux) → fail-open to core count."""
        assert recommended_workers(cores=4, mem_available_bytes=0, mem_per_worker_bytes=512 * MiB) == 4

    def test_invalid_inputs_raise_typed(self) -> None:
        with pytest.raises(SwitchError):
            recommended_workers(cores=0, mem_available_bytes=GiB, mem_per_worker_bytes=MiB)
        with pytest.raises(SwitchError):
            recommended_workers(cores=1, mem_available_bytes=-1, mem_per_worker_bytes=MiB)
        with pytest.raises(SwitchError):
            recommended_workers(cores=1, mem_available_bytes=GiB, mem_per_worker_bytes=0)


class TestScaleWithDemand:
    def test_idle_host_sits_at_the_floor(self) -> None:
        scaler = Autoscaler(cores=4, mem_available_bytes=16 * GiB, load_reader=_load(0.2), min_workers=1)
        assert scaler.advise().workers == 1

    def test_rising_load_scales_up_linearly(self) -> None:
        scaler = Autoscaler(cores=4, mem_available_bytes=16 * GiB, load_reader=_load(2.2), min_workers=1)
        advice = scaler.advise()
        assert advice.workers == 3  # ceil(2.2) workers on demand
        assert advice.load1 == 2.2

    def test_saturated_host_uses_full_cores(self) -> None:
        """Padat = full hardware. Never below cores at high load."""
        scaler = Autoscaler(cores=4, mem_available_bytes=16 * GiB, load_reader=_load(64.0), min_workers=1)
        advice = scaler.advise()
        assert advice.workers == 4
        assert any("saturat" in note or "backpressure" in note for note in advice.notes)

    def test_never_exceeds_core_cap(self) -> None:
        scaler = Autoscaler(cores=2, mem_available_bytes=64 * GiB, load_reader=_load(500.0), min_workers=1)
        assert scaler.advise().workers == 2

    def test_ram_allowance_caps_full_scale(self) -> None:
        scaler = Autoscaler(cores=16, mem_available_bytes=2700 * MiB, load_reader=_load(99.0), min_workers=1)
        assert scaler.advise().workers == 5

    def test_drill_load_sweep_scales_up_and_down_with_demand(self) -> None:
        """Drill: load 0→200%→0; workers track demand, cap at cores, floor at min."""
        state = {"load": 0.0}
        scaler = Autoscaler(
            cores=2,
            mem_available_bytes=16 * GiB,
            load_reader=lambda: (state["load"], 0, 0),
            min_workers=1,
        )
        seen: list[int] = []
        for load in (0.0, 0.5, 1.2, 2.0, 4.0, 1.0, 0.2, 0.0):
            state["load"] = load
            seen.append(scaler.advise().workers)
        assert seen == [1, 1, 2, 2, 2, 1, 1, 1]
        assert max(seen) == 2  # cap = cores
        assert min(seen) == 1  # floor

    def test_unknown_load_fails_open_to_full_parallelism(self) -> None:
        def _broken() -> tuple[float, float, float]:
            raise OSError("no /proc/loadavg")

        scaler = Autoscaler(cores=4, mem_available_bytes=16 * GiB, load_reader=_broken)
        advice = scaler.advise()
        assert advice.workers == 4  # no load info → no restriction
        assert any("unknown" in note for note in advice.notes)

    def test_no_nice_no_repriority(self) -> None:
        """The advisor must not deprioritize anything: no nice at all."""
        scaler = Autoscaler(cores=2, mem_available_bytes=GiB)
        assert not hasattr(scaler, "nice")
        assert not hasattr(scaler, "nice_delta")


class TestKillSwitchAndKnobs:
    def test_disabled_returns_static_default(self) -> None:
        scaler = Autoscaler(cores=8, mem_available_bytes=16 * GiB, enabled=False, static_workers=2)
        advice = scaler.advise()
        assert advice.workers == 2
        assert any("disabled" in note for note in advice.notes)

    def test_autoscaler_disabled_helper_strict(self) -> None:
        assert autoscaler_disabled({}) is False
        assert autoscaler_disabled({ENV_DISABLE_HW_AUTOSCALER: "on"}) is True
        with pytest.raises(SwitchError):
            autoscaler_disabled({ENV_DISABLE_HW_AUTOSCALER: "perhaps"})

    def test_from_env_uses_knobs(self) -> None:
        scaler = Autoscaler.from_env(
            env={"KNOW_YOUR_CONTEXT_HW_MEM_PER_WORKER_MIB": "128"},
            cores=8,
            mem_available_bytes=16 * GiB,
            load_reader=_load(99.0),
        )
        # 16 GiB / 128 MiB = 128 allowance → saturated load uses all 8 cores.
        assert scaler.advise().workers == 8

    def test_from_env_min_workers_knob(self) -> None:
        scaler = Autoscaler.from_env(
            env={"KNOW_YOUR_CONTEXT_HW_MIN_WORKERS": "2"},
            cores=8,
            mem_available_bytes=16 * GiB,
            load_reader=_load(0.0),
        )
        assert scaler.advise().workers == 2

    def test_from_env_kill_switch_disables(self) -> None:
        scaler = Autoscaler.from_env(
            env={ENV_DISABLE_HW_AUTOSCALER: "1"},
            cores=8,
            mem_available_bytes=16 * GiB,
        )
        advice = scaler.advise()
        assert advice.workers == 1  # static safe default
        assert any("disabled" in note for note in advice.notes)

    def test_from_env_invalid_knob_fails_closed(self) -> None:
        with pytest.raises(SwitchError):
            Autoscaler.from_env(env={"KNOW_YOUR_CONTEXT_HW_MIN_WORKERS": "many"})

    def test_real_host_advice_never_raises(self) -> None:
        advice = Autoscaler.from_env(env={}).advise()
        assert isinstance(advice, ParallelismAdvice)
        assert advice.workers >= 1
