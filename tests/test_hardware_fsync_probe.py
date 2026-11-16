"""Proof tests for measured fsync calibration (TASK1-PUSH200-R2 item C).

The disk classification today trusts the sysfs ``queue/rotational`` flag
only. Virtualized/block-device stacks routinely report no flag at all
(fail-open UNKNOWN → middle-of-the-road profile) or a misleading one. This
probe MEASURES commit latency (write page + fsync, N samples) in the
directory that will actually hold the database and arbitrates the flag:
UNKNOWN + measured evidence → reclassify; known flag → flag wins, the
measurement is recorded as a note.
"""

from __future__ import annotations

import pytest

from know_your_context.hardware import probe
from know_your_context.hardware.probe import (
    DiskKind,
    DiskProbe,
    FsyncProbe,
    ProbeError,
    classify_fsync,
    probe_fsync_latency,
    resolve_disk_kind,
)

# ------------------------------------------------------------ pure classifier


@pytest.mark.parametrize(
    ("p50", "expected"),
    [
        (0.05, DiskKind.SSD),
        (0.99, DiskKind.SSD),
        (1.0, DiskKind.UNKNOWN),  # the gap is honestly unknown
        (5.0, DiskKind.UNKNOWN),
        (8.0, DiskKind.UNKNOWN),
        (8.01, DiskKind.HDD),
        (25.0, DiskKind.HDD),
        (None, DiskKind.UNKNOWN),
    ],
)
def test_classify_fsync_boundaries(p50, expected):
    probe_result = FsyncProbe(p50_ms=p50, p95_ms=None if p50 is None else p50 * 2, samples=0, note="t")
    assert classify_fsync(probe_result) is expected


# ------------------------------------------------------------ real probe


def test_probe_fsync_real_directory(tmp_path):
    result = probe_fsync_latency(tmp_path, samples=8)
    assert result.samples == 8
    assert result.p50_ms is not None and result.p50_ms > 0
    assert result.p95_ms is not None and result.p95_ms >= result.p50_ms
    assert result.note
    # no probe file left behind
    assert list(tmp_path.iterdir()) == []


def test_probe_fsync_unwritable_fails_open():
    result = probe_fsync_latency("/proc/1", samples=2)
    assert result.p50_ms is None
    assert result.samples == 0
    assert result.note


def test_probe_fsync_unwritable_strict_raises():
    with pytest.raises(ProbeError):
        probe_fsync_latency("/proc/1", samples=2, strict=True)


# ------------------------------------------------------------ arbitration


def test_resolve_unknown_flag_uses_measurement():
    flag = DiskProbe(DiskKind.UNKNOWN, None, None, "sysfs absent")
    measured = FsyncProbe(p50_ms=0.3, p95_ms=0.6, samples=16, note="measured fsync p50=0.3 ms")
    resolved = resolve_disk_kind(flag, measured)
    assert resolved.kind is DiskKind.SSD
    assert "fsync" in resolved.note
    assert "sysfs" in resolved.note or "absent" in resolved.note


def test_resolve_known_flag_wins_and_records_disagreement():
    flag = DiskProbe(DiskKind.SSD, "sda", False, "queue/rotational=0")
    measured = FsyncProbe(p50_ms=12.0, p95_ms=20.0, samples=16, note="measured fsync p50=12.0 ms")
    resolved = resolve_disk_kind(flag, measured)
    assert resolved.kind is DiskKind.SSD  # flag wins; measurement is evidence
    assert "fsync" in resolved.note


def test_resolve_gap_stays_unknown():
    flag = DiskProbe(DiskKind.UNKNOWN, None, None, "sysfs absent")
    measured = FsyncProbe(p50_ms=3.0, p95_ms=6.0, samples=16, note="measured fsync p50=3.0 ms")
    assert resolve_disk_kind(flag, measured).kind is DiskKind.UNKNOWN


# ------------------------------------------------------------ integration


def test_probe_hardware_enriches_unknown_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        probe, "probe_disk_kind", lambda *a, **k: DiskProbe(DiskKind.UNKNOWN, None, None, "sysfs absent")
    )
    profile = probe.probe_hardware(tmp_path, refresh=True, measure_fsync=True)
    assert "fsync" in profile.disk.note


def test_probe_hardware_measure_disabled_keeps_flag_only(tmp_path, monkeypatch):
    monkeypatch.setattr(
        probe, "probe_disk_kind", lambda *a, **k: DiskProbe(DiskKind.UNKNOWN, None, None, "sysfs absent")
    )
    profile = probe.probe_hardware(tmp_path, refresh=True, measure_fsync=False)
    assert profile.disk.kind is DiskKind.UNKNOWN
    assert "fsync" not in profile.disk.note
