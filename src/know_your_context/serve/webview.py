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
* ``/api/nodes`` / ``/api/edges`` / ``/api/graph/edges`` — paged list reads
  over live nodes, ``node_relation`` rows and ``graph_edges`` rows.
* ``/api/node/<id>`` — node detail: P/M/E scores, provenance, relations.
* ``/api/node/<id>/edges|evidence|revisions`` — per-node sub-resources.
* ``/api/search?q=&kind=&lang=&limit=`` — tiered symbol search (retrieval_v2).
* ``/api/search/symbols?q=`` — symbol-name prefix search (wildcards literal).
* ``/api/symbols`` / ``/api/symbols/<id>`` — paged symbol rows / one record.
* ``/api/hierarchy/<id>?depth=`` — callers + callees (retrieval_v2).
* ``/api/provenance/<id>`` — the five-link provenance verdict (read-only).
* ``/api/refs`` + ``/api/refs/pending|failed|resolved`` — ref stats + lists.
* ``/api/metrics`` — :func:`ops.metrics.collect_ops_metrics` (redacted).
* ``/api/metrics/series`` / ``/api/metrics/buckets`` / ``/api/metrics/operations``
  — time series per bucket and per-operation journal counts.
* ``/api/health`` / ``/api/health/detail`` / ``/api/health/findings`` /
  ``/api/health/checks`` — :func:`ops.diagnose.run_diagnostics` breakdowns.
* ``/api/analytics`` (+ ``/centrality`` / ``/communities`` / ``/bridges``).
* ``/api/audit`` / ``/api/audit/tail`` / ``/api/audit/verify`` — paged and
  tail reads plus the event-log hash-chain verification.
* ``/api/proposals`` / ``/api/improvements`` (+ ``/<id>``).
* ``/api/extractions`` (+ ``/summary`` and ``/<receipt_id>``) — contract-v4
  review queue, read-only.
* ``/api/debt`` / ``/api/debt/report`` — the knowledge debt queue plus the
  burn-down ledger metrics.
* ``/api/ledger/health|debt|experience`` — hash-chained ledger tails; an
  absent sidecar is reported honestly, never created.
* ``/api/query`` — graph neighborhood query around one node.
* ``/api/bench`` / ``/api/bench/<operation>`` — latency summaries + samples.
* ``/api/store/stats|tables|tables/<name>|schema`` — store inspection.
* ``/api/switches`` (+ ``/<name>``) — kill-switch states.
* ``/api/config`` (+ ``/<name>``) — the resolved W15 config knobs.
* ``/api/export/subgraph`` / ``/api/export/node/<id>`` — in-memory export
  payloads (artifact manifests, never a disk write).
* ``/api/routes`` — the route table itself (discovery).

W15 consistency: every error uses the one typed envelope
(``{"error": {code, message, retryable}}``); routes are rate-limited per
class (read/heavy/export, token bucket, knob-driven); feature-flag knobs
gate the export/config/debt/provenance/ledger groups fail-closed.

Determinism: every payload is a pure function of store state (sorted keys,
canonical orderings inherited from the underlying modules), so two requests
against an unchanged store answer byte-identically.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import re
import secrets
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
from know_your_context.ops.switches import (
    ENV_DISABLE_WEBVIEW,
    KILL_SWITCHES,
    KnobBoard,
    load_knobs,
    load_switches,
)
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
    "RateLimiter",
    "RateLimitedError",
    "StoreUnavailableError",
    "WebviewApp",
    "WebviewError",
    "WebviewServer",
    "escape_json_bytes",
    "generate_token",
    "host_allowed",
    "main",
    "origin_allowed",
    "route_class_for",
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
_DOTTED_NAME_RE = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+){0,3}")
_TABLE_NAME_RE = re.compile(r"[a-z0-9_]{1,64}")
_QUERY_DIRECTIONS = ("out", "in", "both")
#: Route classes for rate limiting (W15): ``read`` is the default cheap JSON
#: read, ``heavy`` runs store-wide computation, ``export`` builds artifacts.
ROUTE_CLASSES: tuple[str, ...] = ("read", "heavy", "export")

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


class _ExportTooLarge(Exception):
    """A built export artifact exceeded its byte cap (mapped to 413)."""


class _UnknownRoute(Exception):
    """No route matched the tail (mapped to 404)."""


# --------------------------------------------------------------- rate limiting


class RateLimitedError(Exception):
    """A route class exceeded its token bucket (mapped to 429, retryable)."""

    code = "RATE_LIMITED"
    status = 429

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class RateLimiter:
    """Per-route-class token buckets over an injectable clock (W15).

    Process-local and in-memory only (the viewer is a single-operator
    loopback tool); classes not configured fail closed by denying.
    """

    def __init__(self, rates: Mapping[str, tuple[float, int]], *, clock: Callable[[], float] = time.monotonic) -> None:
        for name, (rate, burst) in rates.items():
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
                raise ValueError(f"rate for class {name!r} must be a positive finite number, got {rate!r}")
            if isinstance(burst, bool) or not isinstance(burst, int) or burst < 1:
                raise ValueError(f"burst for class {name!r} must be an int >= 1, got {burst!r}")
        self._rates = {name: (float(rate), int(burst)) for name, (rate, burst) in rates.items()}
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def check(self, route_class: str) -> None:
        """Consume one token for *route_class* or raise :class:`RateLimitedError`."""
        spec = self._rates.get(route_class)
        if spec is None:
            raise RateLimitedError(f"unknown route class {route_class!r} is denied by default", retry_after_seconds=1)
        rate, burst = spec
        now = self._clock()
        tokens, updated = self._buckets.get(route_class, (float(burst), now))
        tokens = min(float(burst), tokens + max(0.0, now - updated) * rate)
        if tokens < 1.0:
            self._buckets[route_class] = (tokens, now)
            raise RateLimitedError(
                f"route class {route_class!r} exceeded its rate limit; retry later",
                retry_after_seconds=max(1, math.ceil((1.0 - tokens) / rate)),
            )
        self._buckets[route_class] = (tokens - 1.0, now)


def route_class_for(tail: str) -> str:
    """Classify one ``/api/`` tail into its rate-limit route class."""
    if tail.startswith("export/"):
        return "export"
    if tail == "graph" or tail == "audit/verify" or tail.startswith(("analytics", "proposals", "debt", "health")):
        return "heavy"
    return "read"


def _route_group_flag(tail: str) -> str | None:
    """The feature-flag knob gating *tail*'s route group, if any (W15)."""
    if tail.startswith("export/"):
        return "webview.routes.export_enabled"
    if tail == "config" or tail.startswith("config/"):
        return "webview.routes.config_enabled"
    if tail == "debt/report":
        return "webview.routes.debt_enabled"
    if tail.startswith("provenance/"):
        return "webview.routes.provenance_enabled"
    if tail.startswith("ledger/") or tail == "audit/verify":
        return "webview.routes.ledger_enabled"
    return None


# ------------------------------------------------------------------ routes


@dataclass(frozen=True)
class ApiRoute:
    """One JSON API route of the viewer (all read-only GET, authenticated).

    ``route_class`` feeds the per-class rate limiter: ``read`` (cheap JSON
    reads), ``heavy`` (store-wide computation), ``export`` (artifact builds).
    """

    path: str
    method: str
    access: RouteAccess
    summary: str
    route_class: str = "read"

    def __post_init__(self) -> None:
        if not self.path.startswith("/api/"):
            raise WebviewError(f"api route path must start with /api/, got {self.path!r}")
        if self.method != "GET":
            raise WebviewError(f"the viewer is read-only; route {self.path!r} declares {self.method!r}")
        if self.route_class not in ROUTE_CLASSES:
            raise WebviewError(f"route {self.path!r} declares unknown class {self.route_class!r}")


