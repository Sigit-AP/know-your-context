"""Integrity gates for the W15 fixture corpus manifest (SUB-129).

These tests fail closed on every form of fixture padding the corpus forbids:
missing files, empty files, duplicate paths, unknown languages, expectation-
free entries, and fixtures no test consumes (the manifest itself is the
consumption registry — every entry is parametrized into extraction tests by
``test_fixture_corpus_languages.py``).
"""

from __future__ import annotations

import pytest

from _fixture_corpus import FIXTURES_ROOT, corpus_cases, load_manifest
from know_your_context.ingest.languages import LANGUAGES, language_for_path

MANIFEST_LANGUAGES = 33


@pytest.fixture(scope="module")
def manifest_cases():
    return load_manifest()


class TestManifestShape:
    def test_manifest_exists_and_parses(self) -> None:
        cases = load_manifest()
        assert len(cases) >= 499, f"language corpus too small: {len(cases)} entries"

    def test_corpus_has_600_plus_fixture_files(self) -> None:
        """The 600+ target counts real fixture files on disk (code + synthesis
        scenarios + helper cases + documents + edge-kind + provenance/debt)."""
        files = [
            path
            for path in FIXTURES_ROOT.rglob("*")
            if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
        ]
        assert len(files) >= 600, f"fixture corpus too small: {len(files)} files (target 600+)"

    def test_paths_are_unique(self) -> None:
        cases = load_manifest()
        paths = [case.path for case in cases]
        assert len(paths) == len(set(paths)), "duplicate fixture paths in manifest"

    def test_manifest_is_sorted_by_path(self) -> None:
        cases = load_manifest()
        paths = [case.path for case in cases]
        assert paths == sorted(paths), "manifest must be deterministically sorted"

    def test_every_entry_has_assertions(self) -> None:
        for case in load_manifest():
            assert case.nodes or case.imports, f"{case.path}: fixture without any expectation is padding"

    def test_every_language_is_registered(self) -> None:
        for case in load_manifest():
            if case.path.startswith("ingest/"):
                assert case.language in LANGUAGES, f"{case.path}: unknown language {case.language}"

    def test_language_matches_extension(self) -> None:
        for case in load_manifest():
            if not case.path.startswith("ingest/"):
                continue
            detected = language_for_path(case.path)
            assert detected == case.language, f"{case.path}: ext→{detected}, manifest says {case.language}"


class TestFixtureFiles:
    def test_every_manifest_file_exists_nonempty(self) -> None:
        for case in load_manifest():
            path = case.abs_path
            assert path.is_file(), f"missing fixture file: {case.path}"
            content = path.read_bytes()
            assert len(content) > 0, f"empty fixture file: {case.path}"

    def test_code_fixtures_have_real_content(self) -> None:
        """Every code fixture carries at least one non-comment, non-blank line."""
        comment_leaders = ("#", "//", "--", ";", "/*", "*", "<!--", "%")
        for case in corpus_cases():
            lines = [
                line.strip()
                for line in case.abs_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith(comment_leaders)
            ]
            assert lines, f"{case.path}: no real code lines (comment-only fixtures are padding)"

    def test_every_code_fixture_on_disk_is_in_manifest(self) -> None:
        """No orphan fixtures: every file under ingest/<language>/ is consumed."""
        manifested = {case.path for case in load_manifest()}
        orphans: list[str] = []
        for language in sorted(LANGUAGES):
            directory = FIXTURES_ROOT / "ingest" / language
            if not directory.is_dir():
                continue
            for file in sorted(directory.iterdir()):
                if file.is_file():
                    rel = f"ingest/{language}/{file.name}"
                    if rel not in manifested:
                        orphans.append(rel)
        assert not orphans, f"fixture files not consumed by any test: {orphans}"

    def test_language_coverage_is_complete(self) -> None:
        """All 33 registry languages carry a corpus of at least 15 fixtures."""
        per_language: dict[str, int] = {}
        for case in corpus_cases():
            per_language[case.language] = per_language.get(case.language, 0) + 1
        missing = sorted(set(LANGUAGES) - set(per_language))
        assert not missing, f"languages without corpus fixtures: {missing}"
        thin = {lang: n for lang, n in sorted(per_language.items()) if n < 15}
        assert not thin, f"languages under 15 fixtures: {thin}"
        assert len(per_language) == MANIFEST_LANGUAGES
