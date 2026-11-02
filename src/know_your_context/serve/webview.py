"""Loopback-only, token-authenticated, read-only web viewer (W7 SUB-101).

A stdlib-only (``http.server``) web UI over the knowledge store, following
the codegraph ``ui-server`` security model (RESEARCH-COVERAGE-AUDIT §4.5):

* **Threat model: DNS rebinding.** The ``Host`` header must name a loopback
  address (``localhost`` / ``127.0.0.1`` / ``[::1]``) **with the server's own
  port** — anything else is 403, checked before auth. A non-loopback
  ``Origin`` header is likewise 403. No CORS header is ever emitted.
* **Token auth in the URL.** A per-start random token (``?token=``) gates
  every byte — assets and API alike — compared with
  :func:`hmac.compare_digest`. The token is never echoed in responses or
  embedded in served assets.
* **Read-only store.** Only ``GET``/``HEAD`` exist (everything else is 405);
  the app runs ``SELECT`` queries and the existing read APIs
  (:mod:`know_your_context.retrieval_v2`, :mod:`know_your_context.ops`,
  :mod:`know_your_context.analytics`) — never a write.
* **No path traversal.** Assets are served from a fixed name allowlist under
  ``webview_assets/``; no request path is ever joined into a filesystem path.
* **JSON is markup-safe.** Every API payload is serialized with
  :func:`escape_json_bytes`, which escapes ``&``, ``<``, ``>`` and the JS
  line separators, so a hostile node label cannot break out of the page
  (anti stored-XSS; the client renders via ``textContent`` only).

Endpoints (all GET, JSON unless noted):

* ``/`` — the static SPA shell (``text/html``, strict CSP).
* ``/static/app.js`` / ``/static/style.css`` — the bundled vanilla assets.
* ``/api/graph`` — nodes + edges + server-computed :mod:`viz.layout` frame.
* ``/api/node/<id>`` — node detail: P/M/E scores, provenance, relations.
* ``/api/search?q=&kind=&lang=&limit=`` — tiered symbol search (retrieval_v2).
* ``/api/hierarchy/<id>?depth=`` — callers + callees (retrieval_v2).
* ``/api/metrics`` — :func:`ops.metrics.collect_ops_metrics` (redacted).
* ``/api/health`` — :func:`ops.diagnose.run_diagnostics` report.
* ``/api/analytics`` — centrality + communities + bridges.
* ``/api/audit?limit=`` — newest audit events, payloads redacted.
* ``/api/proposals`` — review-signal candidates (proposal-only; never writes).
* ``/api/extractions`` — the contract-v4 extraction review queue (read-only).
* ``/api/refs`` — unresolved-reference statistics.

Determinism: every payload is a pure function of store state (sorted keys,
canonical orderings inherited from the underlying modules), so two requests
against an unchanged store answer byte-identically.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import re
import secrets
import sys
import threading
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from know_your_context.analytics.bridges import find_bridges
from know_your_context.analytics.centrality import compute_centrality
from know_your_context.analytics.communities import detect_communities
from know_your_context.analytics.review_signals import collect_review_signals
from know_your_context.domain import Node
from know_your_context.enums import RouteAccess
from know_your_context.ops.diagnose import exit_code as diagnose_exit_code
from know_your_context.ops.diagnose import run_diagnostics
from know_your_context.ops.metrics import collect_ops_metrics, latency_summary
from know_your_context.ops.switches import ENV_DISABLE_WEBVIEW, KILL_SWITCHES, load_switches
from know_your_context.retrieval_v2 import CallHierarchy, HierarchyError, LookupError, SymbolLookup
from know_your_context.security import redact_value, sanitize_text
from know_your_context.serve.middleware import (
    CapabilityDeniedError,
    CapabilityGateMiddleware,
    MiddlewareStack,
    RequestContext,
    RequestIdMiddleware,
    TimingMiddleware,
    TypedErrorMiddleware,
)
from know_your_context.store import EventStore
from know_your_context.viz.layout import LayoutError
from know_your_context.viz.layout import layout as compute_layout

__all__ = [
    "ANALYTICS_MAX_NODES",
    "API_ROUTES",
    "AUDIT_LIMIT_MAX",
    "AUDIT_TAIL_LIMIT_MAX",
    "AsyncWebviewServer",
    "DEFAULT_HOST_ALLOWLIST",
    "HIERARCHY_DEPTH_MAX",
    "HIERARCHY_DEPTH_MIN",
    "MAX_GRAPH_NODES",
    "QUERY_LIMIT_MAX",
    "SCHEMA_VERSION",
    "SEARCH_LIMIT_MAX",
    "SERIES_LIMIT_MAX",
    "ApiRoute",
    "StoreUnavailableError",
    "WebviewApp",
    "WebviewError",
    "WebviewServer",
    "escape_json_bytes",
    "generate_token",
    "host_allowed",
    "main",
    "origin_allowed",
]

SCHEMA_VERSION = 1

#: Fail-closed caps: the layout engine itself caps at 4096 nodes; analytics
#: is capped lower and reports ``partial`` honestly rather than lying.
MAX_GRAPH_NODES = 4096
ANALYTICS_MAX_NODES = 2048
SEARCH_LIMIT_MAX = 100
AUDIT_LIMIT_MAX = 500
#: W13 route caps (fail-closed bounds for the new JSON API surface).
QUERY_LIMIT_MAX = 200
AUDIT_TAIL_LIMIT_MAX = 500
SERIES_LIMIT_MAX = 366
HIERARCHY_DEPTH_MIN, HIERARCHY_DEPTH_MAX = 1, 8

#: Loopback hostnames accepted in the ``Host`` header (codegraph rule 1).
DEFAULT_HOST_ALLOWLIST: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")

_FRAME_WIDTH = 1600.0
_FRAME_HEIGHT = 1000.0
_TOP_RANKING = 50
_TOKEN_MIN_LENGTH = 16
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
_KIND_LANG_RE = re.compile(r"[a-z0-9_+#-]{1,32}")
_PROPOSAL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_QUERY_DIRECTIONS = ("out", "in", "both")

_ASSETS_DIR = Path(__file__).resolve().parent / "webview_assets"
_STATIC_ASSETS: dict[str, tuple[str, str]] = {
    "index.html": ("text/html; charset=utf-8", "index.html"),
    "app.js": ("text/javascript; charset=utf-8", "app.js"),
    "style.css": ("text/css; charset=utf-8", "style.css"),
}

_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

_LATEST_REVISIONS_SQL = (
    "SELECT nr.node_id AS node_id, nr.payload_json AS payload_json FROM node_revision nr "
    "JOIN (SELECT node_id, MAX(version) AS v FROM node_revision GROUP BY node_id) latest "
    "ON latest.node_id = nr.node_id AND latest.v = nr.version "
    "WHERE json_extract(nr.payload_json, '$.lifecycle_status') != 'superseded' "
    "ORDER BY nr.node_id"
)


# ------------------------------------------------------------------ errors


class WebviewError(ValueError):
    """Invalid webview configuration or argument (fail-closed)."""


class StoreUnavailableError(WebviewError):
    """The store path does not exist or is not a readable store file."""


class _NodeNotFound(Exception):
    """Internal: the requested node id is unknown (mapped to 404)."""


class _BadRequest(Exception):
    """A request parameter failed validation (mapped to 400)."""


# ------------------------------------------------------------------ routes


@dataclass(frozen=True)
class ApiRoute:
    """One JSON API route of the viewer (all read-only GET, authenticated)."""

    path: str
    method: str
    access: RouteAccess
    summary: str

    def __post_init__(self) -> None:
        if not self.path.startswith("/api/"):
            raise WebviewError(f"api route path must start with /api/, got {self.path!r}")
        if self.method != "GET":
            raise WebviewError(f"the viewer is read-only; route {self.path!r} declares {self.method!r}")


#: The complete route table (W13): the original tab payloads plus the real
#: JSON API routes (graph query, metrics series, audit tail, improvement
#: proposals list/detail, health detail, bench summary, store stats, switches).
API_ROUTES: tuple[ApiRoute, ...] = (
    ApiRoute("/api/graph", "GET", RouteAccess.AUTHENTICATED, "laid-out graph payload for the viewer"),
    ApiRoute("/api/node/{id}", "GET", RouteAccess.AUTHENTICATED, "one node's detail, relations and evidence"),
    ApiRoute("/api/search", "GET", RouteAccess.AUTHENTICATED, "symbol search over the universal graph"),
    ApiRoute("/api/hierarchy/{id}", "GET", RouteAccess.AUTHENTICATED, "call hierarchy around one node"),
    ApiRoute("/api/metrics", "GET", RouteAccess.AUTHENTICATED, "operational metrics snapshot"),
    ApiRoute("/api/metrics/series", "GET", RouteAccess.AUTHENTICATED, "daily event buckets + health history series"),
    ApiRoute("/api/health", "GET", RouteAccess.AUTHENTICATED, "diagnostic findings summary"),
    ApiRoute("/api/health/detail", "GET", RouteAccess.AUTHENTICATED, "health + store stats + timing + switches"),
    ApiRoute("/api/analytics", "GET", RouteAccess.AUTHENTICATED, "centrality/communities/bridges"),
    ApiRoute("/api/audit", "GET", RouteAccess.AUTHENTICATED, "recent audit events (paged)"),
    ApiRoute("/api/audit/tail", "GET", RouteAccess.AUTHENTICATED, "audit events after a sequence number, ascending"),
    ApiRoute("/api/proposals", "GET", RouteAccess.AUTHENTICATED, "review signals derived from the graph"),
    ApiRoute("/api/improvements", "GET", RouteAccess.AUTHENTICATED, "improvement proposals from the event log"),
    ApiRoute("/api/improvements/{id}", "GET", RouteAccess.AUTHENTICATED, "one improvement proposal with lifecycle"),
    ApiRoute("/api/extractions", "GET", RouteAccess.AUTHENTICATED, "extraction review queue"),
    ApiRoute("/api/refs", "GET", RouteAccess.AUTHENTICATED, "unresolved reference statistics"),
    ApiRoute("/api/query", "GET", RouteAccess.AUTHENTICATED, "graph neighborhood query around one node"),
    ApiRoute("/api/bench", "GET", RouteAccess.AUTHENTICATED, "per-operation latency summary from the journal"),
    ApiRoute("/api/store/stats", "GET", RouteAccess.AUTHENTICATED, "table counts, pragmas and schema version"),
    ApiRoute("/api/switches", "GET", RouteAccess.AUTHENTICATED, "resolved state of every operator kill switch"),
)


# ------------------------------------------------------------------ security


def generate_token() -> str:
    """A fresh URL-safe bearer token for one viewer session."""
    return secrets.token_urlsafe(32)


def escape_json_bytes(payload: Any) -> bytes:
    """Serialize *payload* as deterministic, markup-safe JSON bytes.

    Sorted keys and fixed separators make the output byte-stable; ``&``,
    ``<`` and ``>`` are escaped to ``\\uXXXX`` (``ensure_ascii`` already
    covers U+2028/U+2029), so the bytes can never close an HTML context or
    smuggle markup into the page.
    """
    text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").encode("utf-8")


def _split_host_port(header: str) -> tuple[str, int | None]:
    """Parse ``Host`` into (lowercased host, port); port ``None`` if absent/invalid."""
    value = header.strip().lower()
    if not value:
        return "", None
    if value.startswith("["):  # [v6]:port
        end = value.find("]")
        if end == -1:
            return "", None
        host = value[1:end]
        rest = value[end + 1 :]
        if not rest:
            return host, None
        if not rest.startswith(":"):
            return "", None
        port_text = rest[1:]
    else:
        if value.count(":") != 1:
            return value, None  # bare hostname or junk
        host, port_text = value.rsplit(":", 1)
    if not port_text.isdigit():
        return host, None
    return host, int(port_text)


def host_allowed(header: str, port: int, allowlist: Sequence[str] = DEFAULT_HOST_ALLOWLIST) -> bool:
    """The DNS-rebinding guard: loopback host *and* the server's own port.

    A bare host without a port is refused: the browser always sends one for
    a non-default port, and accepting a port-less Host would let a rebound
    origin's fetch (which copies its own port) mismatch silently.
    """
    host, claimed_port = _split_host_port(header)
    if claimed_port is None or claimed_port != port:
        return False
    return host in {entry.lower() for entry in allowlist}


def origin_allowed(origin: str | None, port: int) -> bool:
    """A present ``Origin`` must be loopback with the server's port (rule 2)."""
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    hostname = (parts.hostname or "").lower()
    if hostname not in DEFAULT_HOST_ALLOWLIST:
        return False
    try:
        return parts.port == port
    except ValueError:
        return False


