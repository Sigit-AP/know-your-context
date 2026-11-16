"""B1 (SUB-174): the knowledge projection must be able to carry an ingested corpus.

``ingest --apply`` publishes ``graph.json`` — the single read-model artifact
pinned by ``retrieval-verbs-v2`` — and the publish gate is fail-closed at
``ARTIFACT_MAX_BYTES`` (64 KiB, the hand-authored-knowledge default). That cap
was sized for a docs corpus of a few dozen nodes, so a *code* corpus turns the
official path into a dead end: measured on the stress L1 corpus (100 files,
1,279 nodes) the apply step died with

    OversizedArtifactError: artifact graph.json is 958728 bytes,
    over the 65536-byte publish cap

which leaves ``query`` exactly as blind as before the apply lane existed. The
cap is the F-3 guard against a store whose projection can never publish, so the
fix is not to delete it: it is to size it from the store's own code index
(deterministic, identical in every process, 64 KiB when there is no code index,
absolute ceiling beyond which the lane refuses typed instead of wedging).

The guard stays: an explicit ``max_artifact_bytes`` below the payload still
raises ``OversizedArtifactError``, and a corpus over the absolute ceiling is a
typed refusal, never a silent half-published generation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from know_your_context.contracts import QueryRequest
from know_your_context.ingest.apply import ApplyError, apply_ingested_corpus
from know_your_context.ingest.pipeline import IngestionBudget, IngestionPipeline
from know_your_context.projection import (
    ARTIFACT_MAX_BYTES,
    ARTIFACT_MAX_BYTES_CEILING,
    GraphProjector,
    OversizedArtifactError,
    resolve_artifact_cap,
    resolve_fragment_bounds,
)
from know_your_context.service import KnowledgeService
from know_your_context.store import EventStore

# 40 modules x (1 class + 3 methods + 3 functions) keeps the applied graph well
# over 64 KiB while staying a sub-second ingest.
MODULE_TEMPLATE = """\
class Cls{n}:
    def add(self, a, b):
        return a + b

    def merge(self, other):
        return Cls{n}()

    def summary(self):
        return "cls{n}"


def fn_{n}_0(value):
    return value


def fn_{n}_1(value):
    return fn_{n}_0(value)


def fn_{n}_2(value):
    return fn_{n}_1(value)
