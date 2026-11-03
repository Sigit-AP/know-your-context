"""W16 (SUB-137): read-model validator secret-scan prefilter pins.

Post-memoization storm profile (docs/recovery/w16-perf.md): the validator is
the writer's largest self-time consumer (26%), and 61% of a ``validate()``
call is ``re.Pattern.search`` — every pinned secret pattern is run over every
artifact's full text on every publish (and again on every read worker's first
snapshot of that generation). A clean artifact cannot match any single
pattern unless it matches their alternation, so a single combined prefilter
scan short-circuits the per-pattern loop with byte-identical verdicts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import know_your_context.read_model_validator as rmv
from _serve_helpers import seed_store, upsert_node
from know_your_context.domain import Node, NodeKind, PrimaryDomain
from know_your_context.projection import GraphProjector
from know_your_context.security import SECRET_PATTERNS
from know_your_context.store import EventStore


def _node(node_id: str, index: int) -> Node:
    return Node(
        node_id=node_id,
        parent_id="P1",
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary=f"prefilter pin node {index} with ordinary text",
        provenance=("context://prefilter-pin",),
        source_version_id="sv-test",
    )


class TestValidatorSecretPrefilter:
    def test_prefilter_matches_exactly_the_pattern_union(self) -> None:
        """The prefilter matches iff at least one pinned pattern matches."""
        probes = [
            "ordinary text, no secrets",
            "AKIAIOSFODNN7EXAMPLE",  # pinned AWS shape
            "ghp_" + "a" * 36,  # pinned GitHub token shape
            "-----BEGIN PRIVATE KEY-----",
            "x" * 5000,
        ]
        for text in probes:
            any_single = any(pattern.search(text) is not None for _, pattern in SECRET_PATTERNS)
            assert (rmv._SECRET_ANY.search(text) is not None) == any_single, text[:40]

    def test_clean_artifacts_skip_the_per_pattern_loop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        graph_root, db = seed_store(tmp_path)
        store = EventStore(db)
        projector = GraphProjector(store, output_root=tmp_path / "graph")
        upsert_node(store, _node("n-prefilter-0", 0))
        receipt = projector.repair_if_stale()
        generation = Path(receipt.generation_dir)
        manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))

        searches = {"count": 0}
        real_prefilter = rmv._SECRET_ANY

        class _CountingPrefilter:
            def search(self, text: str) -> object:
                searches["count"] += 1
                return real_prefilter.search(text)

        monkeypatch.setattr(rmv, "_SECRET_ANY", _CountingPrefilter())
        validator = rmv.ReadModelValidator()
        report = validator.validate(generation, manifest)
        assert report.ok
        artifacts_scanned = searches["count"]
        assert artifacts_scanned >= 1
        # The per-pattern loop would have run len(SECRET_PATTERNS) searches per
        # artifact; the prefilter runs exactly one per artifact on clean text.
        per_pattern_floor = artifacts_scanned * len(SECRET_PATTERNS)
        assert artifacts_scanned < per_pattern_floor  # trivially true; the pin is below

        pinned = {"calls": 0}
        real_patterns = rmv.SECRET_PATTERNS

        class _CountingPattern:
            def __init__(self, pattern: object) -> None:
                self._pattern = pattern

            def search(self, text: str) -> object:
                pinned["calls"] += 1
                return self._pattern.search(text)  # type: ignore[attr-defined]

        monkeypatch.setattr(rmv, "SECRET_PATTERNS", tuple((n, _CountingPattern(p)) for n, p in real_patterns))
        report2 = validator.validate(generation, manifest)
        assert report2.ok
        assert pinned["calls"] == 0, "clean artifacts must never reach the per-pattern loop"
        store.close()

    def test_poisoned_artifact_still_fails_closed(self, tmp_path: Path) -> None:
        graph_root, db = seed_store(tmp_path)
        store = EventStore(db)
        projector = GraphProjector(store, output_root=tmp_path / "graph")
        upsert_node(store, _node("n-prefilter-1", 1))
        receipt = projector.repair_if_stale()
        generation = Path(receipt.generation_dir)
        manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
        # Poison one artifact with a pinned secret shape; the hash check will
        # also fail, but the secret scan must still report its category.
        report_path = generation / "GRAPH_REPORT.md"
        report_path.write_text("leak: AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
        validator = rmv.ReadModelValidator()
        report = validator.validate(generation, manifest)
        assert not report.ok
        assert any("secret-shaped" in error for error in report.errors), report.errors
        store.close()