#: The complete route table (W15): the original tab payloads plus the real
#: JSON API routes — paged per-table reads, node sub-resources, provenance,
#: refs by status, queue detail, ledger tails, audit-chain verification,
#: per-op bench, debt report, switch/config introspection, health breakdown,
#: metrics buckets, store inspection, analytics slices, in-memory exports.
API_ROUTES: tuple[ApiRoute, ...] = (
    ApiRoute("/api/graph", "GET", RouteAccess.AUTHENTICATED, "laid-out graph payload for the viewer", "heavy"),
    ApiRoute("/api/node/{id}", "GET", RouteAccess.AUTHENTICATED, "one node's detail, relations and evidence"),
    ApiRoute("/api/node/{id}/edges", "GET", RouteAccess.AUTHENTICATED, "incident edges of one node, both directions"),
    ApiRoute("/api/node/{id}/evidence", "GET", RouteAccess.AUTHENTICATED, "evidence rows attached to one node"),
    ApiRoute("/api/node/{id}/revisions", "GET", RouteAccess.AUTHENTICATED, "full revision history of one node"),
    ApiRoute("/api/nodes", "GET", RouteAccess.AUTHENTICATED, "paged node list with kind/domain filters"),
    ApiRoute("/api/edges", "GET", RouteAccess.AUTHENTICATED, "paged node_relation rows with relation filter"),
    ApiRoute("/api/graph/edges", "GET", RouteAccess.AUTHENTICATED, "paged code-graph edges (graph_edges table)"),
    ApiRoute("/api/search", "GET", RouteAccess.AUTHENTICATED, "symbol search over the universal graph"),
    ApiRoute("/api/search/symbols", "GET", RouteAccess.AUTHENTICATED, "symbol-name prefix search (autocomplete)"),
    ApiRoute("/api/symbols", "GET", RouteAccess.AUTHENTICATED, "paged symbol rows with kind/path filters"),
    ApiRoute("/api/symbols/{id}", "GET", RouteAccess.AUTHENTICATED, "the symbol record of one node"),
    ApiRoute("/api/hierarchy/{id}", "GET", RouteAccess.AUTHENTICATED, "call hierarchy around one node"),
    ApiRoute("/api/provenance/{id}", "GET", RouteAccess.AUTHENTICATED, "five-link provenance verdict for one node"),
    ApiRoute("/api/refs", "GET", RouteAccess.AUTHENTICATED, "unresolved-reference statistics"),
    ApiRoute("/api/refs/pending", "GET", RouteAccess.AUTHENTICATED, "paged pending unresolved references"),
    ApiRoute("/api/refs/failed", "GET", RouteAccess.AUTHENTICATED, "paged failed unresolved references with reasons"),
    ApiRoute("/api/refs/resolved", "GET", RouteAccess.AUTHENTICATED, "paged resolved references"),
    ApiRoute("/api/metrics", "GET", RouteAccess.AUTHENTICATED, "operational metrics snapshot"),
    ApiRoute("/api/metrics/series", "GET", RouteAccess.AUTHENTICATED, "daily event buckets + health history series"),
    ApiRoute("/api/metrics/buckets", "GET", RouteAccess.AUTHENTICATED, "event-log counts per day/kind/actor bucket"),
    ApiRoute("/api/metrics/operations", "GET", RouteAccess.AUTHENTICATED, "invocation journal counts per operation"),
    ApiRoute("/api/health", "GET", RouteAccess.AUTHENTICATED, "diagnostic findings summary", "heavy"),
    ApiRoute(
        "/api/health/detail", "GET", RouteAccess.AUTHENTICATED, "health + store stats + timing + switches", "heavy"
    ),
    ApiRoute(
        "/api/health/findings", "GET", RouteAccess.AUTHENTICATED, "diagnostic findings filtered by severity", "heavy"
    ),
    ApiRoute(
        "/api/health/checks", "GET", RouteAccess.AUTHENTICATED, "which diagnostics checks ran + finding totals", "heavy"
    ),
    ApiRoute("/api/analytics", "GET", RouteAccess.AUTHENTICATED, "centrality/communities/bridges", "heavy"),
    ApiRoute("/api/analytics/centrality", "GET", RouteAccess.AUTHENTICATED, "centrality ranking only", "heavy"),
    ApiRoute("/api/analytics/communities", "GET", RouteAccess.AUTHENTICATED, "community detection only", "heavy"),
    ApiRoute("/api/analytics/bridges", "GET", RouteAccess.AUTHENTICATED, "bridge detection only", "heavy"),
    ApiRoute("/api/audit", "GET", RouteAccess.AUTHENTICATED, "recent audit events (paged)"),
    ApiRoute("/api/audit/tail", "GET", RouteAccess.AUTHENTICATED, "audit events after a sequence number, ascending"),
    ApiRoute(
        "/api/audit/verify", "GET", RouteAccess.AUTHENTICATED, "verify the event-log hash chain end-to-end", "heavy"
    ),
    ApiRoute("/api/proposals", "GET", RouteAccess.AUTHENTICATED, "review signals derived from the graph", "heavy"),
    ApiRoute("/api/improvements", "GET", RouteAccess.AUTHENTICATED, "improvement proposals from the event log"),
    ApiRoute("/api/improvements/{id}", "GET", RouteAccess.AUTHENTICATED, "one improvement proposal with lifecycle"),
    ApiRoute("/api/extractions", "GET", RouteAccess.AUTHENTICATED, "extraction review queue"),
    ApiRoute("/api/extractions/summary", "GET", RouteAccess.AUTHENTICATED, "extraction queue counts by status"),
    ApiRoute("/api/extractions/{receipt_id}", "GET", RouteAccess.AUTHENTICATED, "one extraction queue row in detail"),
    ApiRoute("/api/debt", "GET", RouteAccess.AUTHENTICATED, "knowledge debt queue (budgeted, prioritized)", "heavy"),
    ApiRoute(
        "/api/debt/report", "GET", RouteAccess.AUTHENTICATED, "debt queue plus burn-down from the ledger", "heavy"
    ),
    ApiRoute("/api/ledger/health", "GET", RouteAccess.AUTHENTICATED, "hash-chained health history tail"),
    ApiRoute("/api/ledger/debt", "GET", RouteAccess.AUTHENTICATED, "hash-chained debt burn-down ledger tail"),
    ApiRoute(
        "/api/ledger/experience", "GET", RouteAccess.AUTHENTICATED, "experience rerank journal tail + verification"
    ),
    ApiRoute("/api/query", "GET", RouteAccess.AUTHENTICATED, "graph neighborhood query around one node"),
    ApiRoute("/api/bench", "GET", RouteAccess.AUTHENTICATED, "per-operation latency summary from the journal"),
    ApiRoute("/api/bench/{operation}", "GET", RouteAccess.AUTHENTICATED, "one operation's latency detail + samples"),
    ApiRoute("/api/store/stats", "GET", RouteAccess.AUTHENTICATED, "table counts, pragmas and schema version"),
    ApiRoute("/api/store/tables", "GET", RouteAccess.AUTHENTICATED, "every table with its row count"),
    ApiRoute("/api/store/tables/{name}", "GET", RouteAccess.AUTHENTICATED, "one table's columns and row count"),
    ApiRoute("/api/store/schema", "GET", RouteAccess.AUTHENTICATED, "applied schema versions + migration registry"),
    ApiRoute("/api/switches", "GET", RouteAccess.AUTHENTICATED, "resolved state of every operator kill switch"),
    ApiRoute(
        "/api/codeintel",
        "GET",
        RouteAccess.AUTHENTICATED,
        "deep code-intelligence ops: callers|callees|path|impact|co-change|boundary",
    ),
    ApiRoute("/api/switches/{name}", "GET", RouteAccess.AUTHENTICATED, "one kill switch with its resolved state"),
    ApiRoute("/api/config", "GET", RouteAccess.AUTHENTICATED, "resolved value + provenance of every config knob"),
    ApiRoute("/api/config/{name}", "GET", RouteAccess.AUTHENTICATED, "one config knob in detail"),
    ApiRoute(
        "/api/export/subgraph",
        "GET",
        RouteAccess.AUTHENTICATED,
        "in-memory subgraph export (ego-net or domain)",
        "export",
    ),
    ApiRoute("/api/export/node/{id}", "GET", RouteAccess.AUTHENTICATED, "full single-node export bundle", "export"),
    ApiRoute("/api/routes", "GET", RouteAccess.AUTHENTICATED, "this route table (discovery)"),
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


def _like_escape(text: str) -> str:
    """Escape LIKE wildcards so user input matches literally (ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --------------------------------------------------------------- the app


class WebviewApp:
    """Read-only application logic over one store; shared by HTTP and tests.

    ``knobs`` (W15) carries the resolved operator configuration; every cap,
    default and rate limit below reads from it. ``None`` resolves the process
    environment once, strictly (fail-closed on invalid values).
    """

    def __init__(self, store_path: str | Path, *, knobs: KnobBoard | None = None) -> None:
        path = Path(store_path)
        if not path.is_file():
            raise StoreUnavailableError(f"store not found or not a file: {path}")
        self._store_path = path
        self._store = EventStore(path)
        self._closed = False
        self._knobs = knobs if knobs is not None else load_knobs()
        self._lookup = SymbolLookup()
        self._hierarchy = CallHierarchy()

    @property
    def knobs(self) -> KnobBoard:
        return self._knobs

    def _knob_int(self, name: str) -> int:
        return int(self._knobs.value(name))

    def _knob_float(self, name: str) -> float:
        return float(self._knobs.value(name))

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
        nodes, links, total, truncated = self._read_model(cap=self._knob_int("webview.max_graph_nodes"))
        frame_width = self._knob_float("webview.frame_width")
        frame_height = self._knob_float("webview.frame_height")
        layout_input = {
            "nodes": [{"id": node["id"]} for node in nodes],
            "links": [{"source": link["source"], "target": link["target"]} for link in links],
        }
        try:
            result = compute_layout(layout_input, width=frame_width, height=frame_height)
            positions = result.positions
            layout_meta = dict(result.metadata)
        except LayoutError:
            positions = {}
            layout_meta = {"mode": "none", "node_count": len(nodes), "quantum": 0.001}
        out_nodes = []
        for node in nodes:
            x, y = positions.get(node["id"], (frame_width / 2.0, frame_height / 2.0))
            entry = {key: value for key, value in node.items() if key != "parent_id"}
            entry["x"], entry["y"] = float(x), float(y)
            out_nodes.append(entry)
        return {
            "schema_version": SCHEMA_VERSION,
            "nodes": out_nodes,
            "edges": [{"source": link["source"], "target": link["target"], "kind": link["relation"]} for link in links],
            "layout": {**layout_meta, "width": frame_width, "height": frame_height},
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
        search_max = self._knob_int("webview.search_limit_max")
        if not query.strip():
            raise _BadRequest("q must be a non-empty search string")
        for label, value in (("kind", kind), ("lang", lang)):
            if value is not None and _KIND_LANG_RE.fullmatch(value) is None:
                raise _BadRequest(f"{label} filter must match {_KIND_LANG_RE.pattern}, got {value!r}")
        if not 1 <= limit <= search_max:
            raise _BadRequest(f"limit must be in [1, {search_max}], got {limit}")
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
        depth_max = self._knob_int("webview.hierarchy_depth_max")
        if not HIERARCHY_DEPTH_MIN <= depth <= depth_max:
            raise _BadRequest(f"depth must be in [{HIERARCHY_DEPTH_MIN}, {depth_max}], got {depth}")
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
        nodes, links, total, truncated = self._read_model(cap=self._knob_int("webview.analytics_max_nodes"))
        top_ranking = self._knob_int("webview.top_ranking")
        graph = {
            "nodes": [
                {"id": node["id"], "domain": node["domain"], "label": node["label"], "version": node["version"]}
                for node in nodes
            ],
            "links": links,
        }
        centrality = compute_centrality(graph, top=top_ranking) if nodes else {"global": {}}
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
        audit_max = self._knob_int("webview.audit_limit_max")
        if not 1 <= limit <= audit_max:
            raise _BadRequest(f"limit must be in [1, {audit_max}], got {limit}")
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
        nodes, links, _, _ = self._read_model(cap=self._knob_int("webview.analytics_max_nodes"))
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

    def debt_payload(self) -> dict[str, Any]:
        """The knowledge debt queue (I4): budgeted, prioritized, read-only.

        Mirrors ``know-your-context debt list``: the pure detector in
        :mod:`know_your_context.analytics.debt` runs over the read-only
        adapter in :mod:`know_your_context.ops.debt`, honouring the same
        ``KYS_DEBT_QUEUE`` kill switch. ``generated_at`` is the wall clock;
        everything else is deterministic.
        """
        from know_your_context.analytics.debt import build_debt_queue
        from know_your_context.ops.debt import load_debt_inputs

        nodes, links, refs = load_debt_inputs(self._store)
        queue = build_debt_queue(nodes, links, now=datetime.now(UTC), unresolved_refs=refs)
        queue["count"] = len(queue["items"])
        return queue

    # ------------------------------------------------------- W13 JSON API

    def query_payload(self, node_id: str, *, kind: str | None, direction: str, limit: int) -> dict[str, Any]:
        """Graph neighborhood of one node: real ``node_relation`` rows plus
        the structural ``parent_of`` link, both directions, fail-closed bounds."""
        if direction not in _QUERY_DIRECTIONS:
            raise _BadRequest(f"direction must be one of {_QUERY_DIRECTIONS}, got {direction!r}")
        query_max = self._knob_int("webview.query_limit_max")
        if not 1 <= limit <= query_max:
            raise _BadRequest(f"limit must be in [1, {query_max}], got {limit}")
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
        series_max = self._knob_int("webview.series_limit_max")
        if not 1 <= limit <= series_max:
            raise _BadRequest(f"limit must be in [1, {series_max}], got {limit}")
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
        tail_max = self._knob_int("webview.audit_tail_limit_max")
        if not 1 <= limit <= tail_max:
            raise _BadRequest(f"limit must be in [1, {tail_max}], got {limit}")
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
                "ORDER BY event_seq LIMIT ?",
                (self._knob_int("webview.improvements_scan_max"),),
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
        samples_max = self._knob_int("webview.refs_samples_max")
        with self._store.read() as conn:
            counts = {
                str(row["status"]): int(row["n"])
                for row in conn.execute("SELECT status, COUNT(*) AS n FROM unresolved_refs GROUP BY status").fetchall()
            }
            top_failed = conn.execute(
                "SELECT name_tail, COUNT(*) AS n FROM unresolved_refs WHERE status = 'failed' "
                "GROUP BY name_tail ORDER BY n DESC, name_tail LIMIT ?",
                (samples_max,),
            ).fetchall()
            samples = conn.execute(
                "SELECT source_node_id, target_name, name_tail, kind, failure_reason, attempts "
                "FROM unresolved_refs WHERE status = 'failed' ORDER BY ref_id LIMIT ?",
                (samples_max,),
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

    # ------------------------------------------------------- W15 code intelligence

    def codeintel_payload(
        self,
        op: str,
        *,
        symbol: str | None = None,
        source: str | None = None,
        target: str | None = None,
        depth: int = 8,
        limit: int = 200,
        min_support: int = 2,
        layers: str | None = None,
        allow: str | None = None,
    ) -> dict[str, Any]:
        """Deep code-intelligence ops over the universal code graph (SUB-130).

        Read-only: the snapshot comes from
        :func:`know_your_context.ops.codeintel_cli.load_code_graph`, the ops
        from :mod:`know_your_context.analytics.code_intel` — the same code
        path as the ``codeintel`` CLI. Unknown symbols map to 404, bad
        knobs/policies to 400, mirroring the hierarchy route's contract.
        """
        from know_your_context.analytics.code_intel import (
            CodeIntelError,
            UnknownSymbolError,
            boundary_violations,
            callees_of,
            callers_of,
            co_change,
            dependency_path,
            impact_analysis,
        )
        from know_your_context.ops.codeintel_cli import (
            _parse_flows,
            _parse_layers,
            change_sets_from_source_versions,
            load_code_graph,
        )

        graph = load_code_graph(self._store)
        try:
            if op == "callers":
                if not symbol:
                    raise _BadRequest("symbol is required for op=callers")
                return callers_of(graph, symbol, max_depth=depth, max_results=limit)
            if op == "callees":
                if not symbol:
                    raise _BadRequest("symbol is required for op=callees")
                return callees_of(graph, symbol, max_depth=depth, max_results=limit)
            if op == "path":
                if not source or not target:
                    raise _BadRequest("source and target are required for op=path")
                return dependency_path(graph, source, target)
            if op == "impact":
                if not symbol:
                    raise _BadRequest("symbol is required for op=impact")
                return impact_analysis(graph, symbol, max_depth=depth, max_results=limit)
            if op == "co-change":
                return co_change(change_sets_from_source_versions(self._store), min_support=min_support, limit=limit)
            if op == "boundary":
                if not layers:
                    raise _BadRequest("layers is required for op=boundary (name:prefix[,prefix];...)")
                parsed_layers = _parse_layers(layers)
                if isinstance(parsed_layers, str):
                    raise _BadRequest(parsed_layers)
                parsed_flows = _parse_flows(allow or "")
                if isinstance(parsed_flows, str):
                    raise _BadRequest(parsed_flows)
                return boundary_violations(graph, layers=parsed_layers, allowed_flows=parsed_flows, limit=limit)
        except UnknownSymbolError as error:
            raise _NodeNotFound(str(error)) from error
        except CodeIntelError as error:
            raise _BadRequest(str(error)) from error
        raise _BadRequest(f"unknown codeintel op {op!r}")

    # ------------------------------------------------------- W15 REST surface

    def _check_page(self, offset: int, limit: int, *, max_limit: int) -> None:
        if offset < 0:
            raise _BadRequest(f"offset must be >= 0, got {offset}")
        if not 1 <= limit <= max_limit:
            raise _BadRequest(f"limit must be in [1, {max_limit}], got {limit}")

    def nodes_payload(self, *, offset: int, limit: int, kind: str | None, domain: str | None) -> dict[str, Any]:
        """Paged live-node list with optional kind/domain filters (real rows)."""
        self._check_page(offset, limit, max_limit=self._knob_int("webview.nodes_page_max"))
        if kind is not None and _KIND_LANG_RE.fullmatch(kind) is None:
            raise _BadRequest(f"kind filter must match {_KIND_LANG_RE.pattern}, got {kind!r}")
        if domain is not None and re.fullmatch(r"[A-Za-z0-9_]{1,16}", domain) is None:
            raise _BadRequest(f"domain filter must be a short alphanumeric token, got {domain!r}")
        nodes = self._live_nodes()
        if kind is not None:
            nodes = [node for node in nodes if node["kind"] == kind]
        if domain is not None:
            nodes = [node for node in nodes if node["domain"] == domain]
        total = len(nodes)
        page = nodes[offset : offset + limit]
        return {
            "schema_version": SCHEMA_VERSION,
            "total": total,
            "offset": offset,
            "limit": limit,
            "count": len(page),
            "nodes": page,
        }

    def edges_payload(self, *, offset: int, limit: int, relation: str | None) -> dict[str, Any]:
        """Paged ``node_relation`` rows, optionally filtered by relation kind."""
        self._check_page(offset, limit, max_limit=self._knob_int("webview.edges_page_max"))
        if relation is not None and _KIND_LANG_RE.fullmatch(relation) is None:
            raise _BadRequest(f"relation filter must match {_KIND_LANG_RE.pattern}, got {relation!r}")
        with self._store.read() as conn:
            if relation is None:
                total = int(conn.execute("SELECT COUNT(*) AS n FROM node_relation").fetchone()["n"])
                rows = conn.execute(
                    "SELECT source_node_id, target_node_id, relation FROM node_relation "
                    "ORDER BY relation_seq LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            else:
                total = int(
                    conn.execute("SELECT COUNT(*) AS n FROM node_relation WHERE relation = ?", (relation,)).fetchone()[
                        "n"
                    ]
                )
                rows = conn.execute(
                    "SELECT source_node_id, target_node_id, relation FROM node_relation WHERE relation = ? "
                    "ORDER BY relation_seq LIMIT ? OFFSET ?",
                    (relation, limit, offset),
                ).fetchall()
        edges = [
            {
                "source": str(row["source_node_id"]),
                "target": str(row["target_node_id"]),
                "relation": str(row["relation"]),
            }
            for row in rows
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "total": total,
            "offset": offset,
            "limit": limit,
            "count": len(edges),
            "edges": edges,
        }

    def graph_edges_payload(self, *, offset: int, limit: int, kind: str | None, source: str | None) -> dict[str, Any]:
        """Paged code-graph edges from the ``graph_edges`` table (real rows)."""
        self._check_page(offset, limit, max_limit=self._knob_int("webview.edges_page_max"))
        if kind is not None and _KIND_LANG_RE.fullmatch(kind) is None:
            raise _BadRequest(f"kind filter must match {_KIND_LANG_RE.pattern}, got {kind!r}")
        if source is not None and not 0 < len(source) <= 256:
            raise _BadRequest("source filter must be 1..256 chars")
        clauses: list[str] = []
        args: list[Any] = []
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        if source is not None:
            clauses.append("source = ?")
            args.append(source)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._store.read() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) AS n FROM graph_edges{where}", args).fetchone()["n"])  # nosec B608 -- clauses are fixed strings, values bound
            rows = conn.execute(
                f"SELECT edge_id, source, target, kind, line, col, provenance FROM graph_edges{where} "  # nosec B608 -- clauses are fixed strings, values bound
                "ORDER BY edge_id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        edges = [
            {
                "edge_id": int(row["edge_id"]),
                "source": str(row["source"]),
                "target": str(row["target"]),
                "kind": str(row["kind"]),
                "line": row["line"] if row["line"] is None else int(row["line"]),
                "col": row["col"] if row["col"] is None else int(row["col"]),
                "provenance": str(row["provenance"]) if row["provenance"] is not None else None,
            }
            for row in rows
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "total": total,
            "offset": offset,
            "limit": limit,
            "count": len(edges),
            "edges": edges,
        }

    def node_edges_payload(self, node_id: str) -> dict[str, Any]:
        """Incident edges of one node (real ``node_relation`` + ``parent_of``)."""
        if self._store.current_node(node_id) is None:
            raise _NodeNotFound(node_id)
        limit = self._knob_int("webview.edges_page_max")
        edges: list[dict[str, str]] = []
        with self._store.read() as conn:
            out_rows = conn.execute(
                "SELECT target_node_id, relation FROM node_relation WHERE source_node_id = ? "
                "ORDER BY relation_seq LIMIT ?",
                (node_id, limit),
            ).fetchall()
            in_rows = conn.execute(
                "SELECT source_node_id, relation FROM node_relation WHERE target_node_id = ? "
                "ORDER BY relation_seq LIMIT ?",
                (node_id, limit),
            ).fetchall()
        for row in out_rows:
            edges.append({"source": node_id, "target": str(row["target_node_id"]), "kind": str(row["relation"])})
        for row in in_rows:
            edges.append({"source": str(row["source_node_id"]), "target": node_id, "kind": str(row["relation"])})
        node = self._store.current_node(node_id)
        if node is not None and node.parent_id is not None:
            edges.append({"source": node.parent_id, "target": node_id, "kind": "parent_of"})
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": node_id,
            "count": len(edges),
            "edges": edges,
        }

    def node_evidence_payload(self, node_id: str, *, limit: int) -> dict[str, Any]:
        """Evidence rows attached to one node, bounded fail-closed."""
        self._check_page(0, limit, max_limit=self._knob_int("webview.evidence_limit_max"))
        if self._store.current_node(node_id) is None:
            raise _NodeNotFound(node_id)
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT evidence_id, source_uri, observed_at, event_seq FROM evidence "
                "WHERE node_id = ? ORDER BY evidence_id LIMIT ?",
                (node_id, limit),
            ).fetchall()
            total = int(
                conn.execute("SELECT COUNT(*) AS n FROM evidence WHERE node_id = ?", (node_id,)).fetchone()["n"]
            )
        evidence = [
            {
                "evidence_id": str(row["evidence_id"]),
                "source_uri": sanitize_text(str(row["source_uri"])),
                "observed_at": str(row["observed_at"]),
                "event_seq": int(row["event_seq"]),
            }
            for row in rows
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": node_id,
            "total": total,
            "count": len(evidence),
            "evidence": evidence,
        }

    def node_revisions_payload(self, node_id: str) -> dict[str, Any]:
        """The full revision history of one node, oldest first."""
        if self._store.current_node(node_id) is None:
            raise _NodeNotFound(node_id)
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT version, lifecycle_status, event_seq, created_at FROM node_revision "
                "WHERE node_id = ? ORDER BY version",
                (node_id,),
            ).fetchall()
        revisions = [
            {
                "version": int(row["version"]),
                "lifecycle_status": str(row["lifecycle_status"]),
                "event_seq": int(row["event_seq"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": node_id,
            "count": len(revisions),
            "revisions": revisions,
        }

    def provenance_payload(self, node_id: str) -> dict[str, Any]:
        """The five-link provenance verdict of one node (read-only verify)."""
        from know_your_context.security.provenance import ProvenanceNodeNotFoundError, verify_provenance

        try:
            report = verify_provenance(self._store, node_id)
        except ProvenanceNodeNotFoundError:
            raise _NodeNotFound(node_id) from None
        return {"schema_version": SCHEMA_VERSION, **report.to_dict()}

    def symbols_payload(self, *, offset: int, limit: int, kind: str | None, path_prefix: str | None) -> dict[str, Any]:
        """Paged symbol rows with optional kind/path-prefix filters."""
        self._check_page(offset, limit, max_limit=self._knob_int("webview.symbols_page_max"))
        if kind is not None and _KIND_LANG_RE.fullmatch(kind) is None:
            raise _BadRequest(f"kind filter must match {_KIND_LANG_RE.pattern}, got {kind!r}")
        if path_prefix is not None and not 0 < len(path_prefix) <= 256:
            raise _BadRequest("path_prefix must be 1..256 chars")
        clauses: list[str] = []
        args: list[Any] = []
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        if path_prefix is not None:
            clauses.append("path LIKE ? ESCAPE '\\'")
            args.append(_like_escape(path_prefix) + "%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._store.read() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) AS n FROM symbols{where}", args).fetchone()["n"])  # nosec B608 -- clauses are fixed strings, values bound
            rows = conn.execute(
                f"SELECT node_id, name, kind, path, line, col FROM symbols{where} "  # nosec B608 -- clauses are fixed strings, values bound
                "ORDER BY node_id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        symbols = [
            {
                "node_id": str(row["node_id"]),
                "name": str(row["name"]),
                "kind": str(row["kind"]),
                "path": str(row["path"]),
                "line": int(row["line"]),
                "col": int(row["col"]),
            }
            for row in rows
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "total": total,
            "offset": offset,
            "limit": limit,
            "count": len(symbols),
            "symbols": symbols,
        }

    def symbol_payload(self, node_id: str) -> dict[str, Any]:
        """The symbol record of one node (404 when the node has none)."""
        symbol = self._store.symbol_by_node(node_id)
        if symbol is None:
            raise _NodeNotFound(node_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": node_id,
            "symbol": {
                "name": symbol.name,
                "kind": symbol.kind,
                "path": symbol.path,
                "line": symbol.line,
                "col": symbol.col,
            },
        }

    def symbol_prefix_payload(self, query: str, *, limit: int) -> dict[str, Any]:
        """Symbol-name prefix search (autocomplete); ``%``/``_`` are literal."""
        search_max = self._knob_int("webview.search_limit_max")
        if not query or not query.strip():
            raise _BadRequest("q must be a non-empty prefix")
        if len(query) > 128:
            raise _BadRequest("q must be at most 128 chars")
        if not 1 <= limit <= search_max:
            raise _BadRequest(f"limit must be in [1, {search_max}], got {limit}")
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT node_id, name, kind, path, line FROM symbols WHERE name LIKE ? ESCAPE '\\' "
                "ORDER BY name, node_id LIMIT ?",
                (_like_escape(query.strip()) + "%", limit),
            ).fetchall()
        results = [
            {
                "node_id": str(row["node_id"]),
                "name": str(row["name"]),
                "kind": str(row["kind"]),
                "path": str(row["path"]),
                "line": int(row["line"]),
            }
            for row in rows
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "query": query.strip(),
            "count": len(results),
            "results": results,
        }

    def refs_by_status_payload(self, status: str, *, offset: int, limit: int) -> dict[str, Any]:
        """Paged unresolved references of exactly one status (route-chosen)."""
        if status not in ("pending", "failed", "resolved"):
            raise _BadRequest(f"status must be one of pending/failed/resolved, got {status!r}")
        self._check_page(offset, limit, max_limit=self._knob_int("webview.page_size_max"))
        with self._store.read() as conn:
            total = int(
                conn.execute("SELECT COUNT(*) AS n FROM unresolved_refs WHERE status = ?", (status,)).fetchone()["n"]
            )
            rows = conn.execute(
                "SELECT ref_id, source_node_id, target_name, name_tail, kind, status, attempts, "
                "failure_reason, first_seen FROM unresolved_refs WHERE status = ? ORDER BY ref_id LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        refs = [
            {
                "ref_id": int(row["ref_id"]),
                "source_node_id": str(row["source_node_id"]),
                "target_name": str(row["target_name"]),
                "name_tail": str(row["name_tail"]),
                "kind": str(row["kind"]),
                "status": str(row["status"]),
                "attempts": int(row["attempts"]),
                "failure_reason": str(row["failure_reason"]) if row["failure_reason"] is not None else None,
                "first_seen": str(row["first_seen"]),
            }
            for row in rows
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "total": total,
            "offset": offset,
            "limit": limit,
            "count": len(refs),
            "refs": refs,
        }

    def extraction_summary_payload(self) -> dict[str, Any]:
        """Extraction queue counts by status; an absent sidecar is honest."""
        from know_your_context.extraction import ExtractionQueue, sidecar_path

        path = sidecar_path(self._store.db_path)
        if self._store.db_path == ":memory:" or not Path(path).is_file():
            return {"schema_version": SCHEMA_VERSION, "state": "absent", "queue_size": 0, "counts": {}}
        queue = ExtractionQueue(path)
        try:
            counts: dict[str, int] = {}
            for row in queue.list():
                counts[row.status] = counts.get(row.status, 0) + 1
            return {
                "schema_version": SCHEMA_VERSION,
                "state": "ok",
                "queue_size": queue.count(),
                "counts": {key: counts[key] for key in sorted(counts)},
            }
        finally:
            queue.close()

    def extraction_detail_payload(self, receipt_id: str) -> dict[str, Any]:
        """One extraction queue row in full (404 when unknown or absent)."""
        if _PROPOSAL_ID_RE.fullmatch(receipt_id) is None:
            raise _BadRequest(f"receipt id must match {_PROPOSAL_ID_RE.pattern}, got {receipt_id!r}")
        from know_your_context.extraction import ExtractionQueue, sidecar_path

        path = sidecar_path(self._store.db_path)
        if self._store.db_path == ":memory:" or not Path(path).is_file():
            raise _NodeNotFound(receipt_id)
        queue = ExtractionQueue(path)
        try:
            row = queue.get(receipt_id)
            if row is None:
                raise _NodeNotFound(receipt_id)
            return {
                "schema_version": SCHEMA_VERSION,
                "receipt_id": row.receipt_id,
                "status": row.status,
                "actor": row.actor,
                "source_sha256": row.source_sha256,
                "source_path": row.source_path,
                "method": redact_value(row.method),
                "method_fingerprint": row.method_fingerprint,
                "payload_sha256": row.payload_sha256,
                "nodes": len(row.payload.get("nodes", [])),
                "edges": len(row.payload.get("edges", [])),
                "confidence_reported": row.confidence_reported,
                "confidence_effective": row.confidence_effective,
                "corroborators": row.corroborators,
                "contradictions": row.contradictions,
                "overlap_ratio": row.overlap_ratio,
                "decision_note": row.decision_note,
                "decided_by": row.decided_by,
                "submitted_at": row.submitted_at,
                "decided_at": row.decided_at,
            }
        finally:
            queue.close()

    def ledger_health_payload(self, *, limit: int) -> dict[str, Any]:
        """Tail of the hash-chained health history (absent/corrupt = honest)."""
        tail_max = self._knob_int("webview.ledger_tail_max")
        if not 1 <= limit <= tail_max:
            raise _BadRequest(f"limit must be in [1, {tail_max}], got {limit}")
        state = "absent"
        snapshots: list[dict[str, Any]] = []
        if str(self._store.db_path) != ":memory:":
            from know_your_context.ops.lifecycle import LifecycleError, history_path_for_store, load_health_history

            history = history_path_for_store(self._store)
            if Path(history).is_file():
                try:
                    loaded = load_health_history(history)
                except LifecycleError as error:
                    state = f"corrupt: {error}"
                else:
                    state = "ok"
                    snapshots = [snap.to_payload() for snap in loaded[-limit:]]
        return {
            "schema_version": SCHEMA_VERSION,
            "state": state,
            "count": len(snapshots),
            "snapshots": snapshots,
        }

    def ledger_debt_payload(self, *, limit: int) -> dict[str, Any]:
        """Tail of the debt burn-down ledger (JSONL hash chain, read-only)."""
        tail_max = self._knob_int("webview.ledger_tail_max")
        if not 1 <= limit <= tail_max:
            raise _BadRequest(f"limit must be in [1, {tail_max}], got {limit}")
        state = "absent"
        entries: list[dict[str, Any]] = []
        if str(self._store.db_path) != ":memory:":
            from know_your_context.ops.debt import DebtLedgerError, ledger_path_for_store, load_debt_ledger

            path = ledger_path_for_store(self._store.db_path)
            if path.is_file():
                try:
                    entries = load_debt_ledger(path)[-limit:]
                except DebtLedgerError as error:
                    state = f"corrupt: {error}"
                else:
                    state = "ok"
        return {
            "schema_version": SCHEMA_VERSION,
            "state": state,
            "count": len(entries),
            "entries": entries,
        }

    def ledger_experience_payload(self, *, limit: int) -> dict[str, Any]:
        """Tail of the experience rerank journal plus chain verification.

        The sidecar is opened read-only (``mode=ro``) and only when it already
        exists — this route never creates or writes the ledger.
        """
        tail_max = self._knob_int("webview.ledger_tail_max")
        if not 1 <= limit <= tail_max:
            raise _BadRequest(f"limit must be in [1, {tail_max}], got {limit}")
        state = "absent"
        entries: list[dict[str, Any]] = []
        verification: dict[str, Any] = {"ok": True, "checked": 0, "first_bad_seq": None}
        db_path = str(self._store.db_path)
        sidecar = Path(f"{db_path}.experience-ledger.db")
        if db_path != ":memory:" and sidecar.is_file():
            import sqlite3

            from know_your_context.retrieval_v2.experience import GENESIS_HASH

            connection = sqlite3.connect(f"file:{sidecar}?mode=ro", uri=True)
            try:
                has_journal = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'journal'"
                ).fetchone()[0]
                if not has_journal:
                    state = "corrupt: journal table missing"
                else:
                    rows = connection.execute(
                        "SELECT seq, prev_hash, entry_hash, payload_json FROM journal ORDER BY seq"
                    ).fetchall()
                    state = "ok"
                    all_entries = [
                        {
                            "seq": int(row[0]),
                            "prev_hash": str(row[1]),
                            "entry_hash": str(row[2]),
                            "payload": json.loads(str(row[3])),
                        }
                        for row in rows
                    ]
                    prev_hash = GENESIS_HASH
                    checked = 0
                    for entry in all_entries:
                        checked += 1
                        canonical = json.dumps(
                            entry["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
                        )
                        expected = hashlib.sha256(f"{prev_hash}\n{canonical}".encode()).hexdigest()
                        if entry["prev_hash"] != prev_hash or entry["entry_hash"] != expected:
                            verification = {"ok": False, "checked": checked, "first_bad_seq": entry["seq"]}
                            break
                        prev_hash = entry["entry_hash"]
                    else:
                        verification = {"ok": True, "checked": checked, "first_bad_seq": None}
                    entries = all_entries[-limit:]
            finally:
                connection.close()
        return {
            "schema_version": SCHEMA_VERSION,
            "state": state,
            "count": len(entries),
            "verification": verification,
            "entries": entries,
        }

    def audit_verify_payload(self) -> dict[str, Any]:
        """End-to-end event-log hash chain verification (read-only)."""
        report = self._store.verify_chain()
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": bool(report.ok),
            "checked": int(report.checked),
            "first_bad_seq": report.first_bad_seq,
        }

    def bench_operation_payload(self, operation: str) -> dict[str, Any]:
        """One operation's latency detail from the invocation journal."""
        if _DOTTED_NAME_RE.fullmatch(operation) is None:
            raise _BadRequest(f"operation must match {_DOTTED_NAME_RE.pattern}, got {operation!r}")
        durations: list[int] = []
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT outcome_json FROM invocation_journal WHERE status = 'complete' AND operation = ? "
                "ORDER BY invocation_id",
                (operation,),
            ).fetchall()
        for row in rows:
            try:
                outcome = json.loads(str(row["outcome_json"]))
            except json.JSONDecodeError, TypeError:
                continue
            duration = outcome.get("duration_ms") if isinstance(outcome, dict) else None
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                continue
            durations.append(duration)
        if not durations:
            raise _NodeNotFound(operation)
        summary = latency_summary(durations)
        return {
            "schema_version": SCHEMA_VERSION,
            "operation": operation,
            "count": summary["count"],
            "min_ms": summary["min"],
            "max_ms": summary["max"],
            "mean_ms": summary["mean"],
            "p50_ms": summary["p50"],
            "p95_ms": summary["p95"],
            "samples": durations[: self._knob_int("webview.bench_rows_max")],
        }

    def debt_report_payload(self) -> dict[str, Any]:
        """Debt queue plus burn-down metrics from the ledger (read-only)."""
        from know_your_context.analytics.debt import build_debt_queue, burndown
        from know_your_context.ops.debt import (
            DebtLedgerError,
            ledger_path_for_store,
            load_debt_inputs,
            load_debt_ledger,
        )

        nodes, links, refs = load_debt_inputs(self._store)
        queue = build_debt_queue(nodes, links, now=datetime.now(UTC), unresolved_refs=refs)
        queue["count"] = len(queue["items"])
        state = "absent"
        snapshots: list[dict[str, Any]] = []
        if str(self._store.db_path) != ":memory:":
            path = ledger_path_for_store(self._store.db_path)
            if path.is_file():
                try:
                    snapshots = load_debt_ledger(path)
                except DebtLedgerError as error:
                    state = f"corrupt: {error}"
                else:
                    state = "ok"
        return {
            "schema_version": SCHEMA_VERSION,
            "queue": queue,
            "ledger": state,
            "burndown": burndown(snapshots),
        }

    def switch_detail_payload(self, name: str) -> dict[str, Any]:
        """One kill switch with its live resolved state."""
        if _DOTTED_NAME_RE.fullmatch(name) is None:
            raise _BadRequest(f"switch name must match {_DOTTED_NAME_RE.pattern}, got {name!r}")
        entry = next((item for item in KILL_SWITCHES if item.name == name), None)
        if entry is None:
            raise _NodeNotFound(name)
        board = load_switches()
        return {
            "schema_version": SCHEMA_VERSION,
            "name": entry.name,
            "env": entry.env,
            "state": str(board.state(entry.name)),
            "affects": entry.affects,
            "fail_safe": entry.fail_safe,
            "owned_here": entry.owned_here,
        }

    def config_payload(self) -> dict[str, Any]:
        """The resolved value + provenance of every config knob."""
        dumped = self._knobs.dump()
        knobs = [{"name": name, **dumped[name]} for name in sorted(dumped)]
        return {"schema_version": SCHEMA_VERSION, "count": len(knobs), "knobs": knobs}

    def config_knob_payload(self, name: str) -> dict[str, Any]:
        """One config knob in detail (404 when undeclared)."""
        if _DOTTED_NAME_RE.fullmatch(name) is None:
            raise _BadRequest(f"knob name must match {_DOTTED_NAME_RE.pattern}, got {name!r}")
        dumped = self._knobs.dump()
        entry = dumped.get(name)
        if entry is None:
            raise _NodeNotFound(name)
        return {"schema_version": SCHEMA_VERSION, "name": name, **entry}

    def health_findings_payload(self, *, severity: str) -> dict[str, Any]:
        """Diagnostic findings filtered to one severity tier."""
        from know_your_context.ops.diagnose import SEVERITY_ORDER

        if severity not in SEVERITY_ORDER:
            raise _BadRequest(f"severity must be one of {SEVERITY_ORDER}, got {severity!r}")
        report = run_diagnostics(self._store)
        findings = [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": sanitize_text(finding.message),
                "details": redact_value(finding.details),
            }
            for finding in report.findings
            if finding.severity == severity
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "severity": severity,
            "count": len(findings),
            "findings": findings,
        }

    def health_checks_payload(self) -> dict[str, Any]:
        """Which diagnostic checks ran; findings stay honestly un-attributed
        (the upstream Finding carries no check field, so none is invented)."""
        report = run_diagnostics(self._store)
        return {
            "schema_version": SCHEMA_VERSION,
            "checked": list(report.checked),
            "count": len(report.checked),
            "checks": [{"name": name, "ran": True} for name in report.checked],
            "finding_total": len(report.findings),
        }

    def metrics_buckets_payload(self, *, bucket: str, limit: int) -> dict[str, Any]:
        """Event-log counts per bucket: day, event kind or actor (real GROUP BY)."""
        if bucket not in ("day", "kind", "actor"):
            raise _BadRequest(f"bucket must be one of day/kind/actor, got {bucket!r}")
        series_max = self._knob_int("webview.series_limit_max")
        if not 1 <= limit <= series_max:
            raise _BadRequest(f"limit must be in [1, {series_max}], got {limit}")
        if bucket == "day":
            sql = (
                "SELECT substr(occurred_at, 1, 10) AS bucket_key, COUNT(*) AS n FROM event_log "
                "GROUP BY bucket_key ORDER BY bucket_key DESC LIMIT ?"
            )
        elif bucket == "kind":
            sql = "SELECT kind AS bucket_key, COUNT(*) AS n FROM event_log GROUP BY kind ORDER BY kind LIMIT ?"
        else:
            sql = "SELECT actor AS bucket_key, COUNT(*) AS n FROM event_log GROUP BY actor ORDER BY actor LIMIT ?"
        with self._store.read() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        buckets = [{"key": str(row["bucket_key"]), "events": int(row["n"])} for row in rows]
        if bucket == "day":
            buckets.reverse()  # ascending days, like /api/metrics/series
        return {
            "schema_version": SCHEMA_VERSION,
            "bucket": bucket,
            "count": len(buckets),
            "buckets": buckets,
        }

    def metrics_operations_payload(self) -> dict[str, Any]:
        """Invocation journal counts per operation with mean real duration."""
        operations: dict[str, dict[str, Any]] = {}
        durations: dict[str, list[int]] = {}
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT operation, status, COUNT(*) AS n FROM invocation_journal GROUP BY operation, status"
            ).fetchall()
            duration_rows = conn.execute(
                "SELECT operation, outcome_json FROM invocation_journal WHERE status = 'complete' "
                "ORDER BY invocation_id"
            ).fetchall()
        for row in rows:
            entry = operations.setdefault(
                str(row["operation"]), {"operation": str(row["operation"]), "total": 0, "complete": 0, "failed": 0}
            )
            status = str(row["status"])
            entry["total"] += int(row["n"])
            if status in ("complete", "failed"):
                entry[status] += int(row["n"])
        for row in duration_rows:
            try:
                outcome = json.loads(str(row["outcome_json"]))
            except json.JSONDecodeError, TypeError:
                continue
            duration = outcome.get("duration_ms") if isinstance(outcome, dict) else None
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                continue
            durations.setdefault(str(row["operation"]), []).append(duration)
        result = []
        for operation in sorted(operations):
            entry = operations[operation]
            samples = durations.get(operation, [])
            entry["mean_duration_ms"] = latency_summary(samples)["mean"] if samples else None
            result.append(entry)
        return {"schema_version": SCHEMA_VERSION, "count": len(result), "operations": result}

    def store_tables_payload(self) -> dict[str, Any]:
        """Every table with its real row count."""
        return {
            "schema_version": SCHEMA_VERSION,
            "tables": [
                {"name": name, "rows": rows} for name, rows in sorted(self.store_stats_payload()["tables"].items())
            ],
        }

    def store_table_payload(self, name: str) -> dict[str, Any]:
        """One table's columns and row count (name allowlisted via sqlite_master)."""
        if _TABLE_NAME_RE.fullmatch(name) is None:
            raise _BadRequest(f"table name must match {_TABLE_NAME_RE.pattern}, got {name!r}")
        with self._store.read() as conn:
            exists = conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()["n"]
            if not exists:
                raise _NodeNotFound(name)
            columns = [
                {
                    "name": str(row["name"]),
                    "type": str(row["type"]),
                    "notnull": bool(row["notnull"]),
                    "pk": bool(row["pk"]),
                }
                for row in conn.execute(f'PRAGMA table_info("{name}")')  # nosec B608 -- name validated + allowlisted above
            ]
            rows = int(conn.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()["n"])  # nosec B608 -- name validated + allowlisted above
        return {
            "schema_version": SCHEMA_VERSION,
            "name": name,
            "rows": rows,
            "columns": columns,
        }

    def store_schema_payload(self) -> dict[str, Any]:
        """Applied schema versions plus the packaged migration registry."""
        from know_your_context.ops.schema_migrations import MIGRATIONS

        stats = self.store_stats_payload()
        return {
            "schema_version": SCHEMA_VERSION,
            "recorded": stats["store_schema_version"],
            "schema_versions": stats["schema_versions"],
            "migrations": [
                {"version": migration.version, "name": migration.name, "description": migration.description}
                for migration in MIGRATIONS
            ],
        }

    def _analytics_slices(self) -> tuple[dict[str, Any], list[dict[str, Any]], int, bool]:
        nodes, links, total, truncated = self._read_model(cap=self._knob_int("webview.analytics_max_nodes"))
        graph = {
            "nodes": [
                {"id": node["id"], "domain": node["domain"], "label": node["label"], "version": node["version"]}
                for node in nodes
            ],
            "links": links,
        }
        return graph, nodes, total, truncated

    def analytics_centrality_payload(self, *, top: int) -> dict[str, Any]:
        """Centrality ranking only; ``top`` clamps fail-safe to the knob."""
        if top < 1:
            raise _BadRequest(f"top must be >= 1, got {top}")
        effective = min(top, self._knob_int("webview.top_ranking"))
        graph, nodes, total, truncated = self._analytics_slices()
        centrality = compute_centrality(graph, top=effective) if nodes else {"global": {}}
        return {
            "schema_version": SCHEMA_VERSION,
            "top": effective,
            "centrality": centrality,
            "node_count": len(nodes),
            "total_nodes": total,
            "partial": truncated,
        }

    def analytics_communities_payload(self) -> dict[str, Any]:
        """Community detection only (same read model as /api/analytics)."""
        graph, nodes, total, truncated = self._analytics_slices()
        communities = detect_communities(graph["nodes"], graph["links"]) if nodes else {"communities": []}
        return {
            "schema_version": SCHEMA_VERSION,
            "communities": communities,
            "node_count": len(nodes),
            "total_nodes": total,
            "partial": truncated,
        }

    def analytics_bridges_payload(self) -> dict[str, Any]:
        """Bridge detection only (same read model as /api/analytics)."""
        graph, nodes, total, truncated = self._analytics_slices()
        communities = detect_communities(graph["nodes"], graph["links"]) if nodes else {"communities": []}
        membership = {
            member: community["id"]
            for community in communities.get("communities", [])
            for member in community["members"]
        }
        bridges = (
            find_bridges(graph["nodes"], graph["links"], membership=membership)
            if nodes
            else {"bridges": [], "counts": {"candidates": 0, "emitted": 0, "by_kind": {}}}
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "bridges": bridges,
            "node_count": len(nodes),
            "total_nodes": total,
            "partial": truncated,
        }

    def export_subgraph_payload(
        self, *, node_id: str | None, domain: str | None, depth: int, max_nodes: int
    ) -> dict[str, Any]:
        """In-memory subgraph export: ego-net or domain slice, real artifacts.

        Nothing is written: the canonical JSON/Mermaid artifacts are built in
        memory and served as a manifest (sha256 + size) plus the read model.
        """
        from know_your_context.ops.subgraph_export import (
            SubgraphCapExceededError,
            SubgraphRequest,
            SubgraphSelectionError,
            UnknownSubgraphNodeError,
            build_subgraph,
            export_subgraph,
            subgraph_manifest_entries,
        )

        if (node_id is None) == (domain is None):
            raise _BadRequest("exactly one selector is required: node_id XOR domain")
        depth_max = self._knob_int("webview.export_depth_max")
        if not 0 <= depth <= depth_max:
            raise _BadRequest(f"depth must be in [0, {depth_max}], got {depth}")
        nodes_max = self._knob_int("webview.export_nodes_max")
        if not 1 <= max_nodes <= nodes_max:
            raise _BadRequest(f"max_nodes must be in [1, {nodes_max}], got {max_nodes}")
        if domain is not None and re.fullmatch(r"[A-Za-z0-9_]{1,16}", domain) is None:
            raise _BadRequest(f"domain must be a short alphanumeric token, got {domain!r}")
        nodes, links, _, _ = self._read_model(cap=nodes_max)
        read_model = {
            "nodes": [{"id": node["id"], "domain": node["domain"], "label": node["label"]} for node in nodes],
            "links": links,
        }
        try:
            request = SubgraphRequest(domain=domain, node_id=node_id, depth=depth, max_nodes=max_nodes)
            result = build_subgraph(read_model, request)
            export = export_subgraph(read_model, request, max_bytes=self._knob_int("webview.export_max_bytes"))
        except UnknownSubgraphNodeError:
            raise _NodeNotFound(node_id) from None
        except SubgraphCapExceededError as error:
            raise _ExportTooLarge(str(error)) from error
        except SubgraphSelectionError as error:
            raise _BadRequest(str(error)) from error
        manifest = subgraph_manifest_entries(export)
        return {
            "schema_version": SCHEMA_VERSION,
            "slug": export.slug,
            "selector": {"node_id": node_id, "domain": domain},
            "depth": depth,
            "max_nodes": max_nodes,
            "node_count": export.node_count,
            "edge_count": export.edge_count,
            "nodes_omitted": export.nodes_omitted,
            "edges_omitted": export.edges_omitted,
            "read_model": result.read_model,
            "artifacts": [
                {"name": name, "sha256": entry["sha256"], "size": entry["size"], "version": entry["version"]}
                for name, entry in sorted(manifest.items())
            ],
        }

    def export_node_payload(self, node_id: str) -> dict[str, Any]:
        """Full single-node export bundle: detail + evidence + provenance."""
        node = self._node_detail_or_raise(node_id)
        evidence = self.node_evidence_payload(node_id, limit=self._knob_int("webview.evidence_limit_max"))
        return {
            "schema_version": SCHEMA_VERSION,
            "node": node,
            "evidence": evidence["evidence"],
            "provenance": self.provenance_payload(node_id),
        }

    def _node_detail_or_raise(self, node_id: str) -> dict[str, Any]:
        node = self._store.current_node(node_id)
        if node is None:
            raise _NodeNotFound(node_id)
        return self._node_detail(node)

    def routes_payload(self) -> dict[str, Any]:
        """The live route table (discovery; reflects feature flags via config)."""
        return {
            "schema_version": SCHEMA_VERSION,
            "count": len(API_ROUTES),
            "routes": [
                {
                    "path": route.path,
                    "method": route.method,
                    "access": str(route.access),
                    "route_class": route.route_class,
                    "summary": route.summary,
                }
                for route in API_ROUTES
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
        knobs: KnobBoard | None = None,
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
        self._knobs = knobs if knobs is not None else load_knobs()
        self._app = WebviewApp(store_path, knobs=self._knobs)
        self._token = resolved_token
        self._host_allowlist = tuple(host_allowlist)
        self._request_id_factory = request_id_factory or (lambda: secrets.token_hex(8))
        # W13 middleware layer: timing + typed error mapping are server-level;
        # the capability gate binds per request (it needs the handler's
        # headers/query) inside the handler's guard chain.
        self._timings = TimingMiddleware(capacity=self._knob("webview.trace_capacity"))
        self._errors = TypedErrorMiddleware()
        # W15: per-class token-bucket rate limiting (knob-driven, fail-closed).
        self._rate_limiter = RateLimiter(
            {
                "read": (
                    float(self._knobs.value("webview.rate.read_per_second")),
                    int(self._knobs.value("webview.rate.read_burst")),
                ),
                "heavy": (
                    float(self._knobs.value("webview.rate.heavy_per_second")),
                    int(self._knobs.value("webview.rate.heavy_burst")),
                ),
                "export": (
                    float(self._knobs.value("webview.rate.export_per_second")),
                    int(self._knobs.value("webview.rate.export_burst")),
                ),
            }
        )
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None
        self._serving = False
        self._closed = False

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def _knob(self, name: str) -> int:
        return int(self._knobs.value(name))

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
            self._thread.join(timeout=float(self._knobs.value("webview.shutdown_join_seconds")))
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
                knobs = server_ref._knobs

                # W15: feature flags gate whole route groups fail-closed (a
                # disabled group answers exactly like an unknown endpoint).
                flag = _route_group_flag(tail)
                if flag is not None and not bool(knobs.value(flag)):
                    self._reject(404, "NOT_FOUND", "unknown api endpoint")
                    return
                # W15: per-class rate limit, checked before any store work.
                if bool(knobs.value("webview.rate.enabled")):
                    try:
                        server_ref._rate_limiter.check(route_class_for(tail))
                    except RateLimitedError as error:
                        self._ctx.response_headers["Retry-After"] = str(error.retry_after_seconds)
                        self._respond_json(
                            429,
                            {"error": {"code": error.code, "message": str(error), "retryable": True}},
                            head_only=head_only,
                        )
                        return

                try:
                    payload = self._dispatch_api(tail, params)
                except _UnknownRoute:
                    self._reject(404, "NOT_FOUND", "unknown api endpoint")
                    return
                except _NodeNotFound:
                    self._reject(404, "NOT_FOUND", "unknown node id")
                    return
                except _ExportTooLarge as error:
                    self._reject(413, "PAYLOAD_TOO_LARGE", str(error))
                    return
                except _BadRequest as error:
                    self._reject(400, "BAD_REQUEST", str(error))
                    return
                self._respond_json(200, payload, head_only=head_only)

            def _dispatch_api(self, tail: str, params: Mapping[str, list[str]]) -> dict[str, Any]:
                """Table-driven dispatch (W15): exact tails via a lookup table,
                parameterized tails via the prefix chain below it."""
                app = server_ref._app
                knobs = server_ref._knobs

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

                def _knob_int(name: str) -> int:
                    return int(knobs.value(name))

                def _query() -> dict[str, Any]:
                    node_id = _str_param("node_id")
                    if node_id is None:
                        raise _BadRequest("node_id is required")
                    kind = _str_param("kind")
                    if kind is not None and _KIND_LANG_RE.fullmatch(kind) is None:
                        raise _BadRequest(f"kind filter must match {_KIND_LANG_RE.pattern}, got {kind!r}")
                    return app.query_payload(
                        node_id,
                        kind=kind,
                        direction=_str_param("direction") or "both",
                        limit=_int_param("limit", _knob_int("webview.query_limit_default")),
                    )

                def _codeintel() -> dict[str, Any]:
                    op = _str_param("op")
                    if op is None:
                        raise _BadRequest("op is required (callers|callees|path|impact|co-change|boundary)")
                    return app.codeintel_payload(
                        op,
                        symbol=_str_param("symbol"),
                        source=_str_param("source"),
                        target=_str_param("target"),
                        depth=_int_param("depth", 8),
                        limit=_int_param("limit", 200),
                        min_support=_int_param("min_support", 2),
                        layers=_str_param("layers"),
                        allow=_str_param("allow"),
                    )

                page_default = _knob_int("webview.page_size_default")
                exact: dict[str, Callable[[], dict[str, Any]]] = {
                    "graph": app.graph_payload,
                    "nodes": lambda: app.nodes_payload(
                        offset=_int_param("offset", 0),
                        limit=_int_param("limit", page_default),
                        kind=_str_param("kind"),
                        domain=_str_param("domain"),
                    ),
                    "edges": lambda: app.edges_payload(
                        offset=_int_param("offset", 0),
                        limit=_int_param("limit", page_default),
                        relation=_str_param("relation"),
                    ),
                    "graph/edges": lambda: app.graph_edges_payload(
                        offset=_int_param("offset", 0),
                        limit=_int_param("limit", page_default),
                        kind=_str_param("kind"),
                        source=_str_param("source"),
                    ),
                    "search": lambda: app.search_payload(
                        params.get("q", [""])[-1],
                        kind=_str_param("kind"),
                        lang=_str_param("lang"),
                        limit=_int_param("limit", _knob_int("webview.search_limit_default")),
                    ),
                    "search/symbols": lambda: app.symbol_prefix_payload(
                        params.get("q", [""])[-1],
                        limit=_int_param("limit", _knob_int("webview.search_limit_default")),
                    ),
                    "symbols": lambda: app.symbols_payload(
                        offset=_int_param("offset", 0),
                        limit=_int_param("limit", page_default),
                        kind=_str_param("kind"),
                        path_prefix=_str_param("path_prefix"),
                    ),
                    "metrics": app.metrics_payload,
                    "metrics/series": lambda: app.metrics_series_payload(
                        limit=_int_param("limit", _knob_int("webview.series_limit_default"))
                    ),
                    "metrics/buckets": lambda: app.metrics_buckets_payload(
                        bucket=_str_param("bucket") or "day",
                        limit=_int_param("limit", _knob_int("webview.series_limit_max")),
                    ),
                    "metrics/operations": app.metrics_operations_payload,
                    "health": app.health_payload,
                    "health/detail": lambda: app.health_detail_payload(timing=server_ref._timings.summary()),
                    "health/findings": lambda: app.health_findings_payload(severity=_str_param("severity") or "error"),
                    "health/checks": app.health_checks_payload,
                    "analytics": app.analytics_payload,
                    "analytics/centrality": lambda: app.analytics_centrality_payload(
                        top=_int_param("top", _knob_int("webview.top_ranking"))
                    ),
                    "analytics/communities": app.analytics_communities_payload,
                    "analytics/bridges": app.analytics_bridges_payload,
                    "audit": lambda: app.audit_payload(
                        limit=_int_param("limit", _knob_int("webview.audit_limit_default"))
                    ),
                    "audit/tail": lambda: app.audit_tail_payload(
                        since=_int_param("since", 0),
                        limit=_int_param("limit", _knob_int("webview.audit_tail_limit_default")),
                    ),
                    "audit/verify": app.audit_verify_payload,
                    "proposals": app.proposals_payload,
                    "improvements": app.improvements_payload,
                    "extractions": app.extractions_payload,
                    "extractions/summary": app.extraction_summary_payload,
                    "debt": app.debt_payload,
                    "debt/report": app.debt_report_payload,
                    "refs": app.refs_payload,
                    "ledger/health": lambda: app.ledger_health_payload(
                        limit=_int_param("limit", _knob_int("webview.ledger_tail_max"))
                    ),
                    "ledger/debt": lambda: app.ledger_debt_payload(
                        limit=_int_param("limit", _knob_int("webview.ledger_tail_max"))
                    ),
                    "ledger/experience": lambda: app.ledger_experience_payload(
                        limit=_int_param("limit", _knob_int("webview.ledger_tail_max"))
                    ),
                    "query": _query,
                    "bench": app.bench_payload,
                    "store/stats": app.store_stats_payload,
                    "store/tables": app.store_tables_payload,
                    "store/schema": app.store_schema_payload,
                    "switches": app.switches_payload,
                    "config": app.config_payload,
                    "export/subgraph": lambda: app.export_subgraph_payload(
                        node_id=_str_param("node_id"),
                        domain=_str_param("domain"),
                        depth=_int_param("depth", _knob_int("webview.export_depth_default")),
                        max_nodes=_int_param("max_nodes", _knob_int("webview.export_nodes_max")),
                    ),
                    "routes": app.routes_payload,
                    "codeintel": _codeintel,
                }
                handler = exact.get(tail)
                if handler is not None:
                    return handler()
                if tail in ("refs/pending", "refs/failed", "refs/resolved"):
                    return app.refs_by_status_payload(
                        tail.rsplit("/", 1)[-1],
                        offset=_int_param("offset", 0),
                        limit=_int_param("limit", page_default),
                    )
                if tail.startswith("hierarchy/"):
                    node_id = tail[len("hierarchy/") :]
                    if not node_id or "/" in node_id:
                        raise _BadRequest("node id must be a single non-empty path segment")
                    return app.hierarchy_payload(
                        node_id, depth=_int_param("depth", _knob_int("webview.hierarchy_depth_default"))
                    )
                return self._dispatch_prefixed(tail, app, params)

            def _dispatch_prefixed(self, tail: str, app: WebviewApp, params: Mapping[str, list[str]]) -> dict[str, Any]:
                """Parameterized tails: one id/segment carried in the path."""
                if tail.startswith("node/"):
                    return self._dispatch_node(tail[len("node/") :], app, params)
                for prefix, method, missing in (
                    ("symbols/", app.symbol_payload, "node id"),
                    ("provenance/", app.provenance_payload, "node id"),
                    ("extractions/", app.extraction_detail_payload, "receipt id"),
                    ("improvements/", app.improvement_detail_payload, "proposal id"),
                    ("bench/", app.bench_operation_payload, "operation"),
                    ("store/tables/", app.store_table_payload, "table name"),
                    ("switches/", app.switch_detail_payload, "switch name"),
                    ("config/", app.config_knob_payload, "knob name"),
                    ("export/node/", app.export_node_payload, "node id"),
                ):
                    if tail.startswith(prefix):
                        segment = tail[len(prefix) :]
                        if not segment or "/" in segment:
                            raise _BadRequest(f"{missing} must be a single non-empty path segment")
                        return method(segment)
                raise _UnknownRoute(tail)

            def _dispatch_node(
                self, node_tail: str, app: WebviewApp, params: Mapping[str, list[str]]
            ) -> dict[str, Any]:
                for suffix in ("/edges", "/evidence", "/revisions"):
                    if node_tail.endswith(suffix):
                        node_id = node_tail[: -len(suffix)]
                        if not node_id or "/" in node_id:
                            raise _BadRequest("node id must be a single non-empty path segment")
                        if suffix == "/edges":
                            return app.node_edges_payload(node_id)
                        if suffix == "/evidence":
                            raw = params.get("limit", [None])[-1]
                            limit = (
                                int(server_ref._knobs.value("webview.evidence_limit_max")) if raw is None else int(raw)
                            )
                            return app.node_evidence_payload(node_id, limit=limit)
                        return app.node_revisions_payload(node_id)
                if not node_tail or "/" in node_tail:
                    raise _BadRequest("node id must be a single non-empty path segment")
                return app.node_payload(node_tail)

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
        heartbeat_poll_seconds: float | None = None,
    ) -> None:
        from know_your_context.serve.watchdog import WatchdogPolicy

        if heartbeat_poll_seconds is None:
            heartbeat_poll_seconds = float(load_knobs().value("webview.heartbeat_poll_seconds"))
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
