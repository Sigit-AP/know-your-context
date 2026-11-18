"""Hardware-aware storage tuning computations (verdict plan §5.2–§5.3).

Pure computation only — this module never opens a database. The pragma
recipe stays owned by ``store.py``; here we turn a probe + knobs into a
:class:`TuningPlan` the store applies verbatim.

Measured basis (GPU-VERDICT §2/§3): page-cache hit ~9.7 ns vs DRAM pointer
chase ~154 ns vs SSD random read ~50–100 µs — five orders of magnitude from
cache to disk, so keeping the working set mapped is the largest available
"accelerator". mmap_size = min(db_size, ½ × MemAvailable, cap).
"""

from __future__ import annotations

from dataclasses import dataclass

from know_your_context.hardware.probe import DiskKind, DiskProbe, MemoryProbe
from know_your_context.ops.switches import KnobBoard

__all__ = [
    "DISK_PROFILES",
    "DiskProfile",
    "INGEST_DISABLE_HW_TUNING_ENV",
    "TuningError",
    "TuningPlan",
    "plan_for_ingest_rss",
    "plan_storage_tuning",
    "recommended_mmap_size",
    "validate_page_size",
]

#: SUB-209: the ingest/write-path kill switch. ``ingest/pipeline.py`` reads
#: it to force workers=1 (fan-out off); the store reads it too so the two
#: pragmas that grow RSS with db size (mmap, page cache) are capped on the
#: write path. The read webview/serve path is a separate process and keeps
#: the full probe recipe unless the operator sets this explicitly.
INGEST_DISABLE_HW_TUNING_ENV = "INGEST_DISABLE_HW_TUNING"


