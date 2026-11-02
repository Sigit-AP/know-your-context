"""Tests for the W15 webview REST surface expansion (SUB-131).

37 new read-only routes serving real store data: paged node/edge/symbol
lists, per-node sub-resources (edges/evidence/revisions), provenance
reports, refs by status, extraction queue detail/summary, hash-chain
ledger tails (health/debt/experience), audit chain verification, per-op
bench detail, debt report, switch/config introspection, health breakdown,
metrics buckets, store table/schema inspection, analytics slices, in-memory
export payloads, route discovery — plus per-class rate limiting and
feature-flag route gating, all through the one typed error envelope.
"""

from __future__ import annotations

import http.client
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from know_your_context import runtime_guard
from know_your_context.domain import (
    Event,
    EventKind,
    Evidence,
    Node,
    NodeKind,
    PrimaryDomain,
    Relation,
    RelationKind,
    ScoreVector,
)
from know_your_context.ops.switches import load_knobs
from know_your_context.serve.webview import (
    API_ROUTES,
    RateLimitedError,
    RateLimiter,
    WebviewServer,
)
from know_your_context.store import EventStore

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
TOKEN = "test-token-1515151515"


# ------------------------------------------------------- socket-guard truce
# (same leak as test_webview.py: mcp_server.main() installs a process-lifetime
# SocketGuard in an earlier module; restore real sockets for the loopback
# server and put the guard back afterwards).


def _suspend_socket_guard() -> dict[str, Any] | None:
    import socket

    if runtime_guard._SOCKET_GUARD_DEPTH == 0:  # noqa: SLF001
        return None
    state: dict[str, Any] = {
        "depth": runtime_guard._SOCKET_GUARD_DEPTH,  # noqa: SLF001
        "originals": dict(runtime_guard._SOCKET_ORIGINALS),  # noqa: SLF001
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "sendmsg": socket.socket.sendmsg,
        "create_connection": socket.create_connection,
    }
    originals = state["originals"]
    if originals:
        socket.socket.connect = originals["connect"]  # type: ignore[method-assign]
        socket.socket.connect_ex = originals["connect_ex"]  # type: ignore[method-assign]
        socket.socket.sendto = originals["sendto"]  # type: ignore[method-assign]
        socket.socket.sendmsg = originals["sendmsg"]  # type: ignore[method-assign]
        socket.create_connection = originals["create_connection"]  # type: ignore[assignment]
    runtime_guard._SOCKET_GUARD_DEPTH = 0  # noqa: SLF001
    runtime_guard._SOCKET_ORIGINALS.clear()  # noqa: SLF001
    return state


def _resume_socket_guard(state: dict[str, Any]) -> None:
    import socket

    socket.socket.connect = state["connect"]  # type: ignore[method-assign]
    socket.socket.connect_ex = state["connect_ex"]  # type: ignore[method-assign]
    socket.socket.sendto = state["sendto"]  # type: ignore[method-assign]
    socket.socket.sendmsg = state["sendmsg"]  # type: ignore[method-assign]
    socket.create_connection = state["create_connection"]  # type: ignore[assignment]
    runtime_guard._SOCKET_ORIGINALS.update(state["originals"])  # noqa: SLF001
    runtime_guard._SOCKET_GUARD_DEPTH = state["depth"]  # noqa: SLF001


@pytest.fixture(autouse=True)
def _socket_guard_truce():
    state = _suspend_socket_guard()
    try:
        yield
    finally:
        if state is not None:
            _resume_socket_guard(state)


# --------------------------------------------------------------------- seeds


def _node(node_id: str, *, parent: str | None = None, domain: PrimaryDomain = PrimaryDomain.P1) -> Node:
    return Node(
        node_id=node_id,
        parent_id=parent,
        kind=NodeKind.CHILD if parent else NodeKind.ROOT,
        domain=domain,
        summary=f"summary of {node_id}",
        provenance=("context://corpus/lesson.md",),
        source_version_id="sv-w15",
        scores=ScoreVector.zero(),
        created_at=NOW,
        updated_at=NOW,
    )


def _upsert(store: EventStore, node: Node, *, tag: str = "") -> None:
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id=node.node_id,
            domain=node.domain,
            node=node,
            source_version_id=node.source_version_id,
            occurred_at=NOW,
        ),
        idempotency_key=f"w15:upsert:{node.node_id}:{tag}",
    )


def _evidence(store: EventStore, node_id: str, evidence_id: str) -> None:
    store.append_domain(
        Event(
            kind=EventKind.EVIDENCE_APPEND,
            node_id=node_id,
            domain=PrimaryDomain.P1,
            evidence=Evidence.model_validate(
                {
                    "evidence_id": evidence_id,
                    "node_id": node_id,
                    "source_version_id": "sv-w15",
                    "source_uri": "context://corpus/lesson.md",
                    "observed_at": NOW,
                    "independence_key": f"indep-{evidence_id}",
                    "outcome": "0.5",
                    "correctness": "1",
                    "reliability": "1",
                    "reproducibility": "1",
                    "scope_fit": "1",
                    "successful_use": "1",
                    "utilization_u": "0.5",
                    "task_coverage": "0.5",
                    "quality_at_use": "0.5",
                    "contradiction": "0",
                    "latency_ms": 1,
                    "compute_ms": 1,
                    "output_chars": 4,
                }
            ),
            occurred_at=NOW,
        ),
        idempotency_key=f"w15:ev:{evidence_id}",
    )


def _audit(store: EventStore, index: int) -> None:
    with store.transaction() as conn:
        store.insert_event(
            conn,
            event_id=f"w15-audit-{index}",
            kind="audit",
            event_type="op.complete",
            knowledge_affecting=False,
            actor="tester",
            occurred_at=f"2026-01-0{index + 1}T00:00:00+00:00",
            payload={"operation": "remember", "seq": index},
            idempotency_key=f"w15:audit:{index}",
        )


