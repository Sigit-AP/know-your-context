"""The CLI's traversal defaults follow the hardware tier.

``docs/research/hardware-tiers-spec.md`` makes depth/limit the *quality*
knobs: bigger hardware answers more completely. These tests pin that the CLI
defaults come from the tier resolver, that an operator override still wins,
and that a probe failure degrades to the historical 8/200 pair instead of
raising.
"""

from __future__ import annotations

import argparse

import pytest

from know_your_context import cli
from know_your_context.hardware import tiers


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = cli.build_parser()
    return parser.parse_args(argv)


@pytest.mark.parametrize(
    ("tier", "expected_depth", "expected_limit"),
    [
        (tiers.Tier.MIN, 6, 100),
        (tiers.Tier.STANDARD, 8, 200),
        (tiers.Tier.HIGH, 10, 500),
        (tiers.Tier.EXTREME, 12, 1000),
    ],
)
def test_cli_depth_and_limit_follow_the_tier(
    monkeypatch: pytest.MonkeyPatch, tier: tiers.Tier, expected_depth: int, expected_limit: int
) -> None:
    monkeypatch.setenv(tiers.ENV_TIER, str(int(tier)))
    args = _parse(["codeintel", "callers-of", "parse_args"])
    assert args.depth == expected_depth
    assert args.limit == expected_limit


def test_explicit_flags_still_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tiers.ENV_TIER, str(int(tiers.Tier.EXTREME)))
    args = _parse(["codeintel", "callers-of", "parse_args", "--depth", "2", "--limit", "3"])
    assert (args.depth, args.limit) == (2, 3)


def test_impact_keeps_its_wider_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """``impact`` historically defaults to 500 entries; a small tier must not shrink it."""
    monkeypatch.setenv(tiers.ENV_TIER, str(int(tiers.Tier.MIN)))
    args = _parse(["codeintel", "impact", "parse_args"])
    assert args.depth == 6
    assert args.limit == 500


def test_probe_failure_degrades_to_historical_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("probe exploded")

    monkeypatch.delenv(tiers.ENV_TIER, raising=False)
    monkeypatch.setattr(tiers, "active_profile", boom)
    assert cli._tier_defaults() == (8, 200)