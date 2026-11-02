"""Hardware-aware probing, tuning, and parallelism (W15).

Operator direction: the product USES the hardware fully — workers follow
demand up to the core count, the page cache and mmap grow with available
RAM, and overload is absorbed by typed backpressure + fairness, never by
throttling down. One cached HardwarePlan (``hardware.profile``) is the
single source of truth consumed by every subsystem. GPU stays 0% for the
interactive workload (GPU-VERDICT.md); the gate is a measured formula,
default NO-GPU.
"""

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

__all__ = [
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