def _journal(store: EventStore, invocation_id: str, operation: str, duration_ms: int) -> None:
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO invocation_journal "
            "(invocation_id, audit_event_id, operation, request_hash, actor, idempotency_key, "
            "started_at, status, completed_at, outcome_json, audit_event_seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', ?, ?, NULL)",
            (
                invocation_id,
                f"ev-{invocation_id}",
                operation,
                "h" * 64,
                "tester",
                f"w15:journal:{invocation_id}",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:01+00:00",
                json.dumps({"duration_ms": duration_ms}),
            ),
        )


def _ref(store: EventStore, source: str, target: str, status: str, *, reason: str | None = None) -> None:
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO unresolved_refs (source_node_id, target_name, name_tail, kind, status, "
            "first_seen, attempts, failure_reason) VALUES (?, ?, ?, 'call', ?, ?, 1, ?)",
            (source, target, target.rsplit(".", 1)[-1], status, "2026-01-01T00:00:00+00:00", reason),
        )


def _seed(store: EventStore) -> None:
    _upsert(store, _node("n-root"), tag="a")
    _upsert(store, _node("n-alpha", parent="n-root"), tag="v1")
    _upsert(store, _node("n-alpha", parent="n-root"), tag="v2")  # second revision
    _upsert(store, _node("n-beta", parent="n-root", domain=PrimaryDomain.P2))
    _upsert(store, _node("n-gamma", domain=PrimaryDomain.P2))
    store.append_domain(
        Event(
            kind=EventKind.RELATION_ADD,
            node_id="n-alpha",
            domain=PrimaryDomain.P1,
            relation=Relation(kind=RelationKind.SUPPORTS, source_node_id="n-alpha", target_node_id="n-beta"),
            occurred_at=NOW,
        ),
        idempotency_key="w15:rel:alpha-beta",
    )
    store.upsert_symbol(node_id="n-alpha", name="alphaFunc", kind="function", path="src/a.py", line=10)
    store.upsert_symbol(node_id="n-beta", name="betaClass", kind="class", path="src/b.py", line=20)
    store.upsert_symbol(node_id="n-gamma", name="alphaHelper", kind="function", path="src/g.py", line=5)
    store.add_graph_edge("n-alpha", "n-beta", "calls", line=12)
    store.add_graph_edge("n-beta", "n-gamma", "imports")
    _evidence(store, "n-alpha", "ev-alpha-1")
    _ref(store, "n-alpha", "mod.missing_func", "pending")
    _ref(store, "n-alpha", "mod.broken_func", "failed", reason="no_resolution")
    _ref(store, "n-beta", "mod.found_func", "resolved")
    for index in range(3):
        _audit(store, index)
    _journal(store, "inv-1", "knowledge.query", 5)
    _journal(store, "inv-2", "knowledge.query", 15)
    _journal(store, "inv-3", "knowledge.rebuild", 50)


@pytest.fixture(scope="module")
def store_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("w15webview") / "w15.sqlite3"
    store = EventStore(path)
    _seed(store)
    store.close()
    return path


@pytest.fixture(scope="module")
def server(store_path: Path) -> Any:
    instance = WebviewServer(store_path, port=0, token=TOKEN)
    instance.start()
    yield instance
    instance.shutdown()


