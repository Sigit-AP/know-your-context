"""Webview tests for the code-intelligence endpoint (W15, SUB-130).

``GET /api/codeintel?op=...`` serves the deep graph ops
(:mod:`know_your_context.analytics.code_intel`) read-only over the live
store — the same code path as the ``codeintel`` CLI.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from know_your_context.serve.webview import API_ROUTES, WebviewApp, _BadRequest, _NodeNotFound
from know_your_context.store import EventStore


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "ci.sqlite3"
    store = EventStore(path)
    store.upsert_symbol(node_id="n:a", name="alpha", kind="function", path="core/f.py", line=1, col=0)
    store.upsert_symbol(node_id="n:b", name="beta", kind="function", path="core/f.py", line=5, col=0)
    store.upsert_symbol(node_id="n:c", name="gamma", kind="function", path="ui/g.py", line=2, col=0)
    store.add_graph_edge("n:a", "n:b", "calls", line=3)
    store.add_graph_edge("n:b", "n:c", "calls", line=7)
    store.close()
    return path


@pytest.fixture()
def app(store_path: Path):
    app = WebviewApp(store_path)
    yield app
    app.close()


class TestRoute:
    def test_api_route_is_registered(self) -> None:
        assert "/api/codeintel" in {route.path for route in API_ROUTES}

    def test_route_is_get_and_authenticated(self) -> None:
        route = next(r for r in API_ROUTES if r.path == "/api/codeintel")
        assert route.method == "GET"
        assert route.access == "authenticated"

    def test_tab_is_wired_in_the_shell(self) -> None:
        html = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "know_your_context"
            / "serve"
            / "webview_assets"
            / "index.html"
        ).read_text(encoding="utf-8")
        assert 'data-tab="codeintel"' in html
        assert "codeintel-form" in html
        app_js = (
            Path(__file__).resolve().parent.parent / "src" / "know_your_context" / "serve" / "webview_assets" / "app.js"
        ).read_text(encoding="utf-8")
        assert "/api/codeintel?op=" in app_js


class TestPayload:
    def test_callers(self, app: WebviewApp) -> None:
        payload = app.codeintel_payload("callers", symbol="gamma")
        assert payload["kind"] == "analytics.code_intel.callers_of"
        assert [e["name"] for e in payload["entries"]] == ["beta", "alpha"]

    def test_callees(self, app: WebviewApp) -> None:
        payload = app.codeintel_payload("callees", symbol="alpha")
        assert [e["name"] for e in payload["entries"]] == ["beta", "gamma"]

    def test_dependency_path(self, app: WebviewApp) -> None:
        payload = app.codeintel_payload("path", source="alpha", target="gamma")
        assert payload["paths"][0]["node_ids"] == ["n:a", "n:b", "n:c"]

    def test_impact(self, app: WebviewApp) -> None:
        payload = app.codeintel_payload("impact", symbol="gamma")
        assert payload["direct_dependents"] == 1

    def test_co_change_empty_history(self, app: WebviewApp) -> None:
        payload = app.codeintel_payload("co-change")
        assert payload["pair_count"] == 0

    def test_boundary(self, app: WebviewApp) -> None:
        payload = app.codeintel_payload("boundary", layers="core:core;ui:ui")
        assert payload["total_violations"] == 1

    def test_unknown_symbol_maps_to_not_found(self, app: WebviewApp) -> None:
        with pytest.raises(_NodeNotFound):
            app.codeintel_payload("callers", symbol="ghost")

    def test_missing_symbol_maps_to_bad_request(self, app: WebviewApp) -> None:
        with pytest.raises(_BadRequest):
            app.codeintel_payload("callers")

    def test_unknown_op_maps_to_bad_request(self, app: WebviewApp) -> None:
        with pytest.raises(_BadRequest):
            app.codeintel_payload("nonsense", symbol="alpha")

    def test_bad_layers_map_to_bad_request(self, app: WebviewApp) -> None:
        with pytest.raises(_BadRequest):
            app.codeintel_payload("boundary", layers="bogus")

    def test_missing_layers_maps_to_bad_request(self, app: WebviewApp) -> None:
        with pytest.raises(_BadRequest):
            app.codeintel_payload("boundary")

    def test_payload_is_read_only(self, app: WebviewApp, store_path: Path) -> None:
        before = store_path.read_bytes()
        app.codeintel_payload("impact", symbol="beta")
        app.codeintel_payload("co-change")
        assert store_path.read_bytes() == before


class TestHttpWiring:
    def _get(self, server, path: str) -> tuple[int, dict | None]:
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        try:
            connection.request("GET", f"{path}{'&' if '?' in path else '?'}token={server.token}")
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body) if body else None
        finally:
            connection.close()

    def test_endpoint_over_http(self, store_path: Path) -> None:
        from know_your_context.serve.webview import WebviewServer

        server = WebviewServer(store_path, port=0)
        server.start()
        try:
            status, payload = self._get(server, "/api/codeintel?op=callers&symbol=gamma")
            assert status == 200
            assert payload is not None
            assert payload["kind"] == "analytics.code_intel.callers_of"
            assert len(payload["entries"]) == 2
        finally:
            server.shutdown()

    def test_missing_op_is_400(self, store_path: Path) -> None:
        from know_your_context.serve.webview import WebviewServer

        server = WebviewServer(store_path, port=0)
        server.start()
        try:
            status, payload = self._get(server, "/api/codeintel")
            assert status == 400
            assert payload is not None
            assert "op is required" in json.dumps(payload)
        finally:
            server.shutdown()

    def test_unknown_symbol_is_404(self, store_path: Path) -> None:
        from know_your_context.serve.webview import WebviewServer

        server = WebviewServer(store_path, port=0)
        server.start()
        try:
            status, _ = self._get(server, "/api/codeintel?op=callers&symbol=ghost")
            assert status == 404
        finally:
            server.shutdown()
