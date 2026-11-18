"""SUB-215: empty-extraction publish must not import the graphify package.

RED (measured): building a generation from a store with zero knowledge events
pulled the ENTIRE graphify package into the builder process — ~400 modules
including the full language-extractor set, ~20 MB RSS (probe: 397 modules,
32.9 -> 53.8 MB) — through the build gate (``resolve_fragment_bounds``),
the adapter build/partition/analysis and the deep read-model validation.
Every one of those is provably vacuous when the extraction has zero nodes:
no count, byte cap, schema, path, secret or confidence rule can fire on an
empty graph, and the generation is content-addressed, so the bytes are the
canonical empty generation every prior run has already validated.

GREEN: an empty extraction is published from pinned byte-identical artifacts
without importing ``graphify``; per-artifact size caps are still enforced at
the write point; the full read-model validator still passes the published
generation; a second rebuild is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from know_your_context.projection import ARTIFACT_NAMES, GraphProjector
from know_your_context.read_model_validator import ReadModelValidator
from know_your_context.store import EventStore

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)

#: sha256 of the canonical empty ``graph.json`` — zero nodes, zero links,
#: ``{"directed":true,"graph":{"generator":"know-your-context",
#: "partitioner":"networkx-louvain","schema_version":1},"hyperedges":[],
#: "links":[],"multigraph":false,"nodes":[]}``. Pinned so a fast-path byte
#: drift fails closed instead of silently publishing a different artifact
#: set under the same content identity rules.
EMPTY_GRAPH_SHA256 = "3da26c234a35f603a4eaefff1cddcaa1e354d51ac98ab97a7c8ed62108e8fb12"


def _empty_projection(tmp_path: Path) -> tuple[EventStore, GraphProjector, Path]:
    store = EventStore(str(tmp_path / "store.sqlite3"))
    output = tmp_path / "projection"
    projector = GraphProjector(store, output_root=output)
    return store, projector, output


def _new_graphify_modules(before: set[str]) -> list[str]:
    after = set(sys.modules)
    return sorted(m for m in after - before if m == "graphify" or m.startswith("graphify."))


def test_empty_rebuild_imports_no_graphify(tmp_path: Path) -> None:
    store, projector, _output = _empty_projection(tmp_path)
    before = set(sys.modules)
    receipt = projector.rebuild()
    assert _new_graphify_modules(before) == [], (
        "an empty-extraction rebuild must not import the graphify package "
        f"(imported: {_new_graphify_modules(before)[:5]}...)"
    )
    assert receipt.node_count == 0
    assert receipt.edge_count == 0
    assert receipt.activated is True
    assert receipt.projection_version.startswith("gen-")


def test_empty_generation_matches_pinned_canonical_bytes(tmp_path: Path) -> None:
    store, projector, output = _empty_projection(tmp_path)
    before = set(sys.modules)
    receipt = projector.rebuild()
    assert _new_graphify_modules(before) == []
    generation = Path(receipt.generation_dir)
    assert generation.is_dir()
    for name in ARTIFACT_NAMES:
        assert (generation / name).is_file(), f"missing artifact {name}"
    graph_bytes = (generation / "graph.json").read_bytes()
    assert hashlib.sha256(graph_bytes).hexdigest() == EMPTY_GRAPH_SHA256
    manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["node_count"] == 0
    assert manifest["edge_count"] == 0
    assert receipt.content_hash == manifest["content_hash"]
    # The fast path still produces a generation the FULL validator accepts.
    report = ReadModelValidator(extra_forbidden_roots=(str(output.resolve()),)).validate(generation, manifest)
    assert report.ok, report.errors
    assert report.node_count == 0 and report.edge_count == 0


def test_empty_rebuild_is_idempotent(tmp_path: Path) -> None:
    store, projector, output = _empty_projection(tmp_path)
    receipt1 = projector.rebuild()
    receipt2 = projector.rebuild()
    assert receipt1.content_hash == receipt2.content_hash
    assert receipt1.generation_dir == receipt2.generation_dir
    assert len(list(Path(output).glob("generations/*"))) == 1
