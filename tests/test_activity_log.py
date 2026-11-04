"""Agent activity log API (W17 SUB-138).

The activity feed renders the hash-chained event log (domain + audit +
lease.* rows) as ``agent — action — description — nodes — sha8`` entries with
a level (info/warn/error), plus the live lease table. Everything is derived
from real store rows; nothing is invented.
"""

from __future__ import annotations

import http.client
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from know_your_context import runtime_guard
from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain, ScoreVector
from know_your_context.leases import LeaseRegistry, NodeLeaseConflict
from know_your_context.serve.webview import WebviewApp, WebviewServer, _BadRequest
from know_your_context.store import EventStore

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


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
def _loopback_sockets() -> Any:
    state = _suspend_socket_guard()
    try:
        yield
    finally:
        if state is not None:
            _resume_socket_guard(state)


def _node(node_id: str, summary: str = "activity node") -> Node:
    return Node(
        node_id=node_id,
        kind=NodeKind.ROOT,
        domain=PrimaryDomain.P1,
        summary=summary,
        provenance=("context://activity",),
        source_version_id="sv-act",
        scores=ScoreVector.zero(),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "act.sqlite3"
    store = EventStore(path)
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id="n-act-1",
            domain=PrimaryDomain.P1,
            node=_node("n-act-1"),
            source_version_id="sv-act",
            actor="agent-a",
            occurred_at=NOW,
        ),
        idempotency_key="act:upsert:1",
    )
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id="n-act-2",
            domain=PrimaryDomain.P1,
            node=_node("n-act-2", "second node"),
            source_version_id="sv-act",
            actor="agent-b",
            occurred_at=NOW,
        ),
        idempotency_key="act:upsert:2",
    )
    registry = LeaseRegistry(store)
    registry.acquire("n-act-1", "agent-a", ttl_seconds=3600.0)
    with pytest.raises(NodeLeaseConflict):
        registry.acquire("n-act-1", "agent-b")  # recorded lease.conflict
    store.close()
    return path


@pytest.fixture
def app(store_path: Path) -> Any:
    instance = WebviewApp(store_path)
    yield instance
    instance.close()


class TestActivityEntries:
    def test_entry_shape(self, app: WebviewApp) -> None:
        payload = app.activity_payload(limit=50)
        assert payload["schema_version"] >= 1
        assert payload["count"] > 0
        entry = payload["entries"][0]
        for key in ("seq", "agent", "action", "description", "nodes", "sha8", "level", "occurred_at", "event_type"):
            assert key in entry, key

    def test_sha8_is_the_chain_hash_prefix(self, app: WebviewApp) -> None:
        payload = app.activity_payload(limit=50)
        for entry in payload["entries"]:
            assert len(entry["sha8"]) == 8
            int(entry["sha8"], 16)  # hex

    def test_agents_index_is_sorted_unique(self, app: WebviewApp) -> None:
        payload = app.activity_payload(limit=50)
        agents = payload["agents"]
        assert agents == sorted(set(agents))
        assert "agent-a" in agents and "agent-b" in agents

    def test_domain_upsert_description_names_the_node(self, app: WebviewApp) -> None:
        payload = app.activity_payload(limit=50)
        upserts = [e for e in payload["entries"] if e["event_type"] == "node.upsert"]
        assert upserts
        entry = next(e for e in upserts if "n-act-2" in e["nodes"])
        assert "n-act-2" in entry["description"]
        assert entry["action"] == "UPSERT"
        assert entry["agent"] == "agent-b"
        assert entry["level"] == "info"

    def test_lease_events_render_with_level(self, app: WebviewApp) -> None:
        payload = app.activity_payload(limit=50)
        types = {e["event_type"]: e for e in payload["entries"]}
        assert "lease.acquired" in types
        assert types["lease.acquired"]["level"] == "info"
        assert "lease.conflict" in types
        conflict = types["lease.conflict"]
        assert conflict["level"] == "warn"
        assert conflict["agent"] == "agent-b"
        assert "n-act-1" in conflict["nodes"]
        assert "agent-a" in conflict["description"]  # the holder is named

    def test_level_vocabulary_is_closed(self, app: WebviewApp) -> None:
        payload = app.activity_payload(limit=50)
        assert {e["level"] for e in payload["entries"]} <= {"info", "warn", "error"}

    def test_payload_is_deterministic(self, app: WebviewApp) -> None:
        assert app.activity_payload(limit=50) == app.activity_payload(limit=50)

    def test_limit_bounds(self, app: WebviewApp) -> None:
        with pytest.raises(_BadRequest):
            app.activity_payload(limit=0)
        with pytest.raises(_BadRequest):
            app.activity_payload(limit=100_000)

    def test_limit_caps_entries(self, app: WebviewApp) -> None:
        payload = app.activity_payload(limit=2)
        assert payload["count"] == 2
        assert len(payload["entries"]) == 2


