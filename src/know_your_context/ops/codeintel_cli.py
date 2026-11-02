"""Operational surface for the deep code-intelligence graph ops (W15, SUB-130).

Two read-only store adapters lift :mod:`know_your_context.analytics.code_intel`
inputs out of the store (strictly read-only SQL — the pattern documented in
``docs/recovery/atm-wave-c2.md``):

* :func:`load_code_graph` — the universal code graph from the v3
  ``symbols`` + ``graph_edges`` tables (canonical row order).
* :func:`change_sets_from_source_versions` — file co-change history from
  consecutive ``source_version`` manifests of the same source: a change
  set is the paths added, removed or content-changed between two
  consecutive versions. This is the only honest "changed together" history
  the store records (the append-only event log carries per-invocation
  aggregates, not per-file lists).

And the CLI handler :func:`run_codeintel_command` backing
``codeintel callers-of|callees-of|dependency-path|impact|co-change|boundary-violations``.
OPS surface only: never a knowledge op, never dispatched over MCP or HTTP.
Every failure uses the canonical error envelope; exit codes mirror the
``debt``/``report`` commands (2 usage, 1 operation, 0 ok).
"""

from __future__ import annotations

import argparse
import json
import sys

from know_your_context.analytics.code_intel import (
    CodeGraph,
    CodeIntelError,
    boundary_violations,
    build_code_graph,
    callees_of,
    callers_of,
    co_change,
    dependency_path,
    impact_analysis,
)
from know_your_context.domain import canonical_json
from know_your_context.store import EventStore

__all__ = [
    "change_sets_from_source_versions",
    "load_code_graph",
    "run_codeintel_command",
]

_SYMBOLS_SQL = "SELECT node_id, name, kind, path, line, col FROM symbols ORDER BY path, line, col, node_id"
_EDGES_SQL = "SELECT source, target, kind, line FROM graph_edges ORDER BY source, target, kind, line"
_SOURCE_VERSIONS_SQL = (
    "SELECT source_id, source_version_id, manifest_json FROM source_version "
    "ORDER BY source_id, observed_at, source_version_id"
)


def load_code_graph(store: EventStore) -> CodeGraph:
    """Read-only adapter: the code graph snapshot from the v3 tables."""
    with store.read() as conn:
        node_rows = conn.execute(_SYMBOLS_SQL).fetchall()
        edge_rows = conn.execute(_EDGES_SQL).fetchall()
    nodes = [
        {
            "node_id": str(row["node_id"]),
            "name": str(row["name"]),
            "kind": str(row["kind"]),
            "path": str(row["path"]),
            "line": int(row["line"]),
            "col": int(row["col"]),
        }
        for row in node_rows
    ]
    edges = [
        {
            "source": str(row["source"]),
            "target": str(row["target"]),
            "kind": str(row["kind"]),
            "line": int(row["line"]) if row["line"] is not None else 0,
        }
        for row in edge_rows
    ]
    return build_code_graph(nodes, edges)


def change_sets_from_source_versions(store: EventStore) -> tuple[tuple[str, ...], ...]:
    """Change sets from consecutive source-version manifests (per source).

    A change set is the sorted paths that were added, removed or whose
    content hash changed between two consecutive versions of one source.
    Single-file change sets carry no co-change signal but are kept — the
    analytics layer counts them under ``discarded_sets``.
    """
    with store.read() as conn:
        rows = conn.execute(_SOURCE_VERSIONS_SQL).fetchall()
    change_sets: list[tuple[str, ...]] = []
    previous: dict[str, tuple[str, dict[str, str]]] = {}  # source_id -> (version_id, manifest)
    for row in rows:
        source_id = str(row["source_id"])
        version_id = str(row["source_version_id"])
        try:
            entries = json.loads(str(row["manifest_json"]))
        except json.JSONDecodeError:
            continue  # corrupt manifests are skipped, never fatal
        manifest = {
            str(entry["path"]): str(entry["sha256"])
            for entry in entries
            if isinstance(entry, dict) and "path" in entry and "sha256" in entry
        }
        if source_id in previous:
            _prior_id, prior = previous[source_id]
            changed = tuple(
                sorted(path for path in set(manifest) | set(prior) if manifest.get(path) != prior.get(path))
            )
            if changed:
                change_sets.append(changed)
        previous[source_id] = (version_id, manifest)
    return tuple(change_sets)


# ------------------------------------------------------------------ cli


def _usage_error(message: str) -> int:
    envelope = {"error": {"code": "CLI_USAGE", "message": message, "retryable": False, "audit_event_id": None}}
    print(canonical_json(envelope), file=sys.stderr)
    return 2


def _operation_error(code: str, message: str) -> int:
    envelope = {"error": {"code": code, "message": message, "retryable": False, "audit_event_id": None}}
    print(canonical_json(envelope), file=sys.stderr)
    return 1


def _parse_layers(raw: str) -> dict[str, tuple[str, ...]] | str:
    """Parse ``name:prefix1,prefix2;name2:prefix3`` into a layers mapping."""
    layers: dict[str, tuple[str, ...]] = {}
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            return f"layer spec must be name:prefix[,prefix...], got {chunk!r}"
        name, _, prefixes = chunk.partition(":")
        name = name.strip()
        prefix_tuple = tuple(part.strip() for part in prefixes.split(",") if part.strip())
        if not name or not prefix_tuple:
            return f"layer spec must be name:prefix[,prefix...], got {chunk!r}"
        if name in layers:
            return f"duplicate layer name {name!r}"
        layers[name] = prefix_tuple
    if not layers:
        return "at least one layer is required"
    return layers


def _parse_flows(raw: str) -> list[tuple[str, str]] | str:
    """Parse ``from:to,from2:to2`` into allowed-flow pairs."""
    flows: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            return f"allowed flow must be from:to, got {chunk!r}"
        source, _, target = chunk.partition(":")
        if not source.strip() or not target.strip():
            return f"allowed flow must be from:to, got {chunk!r}"
        flows.append((source.strip(), target.strip()))
    return flows


def run_codeintel_command(args: argparse.Namespace) -> int:
    """Run the ``codeintel`` command; return the exit code."""
    action = str(args.codeintel_action)
    store = EventStore(args.store)
    try:
        graph = load_code_graph(store)
        try:
            if action == "callers-of":
                result = callers_of(graph, args.symbol, max_depth=args.depth, max_results=args.limit, file=args.file)
            elif action == "callees-of":
                result = callees_of(graph, args.symbol, max_depth=args.depth, max_results=args.limit, file=args.file)
            elif action == "dependency-path":
                result = dependency_path(graph, args.source, args.target)
            elif action == "impact":
                result = impact_analysis(graph, args.symbol, max_depth=args.depth, max_results=args.limit)
            elif action == "co-change":
                result = co_change(
                    change_sets_from_source_versions(store),
                    min_support=args.min_support,
                    limit=args.limit,
                )
            elif action == "boundary-violations":
                layers = _parse_layers(args.layers)
                if isinstance(layers, str):
                    return _usage_error(layers)
                flows = _parse_flows(args.allow or "")
                if isinstance(flows, str):
                    return _usage_error(flows)
                result = boundary_violations(graph, layers=layers, allowed_flows=flows, limit=args.limit)
            else:
                return _usage_error(f"unknown codeintel action {action!r}")
        except CodeIntelError as error:
            return _operation_error("CODEINTEL_QUERY", str(error))
        print(canonical_json(result))
        return 0
    finally:
        store.close()
