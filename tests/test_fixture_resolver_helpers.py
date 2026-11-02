"""Parametrized tests for the per-language resolver helpers (SUB-129).

Each case in ``tests/fixtures/helpers/cases.json`` binds three things:

* a real source fixture file whose import statement is extracted by the real
  tree-sitter walker (the case's ``target`` MUST appear among the parked
  import refs — fixture and case can never drift apart);
* the helper's exact ordered candidate tuple for that target;
* the registry-wide invariants (repo-confined, deterministic, total).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.ingest.extractor import TreeSitterExtractor
from know_your_context.ingest.languages import get_spec, language_for_path
from know_your_context.resolve.lang_helpers import HELPERS, get_helper

HELPERS_ROOT = Path(__file__).parent / "fixtures" / "helpers"
CASES_PATH = HELPERS_ROOT / "cases.json"


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return _cases()


def test_every_registered_helper_has_a_case(cases) -> None:
    covered = {case["language"] for case in cases}
    missing = sorted(set(HELPERS) - covered)
    assert not missing, f"helpers without fixture cases: {missing}"


def test_every_case_fixture_exists_and_mentions_target(cases) -> None:
    for case in cases:
        fixture = HELPERS_ROOT / case["fixture"]
        assert fixture.is_file(), f"missing fixture {case['fixture']}"
        assert case["target"], "empty target"


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['language']}:{c['target']}")
def test_case_target_is_extracted_from_fixture(case) -> None:
    """The case target really comes out of the fixture's import statement."""
    fixture = HELPERS_ROOT / case["fixture"]
    # helper names (typescriptreact) may be helper-only; the extractor's
    # language comes from the fixture's extension.
    spec_language = language_for_path(case["fixture"])
    assert spec_language is not None, f"no language for {case['fixture']}"
    spec = get_spec(spec_language)
    assert spec is not None, f"no spec for {spec_language}"
    result = TreeSitterExtractor(spec).extract(case["fixture"], fixture.read_text(encoding="utf-8"))
    targets = {ref.target_name for ref in result.unresolved_refs if ref.kind == "imports"}
    assert case["target"] in targets, f"{case['fixture']}: target {case['target']!r} not in extracted {sorted(targets)}"


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['language']}:{c['target']}")
def test_helper_candidates_match_exactly(case) -> None:
    helper = get_helper(case["language"])
    assert helper is not None, f"no helper for {case['language']}"
    candidates = helper.candidates(case["target"], case["source_path"])
    actual = [(candidate.path, candidate.is_dir) for candidate in candidates]
    expected = [(entry["path"], bool(entry.get("is_dir", False))) for entry in case["expected"]]
    assert actual == expected, f"{case['language']} candidates for {case['target']!r}: {actual} != {expected}"


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['language']}:{c['target']}")
def test_candidates_are_repo_confined(case) -> None:
    helper = get_helper(case["language"])
    assert helper is not None
    for candidate in helper.candidates(case["target"], case["source_path"]):
        assert not candidate.path.startswith("/"), candidate.path
        assert ".." not in candidate.path.split("/"), candidate.path


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['language']}:{c['target']}")
def test_candidates_are_deterministic(case) -> None:
    helper = get_helper(case["language"])
    assert helper is not None
    first = helper.candidates(case["target"], case["source_path"])
    second = helper.candidates(case["target"], case["source_path"])
    assert first == second
