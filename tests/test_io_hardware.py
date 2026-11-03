"""Disk-profile I/O buffers for backup / projection / ops.migrate (SUB-135).

The buffer comes from the single HardwarePlan (``hardware.profile
.io_buffer_bytes``): SSD/UNKNOWN keep the historical 1 MiB, HDD scales to
4 MiB — scale-up only. Kill switch KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING or a
failed probe restores the historical buffer; digests and artifact bytes are
identical either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from know_your_context.backup import _sha256 as backup_sha256
from know_your_context.hardware.probe import DiskKind, DiskProbe, GpuKind, GpuProbe, HardwareProfile, MemoryProbe
from know_your_context.hardware.profile import HISTORICAL_IO_BUFFER_BYTES, io_buffer_bytes, reset_plan_cache
from know_your_context.ops.migrate import _sha256 as migrate_sha256
from know_your_context.projection import _artifact_buffering


def _fake_disk(monkeypatch: pytest.MonkeyPatch, kind: DiskKind) -> None:
    profile = HardwareProfile(
        cpu_count=4,
        memory=MemoryProbe(total_bytes=64 * 1024**3, available_bytes=16 * 1024**3, note="synthetic"),
        disk=DiskProbe(kind=kind, device="t", rotational=kind is DiskKind.HDD, note="synthetic"),
        gpu=GpuProbe(kind=GpuKind.NONE, devices=(), notes=()),
        notes=(),
    )
    monkeypatch.setattr(
        "know_your_context.hardware.profile.probe_hardware", lambda path=None, *, refresh=False: profile
    )
    reset_plan_cache()


class TestIoBufferBytes:
    def test_historical_floor_on_this_host(self) -> None:
        assert io_buffer_bytes(None) >= HISTORICAL_IO_BUFFER_BYTES
        reset_plan_cache()

    def test_hdd_scales_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_disk(monkeypatch, DiskKind.HDD)
        assert io_buffer_bytes(None) == 4 * 1024**2
        reset_plan_cache()

    def test_ssd_stays_historical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_disk(monkeypatch, DiskKind.SSD)
        assert io_buffer_bytes(None) == HISTORICAL_IO_BUFFER_BYTES
        reset_plan_cache()

    def test_kill_switch_restores_historical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_disk(monkeypatch, DiskKind.HDD)
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        assert io_buffer_bytes(None) == HISTORICAL_IO_BUFFER_BYTES
        reset_plan_cache()

    def test_probe_failure_restores_historical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(path=None, *, refresh=False):
            raise RuntimeError("synthetic probe failure")

        monkeypatch.setattr("know_your_context.hardware.profile.probe_hardware", _boom)
        reset_plan_cache()
        assert io_buffer_bytes(None) == HISTORICAL_IO_BUFFER_BYTES
        reset_plan_cache()


class TestDigestsIdentical:
    def test_backup_and_migrate_hashes_match_across_disk_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = tmp_path / "blob.bin"
        payload.write_bytes(bytes(range(256)) * 5000)  # 1.25 MB, crosses the buffer boundary

        _fake_disk(monkeypatch, DiskKind.HDD)
        hdd_backup = backup_sha256(payload)
        hdd_migrate = migrate_sha256(payload)
        _fake_disk(monkeypatch, DiskKind.SSD)
        ssd_backup = backup_sha256(payload)
        ssd_migrate = migrate_sha256(payload)
        reset_plan_cache()
        assert hdd_backup == ssd_backup
        assert hdd_migrate == ssd_migrate
        import hashlib

        assert hdd_backup == hashlib.sha256(payload.read_bytes()).hexdigest()


class TestProjectionBuffering:
    def test_default_buffering_when_tuning_off(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        assert _artifact_buffering(tmp_path) == -1
        reset_plan_cache()

    def test_plan_buffer_when_enabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_disk(monkeypatch, DiskKind.HDD)
        assert _artifact_buffering(tmp_path) == 4 * 1024**2
        reset_plan_cache()

    def test_artifact_bytes_identical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.projection import FragmentCache

        _fake_disk(monkeypatch, DiskKind.HDD)
        cache = FragmentCache(tmp_path / "cache")
        cache.put("k1", {"a": 1, "b": ["x", "y"]})
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
        cache.put("k2", {"a": 1, "b": ["x", "y"]})
        written = {p.name: p.read_bytes() for p in (tmp_path / "cache").rglob("*.json")}
        reset_plan_cache()
        assert written["k1.json"] == written["k2.json"]