def _valid_token(token: str) -> bool:
    return len(token) >= _TOKEN_MIN_LENGTH and _TOKEN_RE.fullmatch(token) is not None


# --------------------------------------------------------------- the app


class WebviewApp:
    """Read-only application logic over one store; shared by HTTP and tests."""

    def __init__(self, store_path: str | Path) -> None:
        path = Path(store_path)
        if not path.is_file():
            raise StoreUnavailableError(f"store not found or not a file: {path}")
        self._store_path = path
        self._store = EventStore(path)
        self._closed = False
        self._lookup = SymbolLookup()
        self._hierarchy = CallHierarchy()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._store.close()

    # ------------------------------------------------------- read model

    def _live_nodes(self) -> list[dict[str, Any]]:
        """Latest non-superseded revision per node, canonical id order."""
        with self._store.read() as conn:
            rows = conn.execute(_LATEST_REVISIONS_SQL).fetchall()
        nodes: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            scores = payload.get("scores") or {}
            nodes.append(
                {
                    "id": str(payload["node_id"]),
                    "label": sanitize_text(str(payload.get("summary") or payload["node_id"])),
                    "domain": str(payload.get("domain", "")),
                    "kind": str(payload.get("kind", "leaf")),
                    "lifecycle_status": str(payload.get("lifecycle_status", "active")),
                    "version": int(payload.get("version", 1)),
                    "parent_id": payload.get("parent_id"),
                    "scores": {
                        key: str(scores.get(key, "0.000000000000"))
                        for key in ("p", "m", "e", "uncertainty", "confidence")
                    },
                    "provenance": [sanitize_text(str(item)) for item in payload.get("provenance") or []],
                }
            )
        return nodes

    def _live_links(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``node_relation`` rows plus ``parent_of`` edges, canonical order."""
        node_ids = {node["id"] for node in nodes}
        links: list[dict[str, Any]] = []
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT source_node_id, target_node_id, relation FROM node_relation ORDER BY relation_seq"
            ).fetchall()
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            source, target, relation = str(row["source_node_id"]), str(row["target_node_id"]), str(row["relation"])
            if source in node_ids and target in node_ids and (source, target, relation) not in seen:
                seen.add((source, target, relation))
                links.append({"source": source, "target": target, "relation": relation, "confidence": "EXTRACTED"})
        for node in nodes:
            parent = node.get("parent_id")
            if isinstance(parent, str) and parent in node_ids:
                key = (parent, node["id"], "parent_of")
                if key not in seen:
                    seen.add(key)
                    links.append(
                        {"source": parent, "target": node["id"], "relation": "parent_of", "confidence": "EXTRACTED"}
                    )
        links.sort(key=lambda link: (link["source"], link["target"], link["relation"]))
        return links

    def _read_model(self, *, cap: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
        nodes = self._live_nodes()
        total = len(nodes)
        truncated = total > cap
        kept = nodes[:cap]
        links = self._live_links(kept)
        return kept, links, total, truncated

    # ------------------------------------------------------- endpoints

    def graph_payload(self) -> dict[str, Any]:
        nodes, links, total, truncated = self._read_model(cap=MAX_GRAPH_NODES)
        layout_input = {
            "nodes": [{"id": node["id"]} for node in nodes],
            "links": [{"source": link["source"], "target": link["target"]} for link in links],
        }
        try:
            result = compute_layout(layout_input, width=_FRAME_WIDTH, height=_FRAME_HEIGHT)
            positions = result.positions
            layout_meta = dict(result.metadata)
        except LayoutError:
            positions = {}
            layout_meta = {"mode": "none", "node_count": len(nodes), "quantum": 0.001}
        out_nodes = []
        for node in nodes:
            x, y = positions.get(node["id"], (_FRAME_WIDTH / 2.0, _FRAME_HEIGHT / 2.0))
            entry = {key: value for key, value in node.items() if key != "parent_id"}
            entry["x"], entry["y"] = float(x), float(y)
            out_nodes.append(entry)
        return {
            "schema_version": SCHEMA_VERSION,
            "nodes": out_nodes,
            "edges": [{"source": link["source"], "target": link["target"], "kind": link["relation"]} for link in links],
            "layout": {**layout_meta, "width": _FRAME_WIDTH, "height": _FRAME_HEIGHT},
            "total_nodes": total,
            "total_edges": len(links),
            "truncated": truncated,
        }

    def node_payload(self, node_id: str) -> dict[str, Any]:
        node = self._store.current_node(node_id)
        if node is None:
            raise _NodeNotFound(node_id)
        return self._node_detail(node)

    def _node_detail(self, node: Node) -> dict[str, Any]:
        # Relations and evidence live in their own tables: a node's stored
        # revision predates later relation.add / evidence.append events, so
        # the truth is queried, never read from the stale revision payload.
        relations = [
            {"kind": rel.kind.value, "target": rel.target_node_id}
            for rel in sorted(
                self._store.relations_from(node.node_id),
                key=lambda rel: (rel.kind.value, rel.target_node_id),
            )
        ]
        incident: list[dict[str, str]] = []
        if node.parent_id is not None:
            incident.append({"source": node.parent_id, "target": node.node_id, "kind": "parent_of"})
        for rel in relations:
            incident.append({"source": node.node_id, "target": rel["target"], "kind": rel["kind"]})
        with self._store.read() as conn:
            incoming = conn.execute(
                "SELECT source_node_id, relation FROM node_relation WHERE target_node_id = ? ORDER BY relation_seq",
                (node.node_id,),
            ).fetchall()
            evidence_rows = conn.execute(
                "SELECT evidence_id FROM evidence "
                "WHERE json_extract(payload_json, '$.node_id') = ? ORDER BY evidence_id",
                (node.node_id,),
            ).fetchall()
        evidence_ids = [str(row["evidence_id"]) for row in evidence_rows]
        for row in incoming:
            incident.append(
                {"source": str(row["source_node_id"]), "target": node.node_id, "kind": str(row["relation"])}
            )
        symbol = self._store.symbol_by_node(node.node_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": node.node_id,
            "parent_id": node.parent_id,
            "domain": node.domain.value,
            "kind": node.kind.value,
            "summary": sanitize_text(node.summary),
            "lifecycle_status": node.lifecycle_status.value,
            "version": node.version,
            "scores": node.scores.quantized(),
            "professional_level": node.scores.professional_level,
            "provenance": [sanitize_text(item) for item in node.provenance],
            "context_tags": [sanitize_text(item) for item in node.context_tags],
            "evidence_ids": evidence_ids,
            "relations": relations,
            "incident_edges": incident,
            "symbol": (
                {"name": symbol.name, "kind": symbol.kind, "path": symbol.path, "line": symbol.line, "col": symbol.col}
                if symbol is not None
                else None
            ),
        }

    def search_payload(self, query: str, *, kind: str | None, lang: str | None, limit: int) -> dict[str, Any]:
        if not query.strip():
            raise _BadRequest("q must be a non-empty search string")
        for label, value in (("kind", kind), ("lang", lang)):
            if value is not None and _KIND_LANG_RE.fullmatch(value) is None:
                raise _BadRequest(f"{label} filter must match {_KIND_LANG_RE.pattern}, got {value!r}")
        if not 1 <= limit <= SEARCH_LIMIT_MAX:
            raise _BadRequest(f"limit must be in [1, {SEARCH_LIMIT_MAX}], got {limit}")
        composed = query.strip()
        if kind is not None:
            composed += f" kind:{kind}"
        if lang is not None:
            composed += f" lang:{lang}"
        try:
            hits = self._lookup.search(self._store, composed, limit=limit)
        except LookupError as error:
            raise _BadRequest(str(error)) from error
        results = [
            {
                "node_id": hit.node_id,
                "name": hit.name,
                "kind": hit.kind,
                "path": hit.path,
                "line": hit.line,
                "col": hit.col,
                "score": hit.score,
                "source": hit.source,
            }
            for hit in hits
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "query": query,
            "filters": {"kind": kind, "lang": lang},
            "limit": limit,
            "count": len(results),
            "results": results,
        }

    def hierarchy_payload(self, node_id: str, *, depth: int) -> dict[str, Any]:
        if not HIERARCHY_DEPTH_MIN <= depth <= HIERARCHY_DEPTH_MAX:
            raise _BadRequest(f"depth must be in [{HIERARCHY_DEPTH_MIN}, {HIERARCHY_DEPTH_MAX}], got {depth}")
        try:
            callers_report = self._hierarchy.callers(self._store, node_id, depth=depth)
            callees_report = self._hierarchy.callees(self._store, node_id, depth=depth)
        except HierarchyError as error:
            raise _BadRequest(str(error)) from error
        if not callers_report.definitions and not callees_report.definitions:
            raise _NodeNotFound(node_id)

        def _entries(report: Any) -> dict[str, Any]:
            entries = [
                {
                    "node_id": entry.node_id,
                    "name": entry.name,
                    "kind": entry.kind,
                    "path": entry.path,
                    "line": entry.line,
                    "depth": entry.depth,
                    "via_edge_kind": entry.via_edge_kind,
                    "via_line": entry.via_line,
                }
                for definition in report.definitions
                for entry in definition.entries
            ]
            return {
                "entries": entries,
                "definitions": len(report.definitions),
                "truncated": any(definition.truncated for definition in report.definitions),
            }

        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": node_id,
            "depth": depth,
            "callers": _entries(callers_report),
            "callees": _entries(callees_report),
        }

    def metrics_payload(self) -> dict[str, Any]:
        return redact_value(collect_ops_metrics(self._store))

    def health_payload(self) -> dict[str, Any]:
        report = run_diagnostics(self._store)
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": report.ok,
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "worst_severity": report.worst_severity,
            "exit_code": diagnose_exit_code(report),
            "checked": list(report.checked),
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": sanitize_text(finding.message),
                    "details": redact_value(finding.details),
                }
                for finding in report.findings
            ],
        }

    def analytics_payload(self) -> dict[str, Any]:
        nodes, links, total, truncated = self._read_model(cap=ANALYTICS_MAX_NODES)
        graph = {
            "nodes": [
                {"id": node["id"], "domain": node["domain"], "label": node["label"], "version": node["version"]}
                for node in nodes
            ],
            "links": links,
        }
        centrality = compute_centrality(graph, top=_TOP_RANKING) if nodes else {"global": {}}
        communities = detect_communities(graph["nodes"], links) if nodes else {"communities": []}
        membership = {
            member: community["id"]
            for community in communities.get("communities", [])
            for member in community["members"]
        }
        bridges = (
            find_bridges(graph["nodes"], links, membership=membership)
            if nodes
            else {"bridges": [], "counts": {"candidates": 0, "emitted": 0, "by_kind": {}}}
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "centrality": centrality,
            "communities": communities,
            "bridges": bridges,
            "node_count": len(nodes),
            "total_nodes": total,
            "partial": truncated,
        }

    def audit_payload(self, *, limit: int) -> dict[str, Any]:
        if not 1 <= limit <= AUDIT_LIMIT_MAX:
            raise _BadRequest(f"limit must be in [1, {AUDIT_LIMIT_MAX}], got {limit}")
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT event_seq, event_type, actor, occurred_at, payload_json FROM event_log "
                "WHERE kind = 'audit' ORDER BY event_seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                payload = None
            events.append(
                {
                    "event_seq": int(row["event_seq"]),
                    "event_type": str(row["event_type"]),
                    "actor": str(row["actor"]),
                    "occurred_at": str(row["occurred_at"]),
                    "payload": redact_value(payload),
                }
            )
        return {"schema_version": SCHEMA_VERSION, "count": len(events), "events": events}

    def proposals_payload(self) -> dict[str, Any]:
        nodes, links, _, _ = self._read_model(cap=ANALYTICS_MAX_NODES)
        communities = detect_communities([{"id": node["id"], "label": node["label"]} for node in nodes], links)
        report = collect_review_signals(
            [{"id": node["id"], "version": node["version"], "provenance": node["provenance"]} for node in nodes],
            links,
            communities=communities.get("communities", []),
        )
        items = report.get("items", [])
        return {
            "schema_version": SCHEMA_VERSION,
            "count": len(items),
            "signals": items,
            "signal_counts": report.get("signal_counts", {}),
        }

    def extractions_payload(self) -> dict[str, Any]:
        """The contract-v4 extraction review queue (read-only).

        The queue lives in the extraction package's sidecar database; a store
        whose sidecar was never created reports an empty queue, and this view
        never writes (a missing sidecar is not created here).
        """
        from know_your_context.extraction import ExtractionQueue, sidecar_path

        path = sidecar_path(self._store.db_path)
        if self._store.db_path == ":memory:" or not Path(path).is_file():
            return {"schema_version": SCHEMA_VERSION, "count": 0, "queue_size": 0, "rows": []}
        queue = ExtractionQueue(path)
        try:
            rows = queue.list()
            return {
                "schema_version": SCHEMA_VERSION,
                "count": len(rows),
                "queue_size": queue.count(),
                "rows": [
                    {
                        "receipt_id": row.receipt_id,
                        "status": row.status,
                        "actor": row.actor,
                        "source_sha256": row.source_sha256,
                        "nodes": len(row.payload.get("nodes", [])),
                        "edges": len(row.payload.get("edges", [])),
                        "confidence_reported": row.confidence_reported,
                        "confidence_effective": row.confidence_effective,
                        "corroborators": row.corroborators,
                        "contradictions": row.contradictions,
                        "agent": str(row.method.get("agent", "")),
                        "decided_by": row.decided_by,
                    }
                    for row in rows
                ],
            }
        finally:
            queue.close()

    # ------------------------------------------------------- W13 JSON API

    def query_payload(self, node_id: str, *, kind: str | None, direction: str, limit: int) -> dict[str, Any]:
        """Graph neighborhood of one node: real ``node_relation`` rows plus
        the structural ``parent_of`` link, both directions, fail-closed bounds."""
        if direction not in _QUERY_DIRECTIONS:
            raise _BadRequest(f"direction must be one of {_QUERY_DIRECTIONS}, got {direction!r}")
        if not 1 <= limit <= QUERY_LIMIT_MAX:
            raise _BadRequest(f"limit must be in [1, {QUERY_LIMIT_MAX}], got {limit}")
        node = self._store.current_node(node_id)
        if node is None:
            raise _NodeNotFound(node_id)
        edges: list[dict[str, str]] = []
        if direction in ("out", "both"):
            with self._store.read() as conn:
                rows = conn.execute(
                    "SELECT target_node_id, relation FROM node_relation WHERE source_node_id = ? "
                    "ORDER BY relation_seq LIMIT ?",
                    (node_id, limit),
                ).fetchall()
            for row in rows:
                if kind is None or str(row["relation"]) == kind:
                    edges.append(
                        {"source": node_id, "target": str(row["target_node_id"]), "kind": str(row["relation"])}
                    )
        if direction in ("in", "both"):
            with self._store.read() as conn:
                rows = conn.execute(
                    "SELECT source_node_id, relation FROM node_relation WHERE target_node_id = ? "
                    "ORDER BY relation_seq LIMIT ?",
                    (node_id, limit),
                ).fetchall()
            for row in rows:
                if kind is None or str(row["relation"]) == kind:
                    edges.append(
                        {"source": str(row["source_node_id"]), "target": node_id, "kind": str(row["relation"])}
                    )
        if node.parent_id is not None and direction in ("in", "both") and (kind is None or kind == "parent_of"):
            edges.append({"source": node.parent_id, "target": node_id, "kind": "parent_of"})
        edges = edges[:limit]
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": node_id,
            "direction": direction,
            "kind": kind,
            "count": len(edges),
            "edges": edges,
        }

    def metrics_series_payload(self, *, limit: int) -> dict[str, Any]:
        """Time series: daily event-log buckets plus the hash-chained health
        history (when the operator records one). Absent history is reported
        honestly, never invented; a corrupt history is announced, not hidden."""
        if not 1 <= limit <= SERIES_LIMIT_MAX:
            raise _BadRequest(f"limit must be in [1, {SERIES_LIMIT_MAX}], got {limit}")
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT substr(occurred_at, 1, 10) AS day, COUNT(*) AS n FROM event_log "
                "GROUP BY day ORDER BY day DESC LIMIT ?",
                (limit,),
            ).fetchall()
        event_series = [{"day": str(row["day"]), "events": int(row["n"])} for row in reversed(rows)]
        health_series: list[dict[str, Any]] = []
        health_state = "absent"
        if str(self._store.db_path) != ":memory:":
            from know_your_context.ops.lifecycle import LifecycleError, history_path_for_store, load_health_history

            history = history_path_for_store(self._store)
            if Path(history).is_file():
                try:
                    snapshots = load_health_history(history)
                except LifecycleError as error:
                    health_state = f"corrupt: {error}"
                else:
                    health_state = "ok"
                    health_series = [
                        {
                            "recorded_at": snap.recorded_at,
                            "score": snap.score,
                            "max_score": snap.max_score,
                            "event_count": snap.event_count,
                        }
                        for snap in snapshots[-limit:]
                    ]
        return {
            "schema_version": SCHEMA_VERSION,
            "event_series": event_series,
            "health_series": health_series,
            "health_history": health_state,
        }

    def audit_tail_payload(self, *, since: int, limit: int) -> dict[str, Any]:
        """Audit events with ``event_seq > since``, ascending — tail semantics
        for polling consumers (the paged ``/api/audit`` view serves DESC)."""
        if since < 0:
            raise _BadRequest(f"since must be >= 0, got {since}")
        if not 1 <= limit <= AUDIT_TAIL_LIMIT_MAX:
            raise _BadRequest(f"limit must be in [1, {AUDIT_TAIL_LIMIT_MAX}], got {limit}")
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT event_seq, event_type, actor, occurred_at, payload_json FROM event_log "
                "WHERE kind = 'audit' AND event_seq > ? ORDER BY event_seq ASC LIMIT ?",
                (since, limit),
            ).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                payload = None
            events.append(
                {
                    "event_seq": int(row["event_seq"]),
                    "event_type": str(row["event_type"]),
                    "actor": str(row["actor"]),
                    "occurred_at": str(row["occurred_at"]),
                    "payload": redact_value(payload),
                }
            )
        return {"schema_version": SCHEMA_VERSION, "since": since, "count": len(events), "events": events}

    def _improvement_events(self) -> list[dict[str, Any]]:
        """Domain events carrying improvement markers (proposal/apply/rollback)."""
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT event_seq, event_type, actor, occurred_at, payload_json FROM event_log "
                "WHERE kind = 'domain' AND event_type IN "
                "('knowledge.propose_improvement', 'knowledge.apply_improvement', 'knowledge.rollback') "
                "ORDER BY event_seq"
            ).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue  # an unreadable payload is skipped, never guessed
            inner = payload.get("payload") or {}
            marker = (
                inner.get("improvement_proposal") or inner.get("improvement_apply") or inner.get("improvement_rollback")
            )
            if not isinstance(marker, dict):
                continue
            events.append(
                {
                    "event_seq": int(row["event_seq"]),
                    "event_type": str(row["event_type"]),
                    "actor": str(row["actor"]),
                    "occurred_at": str(row["occurred_at"]),
                    "marker": redact_value(marker),
                }
            )
        return events

    def improvements_payload(self) -> dict[str, Any]:
        """List improvement proposals with their derived lifecycle status."""
        proposals: dict[str, dict[str, Any]] = {}
        for event in self._improvement_events():
            marker = event["marker"]
            if event["event_type"] == "knowledge.propose_improvement":
                proposal_id = str(marker.get("proposal_id", ""))
                if proposal_id:
                    proposals[proposal_id] = {
                        "proposal_id": proposal_id,
                        "node_id": marker.get("node_id"),
                        "impact": marker.get("impact"),
                        "proposed_at": event["occurred_at"],
                        "status": "proposed",
                    }
            elif event["event_type"] == "knowledge.apply_improvement":
                proposal_id = str(marker.get("proposal_id", ""))
                if proposal_id in proposals:
                    proposals[proposal_id]["status"] = "applied"
                    proposals[proposal_id]["applied_at"] = event["occurred_at"]
        items = [proposals[key] for key in sorted(proposals)]
        return {"schema_version": SCHEMA_VERSION, "count": len(items), "proposals": items}

    def improvement_detail_payload(self, proposal_id: str) -> dict[str, Any]:
        """One proposal with its full event trail."""
        if _PROPOSAL_ID_RE.fullmatch(proposal_id) is None:
            raise _BadRequest(f"proposal id must match {_PROPOSAL_ID_RE.pattern}, got {proposal_id!r}")
        proposal: dict[str, Any] | None = None
        trail: list[dict[str, Any]] = []
        status = "proposed"
        for event in self._improvement_events():
            marker = event["marker"]
            if event["event_type"] == "knowledge.propose_improvement" and marker.get("proposal_id") == proposal_id:
                proposal = marker
                trail.append(
                    {
                        "event_seq": event["event_seq"],
                        "event_type": event["event_type"],
                        "occurred_at": event["occurred_at"],
                        "actor": event["actor"],
                    }
                )
            elif event["event_type"] == "knowledge.apply_improvement" and marker.get("proposal_id") == proposal_id:
                status = "applied"
                trail.append(
                    {
                        "event_seq": event["event_seq"],
                        "event_type": event["event_type"],
                        "occurred_at": event["occurred_at"],
                        "actor": event["actor"],
                    }
                )
        if proposal is None:
            raise _NodeNotFound(proposal_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "proposal": proposal,
            "status": status,
            "events": trail,
        }

    def store_stats_payload(self) -> dict[str, Any]:
        """Real store statistics: per-table row counts, pragmas, schema ledger."""
        tables: dict[str, int] = {}
        with self._store.read() as conn:
            names = [
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
            ]
            for name in names:
                tables[name] = int(conn.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()["n"])  # nosec B608 -- name from sqlite_master
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            versions = [
                int(row["version"]) for row in conn.execute("SELECT version FROM schema_versions ORDER BY version")
            ]
        return {
            "schema_version": SCHEMA_VERSION,
            "store_schema_version": max(versions) if versions else 0,
            "schema_versions": versions,
            "tables": tables,
            "page_count": page_count,
            "page_size_bytes": page_size,
            "file_size_bytes": page_count * page_size,
            "freelist_pages": freelist,
            "journal_mode": journal_mode,
        }

    def bench_payload(self) -> dict[str, Any]:
        """Per-operation latency summary from the invocation journal (real
        measured durations; integer-only statistics via ``latency_summary``)."""
        durations: dict[str, list[int]] = {}
        malformed = 0
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT operation, outcome_json FROM invocation_journal WHERE status = 'complete' "
                "ORDER BY invocation_id"
            ).fetchall()
        for row in rows:
            try:
                outcome = json.loads(str(row["outcome_json"]))
            except json.JSONDecodeError, TypeError:
                malformed += 1
                continue
            duration = outcome.get("duration_ms") if isinstance(outcome, dict) else None
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                malformed += 1
                continue
            durations.setdefault(str(row["operation"]), []).append(duration)
        operations = [
            {
                "operation": operation,
                "count": summary["count"],
                "min_ms": summary["min"],
                "max_ms": summary["max"],
                "mean_ms": summary["mean"],
                "p50_ms": summary["p50"],
                "p95_ms": summary["p95"],
            }
            for operation in sorted(durations)
            for summary in (latency_summary(durations[operation]),)
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "operations": operations,
            "malformed_outcomes": malformed,
        }

    def switches_payload(self) -> dict[str, Any]:
        """The resolved state of every operator kill switch (read at request
        time — the operator's latest env always wins)."""
        board = load_switches()
        return {
            "schema_version": SCHEMA_VERSION,
            "switches": [
                {
                    "name": entry.name,
                    "env": entry.env,
                    "state": str(board.state(entry.name)),
                    "affects": entry.affects,
                    "fail_safe": entry.fail_safe,
                }
                for entry in KILL_SWITCHES
            ],
            "announcements": list(board.announcements()),
        }

    def health_detail_payload(self, *, timing: Mapping[str, int]) -> dict[str, Any]:
        """Health findings plus store stats, request timing and switch states."""
        base = self.health_payload()
        stats = self.store_stats_payload()
        base["store"] = {
            "tables": len(stats["tables"]),
            "schema_versions": stats["schema_versions"],
            "file_size_bytes": stats["file_size_bytes"],
            "freelist_pages": stats["freelist_pages"],
        }
        base["timing"] = dict(timing)
        base["switches"] = self.switches_payload()["switches"]
        return base

    def refs_payload(self) -> dict[str, Any]:
        with self._store.read() as conn:
            counts = {
                str(row["status"]): int(row["n"])
                for row in conn.execute("SELECT status, COUNT(*) AS n FROM unresolved_refs GROUP BY status").fetchall()
            }
            top_failed = conn.execute(
                "SELECT name_tail, COUNT(*) AS n FROM unresolved_refs WHERE status = 'failed' "
                "GROUP BY name_tail ORDER BY n DESC, name_tail LIMIT 20"
            ).fetchall()
            samples = conn.execute(
                "SELECT source_node_id, target_name, name_tail, kind, failure_reason, attempts "
                "FROM unresolved_refs WHERE status = 'failed' ORDER BY ref_id LIMIT 20"
            ).fetchall()
        return {
            "schema_version": SCHEMA_VERSION,
            "pending": counts.get("pending", 0),
            "resolved": counts.get("resolved", 0),
            "failed": counts.get("failed", 0),
            "total": sum(counts.values()),
            "top_failed": [{"name_tail": str(row["name_tail"]), "count": int(row["n"])} for row in top_failed],
            "failed_samples": [
                {
                    "source_node_id": str(row["source_node_id"]),
                    "target_name": str(row["target_name"]),
                    "name_tail": str(row["name_tail"]),
                    "kind": str(row["kind"]),
                    "failure_reason": str(row["failure_reason"]) if row["failure_reason"] is not None else None,
                }
                for row in samples
            ],
        }


# --------------------------------------------------------------- http layer


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "retryable": False}}


