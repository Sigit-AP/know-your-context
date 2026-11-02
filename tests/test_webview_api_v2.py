"""Tests for the W13 webview JSON API routes + middleware wiring (SUB-125).

New read-only routes serving real store data: ``/api/query``,
``/api/metrics/series``, ``/api/audit/tail``, ``/api/improvements`` (+detail),
``/api/health/detail``, ``/api/bench``, ``/api/store/stats``, ``/api/switches``.
Plus the middleware layer (request-id echo, Server-Timing) and the
``KNOW_YOUR_CONTEXT_DISABLE_WEBVIEW`` kill switch.
"""

from __future__ import annotations

import http.client
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from know_your_context.domain import (
    Event,
    EventKind,
    Node,
    NodeKind,
    PrimaryDomain,
    Relation,
    RelationKind,
    ScoreVector,
)
from know_your_context.ops import switches
from know_your_context.serve.middleware import REQUEST_ID_HEADER, SERVER_TIMING_HEADER
from know_your_context.serve.webview import API_ROUTES, WebviewError, WebviewServer
from know_your_context.store import EventStore

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
TOKEN = "test-token-0000000000"


# --------------------------------------------------------------------- seeds


def _node(node_id: str, *, parent: str | None = None, domain: PrimaryDomain = PrimaryDomain.P1) -> Node:
    return Node(
        node_id=node_id,
        parent_id=parent,
        kind=NodeKind.CHILD if parent else NodeKind.ROOT,
        domain=domain,
        summary=f"summary of {node_id}",
        provenance=("context://corpus/lesson.md",),
        source_version_id="sv-w13",
        scores=ScoreVector.zero(),
        created_at=NOW,
        updated_at=NOW,
    )


def _upsert(store: EventStore, node: Node) -> None:
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id=node.node_id,
            domain=node.domain,
            node=node,
            source_version_id=node.source_version_id,
            occurred_at=NOW,
        ),
        idempotency_key=f"w13:upsert:{node.node_id}:{store.node_version(node.node_id)}",
    )


def _audit(store: EventStore, index: int, *, operation: str = "remember") -> None:
    with store.transaction() as conn:
        store.insert_event(
            conn,
            event_id=f"w13-audit-{index}",
            kind="audit",
            event_type="op.complete",
            knowledge_affecting=False,
            actor="tester",
            occurred_at=f"2026-01-0{index + 1}T00:00:00+00:00",
            payload={"operation": operation, "seq": index},
            idempotency_key=f"w13:audit:{index}",
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
                f"w13:journal:{invocation_id}",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:01+00:00",
                json.dumps({"duration_ms": duration_ms}),
            ),
        )


def _proposal(store: EventStore, proposal_id: str, node_id: str) -> None:
    with store.transaction() as conn:
        store.insert_event(
            conn,
            event_id=f"w13-propose-{proposal_id}",
            kind="domain",
            event_type="knowledge.propose_improvement",
            knowledge_affecting=False,
            actor="tester",
            occurred_at="2026-01-02T00:00:00+00:00",
            payload={
                "payload": {
                    "improvement_proposal": {
                        "proposal_id": proposal_id,
                        "node_id": node_id,
                        "base_version": 1,
                        "impact": "low",
                        "diff": [],
                        "rationale": "tighten the summary",
                    }
                }
            },
            idempotency_key=f"w13:propose:{proposal_id}",
        )


def _apply_marker(store: EventStore, proposal_id: str) -> None:
    with store.transaction() as conn:
        store.insert_event(
            conn,
            event_id=f"w13-apply-{proposal_id}",
            kind="domain",
            event_type="knowledge.apply_improvement",
            knowledge_affecting=True,
            actor="tester",
            occurred_at="2026-01-03T00:00:00+00:00",
            payload={"payload": {"improvement_apply": {"proposal_id": proposal_id, "from_version": 1}}},
            idempotency_key=f"w13:apply:{proposal_id}",
        )


