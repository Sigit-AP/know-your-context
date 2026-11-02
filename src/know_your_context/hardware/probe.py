"""Read-only hardware detection from /proc + /sys — no root, no new deps.

Verdict basis (GPU-VERDICT.md, SUB-133): this host has no GPGPU at all, so the
product must be GPU-optional and never GPU-required; every probe here fails
open (UNKNOWN / NONE with an honest note) instead of guessing or raising.

Design principles (EXPERT-THINKING):
* fail open on missing data — a VM or macOS host without sysfs is a fact,
  not an error;
* strict=True turns malformed data into typed :class:`ProbeError` for
  operators who want fail-closed probing;
* probes never write to the filesystem and never spawn subprocesses;
  results are cached per path because hardware does not change mid-process.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "VIRTUAL_DISPLAY_DRIVERS",
    "DiskKind",
    "DiskProbe",
    "GpuKind",
    "GpuProbe",
    "HardwareProfile",
    "MemoryProbe",
    "ProbeError",
    "probe_cpu_count",
    "probe_disk_kind",
    "probe_gpu",
    "probe_hardware",
    "probe_memory",
    "reset_probe_cache",
]


class ProbeError(RuntimeError):
    """A typed probe failure (strict mode only; default probing fails open)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class DiskKind(StrEnum):
    """Rotational-media classification of the block device behind a path."""

    SSD = "ssd"
    HDD = "hdd"
    UNKNOWN = "unknown"


class GpuKind(StrEnum):
    """Honest GPU classification: a virtual display adapter is NOT compute."""

    NONE = "none"
    VIRTUAL_DISPLAY = "virtual_display"
    COMPUTE = "compute"


@dataclass(frozen=True)
class DiskProbe:
    kind: DiskKind
    device: str | None
    rotational: bool | None
    note: str


@dataclass(frozen=True)
class MemoryProbe:
    total_bytes: int
    available_bytes: int
    note: str


@dataclass(frozen=True)
class GpuProbe:
    kind: GpuKind
    devices: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class HardwareProfile:
    cpu_count: int
    memory: MemoryProbe
    disk: DiskProbe
    gpu: GpuProbe
    notes: tuple[str, ...]


#: DRM drivers that render pixels but expose no compute API we can use.
VIRTUAL_DISPLAY_DRIVERS: frozenset[str] = frozenset(
    {"vmwgfx", "virtio-gpu", "vgem", "vkms", "hyperv_drm", "qxl", "cirrus", "bochs-drm", "bochs"}
)

_MAX_BLOCK_WALK_DEPTH = 6
_KIB = 1024


def _fail(strict: bool, code: str, message: str) -> None:
    if strict:
        raise ProbeError(code, message)


# --------------------------------------------------------------------- disk


