"""Cross-engine differential fuzzing proofs (I3, W15, SUB-130).

The three plan-named proofs:

* ``test_seeded_corpus_byte_parity_across_engines`` — parametrized over
  every generator language × a fixed seed window; each case asserts the
  pure-Python reference walker and the Rust kernel produce byte-identical
  canonical artifacts (or fail identically).
* ``test_injected_divergence_detected_and_shrunk`` — an instrumented
  kernel outcome (one node dropped) must be detected, and the
  deterministic shrinker must reduce the repro to a smaller case that
  still diverges.
* ``test_corpus_generation_seed_stable`` — the corpus is a pure function
  of the seed (bytes identical across regenerations; distinct seeds
  diverge).

Plus a heavyweight sweep (``KYS_FUZZ_SWEEP`` cases per language, default
1000) whose summary is the "N thousand cases, zero divergences" evidence
recorded in ``docs/recovery/w15-fuzz-query.md``.
"""

from __future__ import annotations

import os

import pytest

from know_your_context.domain import canonical_json
from know_your_context.ingest import diffuzz, kernel_bridge
from know_your_context.ingest.diffuzz import DiffuzzError, Divergence, compare_case, compare_outcomes, shrink
from know_your_context.ingest.fuzzgen import GENERATOR_LANGUAGES, generate_case, generate_corpus

PARAM_SEEDS = 25  # parametrized window per language (8 × 25 = 200 cases)
SWEEP_DEFAULT = 1000


def _require_kernel() -> None:
    if kernel_bridge.kernel_available():
        return
    reason = kernel_bridge.kernel_unavailable_reason()
    if os.environ.get("KYS_REQUIRE_RUST_KERNEL"):
        pytest.fail(f"Rust kernel required but unavailable: {reason}")
    pytest.skip(f"Rust kernel not built ({reason}); skipping differential tests")


# ------------------------------------------------------------------ proof 1


@pytest.mark.parametrize("language", GENERATOR_LANGUAGES)
@pytest.mark.parametrize("seed", range(PARAM_SEEDS))
def test_seeded_corpus_byte_parity_across_engines(language, seed):
    _require_kernel()
    divergence = compare_case(generate_case(language, seed))
    assert divergence is None, divergence.repro() if divergence else ""


def test_thousand_case_sweep_per_language():
    """Heavyweight aggregate: SWEEP_DEFAULT cases per language, zero divergence."""
    _require_kernel()
    count = int(os.environ.get("KYS_FUZZ_SWEEP", SWEEP_DEFAULT))
    divergences: list[Divergence] = []
    total = 0
    for language in GENERATOR_LANGUAGES:
        for case in generate_corpus(language, count):
            total += 1
            divergence = compare_case(case)
            if divergence is not None:
                divergences.append(divergence)
    assert divergences == [], [d.repro() for d in divergences[:3]]


# ------------------------------------------------------------------ proof 2


def test_injected_divergence_detected_and_shrunk():
    _require_kernel()
    case = generate_case("python", 11)
    baseline_python = diffuzz.python_outcome(case)
    assert baseline_python[0] == "ok"

    def _mutated_kernel_outcome(candidate):
        """An instrumented kernel that drops the last node of the artifact."""
        outcome = diffuzz.kernel_outcome(candidate)
        if outcome[0] != "ok":
            return outcome
        import json

        artifact = json.loads(outcome[1])
        if len(artifact["nodes"]) > 1:
            artifact["nodes"] = artifact["nodes"][:-1]
            return ("ok", canonical_json(artifact))
        return outcome

    mutated = _mutated_kernel_outcome(case)
    divergence = compare_outcomes(case, baseline_python, mutated)
    assert divergence is not None
    assert "canonical artifacts differ" in divergence.summary

    def _still_diverges(candidate) -> bool:
        python = diffuzz.python_outcome(candidate)
        return compare_outcomes(candidate, python, _mutated_kernel_outcome(candidate)) is not None

    shrunk, trace = shrink(case, diverges=_still_diverges)
    assert _still_diverges(shrunk)
    assert len(shrunk.source) < len(case.source)
    assert trace  # at least one accepted reduction, all recorded


def test_shrink_is_deterministic():
    _require_kernel()
    case = generate_case("go", 0)

    def _diverges_when_two_functions(candidate) -> bool:
        import json

        outcome = diffuzz.python_outcome(candidate)
        if outcome[0] != "ok":
            return False
        artifact = json.loads(outcome[1])
        return sum(1 for node in artifact["nodes"] if node["kind"] == "function") >= 2

    assert _diverges_when_two_functions(case)
    first, trace_a = shrink(case, diverges=_diverges_when_two_functions)
    second, trace_b = shrink(case, diverges=_diverges_when_two_functions)
    assert first == second
    assert trace_a == trace_b


def test_shrink_rejects_non_divergent_case():
    case = generate_case("python", 3)
    with pytest.raises(DiffuzzError):
        shrink(case, diverges=lambda _candidate: False)


def test_shrink_budget_validation():
    case = generate_case("python", 3)
    with pytest.raises(DiffuzzError):
        shrink(case, diverges=lambda _c: True, budget=0)


def test_shrink_reaches_minimal_core():
    """A predicate requiring one marker shrinks away everything irrelevant."""
    _require_kernel()
    case = generate_case("ruby", 0)
    marker = "Walker"  # appears on exactly one line of this case
    assert case.source.count(marker) == 1

    def _has_marker(candidate) -> bool:
        return marker in candidate.source

    shrunk, trace = shrink(case, diverges=_has_marker)
    assert marker in shrunk.source
    # 1-minimality: no single remaining line can be removed.
    # 1-minimality: removing any single remaining line must break the marker.
    remaining = shrunk.source.split("\n")
    for index in range(len(remaining)):
        candidate = "\n".join(part for i, part in enumerate(remaining) if i != index)
        assert not (candidate and _has_marker(candidate)), "non-minimal shrink"


# ------------------------------------------------------------------ proof 3


def test_corpus_generation_seed_stable():
    first = generate_corpus("python", 100, seed=9000)
    second = generate_corpus("python", 100, seed=9000)
    assert first == second
    third = generate_corpus("python", 100, seed=9001)
    assert first != third


def test_outcome_kind_divergence_summary():
    case = generate_case("python", 0)
    divergence = compare_outcomes(case, ("ok", "{}"), ("error", "SourceDecodeError"))
    assert divergence is not None
    assert "outcome kind differs" in divergence.summary
    repro = divergence.repro()
    assert repro["language"] == "python"
    assert len(repro["source_sha256"]) == 64
