"""Tests for hardware.probe: read-only, fail-open hardware detection (W15).

Probe functions never guess: absent data yields UNKNOWN/NONE with an honest
note; ``strict=True`` turns malformed data into typed ProbeError instead.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from know_your_context.hardware.probe import (
    DiskKind,
    DiskProbe,
    GpuKind,
    GpuProbe,
    HardwareProfile,
    MemoryProbe,
    ProbeError,
    probe_cpu_count,
    probe_disk_kind,
    probe_gpu,
    probe_hardware,
    probe_memory,
    reset_probe_cache,
)


def _fake_sysfs_for(tmp_path: Path, rotational: str) -> tuple[Path, Path]:
    """Build a fake /sys tree whose dev/block entry matches tmp_path's device."""
    probe_file = tmp_path / "data" / "store.sqlite3"
    probe_file.parent.mkdir(parents=True)
    probe_file.write_bytes(b"x")
    dev = os.stat(probe_file).st_dev
    major, minor = os.major(dev), os.minor(dev)
    sys_root = tmp_path / "sys"
    disk_dir = sys_root / "devices" / "pci0000:00" / "block" / "sda"
    part_dir = disk_dir / "sda1"
    (part_dir / "queue").mkdir(parents=True)
    (disk_dir / "queue").mkdir(parents=True)
    (disk_dir / "queue" / "rotational").write_text(rotational)
    link_dir = sys_root / "dev" / "block"
    link_dir.mkdir(parents=True)
    os.symlink(part_dir, link_dir / f"{major}:{minor}")
    return probe_file, sys_root


class TestDiskProbe:
    def test_rotational_zero_means_ssd(self, tmp_path: Path) -> None:
        probe_file, sys_root = _fake_sysfs_for(tmp_path, "0\n")
        result = probe_disk_kind(probe_file, sys_root=sys_root)
        assert result.kind is DiskKind.SSD
        assert result.rotational is False
        assert result.device == "sda"

    def test_rotational_one_means_hdd(self, tmp_path: Path) -> None:
        probe_file, sys_root = _fake_sysfs_for(tmp_path, "1\n")
        result = probe_disk_kind(probe_file, sys_root=sys_root)
        assert result.kind is DiskKind.HDD
        assert result.rotational is True

    def test_missing_sysfs_is_unknown_not_error(self, tmp_path: Path) -> None:
        result = probe_disk_kind(tmp_path, sys_root=tmp_path / "no-such-sys")
        assert result.kind is DiskKind.UNKNOWN
        assert result.rotational is None
        assert "sysfs" in result.note

    def test_missing_rotational_file_is_unknown(self, tmp_path: Path) -> None:
        probe_file, sys_root = _fake_sysfs_for(tmp_path, "0\n")
        (sys_root / "devices" / "pci0000:00" / "block" / "sda" / "queue" / "rotational").unlink()
        result = probe_disk_kind(probe_file, sys_root=sys_root)
        assert result.kind is DiskKind.UNKNOWN

    def test_malformed_rotational_fails_open_by_default(self, tmp_path: Path) -> None:
        probe_file, sys_root = _fake_sysfs_for(tmp_path, "banana\n")
        result = probe_disk_kind(probe_file, sys_root=sys_root)
        assert result.kind is DiskKind.UNKNOWN
        assert "banana" in result.note

    def test_malformed_rotational_raises_in_strict_mode(self, tmp_path: Path) -> None:
        probe_file, sys_root = _fake_sysfs_for(tmp_path, "banana\n")
        with pytest.raises(ProbeError) as excinfo:
            probe_disk_kind(probe_file, sys_root=sys_root, strict=True)
        assert excinfo.value.code == "MALFORMED_ROTATIONAL"

    def test_ghost_file_resolves_via_existing_ancestor(self, tmp_path: Path) -> None:
        """A not-yet-created db inherits its directory's block device."""
        probe_file, sys_root = _fake_sysfs_for(tmp_path, "0\n")
        ghost = probe_file.parent / "fresh.db"
        assert not ghost.exists()
        result = probe_disk_kind(ghost, sys_root=sys_root)
        assert result.kind is DiskKind.SSD
        assert "ancestor" in result.note

    def test_missing_path_raises_typed_in_strict_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _always_fail(path: object) -> os.stat_result:
            raise OSError("simulated total stat failure")

        monkeypatch.setattr(os, "stat", _always_fail)
        with pytest.raises(ProbeError) as excinfo:
            probe_disk_kind(tmp_path / "ghost", sys_root=tmp_path, strict=True)
        assert excinfo.value.code == "STAT_FAILED"

    def test_missing_path_fails_open_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _always_fail(path: object) -> os.stat_result:
            raise OSError("simulated total stat failure")

        monkeypatch.setattr(os, "stat", _always_fail)
        result = probe_disk_kind(tmp_path / "ghost", sys_root=tmp_path)
        assert result.kind is DiskKind.UNKNOWN

    def test_real_host_probe_returns_a_kind_and_never_raises(self) -> None:
        result = probe_disk_kind("/")
        assert result.kind in (DiskKind.SSD, DiskKind.HDD, DiskKind.UNKNOWN)
        assert isinstance(result.note, str)

    def test_probe_is_read_only(self, tmp_path: Path) -> None:
        probe_file, sys_root = _fake_sysfs_for(tmp_path, "0\n")
        before = {p: p.read_bytes() for p in sys_root.rglob("*") if p.is_file()}
        probe_disk_kind(probe_file, sys_root=sys_root)
        after = {p: p.read_bytes() for p in sys_root.rglob("*") if p.is_file()}
        assert before == after


