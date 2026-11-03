"""Hardware-derived daemon sizing (SUB-135): scale-up only, never down.

The daemon consumes the single cached HardwarePlan (hardware.profile); it
never probes hardware itself. Rules:

* a field left at its historical default is *auto* — derived from the plan
  (max(default, derived), so small hosts keep the historical sizing exactly);
* an explicitly non-default value is honored verbatim;
* kill switches: KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER freezes worker and
  queue sizing, KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING freezes the disk-profile
  group-commit batch;
* a failed probe falls back to the historical recipe, honestly noted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from know_your_context.hardware.probe import DiskKind, DiskProbe, GpuKind, GpuProbe, HardwareProfile, MemoryProbe
from know_your_context.hardware.profile import reset_plan_cache
from know_your_context.serve.daemon_config import (
    DEFAULT_LISTENER_BACKLOG,
    DEFAULT_MAX_AGENTS,
    DaemonConfig,
)

MiB = 1024**2
GiB = 1024**3


def _fake_probe(cores: int, mem_available: int, disk: DiskKind):
    profile = HardwareProfile(
        cpu_count=cores,
        memory=MemoryProbe(total_bytes=128 * GiB, available_bytes=mem_available, note="synthetic"),
        disk=DiskProbe(kind=disk, device="test", rotational=disk is DiskKind.HDD, note="synthetic"),
        gpu=GpuProbe(kind=GpuKind.NONE, devices=(), notes=()),
        notes=(),
    )

    def _probe(path=None, *, refresh=False) -> HardwareProfile:
        return profile

    return _probe


@pytest.fixture()
def fake_hardware(monkeypatch):
    """Install a synthetic probe; returns a setter for the fake host shape."""

    def _install(*, cores: int, mem_available: int, disk: DiskKind = DiskKind.SSD) -> None:
        monkeypatch.setattr(
            "know_your_context.hardware.profile.probe_hardware", _fake_probe(cores, mem_available, disk)
        )
        reset_plan_cache()

    yield _install
    reset_plan_cache()


def _config(tmp_path: Path, **overrides: object) -> DaemonConfig:
    kwargs: dict[str, object] = {"db_path": tmp_path / "soul.sqlite3", "graph_root": tmp_path / "graph"}
    kwargs.update(overrides)
    return DaemonConfig(**kwargs)  # type: ignore[arg-type]


class TestScaleUpOnly:
    def test_small_host_keeps_historical_sizing(self, tmp_path: Path, fake_hardware) -> None:
        """The verdict host shape (2 vCPU / 2.7 GiB) changes nothing structural."""
        fake_hardware(cores=2, mem_available=2700 * MiB)
        config = _config(tmp_path)
        assert config.read_pool_size == 8
        assert config.write_queue_size == 256
        assert config.read_queue_size == 512
        assert config.heavy_queue_size == 16
        assert config.audit_queue_size == 2048
        assert config.listener_backlog == DEFAULT_LISTENER_BACKLOG == 128
        assert config.max_agents == DEFAULT_MAX_AGENTS == 50

    def test_big_host_scales_up(self, tmp_path: Path, fake_hardware) -> None:
        fake_hardware(cores=32, mem_available=64 * GiB)
        config = _config(tmp_path)
        assert config.read_pool_size == 32  # base_workers, above the historical floor
        assert config.write_queue_size == 256 * 16  # queue_scale 16 (64 GiB)
        assert config.read_queue_size == 512 * 16
        assert config.heavy_queue_size == 16 * 16
        assert config.audit_queue_size == 256 * 32  # 256 audit slots per read worker
        assert config.listener_backlog == 128 * 16
        assert config.max_agents == 256  # HARD_MAX_AGENTS

    def test_read_pool_hard_cap(self, tmp_path: Path, fake_hardware) -> None:
        fake_hardware(cores=512, mem_available=512 * GiB)
        config = _config(tmp_path)
        assert config.read_pool_size == 64  # HARD_MAX_READ_POOL

    def test_explicit_values_are_honored(self, tmp_path: Path, fake_hardware) -> None:
        fake_hardware(cores=32, mem_available=64 * GiB)
        config = _config(tmp_path, read_pool_size=4, write_queue_size=99, group_commit_batch=5)
        assert config.read_pool_size == 4
        assert config.write_queue_size == 99
        assert config.group_commit_batch == 5

    def test_audit_tracks_derived_pool(self, tmp_path: Path, fake_hardware) -> None:
        """audit_queue_size >= 2 * read_pool_size must survive derivation."""
        fake_hardware(cores=48, mem_available=256 * GiB)
        config = _config(tmp_path)
        assert config.audit_queue_size >= 2 * config.read_pool_size


class TestDiskProfileBatch:
    def test_ssd_batch(self, tmp_path: Path, fake_hardware) -> None:
        fake_hardware(cores=2, mem_available=2700 * MiB, disk=DiskKind.SSD)
        assert _config(tmp_path).group_commit_batch == 32

    def test_hdd_batch(self, tmp_path: Path, fake_hardware) -> None:
        fake_hardware(cores=2, mem_available=2700 * MiB, disk=DiskKind.HDD)
        assert _config(tmp_path).group_commit_batch == 128


class TestKillSwitches:
    def test_autoscaler_kill_switch_freezes_pool_and_queues(
        self, tmp_path: Path, fake_hardware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_hardware(cores=32, mem_available=64 * GiB)
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
        config = _config(tmp_path)
        assert config.read_pool_size == 8
        assert config.write_queue_size == 256
        assert config.read_queue_size == 512
        assert config.heavy_queue_size == 16
        assert config.audit_queue_size == 2048
        assert config.listener_backlog == 128
        assert config.max_agents == 50
        assert config.group_commit_batch == 32  # disk profile wiring is a separate switch
        assert any("autoscaler" in note for note in config.hardware_notes)

    def test_tuning_kill_switch_freezes_batch(
        self, tmp_path: Path, fake_hardware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_hardware(cores=32, mem_available=64 * GiB)
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        config = _config(tmp_path)
        assert config.group_commit_batch == 8  # historical
        assert config.read_pool_size == 32  # autoscaler wiring unaffected

    def test_both_switches_give_byte_identical_historical(
        self, tmp_path: Path, fake_hardware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_hardware(cores=64, mem_available=256 * GiB)
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        config = _config(tmp_path)
        assert (config.read_pool_size, config.write_queue_size, config.read_queue_size) == (8, 256, 512)
        assert (config.heavy_queue_size, config.audit_queue_size, config.group_commit_batch) == (16, 2048, 8)
        assert (config.listener_backlog, config.max_agents) == (128, 50)


class TestFailSafeFallback:
    def test_probe_failure_keeps_historical_recipe(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(path=None, *, refresh=False):
            raise RuntimeError("synthetic probe failure")

        monkeypatch.setattr("know_your_context.hardware.profile.probe_hardware", _boom)
        reset_plan_cache()
        config = _config(tmp_path)
        reset_plan_cache()
        assert (config.read_pool_size, config.write_queue_size, config.group_commit_batch) == (8, 256, 8)
        assert config.listener_backlog == 128
        assert any("unavailable" in note or "fallback" in note for note in config.hardware_notes)

    def test_notes_announce_derivation(self, tmp_path: Path, fake_hardware) -> None:
        fake_hardware(cores=32, mem_available=64 * GiB)
        config = _config(tmp_path)
        assert config.hardware_notes  # every derivation announces itself
        assert any("read_pool_size" in note for note in config.hardware_notes)