class WebviewServer:
    """The loopback-only viewer server; one token per instance."""

    def __init__(
        self,
        store_path: str | Path,
        *,
        port: int = 0,
        token: str | None = None,
        host_allowlist: Sequence[str] = DEFAULT_HOST_ALLOWLIST,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        # W13 kill switch: the operator can forbid the viewer entirely.
        if load_switches().webview_disabled():
            raise WebviewError(f"the webview is disabled by {ENV_DISABLE_WEBVIEW}=1 (operator kill switch)")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise WebviewError(f"port must be an int in [0, 65535], got {port!r}")
        resolved_token = token if token is not None else generate_token()
        if not _valid_token(resolved_token):
            raise WebviewError(
                f"token must be URL-safe ([A-Za-z0-9_-]) and >= {_TOKEN_MIN_LENGTH} chars, got {resolved_token!r}"
            )
        self._app = WebviewApp(store_path)
        self._token = resolved_token
        self._host_allowlist = tuple(host_allowlist)
        self._request_id_factory = request_id_factory or (lambda: secrets.token_hex(8))
        # W13 middleware layer: timing + typed error mapping are server-level;
        # the capability gate binds per request (it needs the handler's
        # headers/query) inside the handler's guard chain.
        self._timings = TimingMiddleware()
        self._errors = TypedErrorMiddleware()
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None
        self._serving = False
        self._closed = False

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def token(self) -> str:
        return self._token

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self._token}"

    def start(self) -> None:
        """Serve in a daemon thread (returns immediately)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="kys-webview", daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self._serving = True
        try:
            self._httpd.serve_forever()
        finally:
            self._serving = False

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        # BaseServer.shutdown() blocks forever unless serve_forever() is
        # actually looping, so only signal it when serving (started in a
        # thread or foreground); an unstarted server just closes.
        if self._thread is not None or self._serving:
            self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._app.close()

    # ---------------------------------------------------------- handler

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "KysWebview/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
                return  # quiet by design: the terminal belongs to the operator

            # -- plumbing ------------------------------------------------

            def _respond(self, status: int, body: bytes, content_type: str, *, head_only: bool = False) -> None:
                self._last_status = status
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                if content_type.startswith("text/html"):
                    self.send_header("Content-Security-Policy", _CSP)
                ctx = getattr(self, "_ctx", None)
                if ctx is not None:
                    server_ref._timings.stamp(ctx)  # Server-Timing: duration so far
                    for header, value in ctx.response_headers.items():
                        self.send_header(header, value)
                if getattr(self, "_fresh_token", False) and content_type.startswith("text/html"):
                    # Page authenticated via ?token= — issue the session cookie
                    # so static assets and API calls authenticate without the
                    # token in the URL. HttpOnly + SameSite=Strict, loopback.
                    self.send_header(
                        "Set-Cookie",
                        f"kys_token={server_ref._token}; Path=/; HttpOnly; SameSite=Strict",
                    )
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)

            def _respond_json(self, status: int, payload: Any, *, head_only: bool = False) -> None:
                self._respond(
                    status, escape_json_bytes(payload), "application/json; charset=utf-8", head_only=head_only
                )

            def _reject(self, status: int, code: str, message: str) -> None:
                self._respond_json(status, _error_payload(code, message))

            # -- guards (order: host -> origin -> method -> token) -------

            def _token_authorized(self) -> bool:
                """The session-token capability decision (pure check + fresh mark)."""
                query = parse_qs(urlsplit(self.path).query)
                provided = query.get("token", [None])[-1]
                if provided is not None and hmac.compare_digest(str(provided), server_ref._token):
                    self._fresh_token = True  # mark: issue the session cookie
                    return True
                # Fall back to the session cookie issued when the page itself
                # was authenticated (static assets/API calls carry no query
                # token; the cookie is HttpOnly + SameSite=Strict).
                cookie_header = self.headers.get("Cookie", "")
                for part in cookie_header.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == "kys_token" and hmac.compare_digest(value, server_ref._token):
                        return True
                return False

            def _capability_gate(self) -> CapabilityGateMiddleware:
                """The token check as an explicit middleware layer (W13)."""
                return CapabilityGateMiddleware(checker=lambda _ctx: self._token_authorized())

            def _guards_pass(self) -> bool:
                host = self.headers.get("Host")
                if host is None or not host_allowed(host, server_ref.port, server_ref._host_allowlist):
                    self._respond_json(
                        403, _error_payload("HOST_NOT_ALLOWED", "Host header is not an allowlisted loopback origin")
                    )
                    return False
                origin = self.headers.get("Origin")
                if not origin_allowed(origin, server_ref.port):
                    self._respond_json(
                        403, _error_payload("ORIGIN_NOT_ALLOWED", "Origin header is not an allowlisted loopback origin")
                    )
                    return False
                try:
                    self._capability_gate().before(self._ctx)
                except CapabilityDeniedError as error:
                    self._respond_json(
                        error.status, _error_payload("UNAUTHORIZED", "a valid token query parameter is required")
                    )
                    return False
                return True

            # -- verbs ---------------------------------------------------

            def do_GET(self) -> None:
                self._dispatch(head_only=False)

            def do_HEAD(self) -> None:
                self._dispatch(head_only=True)

            def do_POST(self) -> None:
                self._method_not_allowed()

            def do_PUT(self) -> None:
                self._method_not_allowed()

            def do_DELETE(self) -> None:
                self._method_not_allowed()

            def do_PATCH(self) -> None:
                self._method_not_allowed()

            def do_OPTIONS(self) -> None:
                self._method_not_allowed()

            def _method_not_allowed(self) -> None:
                # Guards still run first: an unauthenticated probe must not
                # learn which methods exist.
                self._ensure_ctx()
                if self._guards_pass():
                    self._reject(405, "METHOD_NOT_ALLOWED", "the viewer is read-only; only GET and HEAD exist")

            def _ensure_ctx(self) -> None:
                """Build the per-request middleware context exactly once."""
                if getattr(self, "_ctx", None) is None:
                    self._ctx = RequestContext(
                        request_id=server_ref._request_id_factory(),
                        method=self.command,
                        path=urlsplit(self.path).path,
                        client=str(self.client_address[0]),
                        headers={key: value for key, value in self.headers.items()},
                    )
                    self._last_status = 200

            def _dispatch(self, *, head_only: bool) -> None:
                self._ensure_ctx()
                if not self._guards_pass():
                    return
                ctx = self._ctx
                stack = MiddlewareStack((RequestIdMiddleware(), server_ref._timings))
                stack.before(ctx)
                try:
                    self._route(head_only=head_only)
                except BrokenPipeError, ConnectionResetError:
                    pass  # client left; nothing to report
                except Exception as error:
                    # Typed error mapping (W13): typed errors keep their
                    # code/status; untyped ones become 500 INTERNAL with no
                    # internals leaked — never a bare connection drop.
                    status, payload = server_ref._errors.map_error(error)
                    self._respond_json(status, payload, head_only=head_only)
                finally:
                    stack.after(ctx, status=getattr(self, "_last_status", 200))

            # -- routing -------------------------------------------------

            def _route(self, *, head_only: bool) -> None:
                split = urlsplit(self.path)
                raw_path = split.path
                if "%" in raw_path:
                    raw_path = unquote(raw_path, errors="strict")
                params = parse_qs(split.query)
                if raw_path == "/":
                    self._serve_asset("index.html", head_only=head_only)
                    return
                if raw_path.startswith("/static/"):
                    name = raw_path[len("/static/") :]
                    if "/" in name or name not in _STATIC_ASSETS or name == "index.html":
                        self._reject(404, "NOT_FOUND", "unknown static asset")
                        return
                    self._serve_asset(name, head_only=head_only)
                    return
                if raw_path.startswith("/api/"):
                    self._route_api(raw_path[len("/api/") :], params, head_only=head_only)
                    return
                self._reject(404, "NOT_FOUND", "unknown path")

            def _serve_asset(self, name: str, *, head_only: bool) -> None:
                content_type, filename = _STATIC_ASSETS[name]
                try:
                    body = (_ASSETS_DIR / filename).read_bytes()
                except OSError:
                    self._reject(404, "NOT_FOUND", "asset missing from the installed package")
                    return
                self._respond(200, body, content_type, head_only=head_only)

            def _route_api(self, tail: str, params: Mapping[str, list[str]], *, head_only: bool) -> None:
                app = server_ref._app

                def _int_param(name: str, default: int) -> int:
                    raw = params.get(name, [None])[-1]
                    if raw is None:
                        return default
                    if not re.fullmatch(r"[0-9]+", raw):
                        raise _BadRequest(f"{name} must be a non-negative integer, got {raw!r}")
                    return int(raw)

                def _str_param(name: str) -> str | None:
                    raw = params.get(name, [None])[-1]
                    return raw if raw else None

                try:
                    if tail == "graph":
                        payload = app.graph_payload()
                    elif tail.startswith("node/"):
                        node_id = tail[len("node/") :]
                        if not node_id or "/" in node_id:
                            raise _BadRequest("node id must be a single non-empty path segment")
                        payload = app.node_payload(node_id)
                    elif tail == "search":
                        payload = app.search_payload(
                            params.get("q", [""])[-1],
                            kind=_str_param("kind"),
                            lang=_str_param("lang"),
                            limit=_int_param("limit", 10),
                        )
                    elif tail.startswith("hierarchy/"):
                        node_id = tail[len("hierarchy/") :]
                        if not node_id or "/" in node_id:
                            raise _BadRequest("node id must be a single non-empty path segment")
                        payload = app.hierarchy_payload(node_id, depth=_int_param("depth", 1))
                    elif tail == "metrics":
                        payload = app.metrics_payload()
                    elif tail == "health":
                        payload = app.health_payload()
                    elif tail == "analytics":
                        payload = app.analytics_payload()
                    elif tail == "audit":
                        payload = app.audit_payload(limit=_int_param("limit", 50))
                    elif tail == "proposals":
                        payload = app.proposals_payload()
                    elif tail == "extractions":
                        payload = app.extractions_payload()
                    elif tail == "refs":
                        payload = app.refs_payload()
                    # -- W13 JSON API routes -----------------------------
                    elif tail == "query":
                        node_id = _str_param("node_id")
                        if node_id is None:
                            raise _BadRequest("node_id is required")
                        kind = _str_param("kind")
                        if kind is not None and _KIND_LANG_RE.fullmatch(kind) is None:
                            raise _BadRequest(f"kind filter must match {_KIND_LANG_RE.pattern}, got {kind!r}")
                        payload = app.query_payload(
                            node_id,
                            kind=kind,
                            direction=_str_param("direction") or "both",
                            limit=_int_param("limit", 50),
                        )
                    elif tail == "metrics/series":
                        payload = app.metrics_series_payload(limit=_int_param("limit", 90))
                    elif tail == "audit/tail":
                        payload = app.audit_tail_payload(since=_int_param("since", 0), limit=_int_param("limit", 100))
                    elif tail == "improvements":
                        payload = app.improvements_payload()
                    elif tail.startswith("improvements/"):
                        proposal_id = tail[len("improvements/") :]
                        if not proposal_id or "/" in proposal_id:
                            raise _BadRequest("proposal id must be a single non-empty path segment")
                        payload = app.improvement_detail_payload(proposal_id)
                    elif tail == "health/detail":
                        payload = app.health_detail_payload(timing=server_ref._timings.summary())
                    elif tail == "bench":
                        payload = app.bench_payload()
                    elif tail == "store/stats":
                        payload = app.store_stats_payload()
                    elif tail == "switches":
                        payload = app.switches_payload()
                    else:
                        self._reject(404, "NOT_FOUND", "unknown api endpoint")
                        return
                except _NodeNotFound:
                    self._reject(404, "NOT_FOUND", "unknown node id")
                    return
                except _BadRequest as error:
                    self._reject(400, "BAD_REQUEST", str(error))
                    return
                self._respond_json(200, payload, head_only=head_only)

        return _Handler


# ------------------------------------------------------------------ cli


# ------------------------------------------------------------ async lifecycle


class AsyncWebviewServer:
    """asyncio lifecycle wrapper around :class:`WebviewServer` (W13).

    The serving stack stays the hardened stdlib ``ThreadingHTTPServer`` —
    what the sync server lacked is lifecycle: this wrapper runs the serve
    loop as a heartbeat-beating
    :class:`~know_your_context.serve.watchdog.SupervisedWorker` (the loop
    beats once per ``handle_request`` poll, idle included), optionally
    registered with a :class:`~know_your_context.serve.watchdog.Watchdog`,
    and drains with a bounded async ``stop()``. Cancellation of ``stop()``
    propagates; the wrapper never swallows ``CancelledError``.
    """

    def __init__(
        self,
        server: WebviewServer,
        *,
        watchdog: Any = None,
        watchdog_policy: Any = None,
        heartbeat_poll_seconds: float = 0.5,
    ) -> None:
        from know_your_context.serve.watchdog import WatchdogPolicy

        if (
            isinstance(heartbeat_poll_seconds, bool)
            or not isinstance(heartbeat_poll_seconds, (int, float))
            or heartbeat_poll_seconds <= 0
        ):
            raise WebviewError(f"heartbeat_poll_seconds must be a number > 0, got {heartbeat_poll_seconds!r}")
        self._server = server
        self._watchdog = watchdog
        self._policy = watchdog_policy if watchdog_policy is not None else WatchdogPolicy()
        self._poll = float(heartbeat_poll_seconds)
        self._worker: Any = None

    @property
    def worker(self) -> Any:
        return self._worker

    @property
    def heartbeat(self) -> Any:
        return self._worker.heartbeat if self._worker is not None else None

    def _serve_loop(self, heartbeat: Any, stop: threading.Event) -> None:
        httpd = self._server._httpd
        httpd.timeout = self._poll  # handle_request returns after each poll
        while not stop.is_set():
            try:
                httpd.handle_request()
            except OSError:
                if stop.is_set():
                    break
                raise  # socket died underneath us: the supervisor must see it
            heartbeat.beat()

    async def start(self) -> None:
        from know_your_context.serve.watchdog import SupervisedWorker

        assert self._worker is None, "async webview server already started"
        worker = SupervisedWorker("webview-serve", self._serve_loop, policy=self._policy)
        worker.start()
        self._worker = worker
        if self._watchdog is not None:
            self._watchdog.register(worker)
        for _ in range(200):  # bound the "is it accepting" wait
            if worker.is_alive():
                break
            await asyncio.sleep(0.005)

    async def stop(self, *, timeout_ms: int = 5000) -> bool:
        """Bounded cooperative drain; True when the serve loop joined."""
        from know_your_context.serve.flow_retry import run_with_timeout_async

        worker = self._worker
        if worker is None:
            return True
        try:
            drained: bool = await run_with_timeout_async(
                asyncio.to_thread(worker.stop, timeout_ms=timeout_ms),
                timeout_ms=timeout_ms + 1000,
            )
        finally:
            self._worker = None
            if self._watchdog is not None:
                self._watchdog.unregister(worker.name)
        # The sync shutdown() path only signals httpd.shutdown() when
        # serve_forever() ran — our loop uses handle_request(), so close
        # directly and mark the server closed to keep shutdown() idempotent.
        if not self._server._closed:
            self._server._closed = True
            self._server._httpd.server_close()
            self._server._app.close()
        return drained


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m know_your_context.serve.webview`` entry point."""
    parser = argparse.ArgumentParser(
        prog="webview",
        description="loopback-only, token-authenticated, read-only web viewer for the knowledge store",
    )
    parser.add_argument("--store", required=True, help="path to the store SQLite file (must exist)")
    parser.add_argument("--port", type=int, default=0, help="loopback port; 0 picks a free one (default)")
    parser.add_argument("--token", default=None, help="explicit session token (default: generated per start)")
    parser.add_argument("--open", dest="open_browser", action="store_true", help="open the URL in a browser")
    parser.add_argument(
        "--serve-once",
        action="store_true",
        help="print the URL and exit immediately (smoke/CI mode; the server is shut down again)",
    )
    args = parser.parse_args(argv)
    try:
        server = WebviewServer(args.store, port=int(args.port), token=args.token)
    except WebviewError as error:
        print(f"webview: {error}", file=sys.stderr)
        return 2
    print(f"webview: {server.url}", flush=True)
    if args.open_browser:
        webbrowser.open(server.url)
    if args.serve_once:
        server.shutdown()
        return 0
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