def _seed(store: EventStore) -> None:
    _upsert(store, _node("n-root"))
    _upsert(store, _node("n-alpha", parent="n-root"))
    _upsert(store, _node("n-beta", parent="n-root", domain=PrimaryDomain.P2))
    store.append_domain(
        Event(
            kind=EventKind.RELATION_ADD,
            node_id="n-alpha",
            domain=PrimaryDomain.P1,
            relation=Relation(kind=RelationKind.SUPPORTS, source_node_id="n-alpha", target_node_id="n-beta"),
            occurred_at=NOW,
        ),
        idempotency_key="w13:rel:alpha-beta",
    )
    for index in range(3):
        _audit(store, index)
    _journal(store, "inv-1", "knowledge.query", 5)
    _journal(store, "inv-2", "knowledge.query", 15)
    _journal(store, "inv-3", "knowledge.rebuild", 50)
    _proposal(store, "prop-1", "n-alpha")
    _proposal(store, "prop-2", "n-beta")
    _apply_marker(store, "prop-1")


@pytest.fixture(scope="module")
def store_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("w13webview") / "w13.sqlite3"
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


class TestRouteTable:
    def test_new_routes_registered(self) -> None:
        paths = {route.path for route in API_ROUTES}
        for expected in (
            "/api/query",
            "/api/metrics/series",
            "/api/audit/tail",
            "/api/improvements",
            "/api/improvements/{id}",
            "/api/health/detail",
            "/api/bench",
            "/api/store/stats",
            "/api/switches",
        ):
            assert expected in paths, expected

    def test_routes_are_get_and_authenticated(self) -> None:
        for route in API_ROUTES:
            assert route.method == "GET"
            assert route.access == "authenticated"
            assert route.path.startswith("/api/")
            assert route.summary


# ------------------------------------------------------------------ /api/query


