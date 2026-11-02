"""Tests for the hardware registry wiring: knobs, kill switches, exports.

The hardware layer is governed by the same registries as every other
optional mechanism (EXPERT-THINKING: every mechanism individually killable
and announcing itself). These tests pin the wiring, not the math.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import know_your_context.hardware as hw
from know_your_context.hardware import gpu_gate
from know_your_context.ops.switches import (
    CONFIG_KNOBS,
    KILL_SWITCHES,
    SwitchError,
    describe_knobs,
    describe_switches,
    load_knobs,
    load_switches,
)

_KNOB_NAMES = {knob.name for knob in CONFIG_KNOBS}
_SWITCH_NAMES = {switch.name for switch in KILL_SWITCHES}


class TestKnobRegistration:
    @pytest.mark.parametrize(
        "name",
        [
            "storage.mmap_enabled",
            "storage.mmap_max_bytes",
            "storage.synchronous_normal",
            "storage.page_size",
            "storage.aggressive_cache",
            "storage.cache_max_kib",
            "hw.autoscale_enabled",
            "hw.mem_per_worker_mib",
            "hw.min_workers",
            "hw.gpu_offload",
        ],
    )
    def test_knob_registered(self, name: str) -> None:
        assert name in _KNOB_NAMES

    def test_gpu_knob_env_matches_gate_constant(self) -> None:
        """The registry knob and the gate read the same env var (no drift)."""
        knob = next(knob for knob in CONFIG_KNOBS if knob.name == "hw.gpu_offload")
        assert knob.env == gpu_gate.ENV_GPU_OFFLOAD == "KYS_GPU_OFFLOAD"
        assert knob.default is False  # default NO-GPU

    def test_storage_knob_defaults_are_historical(self) -> None:
        board = load_knobs(env={})
        assert board.value("storage.mmap_enabled") is True
        assert board.value("storage.synchronous_normal") is False
        assert board.value("storage.page_size") == 0
        assert board.value("storage.mmap_max_bytes") == 1073741824

    def test_autoscaler_knob_defaults(self) -> None:
        board = load_knobs(env={})
        assert board.value("hw.autoscale_enabled") is True
        assert board.value("hw.mem_per_worker_mib") == 512
        assert board.value("hw.min_workers") == 1
        assert board.value("storage.aggressive_cache") is True
        assert board.value("storage.cache_max_kib") == 4194304

    def test_knob_bounds_bite(self) -> None:
        with pytest.raises(SwitchError):
            load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_MMAP_MAX_BYTES": "-1"})
        with pytest.raises(SwitchError):
            load_knobs(env={"KNOW_YOUR_CONTEXT_STORAGE_PAGE_SIZE": "131072"})
        with pytest.raises(SwitchError):
            load_knobs(env={"KNOW_YOUR_CONTEXT_HW_MIN_WORKERS": "0"})

    def test_describe_knobs_lists_hardware_family(self) -> None:
        text = describe_knobs()
        assert "storage.mmap_enabled" in text
        assert "hw.gpu_offload" in text
        assert "hw.autoscale_enabled" in text


class TestKillSwitchRegistration:
    def test_switches_registered(self) -> None:
        assert "storage.hw_tuning" in _SWITCH_NAMES
        assert "hw.autoscaler" in _SWITCH_NAMES

    def test_switch_board_accessors(self) -> None:
        board = load_switches(env={})
        assert board.hw_tuning_disabled() is False
        assert board.hw_autoscaler_disabled() is False
        board = load_switches(
            env={
                "KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING": "1",
                "KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER": "true",
            }
        )
        assert board.hw_tuning_disabled() is True
        assert board.hw_autoscaler_disabled() is True

    def test_active_switch_announces_itself(self) -> None:
        board = load_switches(env={"KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING": "1"})
        announcements = board.announcements()
        assert any("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING" in note for note in announcements)

    def test_describe_switches_lists_hardware(self) -> None:
        text = describe_switches()
        assert "storage.hw_tuning" in text
        assert "hw.autoscaler" in text


class TestSingleSourceOfTruth:
    """Operator direction #2: one hardware plan consumed by all subsystems."""

    def test_hardware_plan_aggregates_probe_storage_and_parallelism(self, tmp_path: Path) -> None:
        from know_your_context.hardware.profile import HardwarePlan, hardware_plan

        plan = hardware_plan(tmp_path / "store.db", env={})
        assert isinstance(plan, HardwarePlan)
        assert plan.cores >= 1
        assert plan.storage.profile.kind is not None
        assert plan.base_workers() >= 1
        assert plan.autoscale_enabled is True

    def test_plan_is_cached_per_path(self, tmp_path: Path) -> None:
        from know_your_context.hardware.profile import hardware_plan

        assert hardware_plan(tmp_path) is hardware_plan(tmp_path)
        assert hardware_plan(tmp_path / "a") is not hardware_plan(tmp_path / "b")

    def test_env_bypasses_cache(self, tmp_path: Path) -> None:
        from know_your_context.hardware.profile import hardware_plan

        plan = hardware_plan(tmp_path, env={"KNOW_YOUR_CONTEXT_HW_MEM_PER_WORKER_MIB": "128"})
        assert plan.mem_per_worker_bytes == 128 * 1024**2

    def test_store_consumes_the_shared_plan(self, tmp_path: Path) -> None:
        """The store's pragma recipe comes from hardware_plan, not a private probe."""
        from know_your_context.hardware.profile import hardware_plan, reset_plan_cache
        from know_your_context.store import EventStore

        reset_plan_cache()
        db = tmp_path / "store.db"
        store = EventStore(db)
        try:
            plan = hardware_plan(db)
            assert store.applied_tuning.mmap_bytes == plan.storage.mmap_bytes
            assert store.applied_tuning.cache_kib == plan.storage.cache_kib
        finally:
            store.close()

    def test_autoscaler_consumes_the_same_knobs(self) -> None:
        from know_your_context.hardware.autoscaler import Autoscaler

        scaler = Autoscaler.from_env(env={"KNOW_YOUR_CONTEXT_HW_MEM_PER_WORKER_MIB": "128"})
        assert scaler.mem_per_worker_bytes == 128 * 1024**2


class TestPackageExports:
    def test_probe_surface_exported(self) -> None:
        for name in (
            "DiskKind",
            "GpuKind",
            "HardwareProfile",
            "ProbeError",
            "probe_hardware",
            "reset_probe_cache",
        ):
            assert hasattr(hw, name)

    def test_submodules_importable(self) -> None:
        from know_your_context.hardware import autoscaler, probe, profile, tuning  # noqa: F401

    def test_no_new_dependencies(self) -> None:
        """The hardware layer is stdlib-only (deployment on unknown hosts)."""
        import ast
        from pathlib import Path

        allowed = {
            "os",
            "sys",
            "re",
            "math",
            "shutil",
            "enum",
            "dataclasses",
            "pathlib",
            "collections",
            "typing",
            "__future__",
        }
        for module in Path(hw.__file__).parent.glob("*.py"):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        assert root in allowed | {"know_your_context"}, f"{module.name} imports {alias.name}"
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    root = node.module.split(".")[0]
                    assert root in allowed | {"know_your_context"}, f"{module.name} imports {node.module}"