def probe_disk_kind(
    path: str | Path | None = None,
    *,
    sys_root: str | Path = "/sys",
    strict: bool = False,
) -> DiskProbe:
    """Classify the block device backing *path* via sysfs queue/rotational.

    Resolution: ``os.stat(path).st_dev`` → ``/sys/dev/block/<maj>:<min>`` →
    walk up the device tree until ``queue/rotational`` is found (a partition
    inherits its disk's flag). Absent sysfs (VM without it, macOS, Windows)
    fails open to UNKNOWN; ``0`` → SSD, ``1`` → HDD, anything else is
    malformed (fail-open UNKNOWN, or ProbeError in strict mode).
    """
    target = Path(path) if path is not None else Path("/")
    sys_root = Path(sys_root)

    ancestor_note = ""
    try:
        dev = os.stat(target).st_dev
    except OSError as exc:
        # The file may not exist yet (a store probes before it is created):
        # the block device is a property of the containing directory, so walk
        # up to the nearest existing ancestor before giving up.
        dev = -1
        for ancestor in target.parents:
            try:
                dev = os.stat(ancestor).st_dev
                ancestor_note = f"probed ancestor {ancestor} ({target} does not exist yet); "
                break
            except OSError:
                continue
        if dev < 0:
            _fail(strict, "STAT_FAILED", f"cannot stat {target} or any ancestor: {exc}")
            return DiskProbe(DiskKind.UNKNOWN, None, None, f"stat failed for {target}: {exc}")

    block_link = sys_root / "dev" / "block" / f"{os.major(dev)}:{os.minor(dev)}"
    if not block_link.exists():
        note = f"sysfs absent or device unmapped at {block_link} (fail-open)"
        _fail(strict, "SYSFS_ABSENT", note)
        return DiskProbe(DiskKind.UNKNOWN, None, None, note)

    try:
        current: Path | None = block_link.resolve(strict=False)
    except OSError as exc:
        _fail(strict, "RESOLVE_FAILED", f"cannot resolve {block_link}: {exc}")
        return DiskProbe(DiskKind.UNKNOWN, None, None, f"cannot resolve {block_link}: {exc}")

    device: str | None = None
    for _ in range(_MAX_BLOCK_WALK_DEPTH):
        if current is None:
            break
        rotational_file = current / "queue" / "rotational"
        if rotational_file.is_file():
            device = current.name
            try:
                raw = rotational_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                _fail(strict, "READ_FAILED", f"cannot read {rotational_file}: {exc}")
                return DiskProbe(DiskKind.UNKNOWN, device, None, f"cannot read {rotational_file}: {exc}")
            if raw == "0":
                return DiskProbe(DiskKind.SSD, device, False, ancestor_note + "queue/rotational=0")
            if raw == "1":
                return DiskProbe(DiskKind.HDD, device, True, ancestor_note + "queue/rotational=1")
            note = f"malformed queue/rotational value {raw!r} on {device}"
            _fail(strict, "MALFORMED_ROTATIONAL", note)
            return DiskProbe(DiskKind.UNKNOWN, device, None, note)
        parent = current.parent
        current = parent if parent != current else None

    if device is None:
        device = current.name if current else None
    note = "no queue/rotational found while walking the device tree (fail-open)"
    _fail(strict, "ROTATIONAL_ABSENT", note)
    return DiskProbe(DiskKind.UNKNOWN, device, None, note)


# ------------------------------------------------------------------- memory


def probe_memory(
    *,
    meminfo_path: str | Path = "/proc/meminfo",
    strict: bool = False,
) -> MemoryProbe:
    """Read MemTotal / MemAvailable from procfs (KiB → bytes).

    MemAvailable (kernel ≥ 3.14) is the only honest "how much can we use"
    signal; older kernels without it report 0 with a note rather than a
    fabricated estimate.
    """
    path = Path(meminfo_path)
    if not path.is_file():
        note = f"meminfo absent at {path} (non-Linux or fail-open)"
        _fail(strict, "MEMINFO_ABSENT", note)
        return MemoryProbe(0, 0, note)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(strict, "READ_FAILED", f"cannot read {path}: {exc}")
        return MemoryProbe(0, 0, f"cannot read {path}: {exc}")

    fields: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            key = parts[0][:-1]
            try:
                fields[key] = int(parts[1]) * _KIB
            except ValueError:
                if key in ("MemTotal", "MemAvailable"):
                    _fail(strict, "MALFORMED_MEMINFO", f"line {line!r} has a non-numeric value")
                    return MemoryProbe(0, 0, f"malformed meminfo line: {line!r}")

    total = fields.get("MemTotal", 0)
    available = fields.get("MemAvailable", 0)
    notes: list[str] = []
    if total == 0:
        notes.append("MemTotal absent")
        _fail(strict, "MISSING_FIELD", "MemTotal absent from meminfo")
    if available == 0:
        notes.append("MemAvailable absent (kernel < 3.14 or restricted procfs); reported as 0")
        _fail(strict, "MISSING_FIELD", "MemAvailable absent from meminfo")
    return MemoryProbe(total, available, "; ".join(notes))


# ---------------------------------------------------------------------- cpu


def probe_cpu_count(
    *,
    affinity: Callable[[], set[int]] | None = None,
    cpu_count: Callable[[], int | None] | None = None,
) -> int:
    """Usable CPU count: cgroup/sched affinity first, os.cpu_count fallback.

    Never returns 0 — a caller dividing by this must stay safe.
    """
    if affinity is None:

        def _default_affinity() -> set[int]:
            return set(os.sched_getaffinity(0))  # type: ignore[attr-defined]

        affinity = _default_affinity

    if cpu_count is None:
        cpu_count = os.cpu_count

    try:
        usable = len(affinity())
    except AttributeError, NotImplementedError, OSError:
        usable = 0
    if usable <= 0:
        usable = cpu_count() or 0
    return max(1, usable)


