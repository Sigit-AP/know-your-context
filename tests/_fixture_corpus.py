"""Loader for the W15 large-scale fixture corpus (SUB-129).

The corpus lives under ``tests/fixtures/`` and is described by
``tests/fixtures/manifest.json`` — a deterministic, sorted JSON document with
one entry per corpus fixture:

.. code-block:: json

    {
      "path": "ingest/python/02-functions-async.py",
      "language": "python",
      "category": "async",
      "nodes": [["function", "fetch_all"]],
      "imports": ["asyncio"]
    }

``nodes`` are ``(kind, name)`` pairs that MUST appear in the extraction result
(subset assertion authored from language semantics — never a blind snapshot);
``imports`` are import-target strings that MUST appear among the unresolved
``imports`` refs. Both lists may be empty but never both (a fixture with no
assertions would be padding, which this corpus forbids by construction and by
``test_fixture_corpus_manifest.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from know_your_context.ingest.extractor import ExtractionResult, TreeSitterExtractor
from know_your_context.ingest.languages import get_spec

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
MANIFEST_PATH = FIXTURES_ROOT / "manifest.json"


@dataclass(frozen=True)
class FixtureCase:
    """One corpus fixture plus its authored semantic expectations."""

    path: str  # repo-relative under tests/fixtures/
    language: str
    category: str
    nodes: tuple[tuple[str, str], ...]  # (kind, name) that MUST be extracted
    imports: tuple[str, ...]  # import targets that MUST be parked as refs

    @property
    def abs_path(self) -> Path:
        return FIXTURES_ROOT / self.path


def load_manifest() -> tuple[FixtureCase, ...]:
    """Parse and structurally validate the corpus manifest (fail-closed)."""
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = raw["fixtures"]
    cases: list[FixtureCase] = []
    for entry in entries:
        cases.append(
            FixtureCase(
                path=str(entry["path"]),
                language=str(entry["language"]),
                category=str(entry["category"]),
                nodes=tuple((str(kind), str(name)) for kind, name in entry["nodes"]),
                imports=tuple(str(target) for target in entry["imports"]),
            )
        )
    return tuple(cases)


def corpus_cases() -> tuple[FixtureCase, ...]:
    """Only the per-language extraction fixtures (``ingest/<lang>/…``)."""
    return tuple(case for case in load_manifest() if case.path.startswith("ingest/"))


def extract_case(case: FixtureCase) -> ExtractionResult:
    """Extract one fixture through the real tree-sitter walker.

    The display path is the fixture's corpus-relative path so node ids are
    stable across machines and checkouts.
    """
    spec = get_spec(case.language)
    assert spec is not None, f"no LanguageSpec registered for {case.language}"
    source = case.abs_path.read_text(encoding="utf-8")
    return TreeSitterExtractor(spec).extract(case.path, source)


def case_id(case: FixtureCase) -> str:
    """Pytest id: ``python/async`` style, unique per fixture path."""
    return f"{case.path.removesuffix(case.abs_path.suffix)}"