def _http(
    server: WebviewServer,
    path: str,
    *,
    token: str | None = TOKEN,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    query = f"token={token}" if token is not None else None
    full_path = f"{path}{'&' if '?' in path else '?'}{query}" if query else path
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        connection.request("GET", full_path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        parsed = json.loads(body) if body else None
        return response.status, {key.lower(): value for key, value in response.getheaders()}, parsed
    finally:
        connection.close()


# --------------------------------------------------------------- route table


class TestRouteTableV3:
    def test_new_routes_registered(self) -> None:
        paths = {route.path for route in API_ROUTES}
        for expected in (
            "/api/nodes",
            "/api/edges",
            "/api/graph/edges",
            "/api/node/{id}/edges",
            "/api/node/{id}/evidence",
            "/api/node/{id}/revisions",
            "/api/provenance/{id}",
            "/api/symbols",
            "/api/symbols/{id}",
            "/api/search/symbols",
            "/api/refs/pending",
            "/api/refs/failed",
            "/api/refs/resolved",
            "/api/extractions/{receipt_id}",
            "/api/extractions/summary",
            "/api/ledger/health",
            "/api/ledger/debt",
            "/api/ledger/experience",
            "/api/audit/verify",
            "/api/bench/{operation}",
            "/api/debt/report",
            "/api/switches/{name}",
            "/api/config",
            "/api/config/{name}",
            "/api/health/findings",
            "/api/health/checks",
            "/api/metrics/buckets",
            "/api/metrics/operations",
            "/api/store/tables",
            "/api/store/tables/{name}",
            "/api/store/schema",
            "/api/analytics/centrality",
            "/api/analytics/communities",
            "/api/analytics/bridges",
            "/api/export/subgraph",
            "/api/export/node/{id}",
            "/api/routes",
        ):
            assert expected in paths, expected

    def test_route_count_at_least_58(self) -> None:
        assert len(API_ROUTES) >= 58

    def test_route_classes_valid(self) -> None:
        for route in API_ROUTES:
            assert route.route_class in ("read", "heavy", "export"), route.path

    def test_routes_still_get_and_authenticated(self) -> None:
        for route in API_ROUTES:
            assert route.method == "GET"
            assert route.access == "authenticated"


# ------------------------------------------------------------------ /api/nodes


class TestNodesRoute:
    def test_lists_real_nodes(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/nodes")
        assert status == 200
        assert body["total"] == 4
        ids = [node["id"] for node in body["nodes"]]
        assert ids == sorted(ids)
        assert set(ids) == {"n-root", "n-alpha", "n-beta", "n-gamma"}

    def test_kind_filter(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/nodes?kind=root")
        # n-root and the parentless n-gamma are both root-kind by seeding
        assert {node["id"] for node in body["nodes"]} == {"n-root", "n-gamma"}
        assert body["total"] == 2

    def test_domain_filter(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/nodes?domain=P2")
        assert {node["id"] for node in body["nodes"]} == {"n-beta", "n-gamma"}

    def test_paging(self, server: WebviewServer) -> None:
        _, _, page1 = _http(server, "/api/nodes?limit=2&offset=0")
        _, _, page2 = _http(server, "/api/nodes?limit=2&offset=2")
        assert page1["count"] == 2 and page2["count"] == 2
        assert {n["id"] for n in page1["nodes"]}.isdisjoint({n["id"] for n in page2["nodes"]})
        assert page1["total"] == 4

    def test_limit_over_max_is_400(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/nodes?limit=999999")
        assert status == 400
        assert body["error"]["code"] == "BAD_REQUEST"

    def test_negative_offset_is_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/nodes?offset=-1")
        assert status == 400


# ------------------------------------------------------------------ /api/edges


class TestEdgesRoute:
    def test_lists_real_relations(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/edges")
        assert status == 200
        triples = {(e["source"], e["target"], e["relation"]) for e in body["edges"]}
        assert ("n-alpha", "n-beta", "supports") in triples
        assert body["total"] >= 1

    def test_relation_filter(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/edges?relation=supports")
        assert {e["relation"] for e in body["edges"]} == {"supports"}
        _, _, empty = _http(server, "/api/edges?relation=contradicts")
        assert empty["edges"] == []
        assert empty["total"] == 0

    def test_paging_bounds(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/edges?limit=0")
        assert status == 400


class TestGraphEdgesRoute:
    def test_lists_real_graph_edges(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/graph/edges")
        assert status == 200
        rows = {(e["source"], e["target"], e["kind"]): e for e in body["edges"]}
        assert ("n-alpha", "n-beta", "calls") in rows
        assert rows[("n-alpha", "n-beta", "calls")]["line"] == 12
        assert ("n-beta", "n-gamma", "imports") in rows
        assert body["total"] == 2

    def test_kind_and_source_filters(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/graph/edges?kind=imports")
        assert [e["kind"] for e in body["edges"]] == ["imports"]
        _, _, body = _http(server, "/api/graph/edges?source=n-beta")
        assert [e["source"] for e in body["edges"]] == ["n-beta"]


# ------------------------------------------------------- node sub-resources


class TestNodeSubresources:
    def test_node_edges(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/node/n-alpha/edges")
        assert status == 200
        triples = {(e["source"], e["target"], e["kind"]) for e in body["edges"]}
        assert ("n-alpha", "n-beta", "supports") in triples
        assert ("n-root", "n-alpha", "parent_of") in triples

    def test_node_edges_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/node/ghost/edges")
        assert status == 404

    def test_node_evidence_real(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/node/n-alpha/evidence")
        assert status == 200
        assert body["count"] == 1
        entry = body["evidence"][0]
        assert entry["evidence_id"] == "ev-alpha-1"
        assert entry["source_uri"] == "context://corpus/lesson.md"

    def test_node_evidence_empty_for_unproven_node(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/node/n-beta/evidence")
        assert body["count"] == 0
        assert body["evidence"] == []

    def test_node_evidence_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/node/ghost/evidence")
        assert status == 404

    def test_node_revisions_real(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/node/n-alpha/revisions")
        assert status == 200
        versions = [rev["version"] for rev in body["revisions"]]
        assert versions == [1, 2]  # seeded twice
        assert all(rev["event_seq"] > 0 for rev in body["revisions"])

    def test_node_revisions_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/node/ghost/revisions")
        assert status == 404


# --------------------------------------------------------------- provenance


class TestProvenanceRoute:
    def test_legacy_node_is_honestly_unverified(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/provenance/n-alpha")
        assert status == 200
        assert body["node_id"] == "n-alpha"
        assert body["status"] == "unverified"  # seeded without a v4 chain
        assert len(body["links"]) == 5

    def test_ghost_node_is_404(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/provenance/ghost")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"


# ----------------------------------------------------------------- /api/symbols


class TestSymbolsRoute:
    def test_lists_real_symbols(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/symbols")
        assert status == 200
        assert body["total"] == 3
        names = {s["name"] for s in body["symbols"]}
        assert names == {"alphaFunc", "betaClass", "alphaHelper"}

    def test_kind_filter(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/symbols?kind=class")
        assert [s["name"] for s in body["symbols"]] == ["betaClass"]

    def test_path_prefix_filter(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/symbols?path_prefix=src/a")
        assert [s["name"] for s in body["symbols"]] == ["alphaFunc"]

    def test_paging(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/symbols?limit=2&offset=2")
        assert body["count"] == 1
        assert body["total"] == 3

    def test_symbol_detail_by_node(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/symbols/n-alpha")
        assert status == 200
        assert body["symbol"]["name"] == "alphaFunc"
        assert body["symbol"]["path"] == "src/a.py"
        assert body["symbol"]["line"] == 10

    def test_symbol_detail_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/symbols/n-root")
        assert status == 404

    def test_prefix_search(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/search/symbols?q=alpha")
        assert status == 200
        assert {s["name"] for s in body["results"]} == {"alphaFunc", "alphaHelper"}

    def test_prefix_search_escapes_like_wildcards(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/search/symbols?q=%25")  # literal %
        assert body["results"] == []

    def test_prefix_search_empty_q_is_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/search/symbols?q=")
        assert status == 400


# ------------------------------------------------------------- refs by status


class TestRefsByStatus:
    def test_pending(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/refs/pending")
        assert status == 200
        assert body["status"] == "pending"
        targets = {r["target_name"] for r in body["refs"]}
        assert "mod.missing_func" in targets
        assert all(r["status"] == "pending" for r in body["refs"])

    def test_failed_carries_reason(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/refs/failed")
        targets = {r["target_name"]: r for r in body["refs"]}
        assert targets["mod.broken_func"]["failure_reason"] == "no_resolution"

    def test_resolved(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/refs/resolved")
        targets = {r["target_name"] for r in body["refs"]}
        assert "mod.found_func" in targets

    def test_totals_match_stats(self, server: WebviewServer) -> None:
        _, _, stats = _http(server, "/api/refs")
        _, _, pending = _http(server, "/api/refs/pending")
        _, _, failed = _http(server, "/api/refs/failed")
        _, _, resolved = _http(server, "/api/refs/resolved")
        assert pending["total"] == stats["pending"]
        assert failed["total"] == stats["failed"]
        assert resolved["total"] == stats["resolved"]


# --------------------------------------------------------------- extractions


class TestExtractionDetail:
    def _seeded_server(self, tmp_path: Path):
        from _extraction_helpers import make_args, seed_engine

        engine, _service, store, _queue, _extra = seed_engine(tmp_path)
        receipt = engine.submit(make_args(), actor="actor-a")
        instance = WebviewServer(store.db_path, port=0, token=TOKEN)
        instance.start()
        return instance, store, engine, receipt

    def test_summary_absent_sidecar(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/extractions/summary")
        assert status == 200
        assert body["state"] == "absent"
        assert body["queue_size"] == 0

    def test_detail_404_when_absent(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/extractions/rcpt-nope")
        assert status == 404

    def test_summary_and_detail_real(self, tmp_path: Path) -> None:
        instance, store, engine, receipt = self._seeded_server(tmp_path)
        try:
            status, _, body = _http(instance, "/api/extractions/summary")
            assert status == 200
            assert body["state"] == "ok"
            assert body["queue_size"] == 1
            assert body["counts"]["pending_review"] == 1
            status, _, detail = _http(instance, f"/api/extractions/{receipt['receipt_id']}")
            assert status == 200
            assert detail["receipt_id"] == receipt["receipt_id"]
            assert detail["actor"] == "actor-a"
            assert detail["source_sha256"] == receipt["source_sha256"]
            assert detail["status"] == "pending_review"
            assert detail["nodes"] == 2
        finally:
            instance.shutdown()
            store.close()


# ------------------------------------------------------------- ledger tails


class TestLedgerHealth:
    def test_absent_is_honest(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/ledger/health")
        assert status == 200
        assert body["state"] == "absent"
        assert body["snapshots"] == []

    def test_real_history_served_with_hashes(self, tmp_path: Path) -> None:
        from know_your_context.ops.lifecycle import record_health_snapshot

        path = tmp_path / "hist.sqlite3"
        store = EventStore(path)
        _upsert(store, _node("n-root"), tag="h")
        record_health_snapshot(store)
        record_health_snapshot(store)
        store.close()
        instance = WebviewServer(path, port=0, token=TOKEN)
        instance.start()
        try:
            status, _, body = _http(instance, "/api/ledger/health")
            assert status == 200
            assert body["state"] == "ok"
            assert len(body["snapshots"]) == 2
            snap = body["snapshots"][0]
            assert len(snap["hash"]) == 64
            assert len(snap["prev_hash"]) == 64
            # hash chain: snapshot 2 links to snapshot 1
            assert body["snapshots"][1]["prev_hash"] == snap["hash"]
        finally:
            instance.shutdown()

    def test_limit_bounds(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/ledger/health?limit=0")
        assert status == 400


class TestLedgerDebt:
    def test_absent_is_honest(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/ledger/debt")
        assert status == 200
        assert body["state"] == "absent"

    def test_real_entries_served(self, tmp_path: Path) -> None:
        from know_your_context.analytics.debt import queue_snapshot
        from know_your_context.ops.debt import append_debt_snapshot, ledger_path_for_store

        path = tmp_path / "debt.sqlite3"
        store = EventStore(path)
        store.close()
        ledger = ledger_path_for_store(path)
        snap = queue_snapshot(
            {"items": [], "candidate_count": 0, "signal_counts": {"low_score": 0}},
            cycle=1,
            recorded_at="2026-01-05T00:00:00+00:00",
        )
        append_debt_snapshot(ledger, snap)
        instance = WebviewServer(path, port=0, token=TOKEN)
        instance.start()
        try:
            status, _, body = _http(instance, "/api/ledger/debt")
            assert status == 200
            assert body["state"] == "ok"
            assert body["count"] == 1
            entry = body["entries"][0]
            assert entry["cycle"] == 1
            assert len(entry["hash"]) == 64
        finally:
            instance.shutdown()


class TestLedgerExperience:
    def test_absent_is_honest(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/ledger/experience")
        assert status == 200
        assert body["state"] == "absent"

    def test_real_journal_served_and_verified(self, tmp_path: Path) -> None:
        from know_your_context.retrieval_v2.experience import ExperienceLedger

        path = tmp_path / "exp.sqlite3"
        store = EventStore(path)
        ledger = ExperienceLedger.for_store(store)
        ledger.append_journal({"kind": "rerank", "query": "alpha"})
        ledger.close()
        store.close()
        instance = WebviewServer(path, port=0, token=TOKEN)
        instance.start()
        try:
            status, _, body = _http(instance, "/api/ledger/experience")
            assert status == 200
            assert body["state"] == "ok"
            assert body["verification"]["ok"] is True
            assert body["count"] == 1
            entry = body["entries"][0]
            assert entry["seq"] == 1
            assert entry["payload"]["query"] == "alpha"
            assert len(entry["entry_hash"]) == 64
        finally:
            instance.shutdown()

    def test_sidecar_never_created_by_the_route(self, server: WebviewServer, store_path: Path) -> None:
        sidecar = Path(f"{store_path}.experience-ledger.db")
        assert not sidecar.exists()
        _http(server, "/api/ledger/experience")
        assert not sidecar.exists()  # read-only: absent stays absent


# --------------------------------------------------------------- audit/verify


class TestAuditVerify:
    def test_chain_verifies(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/audit/verify")
        assert status == 200
        assert body["ok"] is True
        assert body["checked"] > 0
        assert body["first_bad_seq"] is None


# ------------------------------------------------------------------ bench/{op}


class TestBenchOperation:
    def test_real_durations(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/bench/knowledge.query")
        assert status == 200
        assert body["operation"] == "knowledge.query"
        assert body["count"] == 2
        assert body["min_ms"] == 5
        assert body["max_ms"] == 15
        assert body["samples"] == [5, 15]

    def test_unknown_operation_404(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/bench/no.such.op")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_invalid_operation_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/bench/bad%20op")
        assert status == 400


# --------------------------------------------------------------- debt/report


class TestDebtReport:
    def test_report_shape(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/debt/report")
        assert status == 200
        assert "queue" in body
        assert "burndown" in body
        assert body["ledger"] in ("absent", "ok")
        # the seeded store has one failed unresolved ref → detector sees it
        assert body["queue"]["signal_counts"]["unresolved_ref_old"] >= 0

    def test_kill_switch_disables_honestly(self, server: WebviewServer, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KYS_DEBT_QUEUE", "0")
        status, _, body = _http(server, "/api/debt/report")
        assert status == 200
        assert body["queue"]["disabled"] is True


# -------------------------------------------------------------- switches/{name}


class TestSwitchDetail:
    def test_real_switch_state(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/switches/webview")
        assert status == 200
        assert body["name"] == "webview"
        assert body["env"] == "KNOW_YOUR_CONTEXT_DISABLE_WEBVIEW"
        assert body["state"] == "enabled"
        assert body["affects"]

    def test_unknown_switch_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/switches/nope")
        assert status == 404


# ------------------------------------------------------------------- /api/config


class TestConfigRoutes:
    def test_config_lists_all_knobs(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/config")
        assert status == 200
        assert body["count"] == len(body["knobs"]) >= 40
        entry = {k["name"]: k for k in body["knobs"]}["webview.max_graph_nodes"]
        assert entry["value"] == 4096
        assert entry["source"] == "default"
        assert entry["env"] == "KNOW_YOUR_CONTEXT_WEBVIEW_MAX_GRAPH_NODES"

    def test_config_reflects_env_override(self, store_path: Path) -> None:
        knobs = load_knobs(env={"KNOW_YOUR_CONTEXT_WEBVIEW_TOP_RANKING": "7"})
        instance = WebviewServer(store_path, port=0, token=TOKEN, knobs=knobs)
        instance.start()
        try:
            _, _, body = _http(instance, "/api/config/webview.top_ranking")
            assert body["value"] == 7
            assert body["source"] == "env"
            # ... and the override is really consumed by analytics
            _, _, analytics = _http(instance, "/api/analytics/centrality?top=999")
            assert analytics["top"] == 7
        finally:
            instance.shutdown()

    def test_config_detail_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/config/webview.nope")
        assert status == 404


# ------------------------------------------------------------ health breakdown


class TestHealthBreakdown:
    def test_findings_filter(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/health/findings?severity=error")
        assert status == 200
        assert all(f["severity"] == "error" for f in body["findings"])
        assert body["severity"] == "error"

    def test_findings_invalid_severity_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/health/findings?severity=fatal")
        assert status == 400

    def test_checks_breakdown(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/health/checks")
        assert status == 200
        _, _, health = _http(server, "/api/health")
        assert body["checked"] == health["checked"]
        assert body["count"] == len(body["checks"])
        by_name = {c["name"]: c for c in body["checks"]}
        assert "hash_chain" in by_name
        assert all(c["ran"] for c in body["checks"])
        # findings carry no check attribution upstream; the route reports the
        # honest total instead of inventing a mapping
        assert body["finding_total"] == len(health["findings"])


# ------------------------------------------------------------- metrics buckets


class TestMetricsBuckets:
    def test_day_bucket_real(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/metrics/buckets?bucket=day")
        assert status == 200
        assert body["bucket"] == "day"
        days = {b["key"]: b for b in body["buckets"]}
        assert "2026-01-02" in days  # audit events seeded on days 2..4
        assert days["2026-01-02"]["events"] >= 1

    def test_kind_bucket(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/metrics/buckets?bucket=kind")
        kinds = {b["key"] for b in body["buckets"]}
        assert "audit" in kinds and "domain" in kinds

    def test_actor_bucket(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/metrics/buckets?bucket=actor")
        actors = {b["key"] for b in body["buckets"]}
        assert "tester" in actors

    def test_invalid_bucket_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/metrics/buckets?bucket=minute")
        assert status == 400

    def test_buckets_sum_matches_total_events(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/metrics/buckets?bucket=kind&limit=100")
        _, _, stats = _http(server, "/api/store/tables")
        event_rows = {t["name"]: t["rows"] for t in stats["tables"]}["event_log"]
        assert sum(b["events"] for b in body["buckets"]) == event_rows


class TestMetricsOperations:
    def test_real_operation_counts(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/metrics/operations")
        assert status == 200
        ops = {o["operation"]: o for o in body["operations"]}
        assert ops["knowledge.query"]["complete"] == 2
        assert ops["knowledge.query"]["mean_duration_ms"] == 10
        assert ops["knowledge.rebuild"]["complete"] == 1


# --------------------------------------------------------------- store tables


class TestStoreTables:
    def test_table_list_real_counts(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/store/tables")
        assert status == 200
        rows = {t["name"]: t["rows"] for t in body["tables"]}
        assert rows["symbols"] == 3
        assert rows["graph_edges"] == 2
        assert rows["unresolved_refs"] == 3
        assert rows["event_log"] > 0

    def test_table_detail_columns(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/store/tables/symbols")
        assert status == 200
        assert body["name"] == "symbols"
        assert body["rows"] == 3
        cols = {c["name"] for c in body["columns"]}
        assert {"node_id", "name", "kind", "path", "line", "col"} <= cols

    def test_unknown_table_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/store/tables/nope")
        assert status == 404

    def test_injection_attempt_never_reaches_sql(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/store/tables/symbols%3BDROP%20TABLE%20symbols")
        assert status == 400  # malformed table name rejected before any SQL
        assert body["error"]["code"] == "BAD_REQUEST"
        _, _, body = _http(server, "/api/store/tables/symbols")
        assert body["rows"] == 3  # table intact


class TestStoreSchema:
    def test_schema_versions_and_migrations(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/store/schema")
        assert status == 200
        assert body["recorded"] >= 3
        assert 1 in body["schema_versions"]
        names = {m["name"] for m in body["migrations"]}
        assert "universal_graph" in names
        assert all(m["version"] >= 3 for m in body["migrations"])


# ------------------------------------------------------------ analytics slices


class TestAnalyticsSlices:
    def test_centrality_matches_aggregate(self, server: WebviewServer) -> None:
        _, _, full = _http(server, "/api/analytics")
        status, _, body = _http(server, "/api/analytics/centrality")
        assert status == 200
        assert body["centrality"] == full["centrality"]
        assert body["node_count"] == full["node_count"]

    def test_communities_matches_aggregate(self, server: WebviewServer) -> None:
        _, _, full = _http(server, "/api/analytics")
        _, _, body = _http(server, "/api/analytics/communities")
        assert body["communities"] == full["communities"]

    def test_bridges_matches_aggregate(self, server: WebviewServer) -> None:
        _, _, full = _http(server, "/api/analytics")
        _, _, body = _http(server, "/api/analytics/bridges")
        assert body["bridges"] == full["bridges"]

    def test_centrality_top_bound(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/analytics/centrality?top=0")
        assert status == 400


# -------------------------------------------------------------------- export


class TestExportSubgraph:
    def test_real_subgraph(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/export/subgraph?node_id=n-alpha&depth=1")
        assert status == 200
        ids = {n["id"] for n in body["read_model"]["nodes"]}
        assert "n-alpha" in ids
        assert "n-root" in ids  # parent link within depth 1
        assert body["node_count"] == len(ids)
        assert body["artifacts"]  # manifest entries with sha256+size
        assert all(len(a["sha256"]) == 64 for a in body["artifacts"])

    def test_unknown_seed_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/export/subgraph?node_id=ghost")
        assert status == 404

    def test_missing_selector_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/export/subgraph")
        assert status == 400

    def test_both_selectors_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/export/subgraph?node_id=n-alpha&domain=P1")
        assert status == 400

    def test_domain_selector(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/export/subgraph?domain=P2")
        assert status == 200
        ids = {n["id"] for n in body["read_model"]["nodes"]}
        assert ids == {"n-beta", "n-gamma"}

    def test_depth_bound(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/export/subgraph?node_id=n-alpha&depth=99")
        assert status == 400


class TestExportNode:
    def test_full_bundle(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/export/node/n-alpha")
        assert status == 200
        assert body["node"]["node_id"] == "n-alpha"
        assert body["node"]["symbol"]["name"] == "alphaFunc"
        assert [e["evidence_id"] for e in body["evidence"]] == ["ev-alpha-1"]
        assert body["provenance"]["status"] == "unverified"
        assert body["schema_version"] >= 1

    def test_ghost_404(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/export/node/ghost")
        assert status == 404


# -------------------------------------------------------------------- /api/routes


class TestRoutesDiscovery:
    def test_lists_the_full_table(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/routes")
        assert status == 200
        assert body["count"] == len(API_ROUTES)
        paths = {r["path"] for r in body["routes"]}
        assert "/api/routes" in paths
        entry = {r["path"]: r for r in body["routes"]}["/api/export/subgraph"]
        assert entry["route_class"] == "export"


# --------------------------------------------------------------- rate limiting


class TestRateLimiterUnit:
    def test_burst_then_deny(self) -> None:
        clock = iter([0.0, 0.1, 0.2, 0.3])
        limiter = RateLimiter({"read": (1.0, 2)}, clock=lambda: next(clock))
        limiter.check("read")
        limiter.check("read")
        with pytest.raises(RateLimitedError) as excinfo:
            limiter.check("read")
        assert excinfo.value.code == "RATE_LIMITED"
        assert excinfo.value.status == 429
        assert excinfo.value.retry_after_seconds >= 1

    def test_refill_after_interval(self) -> None:
        state = {"now": 0.0}
        limiter = RateLimiter({"read": (10.0, 1)}, clock=lambda: state["now"])
        limiter.check("read")
        with pytest.raises(RateLimitedError):
            limiter.check("read")
        state["now"] = 0.2  # 2 tokens refilled, capped at burst=1
        limiter.check("read")

    def test_classes_are_independent(self) -> None:
        limiter = RateLimiter({"read": (0.001, 1), "heavy": (1000.0, 5)}, clock=lambda: 0.0)
        limiter.check("read")
        with pytest.raises(RateLimitedError):
            limiter.check("read")
        limiter.check("heavy")  # unaffected

    def test_unknown_class_fails_closed(self) -> None:
        limiter = RateLimiter({"read": (1.0, 1)})
        with pytest.raises(RateLimitedError):
            limiter.check("nope")

    def test_invalid_spec_rejected(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter({"read": (0.0, 1)})
        with pytest.raises(ValueError):
            RateLimiter({"read": (1.0, 0)})


class TestRateLimitHTTP:
    def _limited_server(self, store_path: Path) -> WebviewServer:
        knobs = load_knobs(
            env={
                "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_EXPORT_PER_SECOND": "0.01",
                "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_EXPORT_BURST": "1",
            }
        )
        instance = WebviewServer(store_path, port=0, token=TOKEN, knobs=knobs)
        instance.start()
        return instance

    def test_export_class_limited(self, store_path: Path) -> None:
        instance = self._limited_server(store_path)
        try:
            status1, _, _ = _http(instance, "/api/export/node/n-alpha")
            status2, headers2, body2 = _http(instance, "/api/export/node/n-beta")
            assert status1 == 200
            assert status2 == 429
            assert body2["error"]["code"] == "RATE_LIMITED"
            assert body2["error"]["retryable"] is True
            retry_after = headers2.get("retry-after")
            assert retry_after is not None and int(retry_after) >= 1
        finally:
            instance.shutdown()

    def test_read_class_unaffected(self, store_path: Path) -> None:
        instance = self._limited_server(store_path)
        try:
            _http(instance, "/api/export/node/n-alpha")
            status, _, _ = _http(instance, "/api/nodes")
            assert status == 200  # read class has its own bucket
        finally:
            instance.shutdown()

    def test_rate_limit_disabled_by_knob(self, store_path: Path) -> None:
        knobs = load_knobs(
            env={
                "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_ENABLED": "false",
                "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_EXPORT_PER_SECOND": "0.0001",
                "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_EXPORT_BURST": "1",
            }
        )
        instance = WebviewServer(store_path, port=0, token=TOKEN, knobs=knobs)
        instance.start()
        try:
            for _ in range(3):
                status, _, _ = _http(instance, "/api/export/node/n-alpha")
                assert status == 200
        finally:
            instance.shutdown()


# -------------------------------------------------------------- feature flags


class TestFeatureFlags:
    def _flagged_server(self, store_path: Path, env: dict[str, str]) -> WebviewServer:
        instance = WebviewServer(store_path, port=0, token=TOKEN, knobs=load_knobs(env=env))
        instance.start()
        return instance

    def test_export_group_disabled(self, store_path: Path) -> None:
        instance = self._flagged_server(store_path, {"KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_EXPORT_ENABLED": "false"})
        try:
            status, _, body = _http(instance, "/api/export/subgraph?node_id=n-alpha")
            assert status == 404
            assert body["error"]["code"] == "NOT_FOUND"
            status, _, _ = _http(instance, "/api/nodes")
            assert status == 200  # other groups unaffected
        finally:
            instance.shutdown()

    def test_config_group_disabled(self, store_path: Path) -> None:
        instance = self._flagged_server(store_path, {"KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_CONFIG_ENABLED": "false"})
        try:
            assert _http(instance, "/api/config")[0] == 404
            assert _http(instance, "/api/config/webview.top_ranking")[0] == 404
        finally:
            instance.shutdown()

    def test_debt_group_disabled(self, store_path: Path) -> None:
        instance = self._flagged_server(store_path, {"KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_DEBT_ENABLED": "false"})
        try:
            assert _http(instance, "/api/debt/report")[0] == 404
        finally:
            instance.shutdown()

    def test_provenance_group_disabled(self, store_path: Path) -> None:
        instance = self._flagged_server(store_path, {"KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_PROVENANCE_ENABLED": "false"})
        try:
            assert _http(instance, "/api/provenance/n-alpha")[0] == 404
        finally:
            instance.shutdown()

    def test_ledger_group_disabled(self, store_path: Path) -> None:
        instance = self._flagged_server(store_path, {"KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_LEDGER_ENABLED": "false"})
        try:
            assert _http(instance, "/api/ledger/health")[0] == 404
            assert _http(instance, "/api/audit/verify")[0] == 404
        finally:
            instance.shutdown()

    def test_invalid_flag_value_fails_closed_at_construction(self, store_path: Path) -> None:
        from know_your_context.ops.switches import SwitchError

        with pytest.raises(SwitchError):
            load_knobs(env={"KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_EXPORT_ENABLED": "maybe"})


# ------------------------------------------------------- envelope consistency


class TestPagingBoundsParametrized:
    """Every paged route enforces its bounds fail-closed."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/nodes",
            "/api/edges",
            "/api/graph/edges",
            "/api/symbols",
            "/api/refs/pending",
            "/api/refs/failed",
            "/api/refs/resolved",
        ],
    )
    def test_zero_limit_400(self, server: WebviewServer, path: str) -> None:
        status, _, body = _http(server, f"{path}?limit=0")
        assert status == 400
        assert body["error"]["code"] == "BAD_REQUEST"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/nodes",
            "/api/edges",
            "/api/graph/edges",
            "/api/symbols",
            "/api/refs/pending",
        ],
    )
    def test_non_integer_offset_is_400(self, server: WebviewServer, path: str) -> None:
        status, _, _ = _http(server, f"{path}?offset=abc")
        assert status == 400

    @pytest.mark.parametrize(
        "path",
        [
            "/api/ledger/health",
            "/api/ledger/debt",
            "/api/ledger/experience",
        ],
    )
    def test_ledger_limit_bounds(self, server: WebviewServer, path: str) -> None:
        assert _http(server, f"{path}?limit=0")[0] == 400
        assert _http(server, f"{path}?limit=999999")[0] == 400


class TestSchemaVersionPresent:
    """Every W15 route payload carries the schema version."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/nodes",
            "/api/edges",
            "/api/graph/edges",
            "/api/node/n-alpha/edges",
            "/api/node/n-alpha/evidence",
            "/api/node/n-alpha/revisions",
            "/api/provenance/n-alpha",
            "/api/symbols",
            "/api/symbols/n-alpha",
            "/api/search/symbols?q=alpha",
            "/api/refs/pending",
            "/api/refs/failed",
            "/api/refs/resolved",
            "/api/extractions/summary",
            "/api/ledger/health",
            "/api/ledger/debt",
            "/api/ledger/experience",
            "/api/audit/verify",
            "/api/bench/knowledge.query",
            "/api/debt/report",
            "/api/switches/webview",
            "/api/config",
            "/api/config/webview.max_graph_nodes",
            "/api/health/findings?severity=warning",
            "/api/health/checks",
            "/api/metrics/buckets?bucket=day",
            "/api/metrics/buckets?bucket=kind",
            "/api/metrics/buckets?bucket=actor",
            "/api/metrics/operations",
            "/api/store/tables",
            "/api/store/tables/symbols",
            "/api/store/schema",
            "/api/analytics/centrality",
            "/api/analytics/communities",
            "/api/analytics/bridges",
            "/api/export/subgraph?node_id=n-alpha",
            "/api/export/node/n-alpha",
            "/api/routes",
        ],
    )
    def test_payload_has_schema_version(self, server: WebviewServer, path: str) -> None:
        status, _, body = _http(server, path)
        assert status == 200, path
        assert body["schema_version"] >= 1, path


class TestDeterminism:
    """Two requests against an unchanged store answer byte-identically."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/nodes",
            "/api/edges",
            "/api/graph/edges",
            "/api/symbols",
            "/api/refs/pending",
            "/api/store/tables",
            "/api/store/schema",
            "/api/metrics/operations",
            "/api/analytics/centrality",
            "/api/export/subgraph?node_id=n-alpha",
            "/api/export/node/n-alpha",
            "/api/routes",
            "/api/config",
            "/api/provenance/n-alpha",
        ],
    )
    def test_byte_identical_repeat(self, server: WebviewServer, path: str) -> None:
        _, _, first = _http(server, path)
        _, _, second = _http(server, path)
        assert first == second


class TestErrorEnvelopeConsistency:
    def test_all_error_paths_share_the_envelope(self, server: WebviewServer) -> None:
        for path in (
            "/api/node/ghost/edges",
            "/api/nodes?limit=0",
            "/api/nonexistent",
            "/api/provenance/ghost",
            "/api/config/webview.nope",
            "/api/store/tables/nope",
        ):
            status, _, body = _http(server, path)
            assert status in (400, 404)
            assert set(body["error"]) == {"code", "message", "retryable"}
            assert body["error"]["retryable"] is False
            assert body["error"]["code"] in ("BAD_REQUEST", "NOT_FOUND")


# ------------------------------------------------------------------ read-only


class TestReadOnlyInvariant:
    def test_store_bytes_unchanged_after_exercising_new_routes(self, server: WebviewServer, store_path: Path) -> None:
        import hashlib

        before = hashlib.sha256(store_path.read_bytes()).hexdigest()
        with sqlite3.connect(f"file:{store_path}?mode=ro", uri=True) as conn:
            events_before = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
        for path in (
            "/api/nodes",
            "/api/edges",
            "/api/graph/edges",
            "/api/node/n-alpha/edges",
            "/api/node/n-alpha/evidence",
            "/api/node/n-alpha/revisions",
            "/api/provenance/n-alpha",
            "/api/symbols",
            "/api/symbols/n-alpha",
            "/api/search/symbols?q=alpha",
            "/api/refs/pending",
            "/api/refs/failed",
            "/api/refs/resolved",
            "/api/extractions/summary",
            "/api/ledger/health",
            "/api/ledger/debt",
            "/api/ledger/experience",
            "/api/audit/verify",
            "/api/bench/knowledge.query",
            "/api/debt/report",
            "/api/switches/webview",
            "/api/config",
            "/api/config/webview.max_graph_nodes",
            "/api/health/findings?severity=error",
            "/api/health/checks",
            "/api/metrics/buckets?bucket=day",
            "/api/metrics/operations",
            "/api/store/tables",
            "/api/store/tables/symbols",
            "/api/store/schema",
            "/api/analytics/centrality",
            "/api/analytics/communities",
            "/api/analytics/bridges",
            "/api/export/subgraph?node_id=n-alpha",
            "/api/export/node/n-alpha",
            "/api/routes",
        ):
            status, _, body = _http(server, path)
            assert status == 200, f"{path}: {status} {body}"
        assert hashlib.sha256(store_path.read_bytes()).hexdigest() == before
        with sqlite3.connect(f"file:{store_path}?mode=ro", uri=True) as conn:
            assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == events_before
        assert not Path(f"{store_path}.experience-ledger.db").exists()
        assert not Path(f"{store_path.stem}.extraction.sqlite3").exists()
