"""Edge-kind coverage fixtures (SUB-129).

``tests/fixtures/edge_kinds/cases.json`` binds every frozen core edge kind to
a concrete (source, target) pair plus its contract facts. Tests assert:

* every registered kind has a case (and vice versa);
* every kind round-trips through the real store with provenance;
* symmetric kinds (``bridges``) store canonically regardless of direction;
* kinds marked ``pipeline_produced`` really are minted by the corpus
  scenarios (cross-checked against a live synthesis run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.graph_model import CORE_EDGE_KINDS, SYMMETRIC_EDGE_KINDS, is_edge_kind
from know_your_context.store import EventStore

EDGE_ROOT = Path(__file__).parent / "fixtures" / "edge_kinds"


def _cases() -> list[dict]:
    return json.loads((EDGE_ROOT / "cases.json").read_text(encoding="utf-8"))["cases"]


CASES = _cases()


@pytest.fixture
def store(tmp_path):
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def test_every_core_edge_kind_has_a_case() -> None:
    covered = {case["kind"] for case in CASES}
    assert covered == set(CORE_EDGE_KINDS), f"gap: {sorted(set(CORE_EDGE_KINDS) - covered)}"


def test_every_case_kind_is_registered() -> None:
    for case in CASES:
        assert is_edge_kind(case["kind"]), case["kind"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["kind"])
def test_edge_round_trips_through_store(store, case) -> None:
    inserted = store.add_graph_edge(case["source"], case["target"], case["kind"], provenance="fixture:edge-kinds")
    assert inserted is True
    with store.read() as conn:
        rows = conn.execute(
            "SELECT source, target, kind, provenance FROM graph_edges WHERE kind = ?", (case["kind"],)
        ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert str(row["provenance"]) == "fixture:edge-kinds"
    if case["kind"] in SYMMETRIC_EDGE_KINDS:
        assert str(row["source"]) < str(row["target"]), "symmetric edges store canonically"
    else:
        assert str(row["source"]) == case["source"]
        assert str(row["target"]) == case["target"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["kind"])
def test_symmetry_flag_matches_contract(case) -> None:
    assert (case["kind"] in SYMMETRIC_EDGE_KINDS) == bool(case.get("symmetric", False))


def test_bridges_store_as_given_at_store_level(store) -> None:
    """Canonical (source < target) ordering for symmetric edges is a
    domain-layer rule; the store layer persists as given and the kind is
    registered symmetric (the contract the corpus pins here)."""
    store.add_graph_edge("n:zzz", "n:aaa", "bridges", provenance="fixture:edge-kinds")
    with store.read() as conn:
        row = conn.execute("SELECT source, target FROM graph_edges WHERE kind = 'bridges'").fetchone()
    assert (str(row["source"]), str(row["target"])) == ("n:zzz", "n:aaa")
    assert "bridges" in SYMMETRIC_EDGE_KINDS


@pytest.mark.parametrize("case", [c for c in CASES if c.get("pipeline_produced")], ids=lambda c: c["kind"])
def test_pipeline_produced_kinds_appear_in_synthesis_corpus(store, tmp_path, case) -> None:
    """Cross-check: a kind claiming pipeline coverage really is minted by a
    corpus scenario (contains/calls/imports/references/type_of)."""
    from _synthesis_fixtures import run_scenario, scenario_dirs

    produced: set[str] = set()
    for directory in scenario_dirs():
        for _source, _target, kind, _provenance in run_scenario(store, directory, tmp_path / directory.name):
            produced.add(kind)
    assert case["kind"] in produced, f"{case['kind']} claims pipeline coverage but no scenario mints it"
