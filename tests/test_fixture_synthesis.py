"""Parametrized tests for the 12 synthesis passes over real fixtures (SUB-129).

Each directory under ``tests/fixtures/synthesis/<pass>/`` is one scenario:
source files (ingested through the real pipeline) or a ``seed.json`` (for
passes whose trigger state the resolver tier consumes first — the seeded rows
are byte-identical to what a real pipeline leaves parked). After
``run_synthesis``, every expected edge must exist with
``provenance == "synthesis:<pass>"``, and a second run must mint nothing.
"""

from __future__ import annotations

import pytest

from _synthesis_fixtures import expected_edges, run_scenario, scenario_dirs
from know_your_context.resolve.synthesis import SYNTH_PASS_ORDER
from know_your_context.store import EventStore

SCENARIOS = scenario_dirs()
PASS_IDS = tuple(SYNTH_PASS_ORDER)


@pytest.fixture
def store(tmp_path):
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def test_all_twelve_passes_have_a_scenario() -> None:
    covered = {path.name for path in SCENARIOS}
    missing = [name for name in PASS_IDS if name not in covered]
    assert not missing, f"synthesis passes without a fixture scenario: {missing}"
    assert len(SCENARIOS) == 12


@pytest.mark.parametrize("directory", SCENARIOS, ids=lambda p: p.name)
def test_scenario_mints_expected_edges(store, tmp_path, directory) -> None:
    edges = run_scenario(store, directory, tmp_path)
    edge_set = set(edges)
    missing = [e for e in expected_edges(store, directory) if e not in edge_set]
    assert not missing, f"{directory.name}: missing synthesized edges {missing}; have {sorted(edge_set)}"


@pytest.mark.parametrize("directory", SCENARIOS, ids=lambda p: p.name)
def test_scenario_is_idempotent(store, tmp_path, directory) -> None:
    from know_your_context.resolve.synthesis import run_synthesis

    run_scenario(store, directory, tmp_path)
    second = run_synthesis(store)
    assert second.inserted == 0, f"{directory.name}: second synthesis run minted {second.inserted} edges"


@pytest.mark.parametrize("directory", SCENARIOS, ids=lambda p: p.name)
def test_scenario_edges_carry_pass_provenance(store, tmp_path, directory) -> None:
    edges = run_scenario(store, directory, tmp_path)
    synth = [e for e in edges if e[3].startswith("synthesis:")]
    assert synth, f"{directory.name}: no synthesis edges at all"
    for _source, _target, _kind, provenance in synth:
        pass_name = provenance.removeprefix("synthesis:")
        assert pass_name in PASS_IDS, f"{directory.name}: unknown pass provenance {provenance}"