class TuningError(ValueError):
    """An invalid tuning input (typed, fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class DiskProfile:
    """Access-pattern defaults for one disk class (rotational or not)."""

    kind: DiskKind
    flush_cadence_ms: int
    group_commit_batch: int
    page_size_hint: int
    #: Streaming I/O buffer (hashing, artifact copies, artifact writes).
    #: SSD/UNKNOWN keep the historical 1 MiB; rotational devices scale up to
    #: 4 MiB so big sequential reads amortize seeks (scale-up, never down).
    io_buffer_bytes: int = 1024 * 1024


#: HDD: bigger group-commit batches and a lazier flush cadence turn random
#: fsync-heavy writes into sequential ones; a larger page size cuts seeks on
#: sequential scans. UNKNOWN sits between the two and keeps the engine page
#: size — without evidence we change nothing structural.
DISK_PROFILES: dict[DiskKind, DiskProfile] = {
    DiskKind.SSD: DiskProfile(DiskKind.SSD, flush_cadence_ms=25, group_commit_batch=32, page_size_hint=4096),
    DiskKind.HDD: DiskProfile(
        DiskKind.HDD, flush_cadence_ms=100, group_commit_batch=128, page_size_hint=8192, io_buffer_bytes=4 * 1024**2
    ),
    DiskKind.UNKNOWN: DiskProfile(DiskKind.UNKNOWN, flush_cadence_ms=50, group_commit_batch=64, page_size_hint=4096),
}

_ALLOWED_PAGE_SIZES: frozenset[int] = frozenset(512 << shift for shift in range(8))  # 512..65536 powers of two


def recommended_mmap_size(*, db_size_bytes: int, mem_available_bytes: int, cap_bytes: int) -> int:
    """min(db_size, ½ × MemAvailable, cap); 0 disables mmap honestly.

    A fresh database has size 0 at open time; the map is a *hint*, not an
    allocation, so an empty db maps up to the memory bound (min(½×MemAvailable,
    cap)) and grows into it.
    """
    for name, value in (
        ("db_size_bytes", db_size_bytes),
        ("mem_available_bytes", mem_available_bytes),
        ("cap_bytes", cap_bytes),
    ):
        if value < 0:
            raise TuningError("INVALID_INPUT", f"{name} must be >= 0, got {value}")
    memory_bound = mem_available_bytes // 2
    if db_size_bytes <= 0:
        return min(memory_bound, cap_bytes)
    return min(db_size_bytes, memory_bound, cap_bytes)


def validate_page_size(value: int) -> int | None:
    """0 → engine default (None); otherwise must be a power of two 512..65536."""
    if value == 0:
        return None
    if value not in _ALLOWED_PAGE_SIZES:
        raise TuningError(
            "INVALID_PAGE_SIZE",
            f"page_size must be 0 or a power of two in 512..65536, got {value}",
        )
    return value


@dataclass(frozen=True)
class TuningPlan:
    """What the store should apply; every deviation carries a note."""

    mmap_bytes: int
    page_size: int | None
    synchronous_normal: bool
    profile: DiskProfile
    notes: tuple[str, ...]
    cache_kib: int = 0


def plan_storage_tuning(
    *,
    db_size_bytes: int,
    disk: DiskProbe,
    memory: MemoryProbe,
    knobs: KnobBoard,
    hw_tuning_disabled: bool,
) -> TuningPlan:
    """Combine probe + knobs into one plan; defaults = historical behavior."""
    notes: list[str] = []
    profile = DISK_PROFILES[disk.kind]

    if hw_tuning_disabled:
        notes.append("kill switch KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING: historical pragma recipe")
        return TuningPlan(0, None, False, profile, tuple(notes), 0)

    mmap_bytes = 0
    if not knobs.value("storage.mmap_enabled"):
        notes.append("storage.mmap_enabled=off: mmap disabled by operator")
    else:
        cap = int(knobs.value("storage.mmap_max_bytes"))
        if memory.available_bytes <= 0:
            notes.append("MemAvailable unknown; mmap bounded by min(db_size, storage.mmap_max_bytes) only")
            mmap_bytes = min(db_size_bytes, cap)
        else:
            mmap_bytes = recommended_mmap_size(
                db_size_bytes=db_size_bytes,
                mem_available_bytes=memory.available_bytes,
                cap_bytes=cap,
            )
        if mmap_bytes == 0:
            notes.append("mmap disabled: computed size is 0 (zero cap)")

    cache_kib = 0
    if not knobs.value("storage.aggressive_cache"):
        notes.append("storage.aggressive_cache=off: page cache stays at the engine default (2 MiB)")
    elif memory.available_bytes <= 0:
        notes.append("MemAvailable unknown; page cache stays at the engine default (fail-open)")
    else:
        bound_kib = memory.available_bytes // 8 // 1024
        size_kib = db_size_bytes // 1024 if db_size_bytes > 0 else bound_kib
        cache_kib = min(size_kib, bound_kib, int(knobs.value("storage.cache_max_kib")))
        if cache_kib < 2048:  # nothing beyond the ~2 MiB engine default to gain
            cache_kib = 0

    page_size = validate_page_size(int(knobs.value("storage.page_size")))
    if page_size is None and disk.kind is DiskKind.HDD:
        page_size = profile.page_size_hint
        notes.append(f"rotational device: fresh-database page_size hint {page_size} (fewer seeks)")

    synchronous_normal = False
    if knobs.value("storage.synchronous_normal"):
        if disk.kind is DiskKind.SSD:
            synchronous_normal = True
            notes.append("synchronous=NORMAL on non-rotational device (WAL stays crash-safe; operator opted in)")
        elif disk.kind is DiskKind.HDD:
            notes.append("synchronous stays FULL: rotational device (random fsync is the HDD bottleneck)")
        else:
            notes.append("synchronous stays FULL: disk type unknown (fail-open to durability)")

    return TuningPlan(mmap_bytes, page_size, synchronous_normal, profile, tuple(notes), cache_kib)


def plan_for_ingest_rss(plan: TuningPlan) -> TuningPlan:
    """Cap the two RSS-growing pragmas for the ingest/write path (SUB-209).

    ``mmap_size`` maps db pages into the process address space — every
    touched page counts in RSS — and the aggressive page cache bounds the
    live SQLite pages; both scale with db size and together cost ~db-sized
    RSS on the first scan query (measured +59 MiB for one ``COUNT(*)`` on
    the 313 MB L4 store vs +3 MiB with the legacy recipe, SUB-206 baseline
    repro). The write path needs neither: WAL writes page out either way and
    the engine's default ~2 MiB page cache is enough at workers=1.
    ``page_size`` / ``synchronous`` are access mechanics, not memory, so the
    plan keeps them. Pure: returns a new plan, never mutates the input;
    an already-capped plan passes through unchanged.
    """
    if plan.mmap_bytes == 0 and plan.cache_kib == 0:
        return plan
    notes = plan.notes + (f"{INGEST_DISABLE_HW_TUNING_ENV}=1: mmap 0 + engine-default page cache (ingest RSS cap)",)
    return TuningPlan(0, plan.page_size, plan.synchronous_normal, plan.profile, notes, 0)
