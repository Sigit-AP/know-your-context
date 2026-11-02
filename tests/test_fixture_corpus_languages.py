"""Parametrized extraction tests over the W15 language corpus (SUB-129).

Every fixture in ``tests/fixtures/manifest.json`` is extracted through the real
tree-sitter walker and asserted against its authored expectations:

* the file parses cleanly (``parse_error is False``);
* every expected ``(kind, name)`` node is present;
* every expected import target is parked as an unresolved ``imports`` ref;
* expected nodes carry sane source positions (1-indexed lines, ordered).

The manifest expectations are authored from language semantics (which
declaration kinds the spec maps, how method-flipping scopes work), never from
blind snapshots — the corpus generator probed every fixture and mismatches
were resolved by fixing the *expectation* only when the extractor was
semantically right (see docs/recovery/w15-fixtures.md for the grammar gaps
that shaped a few fixtures).
"""

from __future__ import annotations

import pytest

from _fixture_corpus import case_id, corpus_cases, extract_case
from know_your_context.ingest.languages import LANGUAGES

CASES = corpus_cases()
EXPECTED_CATEGORIES = frozenset(
    {
        "async",
        "calls",
        "classes",
        "edge-nesting",
        "edge-syntax",
        "errors",
        "functions",
        "imports",
        "module",
        "sample",
        "types",
        "typing",
    }
)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_fixture_extracts_cleanly(case) -> None:
    result = extract_case(case)
    assert result.parse_error is False, f"{case.path}: parse error"
    assert result.language == case.language


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_fixture_expected_nodes_present(case) -> None:
    result = extract_case(case)
    actual = {(node.kind, node.name) for node in result.nodes}
    missing = [(kind, name) for kind, name in case.nodes if (kind, name) not in actual]
    assert not missing, f"{case.path}: missing nodes {missing}; got {sorted(actual)}"


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_fixture_expected_imports_present(case) -> None:
    result = extract_case(case)
    actual = {ref.target_name for ref in result.unresolved_refs if ref.kind == "imports"}
    missing = [target for target in case.imports if target not in actual]
    assert not missing, f"{case.path}: missing import refs {missing}; got {sorted(actual)}"


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_fixture_positions_are_sane(case) -> None:
    result = extract_case(case)
    for node in result.nodes:
        assert node.start_line >= 1, f"{case.path}: {node.name} start_line {node.start_line}"
        assert node.end_line >= node.start_line, f"{case.path}: {node.name} line range inverted"
        assert node.path == case.path


class TestCorpusShape:
    def test_corpus_size(self) -> None:
        assert len(CASES) >= 495, f"language corpus shrank: {len(CASES)}"

    def test_all_languages_covered(self) -> None:
        languages = {case.language for case in CASES}
        assert languages == set(LANGUAGES), f"missing: {sorted(set(LANGUAGES) - languages)}"

    def test_every_language_has_15_plus_fixtures(self) -> None:
        counts: dict[str, int] = {}
        for case in CASES:
            counts[case.language] = counts.get(case.language, 0) + 1
        thin = {lang: n for lang, n in sorted(counts.items()) if n < 15}
        assert not thin, f"languages under 15 fixtures: {thin}"

    def test_categories_are_from_the_known_set(self) -> None:
        unknown = {case.category for case in CASES} - EXPECTED_CATEGORIES
        assert not unknown, f"unknown categories: {sorted(unknown)}"

    def test_every_language_covers_core_categories(self) -> None:
        """Each language exercises functions + imports + module structure."""
        per_language: dict[str, set[str]] = {}
        for case in CASES:
            per_language.setdefault(case.language, set()).add(case.category)
        core = {"functions", "imports", "module"}
        gaps = {lang: sorted(core - cats) for lang, cats in per_language.items() if not core <= cats}
        assert not gaps, f"languages missing core categories: {gaps}"

    def test_90_percent_of_languages_cover_six_plus_categories(self) -> None:
        per_language: dict[str, set[str]] = {}
        for case in CASES:
            per_language.setdefault(case.language, set()).add(case.category)
        rich = sum(1 for cats in per_language.values() if len(cats) >= 6)
        assert rich >= 30, f"only {rich}/33 languages cover 6+ categories"
