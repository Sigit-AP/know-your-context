"""SUB-210: per-process RSS read-path budgets (RED before the fix).

The storm baseline (docs/research/rss-storm-plan.md, SUB-207) showed ~68 MB
per query process; the fix list targets the read-path structures this suite
pins with deterministic tracemalloc budgets (precedent:
tests/test_capability_audit_scaling.py, tests/test_store_scaling_guards.py).

Budgets are scale-relative and host-independent: tracemalloc measures Python
allocation, never OS RSS, so a 2-core CI box and a 64-core box see the same
number. Each budget sits with >= 2x headroom above the measured post-fix
value and well below the pre-fix value, so the tests are RED before the
SUB-210 changes and GREEN after.

Byte-identical query output is pinned separately (the knowledge payload is a
pure function of (store, query); ``audit_event_id`` and recommendation
``expiry`` are invocation-time fields and are masked).
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

from know_your_context.retrieval import BudgetedTraversal, LexicalIndex

# --------------------------------------------------------------------------
# LexicalIndex retained memory (the read path builds it per snapshot)
# --------------------------------------------------------------------------

# 20k synthetic code-style nodes. Pre-fix retention measured ~47 MB (per-node
# frozenset term sets); the inverted-postings design retains ~7 MB. Budget =
# 24 MB is ~3.4x headroom above post-fix and < 50% of pre-fix.
_INDEX_NODES = 20_000
_INDEX_RETAINED_BUDGET_BYTES = 24 * 1024 * 1024
_INDEX_PEAK_BUDGET_BYTES = 48 * 1024 * 1024


def _synthetic_nodes(count: int) -> list[dict]:
    return [
        {
            "id": f"src_mod_{i:06d}",
            "kind": "symbol" if i % 2 else "file",
            "domain": "P1" if i % 2 else "P2",
            "label": f"def parse_{i:06d}(config, cache, resolve) -> {i} results",
            "context_tags": ["parse", "cache", "config", "resolve"],
        }
        for i in range(count)
    ]


def test_lexical_index_construction_stays_within_budget() -> None:
    nodes = _synthetic_nodes(_INDEX_NODES)
    tracemalloc.start()
    index = LexicalIndex.from_graph_nodes(nodes)
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert index.size == _INDEX_NODES
    assert retained < _INDEX_RETAINED_BUDGET_BYTES, f"retained {retained / 1e6:.1f} MB"
    assert peak < _INDEX_PEAK_BUDGET_BYTES, f"peak {peak / 1e6:.1f} MB"


def test_lexical_index_rank_stays_within_budget() -> None:
    """rank() over the full index must not balloon (hits list is transient)."""
    index = LexicalIndex.from_graph_nodes(_synthetic_nodes(_INDEX_NODES))
    tracemalloc.start()
    hits = index.rank(("parse", "cache", "config", "resolve"))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert hits  # the query terms hit synthetic labels
    assert peak < _INDEX_PEAK_BUDGET_BYTES, f"rank peak {peak / 1e6:.1f} MB"


# --------------------------------------------------------------------------
# BudgetedTraversal link objects (one per graph.json link row)
# --------------------------------------------------------------------------

_TRAVERSAL_LINKS = 100_000
_TRAVERSAL_BUDGET_BYTES = 30 * 1024 * 1024
_TRAVERSAL_PEAK_BUDGET_BYTES = 45 * 1024 * 1024


def test_budgeted_traversal_link_objects_stay_within_budget() -> None:
    links = [
        {
            "source": f"src_mod_{i:06d}",
            "target": f"src_mod_{(i + 1) % _TRAVERSAL_LINKS:06d}",
            "relation": "calls",
            "confidence": "EXTRACTED",
        }
        for i in range(_TRAVERSAL_LINKS)
    ]
    tracemalloc.start()
    traversal = BudgetedTraversal.from_graph_json({"links": links})
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(traversal.links) == _TRAVERSAL_LINKS
    assert retained < _TRAVERSAL_BUDGET_BYTES, f"retained {retained / 1e6:.1f} MB"
    assert peak < _TRAVERSAL_PEAK_BUDGET_BYTES, f"peak {peak / 1e6:.1f} MB"


# --------------------------------------------------------------------------
# Read-path validation must not re-read artifact bytes on a warm verdict
# --------------------------------------------------------------------------

_ARTIFACT_PADDING_MB = 6
_LAZY_VALIDATE_PEAK_BUDGET_BYTES = 2 * 1024 * 1024


def _make_generation_dir(tmp_path: Path) -> tuple[Path, dict]:
    """A tiny published generation whose graph.json carries ~6 MB of payload.

    The graph stays valid for the deep validator (one node with a large
    label): the deep path must hash every artifact byte, the lazy read path
    must not read them at all.
    """
    generation = tmp_path / "gen"
    generation.mkdir(parents=True, exist_ok=True)
    from know_your_context.domain import canonical_json

    label = "x" * (_ARTIFACT_PADDING_MB * 1024 * 1024)
    graph = {
        "directed": True,
        "multigraph": False,
        "graph": {"schema_version": 1},
        "nodes": [
            {
                "id": "n1",
                "label": label,
                "file_type": "document",
                "source_file": "n1.md",
                "source_version_id": "sv1",
                "domain": "P1",
                "kind": "leaf",
            }
        ],
        "links": [],
        "hyperedges": [],
    }
    import hashlib

    graph_bytes = (canonical_json(graph) + "\n").encode("utf-8")
    report_bytes = b"# report\n"
    analysis_bytes = b"{}\n"
    labels_bytes = b"{}\n"
    signatures_bytes = b"{}\n"
    html_bytes = b"<html></html>\n"
    artifacts = {
        "graph.json": graph_bytes,
        "GRAPH_REPORT.md": report_bytes,
        "analysis.json": analysis_bytes,
        "labels.json": labels_bytes,
        "signatures.json": signatures_bytes,
        "graph.html": html_bytes,
    }
    artifact_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()}
    manifest = {
        "manifest_version": 1,
        "projection_version": "gen-test",
        "content_hash": "deadbeef",
        "source_knowledge_high_watermark": 1,
        "schema_version": 1,
        "accepted_schema_versions": [1],
        "partitioner": {"name": "networkx-louvain", "resolution": 1.0, "seed": 42},
        "hub_policy": {"ambiguous_scale": 0.5, "exclude_hubs_percentile": 99},
        "generator_version": "1.0.0",
        "adapter_version": "1.0.0",
        "fragment_fingerprint": "fp",
        "cache_namespace": None,
        "graph_content_hash": artifact_hashes["graph.json"],
        "artifacts": artifact_hashes,
        "node_count": 1,
        "edge_count": 0,
        "multigraph": False,
    }
    for name, data in artifacts.items():
        (generation / name).write_bytes(data)
    (generation / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    return generation, manifest


def test_warm_verdict_read_path_never_materializes_artifacts(tmp_path: Path) -> None:
    """After one deep validation, a fresh process's validate() must not
    materialize the artifact bytes: the verdict key streams fixed 64 KiB
    hashing chunks instead of one multi-MB read (SUB-210)."""
    from know_your_context.read_model_validator import ReadModelValidator, clear_validation_cache

    generation, manifest = _make_generation_dir(tmp_path)
    validator = ReadModelValidator(extra_forbidden_roots=(str(tmp_path.resolve()),))
    first = validator.validate(generation, manifest)
    assert first.ok, first.errors
    # A fresh process has a cold in-memory cache; only the disk verdict remains.
    clear_validation_cache()
    tracemalloc.start()
    report = validator.validate(generation, manifest)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert report.ok
    assert peak < _LAZY_VALIDATE_PEAK_BUDGET_BYTES, f"validate re-read artifacts: peak {peak / 1e6:.1f} MB"


# --------------------------------------------------------------------------
# Tamper detection: any byte drift must miss the verdict and fail closed —
# the key hashes every artifact (streaming), so nothing can be served from a
# stale verdict.
# --------------------------------------------------------------------------


def test_manifest_drift_misses_the_lazy_verdict_and_fails_closed(tmp_path: Path) -> None:
    from know_your_context.domain import canonical_json
    from know_your_context.read_model_validator import ReadModelValidator, clear_validation_cache

    generation, manifest = _make_generation_dir(tmp_path)
    validator = ReadModelValidator(extra_forbidden_roots=(str(tmp_path.resolve()),))
    assert validator.validate(generation, manifest).ok
    clear_validation_cache()
    # Re-write manifest.json with a drifted artifact-hash map (the on-disk
    # manifest bytes are part of the lazy key, so the verdict misses).
    drifted = dict(manifest)
    drifted["artifacts"] = {**manifest["artifacts"], "GRAPH_REPORT.md": "0" * 64}
    (generation / "manifest.json").write_text(canonical_json(drifted) + "\n", encoding="utf-8")
    report = validator.validate(generation, manifest)
    assert not report.ok, "a manifest drift must miss the lazy verdict and fail closed"
    assert any("hash mismatch" in error or "does not match" in error for error in report.errors)


# --------------------------------------------------------------------------
# Byte-identical query output (knowledge payload is a pure function of the
# store + query; audit_event_id and recommendation expiry are masked)
# --------------------------------------------------------------------------

GOLDEN_QUERY_PAYLOAD = {
    "access_only": False,
    "candidates": [],
    "confidence": "0.000000000000",
    "estimated_tokens": 550,
    "knowledge_payload_tokens": 472,
    "node_versions": {"P1": 1, "document:07": 1, "document:08": 1, "module:calibration": 1, "section:07": 1},
    "projection_version": "gen-99c420ea978cf2699029bed2",
    "provenance": ["context://corpus/soul.md"],
    "recommendations": {
        "best_actions": [],
        "recommendations": [
            {
                "action_id": "act-document:07",
                "evidence_ids": [],
                "label": "operating_procedure node document:07",
                "learned_class": "operating_procedure",
                "promoted": False,
                "reason_codes": ["UNSCORED"],
                "source_node_ids": ["document:07"],
                "source_node_versions": [1],
            },
            {
                "action_id": "act-module:calibration",
                "evidence_ids": [],
                "label": "operating_procedure node module:calibration",
                "learned_class": "operating_procedure",
                "promoted": False,
                "reason_codes": ["UNSCORED"],
                "source_node_ids": ["module:calibration"],
                "source_node_versions": [1],
            },
            {
                "action_id": "act-section:07",
                "evidence_ids": [],
                "label": "operating_procedure node section:07",
                "learned_class": "operating_procedure",
                "promoted": False,
                "reason_codes": ["UNSCORED"],
                "source_node_ids": ["section:07"],
                "source_node_versions": [1],
            },
        ],
        "unnecessary_actions": [],
    },
    "required_knowledge_watermark": 7,
    "results": [
        {
            "confidence": "0.000000000000",
            "domain": "P1",
            "kind": "leaf",
            "node_id": "document:07",
            "p": "0.000000000000",
            "summary": "07-calibration-bayes.md",
            "version": 1,
        },
        {
            "confidence": "0.000000000000",
            "domain": "P1",
            "kind": "branch",
            "node_id": "module:calibration",
            "p": "0.000000000000",
            "summary": "Calibration and base rate",
            "version": 1,
        },
        {
            "confidence": "0.000000000000",
            "domain": "P1",
            "kind": "leaf",
            "node_id": "section:07",
            "p": "0.000000000000",
            "summary": "SOUL 07 — Calibration and base rate",
            "version": 1,
        },
        {
            "confidence": "0.000000000000",
            "domain": "P1",
            "kind": "primary",
            "node_id": "P1",
            "p": "0.000000000000",
            "summary": "Pembelajaran",
            "version": 1,
        },
        {
            "confidence": "0.000000000000",
            "domain": "P1",
            "kind": "leaf",
            "node_id": "document:08",
            "p": "0.000000000000",
            "summary": "08-anti-bias.md",
            "version": 1,
        },
    ],
    "routing": "ranked",
    "source_knowledge_high_watermark": 7,
    "stale": False,
    "truncated": False,
}


def _masked(payload: dict) -> dict:
    """Drop invocation-time fields (audit event id, recommendation expiry)."""
    cleaned = {key: value for key, value in payload.items() if key != "audit_event_id"}
    recommendations = cleaned.get("recommendations") or {}
    if isinstance(recommendations, dict) and "recommendations" in recommendations:
        for item in recommendations["recommendations"]:
            item.pop("expiry", None)
    return cleaned


def _write_golden_store(root: Path) -> None:
    """Deterministic knowledge corpus via the public store event API.

    Two primary roots, one with a calibration subtree, so ``calibration``
    routes to P1 and ranks content. Written with ``store.append_domain`` —
    no ingest pipeline, so this test never depends on the (concurrently
    evolving) ingest machinery.
    """
    from datetime import UTC, datetime

    from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain, ScoreVector
    from know_your_context.projection import GraphProjector
    from know_your_context.store import EventStore

    now = datetime(2026, 1, 1, tzinfo=UTC)

    def node(
        node_id: str,
        label: str,
        *,
        kind: NodeKind = NodeKind.LEAF,
        parent: str | None = None,
        domain: PrimaryDomain = PrimaryDomain.P1,
        tags: tuple[str, ...] = (),
    ) -> Node:
        return Node(
            node_id=node_id,
            parent_id=parent,
            kind=kind,
            domain=domain,
            summary=label,
            provenance=("context://corpus/soul.md",),
            context_tags=tags,
            scores=ScoreVector.zero(),
            created_at=now,
            updated_at=now,
            source_version_id="sv-golden",
            payload={"source_file": f"{label}.md"},
        )

    store = EventStore(root / "store.sqlite3")
    try:
        nodes = {
            "P1": node("P1", "Pembelajaran", kind=NodeKind.PRIMARY, domain=PrimaryDomain.P1, tags=("P1",)),
            "module:calibration": node(
                "module:calibration", "Calibration and base rate", kind=NodeKind.BRANCH, parent="P1"
            ),
            "document:07": node("document:07", "07-calibration-bayes.md", parent="module:calibration"),
            "section:07": node("section:07", "SOUL 07 — Calibration and base rate", parent="module:calibration"),
            "document:08": node("document:08", "08-anti-bias.md", parent="module:calibration"),
            "P2": node("P2", "Gagasan", kind=NodeKind.PRIMARY, domain=PrimaryDomain.P2, tags=("P2",)),
            "module:experiments": node(
                "module:experiments",
                "Experiments and pretotypes",
                kind=NodeKind.BRANCH,
                parent="P2",
                domain=PrimaryDomain.P2,
            ),
        }
        for node_id, node_obj in nodes.items():
            store.append_domain(
                Event(
                    kind=EventKind.NODE_UPSERT,
                    node_id=node_id,
                    domain=node_obj.domain,
                    node=node_obj,
                    source_version_id=node_obj.source_version_id,
                    occurred_at=now,
                ),
                idempotency_key=f"golden:{node_id}",
            )
        projector = GraphProjector(store, output_root=root / "projection")
        projector.rebuild(allow_shrink=True)
    finally:
        store.close()


def test_query_knowledge_payload_is_byte_identical(tmp_path: Path) -> None:
    """The canonical output of one query is byte-for-byte frozen: the routing,
    results, provenance and versions are a pure function of (store, query).

    Golden captured from the pre-SUB-210 code (HEAD) on this exact store —
    any change to ranking, routing, traversal or result assembly breaks it.
    """
    from know_your_context.http_api import build_stack, dispatch_text

    _write_golden_store(tmp_path)
    stack = build_stack(tmp_path / "store.sqlite3", tmp_path / "projection")
    try:
        response = dispatch_text(
            stack,
            "query",
            json.dumps({"purpose": "calibration bayes", "token_budget": 1024}),
            actor="sub210-test",
        )
    finally:
        stack.close()
    payload = response.model_dump(mode="json")
    assert _masked(payload) == _masked(dict(GOLDEN_QUERY_PAYLOAD)), json.dumps(
        _masked(payload), indent=1, sort_keys=True
    )[:4000]
