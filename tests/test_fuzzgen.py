"""Unit tests for the seeded differential-fuzzing generator (I3, W15, SUB-130)."""

from __future__ import annotations

import hashlib

import pytest

from know_your_context.ingest.fuzzgen import (
    GENERATOR_LANGUAGES,
    FuzzGenError,
    generate_case,
    generate_corpus,
    validate_grammar_coverage,
)


class TestSeedStability:
    @pytest.mark.parametrize("language", GENERATOR_LANGUAGES)
    def test_same_seed_same_bytes(self, language):
        assert generate_case(language, 42).source == generate_case(language, 42).source

    @pytest.mark.parametrize("language", GENERATOR_LANGUAGES)
    def test_different_seeds_differ(self, language):
        assert generate_case(language, 1).source != generate_case(language, 2).source

    @pytest.mark.parametrize("language", GENERATOR_LANGUAGES)
    def test_path_carries_seed_and_extension(self, language):
        case = generate_case(language, 7)
        assert case.path == f"fuzz/{language}/case_7{case.path[case.path.rindex('.') :]}"
        assert "case_7" in case.path

    def test_corpus_stable_across_calls(self):
        first = generate_corpus("python", 50, seed=1000)
        second = generate_corpus("python", 50, seed=1000)
        assert first == second

    def test_corpus_seed_offset(self):
        corpus = generate_corpus("go", 3, seed=10)
        assert [case.seed for case in corpus] == [10, 11, 12]

    def test_corpus_digest_pinned(self):
        # Cross-process determinism anchor: this hash must never change.
        digest = hashlib.sha256()
        for language in GENERATOR_LANGUAGES:
            for case in generate_corpus(language, 10, seed=500):
                digest.update(case.source.encode("utf-8"))
        assert digest.hexdigest() == _EXPECTED_CORPUS_HASH


_EXPECTED_CORPUS_HASH = "b15560bae95a339b8fd99b7887dde7598decbfb50894cdccec6853168a94fd02"


class TestKnobs:
    def test_unknown_language_rejected(self):
        with pytest.raises(FuzzGenError):
            generate_case("cobol", 1)

    def test_negative_seed_rejected(self):
        with pytest.raises(FuzzGenError):
            generate_case("python", -1)

    def test_bool_seed_rejected(self):
        with pytest.raises(FuzzGenError):
            generate_case("python", True)

    def test_zero_count_rejected(self):
        with pytest.raises(FuzzGenError):
            generate_corpus("python", 0)

    def test_coverage_complete(self):
        assert validate_grammar_coverage() == ()


class TestShape:
    @pytest.mark.parametrize("language", GENERATOR_LANGUAGES)
    def test_generates_functions(self, language):
        sources = [generate_case(language, seed).source for seed in range(20)]
        joined = "\n".join(sources)
        marker = {
            "python": "def ",
            "javascript": "function ",
            "typescript": "function ",
            "go": "func ",
            "java": "static int ",
            "c": "int ",
            "rust": "fn ",
            "ruby": "def ",
        }[language]
        assert marker in joined

    @pytest.mark.parametrize("language", GENERATOR_LANGUAGES)
    def test_no_carriage_returns(self, language):
        for seed in range(25):
            assert "\r" not in generate_case(language, seed).source

    def test_anomalies_occur(self):
        sources = [generate_case("python", seed).source for seed in range(300)]
        assert any(not source.strip() for source in sources)  # empty
        assert any(source.strip().startswith("#") and "def " not in source for source in sources)  # comment-only

    def test_unicode_names_occur(self):
        sources = [generate_case("python", seed).source for seed in range(300)]
        assert any("é" in source or "λ" in source or "雪" in source for source in sources)
