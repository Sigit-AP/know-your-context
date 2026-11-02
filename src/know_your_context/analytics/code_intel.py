"""Deep code-intelligence graph operations over the universal code graph (W15, SUB-130).

Companion to :mod:`know_your_context.analytics.paths` (SUB-74): pure,
deterministic, store-agnostic operations with typed errors, canonical
ordering and honest guards. The input is a small frozen snapshot of the
code graph — :class:`CodeGraph` — built from plain mappings by
:func:`build_code_graph` (the CLI/webview adapters in
:mod:`know_your_context.ops.codeintel_cli` lift rows out of the v3
``symbols``/``graph_edges`` tables with read-only SQL, so this module never
touches a database).

Operations:

* ``callers_of`` / ``callees_of`` — transitive, depth-bounded reverse/forward
  closure over the dependency edge kinds (``calls``/``imports``/
  ``references`` by default). Entries are sorted by
  ``(depth, path, line, col, node_id)``.
* ``dependency_path`` — the shortest directed dependency path between two
  symbols; among equal-length paths the lexicographically smallest node-id
  sequence wins, so the result never depends on input ordering.
* ``impact_analysis`` — everything that breaks when a node changes: the
  reverse transitive closure over dependency kinds **plus** the
  ``contains`` ancestor chain (the file/module that owns the node),
  bucketed by depth with fan-out counts.
* ``co_change`` — files that change together, computed from explicit
  change-set history (each change set = the paths touched by one commit /
  ingestion batch). Reports pair support and directional confidence as
  exact decimals.
* ``boundary_violations`` — cross-layer edges: nodes are assigned to layers
  by path prefix; every edge whose (source layer → target layer) flow is
  not explicitly allowed is a violation. Unlayered endpoints are counted,
  never silently treated as violations.

Determinism contract: every operation is a pure function of its inputs —
no clocks, no randomness, no set/dict iteration order leaks (all
collections are sorted before emission). Fail closed: unknown symbols,
ambiguous names, out-of-range knobs and malformed policy raise typed
errors before any traversal runs.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from know_your_context.analytics.algorithms import SCHEMA_VERSION, AlgorithmError

__all__ = [
    "DEFAULT_CONFIDENCE_PLACES",
    "DEFAULT_DEPTH",
    "DEFAULT_LIMIT",
    "DEFAULT_TRAVERSAL_KINDS",
    "DEPTH_BOUNDS",
    "LIMIT_BOUNDS",
    "AmbiguousSymbolError",
    "CodeEdge",
    "CodeGraph",
    "CodeIntelError",
    "CodeNode",
    "InvalidPolicyError",
    "UnknownSymbolError",
    "build_code_graph",
    "boundary_violations",
    "callees_of",
    "callers_of",
    "co_change",
    "dependency_path",
    "impact_analysis",
    "resolve_symbol",
]

#: Edge kinds the traversal ops follow by default (mirrors
#: ``retrieval_v2.hierarchy.DEFAULT_EDGE_KINDS``).
DEFAULT_TRAVERSAL_KINDS: tuple[str, ...] = ("calls", "imports", "references")

#: Impact additionally walks the ``contains`` ancestor chain: the file that
#: owns a changed symbol is always impacted.
_IMPACT_KINDS: tuple[str, ...] = ("calls", "imports", "references", "contains")

DEFAULT_DEPTH = 8
DEPTH_BOUNDS = (1, 32)
DEFAULT_LIMIT = 200
LIMIT_BOUNDS = (1, 5000)
DEFAULT_CONFIDENCE_PLACES = 12


class CodeIntelError(AlgorithmError):
    """Base class for typed code-intelligence failures (fail-closed)."""


class UnknownSymbolError(CodeIntelError):
    """The queried symbol matches no node in the snapshot."""


class AmbiguousSymbolError(CodeIntelError):
    """The queried name matches several distinct nodes; disambiguate."""

    def __init__(self, symbol: str, candidates: tuple[CodeNode, ...]) -> None:
        self.symbol = symbol
        self.candidates = candidates
        rendered = "; ".join(f"{c.name} ({c.kind}) at {c.path}:{c.line}" for c in candidates[:10])
        super().__init__(f"ambiguous symbol {symbol!r}: {len(candidates)} candidates — {rendered}")


class InvalidPolicyError(CodeIntelError):
    """A layering/traversal policy is malformed (fail-closed)."""


@dataclass(frozen=True)
class CodeNode:
    """One symbol/file node of the code graph snapshot."""

    node_id: str
    name: str
    kind: str
    path: str
    line: int
    col: int


@dataclass(frozen=True)
class CodeEdge:
    """One directed edge of the code graph snapshot."""

    source: str
    target: str
    kind: str
    line: int


class CodeGraph:
    """Frozen, canonicalized code-graph snapshot with indexed adjacency.

    Construction sorts and dedupes: the result is a pure function of the
    input *content*, never of its ordering. Edges with unknown endpoints or
    self-loops are dropped and counted (honest guards, never silent).
    """

    __slots__ = ("dropped_edges", "edges", "nodes", "_by_id", "_by_name", "_forward", "_reverse")

    def __init__(self, nodes: tuple[CodeNode, ...], edges: tuple[CodeEdge, ...], dropped_edges: int) -> None:
        self.nodes = nodes
        self.edges = edges
        self.dropped_edges = dropped_edges
        self._by_id = {node.node_id: node for node in nodes}
        by_name: dict[str, list[CodeNode]] = {}
        for node in nodes:
            by_name.setdefault(node.name.lower(), []).append(node)
        self._by_name = {name: tuple(sorted(group, key=_node_sort_key)) for name, group in by_name.items()}
        forward: dict[str, set[tuple[str, str, int]]] = {node.node_id: set() for node in nodes}
        reverse: dict[str, set[tuple[str, str, int]]] = {node.node_id: set() for node in nodes}
        for edge in edges:
            forward[edge.source].add((edge.target, edge.kind, edge.line))
            reverse[edge.target].add((edge.source, edge.kind, edge.line))
        self._forward = {key: tuple(sorted(value)) for key, value in forward.items()}
        self._reverse = {key: tuple(sorted(value)) for key, value in reverse.items()}

    def node(self, node_id: str) -> CodeNode | None:
        return self._by_id.get(node_id)

    def named(self, name: str) -> tuple[CodeNode, ...]:
        return self._by_name.get(name.lower(), ())

    def neighbours(
        self,
        node_id: str,
        direction: Literal["forward", "reverse"],
        kinds: frozenset[str],
    ) -> tuple[tuple[str, str, int], ...]:
        """Sorted ``(other_id, edge_kind, edge_line)`` triples, kind-filtered."""
        table = self._forward if direction == "forward" else self._reverse
        return tuple(entry for entry in table.get(node_id, ()) if entry[1] in kinds)


def _node_sort_key(node: CodeNode) -> tuple[str, int, int, str]:
    return (node.path, node.line, node.col, node.node_id)


def build_code_graph(
    nodes: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
) -> CodeGraph:
    """Canonicalize raw node/edge mappings into a :class:`CodeGraph`.

    Malformed rows, duplicate node ids, edges with unknown endpoints and
    self-loops are dropped and counted in ``dropped_edges`` — never fatal,
    never silent.
    """
    unique_nodes: dict[str, CodeNode] = {}
    for row in nodes:
        node_id = row.get("node_id")
        name = row.get("name")
        kind = row.get("kind")
        path = row.get("path")
        if not (
            isinstance(node_id, str)
            and node_id
            and isinstance(name, str)
            and isinstance(kind, str)
            and isinstance(path, str)
        ):
            continue
        if node_id in unique_nodes:
            continue
        unique_nodes[node_id] = CodeNode(
            node_id=node_id,
            name=name,
            kind=kind,
            path=path,
            line=_as_int(row.get("line")),
            col=_as_int(row.get("col")),
        )
    ordered_nodes = tuple(sorted(unique_nodes.values(), key=_node_sort_key))
    dropped = 0
    seen: set[tuple[str, str, str, int]] = set()
    unique_edges: list[CodeEdge] = []
    for row in edges:
        source, target, kind = row.get("source"), row.get("target"), row.get("kind")
        if not (isinstance(source, str) and isinstance(target, str) and isinstance(kind, str) and kind):
            dropped += 1
            continue
        if source not in unique_nodes or target not in unique_nodes or source == target:
            dropped += 1
            continue
        line = _as_int(row.get("line"))
        key = (source, target, kind, line)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        unique_edges.append(CodeEdge(source=source, target=target, kind=kind, line=line))
    ordered_edges = tuple(sorted(unique_edges, key=lambda edge: (edge.source, edge.target, edge.kind, edge.line)))
    return CodeGraph(ordered_nodes, ordered_edges, dropped)


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _validate_kinds(kinds: Iterable[str]) -> frozenset[str]:
    collected = tuple(kinds)
    if not collected:
        raise InvalidPolicyError("at least one edge kind is required")
    for kind in collected:
        if not isinstance(kind, str) or not kind or not kind.replace("_", "").isalnum() or not kind.islower():
            raise InvalidPolicyError(f"edge kind must be a lowercase identifier, got {kind!r}")
    return frozenset(collected)


def _validate_knobs(max_depth: int, max_results: int) -> None:
    depth_ok = (
        isinstance(max_depth, int)
        and not isinstance(max_depth, bool)
        and DEPTH_BOUNDS[0] <= max_depth <= DEPTH_BOUNDS[1]
    )
    if not depth_ok:
        raise InvalidPolicyError(
            f"max_depth must be an int in [{DEPTH_BOUNDS[0]}, {DEPTH_BOUNDS[1]}], got {max_depth!r}"
        )
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not (LIMIT_BOUNDS[0] <= max_results <= LIMIT_BOUNDS[1])
    ):
        raise InvalidPolicyError(
            f"max_results must be an int in [{LIMIT_BOUNDS[0]}, {LIMIT_BOUNDS[1]}], got {max_results!r}"
        )


def resolve_symbol(graph: CodeGraph, symbol: str, *, file: str | None = None) -> CodeNode:
    """Resolve a node id or (case-insensitive) name to exactly one node.

    Node-id match wins; otherwise the name must match exactly one node
    (after the optional ``file`` filter). Zero matches →
    :class:`UnknownSymbolError`; several → :class:`AmbiguousSymbolError`.
    """
    if not isinstance(symbol, str) or not symbol:
        raise UnknownSymbolError("symbol must be a non-empty string")
    by_id = graph.node(symbol)
    if by_id is not None:
        if file is not None and by_id.path != file:
            raise UnknownSymbolError(f"node {symbol!r} is not in file {file!r}")
        return by_id
    candidates: tuple[CodeNode, ...] = graph.named(symbol)
    if file is not None:
        candidates = tuple(node for node in candidates if node.path == file)
    if not candidates:
        raise UnknownSymbolError(f"unknown symbol: {symbol!r}")
    if len(candidates) > 1:
        raise AmbiguousSymbolError(symbol, candidates)
    return next(iter(candidates))


def _entry(graph: CodeGraph, node_id: str, depth: int, via_kind: str, via_line: int, from_id: str) -> dict[str, Any]:
    node = graph.node(node_id)
    assert node is not None  # adjacency only contains known endpoints
    return {
        "node_id": node.node_id,
        "name": node.name,
        "kind": node.kind,
        "path": node.path,
        "line": node.line,
        "col": node.col,
        "depth": depth,
        "via_edge_kind": via_kind,
        "via_line": via_line,
        "from_node_id": from_id,
    }


def _transitive(
    graph: CodeGraph,
    root: CodeNode,
    direction: Literal["forward", "reverse"],
    kinds: frozenset[str],
    *,
    max_depth: int,
    max_results: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """BFS closure; entries sorted by (depth, path, line, col, node_id)."""
    visited = {root.node_id}
    frontier = (root.node_id,)
    entries: list[dict[str, Any]] = []
    for depth in range(1, max_depth + 1):
        next_frontier: list[str] = []
        for node_id in frontier:
            for other, kind, line in graph.neighbours(node_id, direction, kinds):
                if other in visited:
                    continue
                visited.add(other)
                entries.append(_entry(graph, other, depth, kind, line, node_id))
                next_frontier.append(other)
        frontier = tuple(next_frontier)
        if not frontier:
            break
    entries.sort(key=lambda item: (item["depth"], item["path"], item["line"], item["col"], item["node_id"]))
    total = len(entries)
    return entries[:max_results], total, total > max_results


def _root_payload(node: CodeNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "name": node.name,
        "kind": node.kind,
        "path": node.path,
        "line": node.line,
        "col": node.col,
    }


def callers_of(
    graph: CodeGraph,
    symbol: str,
    *,
    kinds: Iterable[str] = DEFAULT_TRAVERSAL_KINDS,
    max_depth: int = DEFAULT_DEPTH,
    max_results: int = DEFAULT_LIMIT,
    file: str | None = None,
) -> dict[str, Any]:
    """Transitive callers: everyone who (transitively) depends on ``symbol``."""
    return _directional(
        graph, symbol, "reverse", kinds, max_depth=max_depth, max_results=max_results, file=file, op="callers_of"
    )


def callees_of(
    graph: CodeGraph,
    symbol: str,
    *,
    kinds: Iterable[str] = DEFAULT_TRAVERSAL_KINDS,
    max_depth: int = DEFAULT_DEPTH,
    max_results: int = DEFAULT_LIMIT,
    file: str | None = None,
) -> dict[str, Any]:
    """Transitive callees: everything ``symbol`` (transitively) depends on."""
    return _transitive_op(
        graph, symbol, "forward", kinds, max_depth=max_depth, max_results=max_results, file=file, op="callees_of"
    )


def _directional(
    graph: CodeGraph,
    symbol: str,
    direction: Literal["forward", "reverse"],
    kinds: Iterable[str],
    *,
    max_depth: int,
    max_results: int,
    file: str | None,
    op: str,
) -> dict[str, Any]:
    _validate_knobs(max_depth, max_results)
    kind_set = _validate_kinds(kinds)
    root = resolve_symbol(graph, symbol, file=file)
    entries, total, truncated = _transitive(
        graph, root, direction, kind_set, max_depth=max_depth, max_results=max_results
    )
    guards = ["max_results_reached"] if truncated else []
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": f"analytics.code_intel.{op}",
        "parameters": {
            "symbol": symbol,
            "direction": direction,
            "kinds": sorted(kind_set),
            "max_depth": max_depth,
            "max_results": max_results,
            "file": file,
        },
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "root": _root_payload(root),
        "entry_count": len(entries),
        "total": total,
        "entries": entries,
        "guards": guards,
    }


# Backwards-compatible internal alias (kept explicit to aid grepping).
_transitive_op = _directional


def dependency_path(
    graph: CodeGraph,
    source: str,
    target: str,
    *,
    kinds: Iterable[str] = DEFAULT_TRAVERSAL_KINDS,
    max_expansions: int = 65536,
) -> dict[str, Any]:
    """The shortest directed dependency path ``source → target``.

    Among equal-length paths the lexicographically smallest node-id
    sequence wins (uniform-cost search keyed by ``(cost, path)``), so the
    answer is a pure function of the graph content. No path is a **valid
    result** (``path_count: 0``), not an error — only unknown endpoints or
    ``source == target`` fail closed.
    """
    kind_set = _validate_kinds(kinds)
    if isinstance(max_expansions, bool) or not isinstance(max_expansions, int) or max_expansions < 1:
        raise InvalidPolicyError(f"max_expansions must be a positive int, got {max_expansions!r}")
    origin = resolve_symbol(graph, source)
    destination = resolve_symbol(graph, target)
    if origin.node_id == destination.node_id:
        raise CodeIntelError("source and target resolve to the same node")

    budget = max_expansions
    exhausted = False
    heap: list[tuple[int, tuple[str, ...]]] = [(0, (origin.node_id,))]
    best: tuple[str, ...] | None = None
    while heap:
        cost, path = heapq.heappop(heap)
        if budget <= 0:
            exhausted = True
            break
        budget -= 1
        node_id = path[-1]
        if node_id == destination.node_id:
            best = path
            break
        for other, _kind, _line in graph.neighbours(node_id, "forward", kind_set):
            if other in path:
                continue
            heapq.heappush(heap, (cost + 1, (*path, other)))

    guards = ["max_expansions_reached"] if exhausted else []
    paths: list[dict[str, Any]] = []
    if best is not None:
        path_nodes = [node for node_id in best if (node := graph.node(node_id)) is not None]
        paths.append(
            {
                "rank": 0,
                "cost": len(best) - 1,
                "nodes": [_root_payload(node) for node in path_nodes],
                "node_ids": list(best),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "analytics.code_intel.dependency_path",
        "parameters": {
            "source": source,
            "target": target,
            "kinds": sorted(kind_set),
            "max_expansions": max_expansions,
        },
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "source": _root_payload(origin),
        "target": _root_payload(destination),
        "path_count": len(paths),
        "paths": paths,
        "guards": guards,
    }


def impact_analysis(
    graph: CodeGraph,
    symbol: str,
    *,
    max_depth: int = DEFAULT_DEPTH,
    max_results: int = 500,
    file: str | None = None,
) -> dict[str, Any]:
    """What breaks when ``symbol`` changes: reverse closure + owner chain.

    Follows ``calls``/``imports``/``references`` in reverse (dependents)
    and ``contains`` in reverse (the owning file/module chain). Entries are
    bucketed per depth with fan-out counts; ``direct_dependents`` is the
    depth-1 count.
    """
    _validate_knobs(max_depth, max_results)
    root = resolve_symbol(graph, symbol, file=file)
    kind_set = frozenset(_IMPACT_KINDS)
    entries, total, truncated = _transitive(
        graph, root, "reverse", kind_set, max_depth=max_depth, max_results=max_results
    )
    depth_buckets: dict[int, int] = {}
    for entry in entries:
        depth_buckets[entry["depth"]] = depth_buckets.get(entry["depth"], 0) + 1
    by_kind: dict[str, int] = {}
    for entry in entries:
        by_kind[entry["via_edge_kind"]] = by_kind.get(entry["via_edge_kind"], 0) + 1
    impacted_files = sorted({entry["path"] for entry in entries})
    guards = ["max_results_reached"] if truncated else []
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "analytics.code_intel.impact_analysis",
        "parameters": {"symbol": symbol, "max_depth": max_depth, "max_results": max_results, "file": file},
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "root": _root_payload(root),
        "direct_dependents": depth_buckets.get(1, 0),
        "total_impacted": total,
        "depth_histogram": {str(depth): depth_buckets[depth] for depth in sorted(depth_buckets)},
        "via_kind_counts": {kind: by_kind[kind] for kind in sorted(by_kind)},
        "impacted_files": impacted_files,
        "impacted_file_count": len(impacted_files),
        "entry_count": len(entries),
        "entries": entries,
        "guards": guards,
    }


def co_change(
    change_sets: Iterable[Iterable[str]],
    *,
    min_support: int = 2,
    limit: int = 100,
    min_confidence: Decimal | str | int = Decimal(0),
    places: int = DEFAULT_CONFIDENCE_PLACES,
) -> dict[str, Any]:
    """Files that change together, from explicit change-set history.

    Each change set is the collection of paths touched by one commit /
    ingestion batch. Sets are normalized (deduped, sorted, non-string
    entries dropped); sets smaller than two files cannot evidence
    co-change and are counted under ``discarded_sets``. Pair support is
    the number of sets containing both files; ``confidence_a_to_b`` is
    ``support / sets_containing_a`` as an exact decimal string.
    """
    if isinstance(min_support, bool) or not isinstance(min_support, int) or min_support < 1:
        raise InvalidPolicyError(f"min_support must be a positive int, got {min_support!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= LIMIT_BOUNDS[1]):
        raise InvalidPolicyError(f"limit must be an int in [1, {LIMIT_BOUNDS[1]}], got {limit!r}")
    threshold = Decimal(str(min_confidence))
    if not (Decimal(0) <= threshold <= Decimal(1)):
        raise InvalidPolicyError(f"min_confidence must be in [0, 1], got {min_confidence!r}")
    if isinstance(places, bool) or not isinstance(places, int) or not (0 <= places <= 30):
        raise InvalidPolicyError(f"places must be an int in [0, 30], got {places!r}")

    set_count = 0
    discarded = 0
    pair_support: dict[tuple[str, str], int] = {}
    file_support: dict[str, int] = {}
    for raw_set in change_sets:
        set_count += 1
        paths = sorted({path for path in raw_set if isinstance(path, str) and path})
        if len(paths) < 2:
            discarded += 1
            continue
        for path in paths:
            file_support[path] = file_support.get(path, 0) + 1
        for index, left in enumerate(paths):
            for right in paths[index + 1 :]:
                key = (left, right)
                pair_support[key] = pair_support.get(key, 0) + 1

    quantum = Decimal(1).scaleb(-places)
    pairs: list[dict[str, Any]] = []
    below_confidence = 0
    below_support = 0
    for (left, right), support in pair_support.items():
        if support < min_support:
            below_support += 1
            continue
        confidence_left = (Decimal(support) / Decimal(file_support[left])).quantize(quantum)
        confidence_right = (Decimal(support) / Decimal(file_support[right])).quantize(quantum)
        if confidence_left < threshold and confidence_right < threshold:
            below_confidence += 1
            continue
        pairs.append(
            {
                "file_a": left,
                "file_b": right,
                "support": support,
                "sets_containing_a": file_support[left],
                "sets_containing_b": file_support[right],
                "confidence_a_to_b": f"{confidence_left:.{places}f}",
                "confidence_b_to_a": f"{confidence_right:.{places}f}",
            }
        )
    pairs.sort(key=lambda item: (-item["support"], item["file_a"], item["file_b"]))
    total = len(pairs)
    emitted = pairs[:limit]
    truncated = total > limit
    guards = ["limit_reached"] if truncated else []
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "analytics.code_intel.co_change",
        "parameters": {
            "min_support": min_support,
            "limit": limit,
            "min_confidence": f"{threshold:.{places}f}",
            "places": places,
        },
        "change_set_count": set_count,
        "discarded_sets": discarded,
        "file_count": len(file_support),
        "pair_count": len(emitted),
        "total_pairs": total,
        "below_support_pairs": below_support,
        "below_confidence_pairs": below_confidence,
        "pairs": emitted,
        "guards": guards,
    }


def boundary_violations(
    graph: CodeGraph,
    *,
    layers: Mapping[str, Iterable[str]],
    allowed_flows: Iterable[tuple[str, str]] = (),
    edge_kinds: Iterable[str] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Cross-layer edges that the policy does not explicitly allow.

    ``layers`` maps a layer name to path prefixes (POSIX-style). A node's
    layer is its longest matching prefix; ties break by sorted layer name.
    ``allowed_flows`` lists permitted ``(from_layer, to_layer)`` pairs.
    Every edge whose endpoints live in *different* layers and whose flow
    is not listed is a violation. Edges touching unlayered nodes are
    skipped and counted — never silently judged.
    """
    if not layers:
        raise InvalidPolicyError("at least one layer is required")
    compiled: list[tuple[str, str]] = []  # (layer, normalized prefix)
    for name, prefixes in layers.items():
        if not isinstance(name, str) or not name:
            raise InvalidPolicyError(f"layer name must be a non-empty string, got {name!r}")
        prefix_tuple = tuple(prefixes)
        if not prefix_tuple:
            raise InvalidPolicyError(f"layer {name!r} needs at least one path prefix")
        for prefix in prefix_tuple:
            if not isinstance(prefix, str) or not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
                raise InvalidPolicyError(f"layer {name!r} prefix must be a relative POSIX path, got {prefix!r}")
            compiled.append((name, prefix.rstrip("/")))
    allowed: set[tuple[str, str]] = set()
    layer_names = set(layers)
    for flow in allowed_flows:
        if not (isinstance(flow, tuple) and len(flow) == 2 and all(isinstance(part, str) for part in flow)):
            raise InvalidPolicyError(f"allowed flow must be a (from, to) pair of layer names, got {flow!r}")
        unknown = [part for part in flow if part not in layer_names]
        if unknown:
            raise InvalidPolicyError(f"allowed flow references unknown layers: {unknown!r}")
        allowed.add((flow[0], flow[1]))
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= LIMIT_BOUNDS[1]):
        raise InvalidPolicyError(f"limit must be an int in [1, {LIMIT_BOUNDS[1]}], got {limit!r}")
    kind_filter = None if edge_kinds is None else _validate_kinds(edge_kinds)

    def _layer_of(path: str) -> str | None:
        best: tuple[int, str] | None = None
        for name, prefix in compiled:
            if path == prefix or path.startswith(prefix + "/"):
                candidate = (len(prefix), name)
                if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and name < best[1]):
                    best = candidate
        return None if best is None else best[1]

    node_layers: dict[str, str | None] = {node.node_id: _layer_of(node.path) for node in graph.nodes}
    unlayered_nodes = sum(1 for layer in node_layers.values() if layer is None)

    violations: list[dict[str, Any]] = []
    flow_counts: dict[tuple[str, str], int] = {}
    skipped_unlayered = 0
    for edge in graph.edges:
        if kind_filter is not None and edge.kind not in kind_filter:
            continue
        from_layer = node_layers.get(edge.source)
        to_layer = node_layers.get(edge.target)
        if from_layer is None or to_layer is None:
            skipped_unlayered += 1
            continue
        if from_layer == to_layer:
            continue
        if (from_layer, to_layer) in allowed:
            continue
        flow_counts[(from_layer, to_layer)] = flow_counts.get((from_layer, to_layer), 0) + 1
        source_node = graph.node(edge.source)
        target_node = graph.node(edge.target)
        violations.append(
            {
                "source": edge.source,
                "target": edge.target,
                "edge_kind": edge.kind,
                "line": edge.line,
                "from_layer": from_layer,
                "to_layer": to_layer,
                "source_path": source_node.path if source_node else "",
                "target_path": target_node.path if target_node else "",
            }
        )
    violations.sort(key=lambda item: (item["source"], item["target"], item["edge_kind"], item["line"]))
    total = len(violations)
    emitted = violations[:limit]
    truncated = total > limit
    guards = ["limit_reached"] if truncated else []
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "analytics.code_intel.boundary_violations",
        "parameters": {
            "layers": {name: sorted(layers[name]) for name in sorted(layers)},
            "allowed_flows": sorted(allowed),
            "edge_kinds": None if kind_filter is None else sorted(kind_filter),
            "limit": limit,
        },
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "unlayered_nodes": unlayered_nodes,
        "skipped_unlayered_edges": skipped_unlayered,
        "violation_count": len(emitted),
        "total_violations": total,
        "flow_summary": [
            {"from_layer": left, "to_layer": right, "count": flow_counts[(left, right)]}
            for (left, right) in sorted(flow_counts)
        ],
        "violations": emitted,
        "guards": guards,
    }