"""


def _corpus(root: Path, modules: int = 40) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for n in range(modules):
        (root / f"mod{n}.py").write_text(MODULE_TEMPLATE.format(n=n), encoding="utf-8")
    return root


def _ingest(tmp_path: Path, corpus: Path) -> EventStore:
    store = EventStore(tmp_path / "soul.sqlite3")
    pipeline = IngestionPipeline(
        store,
        budget=IngestionBudget(max_files=65536, max_nodes=1_000_000, max_edges=1_000_000, max_refs=1_000_000),
        actor="test",
    )
    pipeline.ingest_path(corpus, incremental=True, recursive=True, resolve=True)
    return store


def test_default_projector_publishes_an_ingested_corpus(tmp_path: Path) -> None:
    """The measured failure: apply dies at 64 KiB and query stays blind."""
    corpus = _corpus(tmp_path / "corpus")
    store = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out")

    receipt = apply_ingested_corpus(store, corpus_root=corpus, projector=projector)
    assert receipt.projection_version, "the apply must publish a generation covering the corpus"

    service = KnowledgeService(store, GraphProjector(store, output_root=tmp_path / "out"))
    response = service.query(QueryRequest(purpose="Cls17", token_budget=1024))
    assert response.stale is False
    summaries = {node.summary for node in response.results}
    assert "Cls17" in summaries, f"query must find the ingested class, got {sorted(summaries)}"
    store.close()


def test_cap_is_the_docs_default_without_a_code_index(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "empty.sqlite3")
    assert resolve_artifact_cap(store) == ARTIFACT_MAX_BYTES
    store.close()


def test_cap_scales_with_the_code_index_and_stays_deterministic(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus", modules=10)
    store = _ingest(tmp_path, corpus)
    cap = resolve_artifact_cap(store)
    assert cap > ARTIFACT_MAX_BYTES
    assert cap <= ARTIFACT_MAX_BYTES_CEILING
    assert cap == resolve_artifact_cap(store), "the cap is a pure function of the store"
    store.close()


def test_explicit_cap_below_the_payload_still_fails_closed(tmp_path: Path) -> None:
    """The F-3 guard must survive the fix: a too-small explicit cap still throws."""
    corpus = _corpus(tmp_path / "corpus", modules=4)
    store = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out", max_artifact_bytes=256)
    with pytest.raises(OversizedArtifactError):
        apply_ingested_corpus(store, corpus_root=corpus, projector=projector)
    store.close()


def test_build_gate_bounds_scale_with_the_code_index(tmp_path: Path) -> None:
    """The pinned fragment bounds (10k nodes / 100k edges / 25 MB) gate one
    LLM *fragment*; a projection carries a whole corpus. L2-scale measured:
    12,927 nodes died at the gate with ``nodes has 12927 entries; max is
    10000`` — the apply lane's work could never publish. The gate's count
    bounds scale from the store's code index (same rule as the byte cap);
    the per-item checks (id charset, schema) are untouched."""
    corpus = _corpus(tmp_path / "corpus", modules=10)
    store = _ingest(tmp_path, corpus)
    files, symbols = store.code_index_size()
    assert files + symbols > 0

    empty_store = EventStore(tmp_path / "empty.sqlite3")
    default_bounds = resolve_fragment_bounds(empty_store)
    scaled = resolve_fragment_bounds(store)
    assert default_bounds == (10_000, 100_000, 25 * 1024 * 1024), "no code index keeps the pinned defaults"
    max_nodes, max_edges, max_payload = scaled
    assert max_nodes == 10_000 + files + symbols + 64, "additive: docs allowance + code index + roots"
    assert max_edges >= 2 * max_nodes, "parent links alone need one edge per node"
    assert max_payload == resolve_artifact_cap(store), "one consistent store-derived cap"
    assert scaled == resolve_fragment_bounds(store), "a pure function of the store"

    # The gate honours the scaled counts: the pinned default rejects 10k+1
    # nodes, the store-scaled gate carries them when the code index says so.
    adapter = GraphProjector(store, output_root=tmp_path / "out")._adapter
    node = {"id": "n0", "source_file": "m.py", "file_type": "document", "label": "x"}
    over_default = {
        "nodes": [{**node, "id": f"n{i}"} for i in range(10_001)],
        "edges": [],
        "hyperedges": [],
    }
    assert any("nodes has" in e for e in adapter.validate_extraction_strict(over_default))
    scaled_errors = adapter.validate_extraction_strict(over_default, max_nodes=10_001 + files + symbols)
    assert not any("nodes has" in e for e in scaled_errors), scaled_errors[:3]
    # Per-item checks are not rescaled: a bad id still fails under the scaled gate.
    bad = {
        "nodes": [{"id": "bad id!", "source_file": "m.py", "file_type": "document", "label": "x"}],
        "edges": [],
        "hyperedges": [],
    }
    assert adapter.validate_extraction_strict(bad, max_nodes=10**9), "per-item checks stay pinned"
    store.close()
    empty_store.close()


def test_build_gate_still_refuses_beyond_the_scaled_bound(tmp_path: Path) -> None:
    """The scaled gate is a guard, not a removal: counts beyond the store's
    own code index still fail closed."""
    corpus = _corpus(tmp_path / "corpus", modules=4)
    store = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    max_nodes, _, _ = resolve_fragment_bounds(store)
    over = [{"id": f"n{i}", "source_file": "m.py"} for i in range(max_nodes + 1)]
    big = {"nodes": over, "edges": [], "hyperedges": []}
    errors = projector._build_gate_errors(big)
    assert any("nodes has" in error for error in errors), errors
    store.close()


def test_corpus_over_the_absolute_ceiling_is_refused_typed(tmp_path: Path) -> None:
    """Beyond the ceiling the lane refuses with a typed error, never a wedged store."""
    corpus = _corpus(tmp_path / "corpus", modules=4)
    store = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    with pytest.raises(ApplyError, match="artifact cap"):
        apply_ingested_corpus(store, corpus_root=corpus, projector=projector, artifact_cap_bytes=1)
    store.close()