class TestMemoryProbe:
    def _meminfo(self, tmp_path: Path, text: str) -> Path:
        path = tmp_path / "meminfo"
        path.write_text(text)
        return path

    def test_parses_total_and_available(self, tmp_path: Path) -> None:
        path = self._meminfo(tmp_path, "MemTotal:       4005184 kB\nMemAvailable:   2700000 kB\n")
        result = probe_memory(meminfo_path=path)
        assert result.total_bytes == 4005184 * 1024
        assert result.available_bytes == 2700000 * 1024

    def test_missing_file_fails_open_with_zeros(self, tmp_path: Path) -> None:
        result = probe_memory(meminfo_path=tmp_path / "absent")
        assert result.total_bytes == 0
        assert result.available_bytes == 0
        assert "absent" in result.note

    def test_missing_file_raises_in_strict_mode(self, tmp_path: Path) -> None:
        with pytest.raises(ProbeError) as excinfo:
            probe_memory(meminfo_path=tmp_path / "absent", strict=True)
        assert excinfo.value.code == "MEMINFO_ABSENT"

    def test_missing_memavailable_is_honest_zero(self, tmp_path: Path) -> None:
        path = self._meminfo(tmp_path, "MemTotal:  1024 kB\nMemFree: 512 kB\n")
        result = probe_memory(meminfo_path=path)
        assert result.total_bytes == 1024 * 1024
        assert result.available_bytes == 0
        assert "MemAvailable" in result.note

    def test_missing_memavailable_raises_in_strict_mode(self, tmp_path: Path) -> None:
        path = self._meminfo(tmp_path, "MemTotal:  1024 kB\n")
        with pytest.raises(ProbeError) as excinfo:
            probe_memory(meminfo_path=path, strict=True)
        assert excinfo.value.code == "MISSING_FIELD"

    def test_malformed_line_raises_in_strict_mode(self, tmp_path: Path) -> None:
        path = self._meminfo(tmp_path, "MemTotal: not-a-number kB\n")
        with pytest.raises(ProbeError) as excinfo:
            probe_memory(meminfo_path=path, strict=True)
        assert excinfo.value.code == "MALFORMED_MEMINFO"

    def test_real_host_meminfo_reports_positive_total(self) -> None:
        result = probe_memory()
        if Path("/proc/meminfo").exists():
            assert result.total_bytes > 0


class TestCpuProbe:
    def test_cpu_count_is_at_least_one(self) -> None:
        assert probe_cpu_count() >= 1

    def test_cpu_count_matches_affinity_when_available(self) -> None:
        if hasattr(os, "sched_getaffinity"):
            assert probe_cpu_count() == len(os.sched_getaffinity(0))

    def test_cpu_count_respects_injected_affinity(self) -> None:
        result = probe_cpu_count(affinity=lambda: {0, 1, 2, 3}, cpu_count=lambda: 8)
        assert result == 4

    def test_cpu_count_falls_back_to_cpu_count(self) -> None:
        def _no_affinity() -> set[int]:
            raise AttributeError("no affinity on this platform")

        assert probe_cpu_count(affinity=_no_affinity, cpu_count=lambda: 6) == 6

    def test_cpu_count_never_returns_zero(self) -> None:
        assert probe_cpu_count(affinity=lambda: set(), cpu_count=lambda: 0) == 1


