"""W16 (SUB-137): lifecycle-meta fast path pins.

``KnowledgeService._meta_after`` names the generation covering a lifecycle
write. It only needs the manifest's ``projection_version`` and watermark, but
it went through ``_snapshot`` — which reads graph.json, re-validates the
generation, parses the whole graph and builds a BudgetedTraversal — purely to
read two manifest fields. In the storm that parse ran once per lifecycle
mutation. The fast path answers from the active manifest (same coverage
checks, same — now verdict-cached — validation gate) and reserves the full
snapshot build for content-serving reads.

Pins: metadata is identical to the slow path; no graph parse happens for
meta; a stale manifest still routes through repair and ends covered.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import know_your_context.service as service_module
from _serve_helpers import seed_store, upsert_node
from know_your_context.audit import AuditLog
from know_your_context.contracts import EvaluateRequest
from know_your_context.domain import Node, NodeKind, PrimaryDomain
from know_your_context.projection import GraphProjector
from know_your_context.service import KnowledgeService
from know_your_context.store import EventStore


def _node(node_id: str, index: int) -> Node:
    return Node(
        node_id=node_id,
        parent_id="P1",
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary=f"meta fastpath node {index}",
        provenance=("context://meta-fastpath",),
        source_version_id="sv-test",
    )


def _service(tmp_path: Path) -> tuple[EventStore, GraphProjector, KnowledgeService]:
    graph_root, db = seed_store(tmp_path)
    store = EventStore(db)
    projector = GraphProjector(store, output_root=tmp_path / "graph")
    service = KnowledgeService(store, projector, audit_log=AuditLog(store))
    return store, projector, service


class TestMetaAfterFastPath:
    def test_meta_matches_manifest_without_graph_parse(self, tmp_path: Path, monkeypatch) -> None:
        store, projector, service = _service(tmp_path)
        upsert_node(store, _node("n-meta-0", 0))
        projector.repair_if_stale()

        parses = {"count": 0}
        real_from_graph_json = service_module.BudgetedTraversal.from_graph_json

        def counting_from_graph_json(graph: Mapping[str, Any]) -> Any:
            parses["count"] += 1
            return real_from_graph_json(graph)

        monkeypatch.setattr(service_module.BudgetedTraversal, "from_graph_json", counting_from_graph_json)

        response = service.evaluate(EvaluateRequest(node_id="n-meta-0", include_descendants=False))
        generation = projector.active_generation_dir()
        assert generation is not None
        manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
        assert response.stale is False
        assert response.projection_version == manifest["projection_version"]
        assert response.source_knowledge_high_watermark == manifest["source_knowledge_high_watermark"]
        assert response.required_knowledge_watermark == store.knowledge_high_watermark()
        assert parses["count"] == 0, "the meta fast path must never parse graph.json"
        store.close()

    def test_stale_manifest_still_repairs_and_covers(self, tmp_path: Path) -> None:
        store, _, service = _service(tmp_path)
        upsert_node(store, _node("n-meta-2", 2))
        response = service.evaluate(EvaluateRequest(node_id="n-meta-2", include_descendants=False))
        assert response.stale is False
        assert response.source_knowledge_high_watermark == store.knowledge_high_watermark()
        assert response.required_knowledge_watermark == store.knowledge_high_watermark()
        store.close()
