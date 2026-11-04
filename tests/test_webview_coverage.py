"""Webview coverage checklist (W17 SUB-138).

Every product feature named in WEBVIEW_FEATURES must be *provably* reachable
from the UI: its tab and view section exist in index.html, every route it
lists is registered in API_ROUTES, and every route answers over real loopback
HTTP against a seeded store (200, or a typed 404 for detail routes whose
resource is absent in the seed — the route exists and fails closed).

Parametrized per feature so a regression names exactly which feature broke.
"""

from __future__ import annotations

import http.client
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from know_your_context import runtime_guard
from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain, ScoreVector
from know_your_context.leases import LeaseRegistry
from know_your_context.serve.webview import _ASSETS_DIR, API_ROUTES, WEBVIEW_FEATURES, WebviewServer
from know_your_context.store import EventStore

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
TOKEN = "test-token-0000000000"

# Detail routes whose resource may legitimately be absent in the seed: the
# route is proven by its typed 404 (fail-closed), not by a 500/405.
_DETAIL_OK_STATUSES = (200, 404)


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


@pytest.fixture(scope="module")
def store_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("coverage") / "cov.sqlite3"
    store = EventStore(path)
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id="n-cov-1",
            domain=PrimaryDomain.P1,
            node=Node(
                node_id="n-cov-1",
                kind=NodeKind.ROOT,
                domain=PrimaryDomain.P1,
                summary="coverage node",
                provenance=("context://coverage",),
                source_version_id="sv-cov",
                scores=ScoreVector.zero(),
                created_at=NOW,
                updated_at=NOW,
            ),
            source_version_id="sv-cov",
            actor="agent-a",
            occurred_at=NOW,
        ),
        idempotency_key="cov:upsert:1",
    )
    store.upsert_file(
        path="a.py",
        sha256="aa" * 32,
        size=10,
        mtime=1,
        language="python",
        generated_hint=False,
        extraction_version=1,
    )
    store.upsert_symbol(node_id="n-cov-1", name="cov_fn", kind="function", path="a.py", line=1)
    LeaseRegistry(store).acquire("n-cov-1", "agent-a", ttl_seconds=3600.0)
    store.close()
    return path


@pytest.fixture(scope="module")
def server(store_path: Path) -> Any:
    instance = WebviewServer(store_path, port=0, token=TOKEN)
    instance.start()
    yield instance
    instance.shutdown()


def _get(server: WebviewServer, path: str) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
    try:
        connection.request("GET", f"{path}{'&' if '?' in path else '?'}token={TOKEN}")
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    try:
        return response.status, json.loads(body)
    except json.JSONDecodeError:
        return response.status, {}


def _resolve(server: WebviewServer, route: str) -> str:
    """Fill {placeholders} and required query params with real resources."""
    if "{id}" in route:
        status, payload = _get(server, "/api/nodes?limit=1")
        assert status == 200 and payload.get("nodes"), "seed must carry at least one node"
        node_id = str(payload["nodes"][0]["id"])
        return route.replace("{id}", node_id)
    if "{receipt_id}" in route:
        return route.replace("{receipt_id}", "absent-receipt")  # typed 404 proves the route
    if "{name}" in route and route.startswith("/api/store/tables/"):
        return route.replace("{name}", "node_leases")
    if "{name}" in route and route.startswith("/api/switches/"):
        return route.replace("{name}", "webview.routes.activity_enabled")
    if "{name}" in route and route.startswith("/api/config/"):
        return route.replace("{name}", "webview.max_graph_nodes")
    if "{operation}" in route:
        return route.replace("{operation}", "knowledge.log_event")
    if route == "/api/search":
        return route + "?q=cov"
    if route == "/api/search/symbols":
        return route + "?q=cov"
    if route == "/api/codeintel":
        return route + "?op=callers&symbol=cov_fn"
    if route in ("/api/query", "/api/export/subgraph"):
        status, payload = _get(server, "/api/nodes?limit=1")
        assert status == 200 and payload.get("nodes"), "seed must carry at least one node"
        node_id = str(payload["nodes"][0]["id"])
        return route + f"?node_id={node_id}"
    return route


_FEATURES_BY_KEY = {feature["key"]: feature for feature in WEBVIEW_FEATURES}
_ALL_ROUTES = {route.path for route in API_ROUTES}
_HTML = (_ASSETS_DIR / "index.html").read_text(encoding="utf-8")


class TestChecklistShape:
    def test_every_feature_names_a_tab_and_routes(self) -> None:
        for feature in WEBVIEW_FEATURES:
            assert feature["key"] and feature["title"], feature
            assert feature["tab"], feature
            assert feature["routes"], feature

    def test_feature_keys_are_unique(self) -> None:
        keys = [feature["key"] for feature in WEBVIEW_FEATURES]
        assert len(keys := keys) == len(set(keys))

    def test_named_product_surfaces_are_covered(self) -> None:
        """The audit list from the W17 brief: every one has a checklist row."""
        keys = set(_FEATURES_BY_KEY)
        for required in (
            "graph",
            "search",
            "activity",
            "leases",
            "metrics",
            "health",
            "analytics",
            "audit",
            "proposals",
            "debt",
            "codeintel",
            "extractions",
            "store",
            "switches",
            "config",
            "hardware",
            "serve",
            "relearn",
            "bench",
            "ledgers",
            "experience-rerank",
            "exports",
            "contracts",
            "provenance",
            "query",
            "setup-lifecycle",
        ):
            assert required in keys, required


class TestFeatureRoutesRegistered:
    @pytest.mark.parametrize("key", sorted(_FEATURES_BY_KEY))
    def test_routes_registered(self, key: str) -> None:
        for route in _FEATURES_BY_KEY[key]["routes"]:
            assert route in _ALL_ROUTES, f"{key}: {route} not in API_ROUTES"


class TestFeatureTabsExist:
    @pytest.mark.parametrize("key", sorted(_FEATURES_BY_KEY))
    def test_tab_button_and_view_exist(self, key: str) -> None:
        tab = _FEATURES_BY_KEY[key]["tab"]
        assert f'data-tab="{tab}"' in _HTML, f"{key}: tab button {tab} missing"
        assert f'id="view-{tab}"' in _HTML, f"{key}: view section {tab} missing"


class TestFeatureRoutesAnswer:
    @pytest.mark.parametrize("key", sorted(_FEATURES_BY_KEY))
    def test_routes_answer_over_http(self, key: str, server: WebviewServer) -> None:
        feature = _FEATURES_BY_KEY[key]
        for route in feature["routes"]:
            resolved = _resolve(server, route)
            status, payload = _get(server, resolved)
            if "{" in route:
                assert status in _DETAIL_OK_STATUSES, f"{key}: {resolved} -> {status}"
                if status == 404:
                    assert "error" in payload, f"{key}: {resolved} 404 without typed error"
            else:
                assert status == 200, f"{key}: {resolved} -> {status} {payload}"
                assert payload.get("schema_version", 0) >= 1, f"{key}: {resolved} lacks schema_version"


class TestFeaturesEndpoint:
    def test_features_payload_matches_module(self, server: WebviewServer) -> None:
        status, payload = _get(server, "/api/features")
        assert status == 200
        assert payload["count"] == len(WEBVIEW_FEATURES)
        keys = {feature["key"] for feature in payload["features"]}
        assert keys == set(_FEATURES_BY_KEY)

    def test_features_endpoint_lists_cli_surface(self, server: WebviewServer) -> None:
        _, payload = _get(server, "/api/features")
        for feature in payload["features"]:
            assert isinstance(feature["cli"], str) and feature["cli"]