class TestGraphQuery:
    def test_query_both_directions(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/query?node_id=n-alpha")
        assert status == 200
        assert body["node_id"] == "n-alpha"
        pairs = {(edge["source"], edge["target"], edge["kind"]) for edge in body["edges"]}
        assert ("n-alpha", "n-beta", "supports") in pairs
        assert ("n-root", "n-alpha", "parent_of") in pairs

    def test_query_direction_out(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/query?node_id=n-alpha&direction=out")
        assert all(edge["source"] == "n-alpha" for edge in body["edges"])
        assert body["edges"]

    def test_query_direction_in(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/query?node_id=n-alpha&direction=in")
        assert all(edge["target"] == "n-alpha" for edge in body["edges"])
        assert body["edges"]

    def test_query_kind_filter(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/query?node_id=n-alpha&kind=supports")
        assert {edge["kind"] for edge in body["edges"]} == {"supports"}

    def test_query_unknown_node_is_404(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/query?node_id=nope")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_query_missing_node_id_is_400(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/query")
        assert status == 400
        assert body["error"]["code"] == "BAD_REQUEST"

    def test_query_bad_direction_is_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/query?node_id=n-alpha&direction=sideways")
        assert status == 400

    def test_query_limit_bounds(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/query?node_id=n-alpha&limit=0")
        assert status == 400
        status, _, _ = _http(server, "/api/query?node_id=n-alpha&limit=999999")
        assert status == 400


# --------------------------------------------------------- /api/metrics/series


class TestMetricsSeries:
    def test_daily_event_buckets_real(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/metrics/series")
        assert status == 200
        days = {entry["day"]: entry["events"] for entry in body["event_series"]}
        # audit events were seeded on 2026-01-01..03 (plus node events on day 1)
        assert days["2026-01-02"] >= 1
        assert days["2026-01-03"] >= 1
        # ascending order
        keys = [entry["day"] for entry in body["event_series"]]
        assert keys == sorted(keys)

    def test_health_history_absent_is_honest(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/metrics/series")
        assert body["health_series"] == []
        assert body["health_history"] == "absent"

    def test_limit_validation(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/metrics/series?limit=0")
        assert status == 400


class TestMetricsSeriesWithHistory:
    def test_health_series_served_when_recorded(self, tmp_path: Path) -> None:
        from know_your_context.ops.lifecycle import load_health_history, record_health_snapshot

        path = tmp_path / "hist.sqlite3"
        store = EventStore(path)
        _upsert(store, _node("n-root"))
        record_health_snapshot(store)
        record_health_snapshot(store)
        store.close()
        # The history file verifies (hash chain) before serving.
        assert len(load_health_history(tmp_path / "lifecycle-health.jsonl")) == 2
        instance = WebviewServer(path, port=0, token=TOKEN)
        instance.start()
        try:
            status, _, body = _http(instance, "/api/metrics/series")
            assert status == 200
            assert body["health_history"] == "ok"
            assert len(body["health_series"]) == 2
            assert all("score" in entry and "recorded_at" in entry for entry in body["health_series"])
        finally:
            instance.shutdown()


# --------------------------------------------------------------- /api/audit/tail


class TestAuditTail:
    def test_tail_after_seq_ascending(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/audit/tail?since=0&limit=10")
        assert status == 200
        seqs = [event["event_seq"] for event in body["events"]]
        assert seqs == sorted(seqs)
        assert body["count"] == len(seqs) >= 3

    def test_tail_since_filters(self, server: WebviewServer) -> None:
        _, _, all_body = _http(server, "/api/audit/tail?since=0&limit=100")
        cutoff = all_body["events"][0]["event_seq"]
        _, _, body = _http(server, f"/api/audit/tail?since={cutoff}&limit=100")
        assert all(event["event_seq"] > cutoff for event in body["events"])

    def test_tail_limit_caps(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/audit/tail?since=0&limit=1")
        assert body["count"] == 1

    def test_tail_bad_since_is_400(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/audit/tail?since=abc")
        assert status == 400


# ------------------------------------------------------------- /api/improvements


class TestImprovements:
    def test_list_real_proposals(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/improvements")
        assert status == 200
        proposals = {item["proposal_id"]: item for item in body["proposals"]}
        assert set(proposals) == {"prop-1", "prop-2"}
        assert proposals["prop-1"]["status"] == "applied"
        assert proposals["prop-2"]["status"] == "proposed"
        assert proposals["prop-1"]["node_id"] == "n-alpha"

    def test_detail_by_id(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/improvements/prop-1")
        assert status == 200
        assert body["proposal"]["proposal_id"] == "prop-1"
        assert body["status"] == "applied"
        event_types = {event["event_type"] for event in body["events"]}
        assert "knowledge.propose_improvement" in event_types
        assert "knowledge.apply_improvement" in event_types

    def test_detail_unknown_is_404(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/improvements/nope")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_detail_rejects_path_traversal(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/improvements/a/b")
        assert status in (400, 404)


# ------------------------------------------------------------ /api/health/detail


class TestHealthDetail:
    def test_detail_contains_health_and_store(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/health/detail")
        assert status == 200
        assert "ok" in body and "findings" in body  # the base health view
        assert body["store"]["tables"] >= 5
        assert "schema_versions" in body["store"]
        assert body["store"]["schema_versions"][-1] >= 4

    def test_detail_contains_timing_and_switches(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/health/detail")
        assert "count" in body["timing"]
        assert isinstance(body["switches"], list)
        names = {entry["name"] for entry in body["switches"]}
        assert "resolver.helper_tier" in names
        states = {entry["state"] for entry in body["switches"]}
        assert states <= {"enabled", "disabled"}


# ------------------------------------------------------------------ /api/bench


class TestBenchSummary:
    def test_latency_per_operation(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/bench")
        assert status == 200
        ops = {entry["operation"]: entry for entry in body["operations"]}
        assert ops["knowledge.query"]["count"] == 2
        assert ops["knowledge.query"]["min_ms"] == 5
        assert ops["knowledge.query"]["max_ms"] == 15
        assert ops["knowledge.rebuild"]["count"] == 1
        assert ops["knowledge.rebuild"]["mean_ms"] == 50


# ------------------------------------------------------------ /api/store/stats


class TestStoreStats:
    def test_table_counts_real(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/store/stats")
        assert status == 200
        counts = body["tables"]
        assert counts["node_revision"] >= 3
        assert counts["invocation_journal"] == 3
        assert counts["event_log"] >= 8
        assert body["page_count"] >= 1
        assert body["page_size_bytes"] >= 512
        assert body["file_size_bytes"] == body["page_count"] * body["page_size_bytes"]
        assert body["store_schema_version"] >= 4
        assert body["freelist_pages"] >= 0

    def test_stats_include_db_identity(self, server: WebviewServer) -> None:
        _, _, body = _http(server, "/api/store/stats")
        assert body["journal_mode"] == "wal"


# --------------------------------------------------------------- /api/switches


class TestSwitchesRoute:
    def test_lists_all_switches_default_enabled(self, server: WebviewServer, monkeypatch) -> None:
        for env_name in switches.SYNTH_PASS_ENV.values():
            monkeypatch.delenv(env_name, raising=False)
        status, _, body = _http(server, "/api/switches")
        assert status == 200
        entries = {entry["name"]: entry for entry in body["switches"]}
        assert len(entries) == len(switches.KILL_SWITCHES)
        assert entries["synthesis"]["state"] == "enabled"
        assert entries["synthesis"]["env"] == switches.ENV_DISABLE_SYNTHESIS
        assert entries["synthesis"]["fail_safe"]

    def test_reflects_env_at_request_time(self, server: WebviewServer, monkeypatch) -> None:
        monkeypatch.setenv(switches.ENV_DISABLE_HELPER_TIER, "1")
        try:
            _, _, body = _http(server, "/api/switches")
            entries = {entry["name"]: entry for entry in body["switches"]}
            assert entries["resolver.helper_tier"]["state"] == "disabled"
        finally:
            monkeypatch.delenv(switches.ENV_DISABLE_HELPER_TIER)


# ----------------------------------------------------------------- middleware


class TestMiddlewareWiring:
    def test_request_id_echoed(self, server: WebviewServer) -> None:
        status, headers, _ = _http(server, "/api/graph", headers={REQUEST_ID_HEADER: "trace-me-1"})
        assert status == 200
        assert headers.get(REQUEST_ID_HEADER.lower()) == "trace-me-1"

    def test_request_id_generated_when_absent(self, server: WebviewServer) -> None:
        _, headers, _ = _http(server, "/api/graph")
        assert headers.get(REQUEST_ID_HEADER.lower())

    def test_server_timing_header_present(self, server: WebviewServer) -> None:
        _, headers, _ = _http(server, "/api/graph")
        assert headers.get(SERVER_TIMING_HEADER.lower(), "").startswith("app;dur=")

    def test_unauthorized_requests_carry_no_timing(self, server: WebviewServer) -> None:
        status, headers, _ = _http(server, "/api/graph", token=None)
        assert status == 401
        # Guards reject before timing; no partial middleware output leaks.
        assert SERVER_TIMING_HEADER.lower() not in headers

    def test_internal_error_maps_to_typed_envelope(self, store_path: Path, monkeypatch) -> None:
        instance = WebviewServer(store_path, port=0, token=TOKEN)
        monkeypatch.setattr(
            instance._app,  # noqa: SLF001
            "graph_payload",
            lambda: (_ for _ in ()).throw(RuntimeError("db password is hunter2")),
        )
        instance.start()
        try:
            status, _, body = _http(instance, "/api/graph")
            assert status == 500
            assert body["error"]["code"] == "INTERNAL"
            assert "hunter2" not in json.dumps(body)
        finally:
            instance.shutdown()


# ------------------------------------------------------------- disable switch


class TestWebviewKillSwitch:
    def test_server_refuses_when_disabled(self, store_path: Path, monkeypatch) -> None:
        monkeypatch.setenv(switches.ENV_DISABLE_WEBVIEW, "1")
        with pytest.raises(WebviewError) as info:
            WebviewServer(store_path, port=0, token=TOKEN)
        assert switches.ENV_DISABLE_WEBVIEW in str(info.value)

    def test_cli_refuses_when_disabled(self, store_path: Path, monkeypatch, capsys) -> None:
        from know_your_context.serve.webview import main

        monkeypatch.setenv(switches.ENV_DISABLE_WEBVIEW, "1")
        code = main(["--store", str(store_path), "--serve-once"])
        assert code == 2
        assert switches.ENV_DISABLE_WEBVIEW in capsys.readouterr().err