class TestGpuProbe:
    def _no_tool(self, name: str) -> None:
        return None

    def test_no_tools_no_devices_means_none(self, tmp_path: Path) -> None:
        result = probe_gpu(
            which=self._no_tool,
            sys_root=tmp_path / "sys",
            opencl_root=tmp_path / "etc" / "OpenCL" / "vendors",
            vulkan_root=tmp_path / "usr" / "share" / "vulkan" / "icd.d",
        )
        assert result.kind is GpuKind.NONE
        assert result.devices == ()

    def test_nvidia_smi_in_path_means_compute(self, tmp_path: Path) -> None:
        result = probe_gpu(
            which=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
            sys_root=tmp_path / "sys",
            opencl_root=tmp_path / "none1",
            vulkan_root=tmp_path / "none2",
        )
        assert result.kind is GpuKind.COMPUTE
        assert any("nvidia-smi" in d for d in result.devices)

    def test_opencl_vendor_icd_means_compute(self, tmp_path: Path) -> None:
        opencl = tmp_path / "vendors"
        opencl.mkdir()
        (opencl / "nvidia.icd").write_text("libnvidia-opencl.so")
        result = probe_gpu(
            which=self._no_tool,
            sys_root=tmp_path / "sys",
            opencl_root=opencl,
            vulkan_root=tmp_path / "none",
        )
        assert result.kind is GpuKind.COMPUTE

    def test_vulkan_icd_means_compute(self, tmp_path: Path) -> None:
        vk = tmp_path / "icd.d"
        vk.mkdir()
        (vk / "radeon_icd.json").write_text("{}")
        result = probe_gpu(
            which=self._no_tool,
            sys_root=tmp_path / "sys",
            opencl_root=tmp_path / "none",
            vulkan_root=vk,
        )
        assert result.kind is GpuKind.COMPUTE

    def _drm(self, tmp_path: Path, driver: str, node: str = "renderD128") -> Path:
        dev = tmp_path / "sys" / "class" / "drm" / node / "device"
        dev.mkdir(parents=True)
        (dev / "uevent").write_text(f"DRIVER={driver}\n")
        return tmp_path / "sys"

    def test_vmwgfx_render_node_is_virtual_display_not_compute(self, tmp_path: Path) -> None:
        sys_root = self._drm(tmp_path, "vmwgfx")
        result = probe_gpu(
            which=self._no_tool,
            sys_root=sys_root,
            opencl_root=tmp_path / "none1",
            vulkan_root=tmp_path / "none2",
        )
        assert result.kind is GpuKind.VIRTUAL_DISPLAY
        assert any("vmwgfx" in d for d in result.devices)

    def test_virtio_gpu_is_virtual_display(self, tmp_path: Path) -> None:
        sys_root = self._drm(tmp_path, "virtio-gpu")
        result = probe_gpu(
            which=self._no_tool,
            sys_root=sys_root,
            opencl_root=tmp_path / "none1",
            vulkan_root=tmp_path / "none2",
        )
        assert result.kind is GpuKind.VIRTUAL_DISPLAY

    def test_real_driver_render_node_means_compute(self, tmp_path: Path) -> None:
        sys_root = self._drm(tmp_path, "nvidia")
        result = probe_gpu(
            which=self._no_tool,
            sys_root=sys_root,
            opencl_root=tmp_path / "none1",
            vulkan_root=tmp_path / "none2",
        )
        assert result.kind is GpuKind.COMPUTE

    def test_virtual_display_never_promoted_to_compute(self, tmp_path: Path) -> None:
        """A display adapter plus no compute signal stays non-compute."""
        sys_root = self._drm(tmp_path, "bochs-drm")
        result = probe_gpu(
            which=self._no_tool,
            sys_root=sys_root,
            opencl_root=tmp_path / "none1",
            vulkan_root=tmp_path / "none2",
        )
        assert result.kind is GpuKind.VIRTUAL_DISPLAY

    def test_malformed_uevent_fails_open(self, tmp_path: Path) -> None:
        sys_root = self._drm(tmp_path, "")
        (next(sys_root.rglob("uevent"))).write_text("garbage without driver\n")
        result = probe_gpu(
            which=self._no_tool,
            sys_root=sys_root,
            opencl_root=tmp_path / "none1",
            vulkan_root=tmp_path / "none2",
        )
        assert result.kind is GpuKind.NONE
        assert any("uevent" in n for n in result.notes)

    def test_real_host_probe_never_raises(self) -> None:
        result = probe_gpu(which=shutil.which)
        assert result.kind in (GpuKind.NONE, GpuKind.VIRTUAL_DISPLAY, GpuKind.COMPUTE)


class TestProbeHardwareCache:
    def setup_method(self) -> None:
        reset_probe_cache()

    def test_profile_aggregates_all_probes(self, tmp_path: Path) -> None:
        profile = probe_hardware(tmp_path)
        assert isinstance(profile, HardwareProfile)
        assert profile.cpu_count >= 1
        assert isinstance(profile.memory, MemoryProbe)
        assert isinstance(profile.disk, DiskProbe)
        assert isinstance(profile.gpu, GpuProbe)

    def test_result_is_cached(self, tmp_path: Path) -> None:
        first = probe_hardware(tmp_path)
        second = probe_hardware(tmp_path)
        assert first is second

    def test_refresh_bypasses_cache(self, tmp_path: Path) -> None:
        first = probe_hardware(tmp_path)
        second = probe_hardware(tmp_path, refresh=True)
        assert first is not second

    def test_reset_clears_cache(self, tmp_path: Path) -> None:
        first = probe_hardware(tmp_path)
        reset_probe_cache()
        assert probe_hardware(tmp_path) is not first

    def test_cache_is_keyed_by_path(self, tmp_path: Path) -> None:
        a = probe_hardware(tmp_path)
        b = probe_hardware(tmp_path / "other")
        assert a is not b
