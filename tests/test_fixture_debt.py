"""Debt-detector fixtures (SUB-129).

``tests/fixtures/debt/cases.json`` drives every detector in
``analytics.debt.build_debt_queue`` — contradiction_flagged, low_score,
stale_access, unresolved_ref_old — with positive and negative cases plus a
budget-truncation case. Each case asserts the exact signal set per node.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.analytics.debt import DebtConfig, build_debt_queue

DEBT_ROOT = Path(__file__).parent / "fixtures" / "debt"
PAYLOAD = json.loads((DEBT_ROOT / "cases.json").read_text(encoding="utf-8"))
CASES = PAYLOAD["cases"]
NOW = PAYLOAD["now"]


def _run(case: dict) -> dict:
    config = DebtConfig(budget=case["budget"]) if "budget" in case else DebtConfig()
    return build_debt_queue(
        case["nodes"],
        case["links"],
        now=NOW,
        unresolved_refs=case["unresolved_refs"],
        config=config,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_case_signals_match_exactly(case) -> None:
    queue = _run(case)
    actual = {item["node_id"]: sorted(item["signals"]) for item in queue["items"]}
    expected = {node_id: sorted(signals) for node_id, signals in case["expected_signals"].items()}
    if "budget" in case:
        # truncation keeps a priority-ordered subset of candidates
        for node_id, signals in actual.items():
            assert signals == expected[node_id], f"{case['name']}: {node_id} signals {signals}"
    else:
        assert actual == expected, f"{case['name']}: {actual} != {expected}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_case_signal_counts_are_consistent(case) -> None:
    queue = _run(case)
    # signal_counts counts CANDIDATES (pre-truncation); derive from the case.
    from collections import Counter

    counts: Counter[str] = Counter()
    for signals in case["expected_signals"].values():
        for signal in signals:
            counts[signal] += 1
    for signal, count in queue["signal_counts"].items():
        assert counts.get(signal, 0) == count, f"{case['name']}: counts[{signal}]={count}!={counts.get(signal, 0)}"


def test_budget_case_truncates() -> None:
    (case,) = [c for c in CASES if c["name"] == "budget_truncates_overflow"]
    queue = _run(case)
    assert queue["candidate_count"] == case["expected_candidate_count"]
    assert queue["truncated"] is case["expected_truncated"]
    assert len(queue["items"]) == case["budget"]


def test_combined_case_priority_ordering() -> None:
    (case,) = [c for c in CASES if c["name"] == "combined_signals_raise_priority"]
    queue = _run(case)
    priorities = [item["priority"] for item in queue["items"]]
    assert priorities == sorted(priorities, reverse=True), "queue must be priority-ordered"
    assert queue["items"][0]["node_id"] == "n:bad", "the four-signal node must outrank the one-signal node"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_case_is_deterministic(case) -> None:
    first = _run(case)
    second = _run(case)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_every_detector_has_a_positive_and_negative_case() -> None:
    signals = ("contradiction_flagged", "low_score", "stale_access", "unresolved_ref_old")
    for signal in signals:
        positive = any(signal in s for case in CASES for s in case["expected_signals"].values())
        negative = any(
            all(signal not in s for s in case["expected_signals"].values()) and not case["expected_signals"]
            for case in CASES
        )
        assert positive, f"{signal}: no positive fixture case"
        assert negative, f"{signal}: no negative fixture case"
