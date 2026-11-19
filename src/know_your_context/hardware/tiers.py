"""Hardware tiers: the "minimum spec, richer output on bigger hardware" model.

The product must always run on the floor (2 cores / ~2.5 GB free RAM / no
GPU) and must *use* extra hardware when it is there — more workers, bigger
SQLite caches, deeper traversals and more complete result sets — without ever
changing the semantics of an answer silently.

Design rules (see ``docs/research/hardware-tiers-spec.md``):

* the tier is a *recommendation* derived from the probe, never a hard gate;
* an explicit ``KNOW_YOUR_CONTEXT_TIER`` env wins over the probe;
* a busy or small host degrades (fail-open) and never pins a higher tier;
* degradation may shrink ``depth``/``limit`` only, and callers are expected to
  report ``truncated``/``depth_capped`` when they use those caps.

The module is deliberately pure: it takes numbers, returns a frozen profile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import IntEnum

__all__ = [
    "ENV_TIER",
    "Tier",
    "TierProfile",
    "active_profile",
    "profile_for",
    "select_tier",
]

#: Explicit operator override, e.g. ``KNOW_YOUR_CONTEXT_TIER=2``.
ENV_TIER = "KNOW_YOUR_CONTEXT_TIER"

#: The floor this product is proven on: 2 cores and 2.5 GiB of MemAvailable
#: (measured: L4 ingest peaks at 718-727 MB, storm at 2,556-2,630 MB).
MIN_CORES = 2
MIN_FREE_RAM_BYTES = 2560 * (1 << 20)
HIGH_FREE_RAM_BYTES = 8 * (1 << 30)
EXTREME_FREE_RAM_BYTES = 32 * (1 << 30)
HIGH_CORES = 8
EXTREME_CORES = 16


class Tier(IntEnum):
    """Hardware class, ordered from the proven floor upwards."""

    MIN = 0
    STANDARD = 1
    HIGH = 2
    EXTREME = 3


@dataclass(frozen=True)
class TierProfile:
    """The knobs one tier recommends.

    ``default_depth``/``default_limit``/``ambiguous_candidates`` are the
    *quality* knobs: bigger hardware answers more completely, not just faster.
    """

    tier: Tier
    ingest_workers: int
    commit_batch: int
    resolve_batch: int
    mmap_bytes: int
    cache_kib: int
    default_depth: int
    default_limit: int
    ambiguous_candidates: int
    projection_wait_seconds: float
    query_parallel: int

    def as_mapping(self) -> dict[str, object]:
        """A JSON-friendly view (used by ``config tier`` and the docs)."""
        return {
            "tier": int(self.tier),
            "tier_name": self.tier.name,
            "ingest_workers": self.ingest_workers,
            "commit_batch": self.commit_batch,
            "resolve_batch": self.resolve_batch,
            "mmap_bytes": self.mmap_bytes,
            "cache_kib": self.cache_kib,
            "default_depth": self.default_depth,
            "default_limit": self.default_limit,
            "ambiguous_candidates": self.ambiguous_candidates,
            "projection_wait_seconds": self.projection_wait_seconds,
            "query_parallel": self.query_parallel,
        }


_PROFILES: dict[Tier, TierProfile] = {
    # The floor: one worker, engine-default page cache, shallow defaults.
    # This is exactly the recipe the 2-core host was measured on.
    Tier.MIN: TierProfile(
        tier=Tier.MIN,
        ingest_workers=1,
        commit_batch=1,
        resolve_batch=1024,
        mmap_bytes=0,
        cache_kib=0,
        default_depth=6,
        default_limit=100,
        ambiguous_candidates=5,
        projection_wait_seconds=5.0,
        query_parallel=10,
    ),
    # Today's defaults: what the repo ships and what the L2/L3 numbers use.
    Tier.STANDARD: TierProfile(
        tier=Tier.STANDARD,
        ingest_workers=2,
        commit_batch=64,
        resolve_batch=4096,
        mmap_bytes=0,
        cache_kib=0,
        default_depth=8,
        default_limit=200,
        ambiguous_candidates=20,
        projection_wait_seconds=5.0,
        query_parallel=25,
    ),
    # Read locality switched back on (mmap + 64 MiB cache) and deeper answers.
    Tier.HIGH: TierProfile(
        tier=Tier.HIGH,
        ingest_workers=HIGH_CORES,
        commit_batch=512,
        resolve_batch=16384,
        mmap_bytes=64 * (1 << 20),
        cache_kib=64 * 1024,
        default_depth=10,
        default_limit=500,
        ambiguous_candidates=100,
        projection_wait_seconds=15.0,
        query_parallel=50,
    ),
    Tier.EXTREME: TierProfile(
        tier=Tier.EXTREME,
        ingest_workers=EXTREME_CORES,
        commit_batch=2048,
        resolve_batch=65536,
        mmap_bytes=256 * (1 << 20),
        cache_kib=256 * 1024,
        default_depth=12,
        default_limit=1000,
        ambiguous_candidates=1_000_000,
        projection_wait_seconds=30.0,
        query_parallel=100,
    ),
}


def profile_for(tier: Tier) -> TierProfile:
    """The frozen profile of ``tier``."""
    return _PROFILES[Tier(int(tier))]


def select_tier(
    *,
    cores: int,
    free_ram_bytes: int,
    disk_rotational: bool = False,
    disk_queue_depth: int = 0,
) -> Tier:
    """Pick a tier from measured resources (fail-open toward the floor).

    A rotational disk or a deep queue means the IO path cannot feed workers,
    so the tier is capped at ``STANDARD`` regardless of core count.
    """
    if cores < MIN_CORES or free_ram_bytes < MIN_FREE_RAM_BYTES:
        return Tier.MIN
    slow_io = disk_rotational or disk_queue_depth > 2
    if cores >= EXTREME_CORES and free_ram_bytes >= EXTREME_FREE_RAM_BYTES and not slow_io:
        return Tier.EXTREME
    if cores >= HIGH_CORES and free_ram_bytes >= HIGH_FREE_RAM_BYTES and not slow_io:
        return Tier.HIGH
    return Tier.STANDARD


def _clamped(value: int) -> Tier:
    return Tier(max(int(Tier.MIN), min(int(Tier.EXTREME), value)))


def active_profile(environ: dict[str, str] | None = None) -> TierProfile:
    """The profile in force: ``KNOW_YOUR_CONTEXT_TIER`` wins, else the probe.

    Unreadable hardware (probe failure) degrades to :data:`Tier.MIN` — the
    proven floor — instead of guessing upward.
    """
    env = os.environ if environ is None else environ
    raw = env.get(ENV_TIER)
    if raw not in (None, ""):
        try:
            return profile_for(_clamped(int(str(raw).strip())))
        except TypeError, ValueError:
            pass  # a malformed override degrades to the probe, never crashes
    try:  # pragma: no cover - exercised on real hardware only
        from know_your_context.hardware.profile import probe_hardware

        probe = probe_hardware()
        # ``DiskProbe`` carries kind/rotational but no queue depth; the kind
        # is the durable signal (rotational ⇒ IO cannot feed many workers).
        rotational = probe.disk.rotational is True or probe.disk.kind == "rotational"
        return profile_for(
            select_tier(
                cores=probe.cpu_count,
                free_ram_bytes=probe.memory.available_bytes,
                disk_rotational=rotational,
            )
        )
    except Exception:  # pragma: no cover - fail-open floor
        return profile_for(Tier.MIN)
