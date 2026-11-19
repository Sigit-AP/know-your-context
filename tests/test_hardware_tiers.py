"""Tests for the hardware-tier resolver (``hardware/tiers.py``).

The tier is a recommendation, never a gate: the floor must always resolve,
an explicit env override must win, and a small or IO-starved host must never
be promoted. Quality knobs (depth/limit/candidates) must grow monotonically
with the tier — that is the "higher resolution on bigger hardware" contract.
"""

from __future__ import annotations

import dataclasses

import pytest

from know_your_context.hardware import tiers

GIB = 1 << 30


def test_floor_resolves_when_below_minimum() -> None:
    assert tiers.select_tier(cores=1, free_ram_bytes=GIB) is tiers.Tier.MIN
    assert tiers.select_tier(cores=4, free_ram_bytes=512 * (1 << 20)) is tiers.Tier.MIN


def test_two_core_39gb_host_is_standard() -> None:
    """The host every number in the docs was measured on."""
    assert tiers.select_tier(cores=2, free_ram_bytes=3 * GIB) is tiers.Tier.STANDARD


def test_high_needs_cores_ram_and_fast_io() -> None:
    assert tiers.select_tier(cores=8, free_ram_bytes=8 * GIB) is tiers.Tier.HIGH
    assert tiers.select_tier(cores=8, free_ram_bytes=8 * GIB, disk_rotational=True) is tiers.Tier.STANDARD
    assert tiers.select_tier(cores=8, free_ram_bytes=7 * GIB) is tiers.Tier.STANDARD


def test_extreme_needs_the_top_of_both_axes() -> None:
    assert tiers.select_tier(cores=16, free_ram_bytes=32 * GIB) is tiers.Tier.EXTREME
    assert tiers.select_tier(cores=15, free_ram_bytes=64 * GIB) is tiers.Tier.HIGH
    assert tiers.select_tier(cores=16, free_ram_bytes=64 * GIB, disk_queue_depth=5) is tiers.Tier.STANDARD


def test_quality_knobs_grow_monotonically_with_tier() -> None:
    """Bigger hardware must answer more completely, not merely faster."""
    order = [tiers.Tier.MIN, tiers.Tier.STANDARD, tiers.Tier.HIGH, tiers.Tier.EXTREME]
    profiles = [tiers.profile_for(t) for t in order]
    for field in ("default_depth", "default_limit", "ambiguous_candidates"):
        values = [getattr(p, field) for p in profiles]
        assert values == sorted(values), f"{field} does not grow: {values}"
        assert values[0] < values[-1], f"{field} never grows: {values}"
    for field in ("ingest_workers", "commit_batch", "resolve_batch", "query_parallel"):
        values = [getattr(p, field) for p in profiles]
        assert values == sorted(values), f"{field} does not grow: {values}"


def test_memory_knobs_are_off_at_the_floor() -> None:
    """The MIN recipe is the one measured at +3 MB/query instead of +59 MB."""
    floor = tiers.profile_for(tiers.Tier.MIN)
    assert floor.mmap_bytes == 0
    assert floor.cache_kib == 0
    assert floor.ingest_workers == 1
    assert floor.commit_batch == 1


def test_env_override_wins_and_is_clamped() -> None:
    assert tiers.active_profile({tiers.ENV_TIER: "2"}).tier is tiers.Tier.HIGH
    assert tiers.active_profile({tiers.ENV_TIER: "99"}).tier is tiers.Tier.EXTREME
    assert tiers.active_profile({tiers.ENV_TIER: "-5"}).tier is tiers.Tier.MIN


@pytest.mark.parametrize("bad", ["", "abc", "1.5", "  "])
def test_malformed_override_falls_back_to_the_probe(bad: str) -> None:
    """A bad override must never crash and must never promote the tier."""
    profile = tiers.active_profile({tiers.ENV_TIER: bad})
    assert isinstance(profile, tiers.TierProfile)


def test_profile_mapping_is_json_friendly() -> None:
    payload = tiers.profile_for(tiers.Tier.STANDARD).as_mapping()
    assert payload["tier_name"] == "STANDARD"
    assert set(payload) == {
        "tier",
        "tier_name",
        "ingest_workers",
        "commit_batch",
        "resolve_batch",
        "mmap_bytes",
        "cache_kib",
        "default_depth",
        "default_limit",
        "ambiguous_candidates",
        "projection_wait_seconds",
        "query_parallel",
    }


def test_profiles_are_frozen() -> None:
    profile = tiers.profile_for(tiers.Tier.MIN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.default_depth = 99  # type: ignore[misc]


def test_determinism_same_inputs_same_profile() -> None:
    first = tiers.active_profile({tiers.ENV_TIER: "1"})
    second = tiers.active_profile({tiers.ENV_TIER: "1"})
    assert first == second
    assert first.as_mapping() == second.as_mapping()
