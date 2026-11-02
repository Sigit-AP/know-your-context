"""Webview tests for the knowledge debt queue endpoint (I4, W14 SUB-128).

``GET /api/debt`` mirrors ``debt list``: the budgeted, prioritized queue is
served read-only over the live store, honouring the same kill switch, and
the Debt tab is wired into the viewer shell.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain, ScoreVector
from know_your_context.serve.webview import API_ROUTES, WebviewApp
from know_your_context.store import EventStore

NOW = datetime(2026, 2, 1, tzinfo=UTC)
OLD = datetime(2025, 12, 1, tzinfo=UTC)


def _upsert(
    store: EventStore,
    node_id: str,
    *,
    confidence: str = "0.600000000000",
    last_used_at: datetime | None = None,
) -> None:
    fresh = datetime.now(UTC)
    node = Node(
        node_id=node_id,
        kind=NodeKind.ROOT,
        domain=PrimaryDomain.P1,
        summary=node_id,
        provenance=("context://corpus/lesson.md",),
        source_version_id="sv-debt-wv",
        scores=ScoreVector.model_validate(
            {"p": "0.5", "m": "0.5", "e": "0.5", "uncertainty": "0.5", "confidence": confidence}
        ),
        created_at=OLD,
        updated_at=fresh,
        last_used_at=last_used_at if last_used_at is not None else fresh,
    )
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id=node.node_id,
            domain=node.domain,
            node=node,
            source_version_id=node.source_version_id,
            occurred_at=NOW,
        ),
        idempotency_key=f"debt-wv:upsert:{node.node_id}:{store.node_version(node.node_id)}",
    )


@pytest.fixture
def app(tmp_path: Path):
    store = EventStore(tmp_path / "wv.sqlite3")
    _upsert(store, "n-low", confidence="0.100000000000")
    _upsert(store, "n-stale", last_used_at=OLD)
    _upsert(store, "n-ok")
    store.close()
    app = WebviewApp(tmp_path / "wv.sqlite3")
    yield app
    app.close()


class TestRoute:
    def test_api_route_is_registered(self) -> None:
        paths = {route.path for route in API_ROUTES}
        assert "/api/debt" in paths

    def test_route_is_get_and_authenticated(self) -> None:
        route = next(r for r in API_ROUTES if r.path == "/api/debt")
        assert route.method == "GET"
        assert route.access == "authenticated"


class TestDebtPayload:
    def test_payload_has_the_contract_keys(self, app: WebviewApp) -> None:
        payload = app.debt_payload()
        for key in ("schema_version", "disabled", "count", "budget", "items", "signal_counts"):
            assert key in payload, key

    def test_debt_nodes_are_listed_in_priority_order(self, app: WebviewApp) -> None:
        payload = app.debt_payload()
        node_ids = [item["node_id"] for item in payload["items"]]
        assert set(node_ids) == {"n-low", "n-stale"}
        priorities = [item["priority"] for item in payload["items"]]
        assert priorities == sorted(priorities, reverse=True)

    def test_items_carry_the_action_and_proposal_draft(self, app: WebviewApp) -> None:
        payload = app.debt_payload()
        for item in payload["items"]:
            assert item["action"] in ("improve", "merge", "retire")
            assert item["proposal"]["diff"]

    def test_empty_store_reports_an_empty_queue(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "empty.sqlite3")
        store.close()
        app = WebviewApp(tmp_path / "empty.sqlite3")
        try:
            payload = app.debt_payload()
            assert payload["count"] == 0
            assert payload["items"] == []
            assert payload["disabled"] is False
        finally:
            app.close()

    def test_kill_switch_disables_the_payload(self, app: WebviewApp, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KYS_DEBT_QUEUE", "0")
        payload = app.debt_payload()
        assert payload["disabled"] is True
        assert payload["items"] == []

    def test_payload_is_read_only(self, app: WebviewApp, tmp_path: Path) -> None:
        raw_before = (tmp_path / "wv.sqlite3").read_bytes()
        app.debt_payload()
        assert (tmp_path / "wv.sqlite3").read_bytes() == raw_before

    def test_payload_is_deterministic_in_structure(self, app: WebviewApp) -> None:
        from know_your_context.domain import canonical_json

        first = app.debt_payload()
        second = app.debt_payload()
        # generated_at is wall clock; everything else must be identical.
        first.pop("generated_at")
        second.pop("generated_at")
        assert canonical_json(first) == canonical_json(second)


class TestTab:
    def test_index_declares_the_debt_tab(self) -> None:
        from know_your_context.serve import webview

        html = (Path(webview.__file__).parent / "webview_assets" / "index.html").read_text(encoding="utf-8")
        assert 'data-tab="debt"' in html
        assert 'id="view-debt"' in html

    def test_app_js_fetches_the_debt_endpoint(self) -> None:
        from know_your_context.serve import webview

        js = (Path(webview.__file__).parent / "webview_assets" / "app.js").read_text(encoding="utf-8")
        assert "/api/debt" in js