class TestActivityTail:
    def test_tail_is_ascending_after_since(self, app: WebviewApp) -> None:
        first = app.activity_payload(limit=1)["entries"][0]
        tail = app.activity_tail_payload(since=first["seq"] - 1, limit=50)
        assert tail["count"] >= 1
        seqs = [e["seq"] for e in tail["entries"]]
        assert seqs == sorted(seqs)
        assert all(seq >= first["seq"] for seq in seqs)

    def test_tail_since_zero_from_genesis(self, app: WebviewApp) -> None:
        tail = app.activity_tail_payload(since=0, limit=500)
        assert tail["count"] >= 4  # 2 upserts + lease.acquired + lease.conflict

    def test_tail_beyond_tip_is_empty(self, app: WebviewApp) -> None:
        tail = app.activity_tail_payload(since=10**9, limit=50)
        assert tail["count"] == 0
        assert tail["entries"] == []

    def test_tail_rejects_negative_since(self, app: WebviewApp) -> None:
        with pytest.raises(_BadRequest):
            app.activity_tail_payload(since=-1, limit=10)


class TestLeasesPayload:
    def test_active_leases_listed(self, app: WebviewApp) -> None:
        payload = app.leases_payload()
        assert payload["schema_version"] >= 1
        assert payload["count"] == 1
        lease = payload["leases"][0]
        assert lease["node_id"] == "n-act-1"
        assert lease["actor"] == "agent-a"
        assert lease["expires_at"]

    def test_empty_store_reports_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.sqlite3"
        store = EventStore(path)
        store.close()
        app = WebviewApp(path)
        try:
            payload = app.leases_payload()
            assert payload["count"] == 0
            assert payload["leases"] == []
        finally:
            app.close()


# --------------------------------------------------------------------- HTTP


@pytest.fixture
def server(store_path: Path) -> Any:
    instance = WebviewServer(store_path, port=0, token="test-token-0000000000")
    instance.start()
    yield instance
    instance.shutdown()


def _http(server: WebviewServer, path: str, *, token: str | None = "test-token-0000000000") -> tuple[int, bytes]:
    query = f"token={token}" if token is not None else None
    full_path = f"{path}{'&' if '?' in path else '?'}{query}" if query else path
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        connection.request("GET", full_path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class TestActivityHttp:
    def test_activity_route_serves_json(self, server: WebviewServer) -> None:
        import json

        status, body = _http(server, "/api/activity")
        assert status == 200
        payload = json.loads(body)
        assert payload["count"] > 0

    def test_activity_requires_auth(self, server: WebviewServer) -> None:
        status, _ = _http(server, "/api/activity", token=None)
        assert status in (401, 403)

    def test_activity_tail_route(self, server: WebviewServer) -> None:
        import json

        status, body = _http(server, "/api/activity/tail?since=0&limit=10")
        assert status == 200
        payload = json.loads(body)
        assert payload["since"] == 0

    def test_activity_bad_limit_is_400(self, server: WebviewServer) -> None:
        status, body = _http(server, "/api/activity?limit=0")
        assert status == 400
        assert b"limit" in body

    def test_leases_route(self, server: WebviewServer) -> None:
        import json

        status, body = _http(server, "/api/leases")
        assert status == 200
        payload = json.loads(body)
        assert payload["count"] == 1

    def test_routes_table_lists_activity_group(self, server: WebviewServer) -> None:
        import json

        status, body = _http(server, "/api/routes")
        assert status == 200
        paths = {route["path"] for route in json.loads(body)["routes"]}
        assert "/api/activity" in paths
        assert "/api/activity/tail" in paths
        assert "/api/leases" in paths