# ---------------------------------------------------------------------- gpu


def probe_gpu(
    *,
    which: Callable[[str], str | None] = shutil.which,
    sys_root: str | Path = "/sys",
    opencl_root: str | Path = "/etc/OpenCL/vendors",
    vulkan_root: str | Path = "/usr/share/vulkan/icd.d",
) -> GpuProbe:
    """Detect a *compute* GPU honestly; a virtual display adapter is not one.

    Signals, in order of strength: vendor tools in PATH (nvidia-smi/clinfo/
    vulkaninfo), OpenCL vendor ICDs, Vulkan ICDs, then DRM render nodes whose
    driver is classified (vmwgfx/virtio/… = display-only virtual). Host
    evidence (GPU-VERDICT §2): vmwgfx + no tools + no ICDs → NONE here.
    """
    devices: list[str] = []
    notes: list[str] = []
    compute = False
    virtual = False

    for tool in ("nvidia-smi", "clinfo", "vulkaninfo", "rocm-smi"):
        try:
            found = which(tool)
        except Exception as exc:  # a broken PATH lookup must not kill the probe
            notes.append(f"which({tool}) failed: {exc}")
            continue
        if found:
            compute = True
            devices.append(f"tool:{tool}({found})")

    for root, glob, label in (
        (Path(opencl_root), "*.icd", "opencl"),
        (Path(vulkan_root), "*.json", "vulkan"),
    ):
        try:
            entries = sorted(root.glob(glob)) if root.is_dir() else []
        except OSError as exc:
            notes.append(f"cannot scan {root}: {exc}")
            continue
        for entry in entries:
            compute = True
            devices.append(f"{label}:{entry.name}")

    drm_root = Path(sys_root) / "class" / "drm"
    try:
        render_nodes = sorted(drm_root.glob("renderD*")) if drm_root.is_dir() else []
    except OSError as exc:
        notes.append(f"cannot scan {drm_root}: {exc}")
        render_nodes = []
    for node in render_nodes:
        uevent = node / "device" / "uevent"
        driver = ""
        try:
            if uevent.is_file():
                for line in uevent.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("DRIVER="):
                        driver = line.split("=", 1)[1].strip()
                        break
        except OSError as exc:
            notes.append(f"cannot read {uevent}: {exc}")
            continue
        if not driver:
            notes.append(f"uevent without DRIVER for {node.name}")
            continue
        if driver in VIRTUAL_DISPLAY_DRIVERS:
            virtual = True
            devices.append(f"drm:{node.name}(driver={driver},display-only)")
        else:
            compute = True
            devices.append(f"drm:{node.name}(driver={driver})")

    if compute:
        kind = GpuKind.COMPUTE
    elif virtual:
        kind = GpuKind.VIRTUAL_DISPLAY
        notes.append("display adapter(s) present but no compute API (vmwgfx/virtio class)")
    else:
        kind = GpuKind.NONE
        notes.append("no compute GPU detected; product runs CPU-only (verdict: GPU 0% for interactive load)")
    return GpuProbe(kind, tuple(devices), tuple(notes))


# ------------------------------------------------------------------ profile

_PROFILE_CACHE: dict[str | None, HardwareProfile] = {}


def probe_hardware(path: str | Path | None = None, *, refresh: bool = False) -> HardwareProfile:
    """The cached full profile for the host as seen from *path*.

    Cached per resolved path: hardware does not change mid-process, and the
    store constructor probes on every open. Pass ``refresh=True`` to re-probe
    (operator tools); tests use :func:`reset_probe_cache`.
    """
    key: str | None = None if path is None else str(Path(path).resolve())
    if not refresh and key in _PROFILE_CACHE:
        return _PROFILE_CACHE[key]
    notes: list[str] = []
    if sys.platform != "linux":
        notes.append(f"platform={sys.platform}: /proc and /sys probing is Linux-only; failing open")
    profile = HardwareProfile(
        cpu_count=probe_cpu_count(),
        memory=probe_memory(),
        disk=probe_disk_kind(path),
        gpu=probe_gpu(),
        notes=tuple(notes),
    )
    _PROFILE_CACHE[key] = profile
    return profile


def reset_probe_cache() -> None:
    """Drop every cached profile (tests; operator refresh flows)."""
    _PROFILE_CACHE.clear()
