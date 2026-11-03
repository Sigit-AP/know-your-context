"""W16 (SUB-137): read-model validation verdict memoization pins.

``ReadModelValidator.validate`` is a pure function of (manifest bytes, artifact
bytes): same content, same verdict. Under the storm the SAME generation was
re-validated constantly — once inside ``rebuild``, again by the writer
service's first ``_try_snapshot``, and once more by every read worker's first
snapshot — ~25ms each, with 61% of that in full-text regex scans. The verdict
cache keys on the sha256 of every input byte read from disk, so a tampered or
rewritten artifact is a cache MISS and is fully re-validated — caching can
never serve a verdict for content that is not on disk right now.

Pins: repeat validation does zero artifact scans; tampering is always caught;
cache is statically bounded; warm and cold verdicts are identical.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import know_your_context.read_model_validator as rmv
from _serve_helpers import seed_store, upsert_node
from know_your_context.domain import Node, NodeKind, PrimaryDomain
from know_your_context.projection import GraphProjector
from know_your_context.store import EventStore


def _node(node_id: str, index: int) -> Node:
    return Node(
        node_id=node_id,
        parent_id="P1",
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary=f"verdict cache pin node {index}",
        provenance=("context://verdict-cache",),
        source_version_id="sv-test",
    )


@pytest.fixture()
def generation(tmp_path: Path):
    graph_root, db = seed_store(tmp_path)
    store = EventStore(db)
    projector = GraphProjector(store, output_root=tmp_path / "graph")
    for index in range(6):
        upsert_node(store, _node(f"n-vc-{index:02d}", index))
    receipt = projector.repair_if_stale()
    gen_dir = Path(receipt.generation_dir)
    manifest = json.loads((gen_dir / "manifest.json").read_text(encoding="utf-8"))
    yield store, gen_dir, manifest
    store.close()


class TestValidationVerdictCache:
    def test_repeat_validation_performs_no_artifact_scans(
        self, generation: tuple[EventStore, Path, dict], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, gen_dir, manifest = generation
        validator = rmv.ReadModelValidator()
        rmv.clear_validation_cache()
        first = validator.validate(gen_dir, manifest)
        assert first.ok

        scans = {"count": 0}
        real_prefilter = rmv._SECRET_ANY

        class _CountingPrefilter:
            def search(self, text: str) -> object:
                scans["count"] += 1
                return real_prefilter.search(text)

        monkeypatch.setattr(rmv, "_SECRET_ANY", _CountingPrefilter())
        second = validator.validate(gen_dir, manifest)
        assert second.ok
        assert scans["count"] == 0, "a byte-identical re-validation must not re-scan artifacts"

    def test_tampered_artifact_is_never_served_from_cache(self, generation: tuple[EventStore, Path, dict]) -> None:
        _, gen_dir, manifest = generation
        validator = rmv.ReadModelValidator()
        rmv.clear_validation_cache()
        assert validator.validate(gen_dir, manifest).ok
        # Flip one byte inside graph.json: the content hash changes, the cache
        # must miss, and the full validation must catch the hash mismatch.
        graph_path = gen_dir / "graph.json"
        raw = graph_path.read_bytes()
        graph_path.write_bytes(raw[:-2] + b" }")
        tampered = validator.validate(gen_dir, manifest)
        assert not tampered.ok, "a tampered generation was served from the verdict cache"

    def test_warm_and_cold_verdicts_are_identical(self, generation: tuple[EventStore, Path, dict]) -> None:
        _, gen_dir, manifest = generation
        validator = rmv.ReadModelValidator()
        rmv.clear_validation_cache()
        cold = validator.validate(gen_dir, manifest)
        warm = validator.validate(gen_dir, manifest)
        assert (cold.ok, cold.errors, cold.node_count, cold.edge_count, cold.collapsed_links) == (
            warm.ok,
            warm.errors,
            warm.node_count,
            warm.edge_count,
            warm.collapsed_links,
        )

    def test_cache_is_statically_bounded(self, generation: tuple[EventStore, Path, dict]) -> None:
        _, gen_dir, manifest = generation
        validator = rmv.ReadModelValidator()
        rmv.clear_validation_cache()
        for _ in range(3):
            validator.validate(gen_dir, manifest)
        assert rmv.validation_cache_size() <= rmv.VALIDATION_CACHE_MAXSIZE
        assert rmv.VALIDATION_CACHE_MAXSIZE <= 256
