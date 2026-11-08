"""WS-D / TASK2-PLAN B4: containment hierarchy queries must be index-backed.

Baseline (cluster-b verdict, B4): ``propose_primary_splits`` consumed 92% of
ingest wall (134.4s of 145.4s) because ``contains_children`` /
``contains_parent`` scanned the ENTIRE edge mapping per call — 10.859 calls
x full scan = 56.8M enum ``.value`` accesses for a single ingest batch.

Pins (deterministic, host-independent — no wall-clock assertions):

* semantics unchanged: the hierarchy accessors agree with a brute-force
  scan over ``graph.edges()`` on a mixed fixture graph (contains forest +
  bridges + references), for every node;
* bounded work: ``propose_primary_splits`` iterates the edge mapping O(1)
  times per edge overall, not O(E) per visited node — measured by counting
  ``_edges.values()`` materializations around the call.
"""

from __future__ import annotations

from know_your_context.domain import PrimaryDomain
from know_your_context.graph_model import (
    GraphEdge,
    GraphNode,
    SplitThresholds,
    UniversalGraph,
    file_node_id,
    make_node_id,
    propose_primary_splits,
)


def _node(kind: str, name: str, path: str, line: int | None = None, **kw: object) -> GraphNode:
    node_id = file_node_id(path) if kind == "file" else make_node_id(path=path, kind=kind, name=name, line=line)
    return GraphNode(
        node_id=node_id,
        kind=kind,
        name=name,
        path=path,
        line=line,
        **kw,  # type: ignore[arg-type]
    )


def _mixed_fixture() -> UniversalGraph:
    """Contains forest (3 levels) + a bridge + reference edges."""
    graph = UniversalGraph()
    root = _node("primary", "root", "", domain=PrimaryDomain.P1, purpose="fixture root")
    other = _node("primary", "other", "", domain=PrimaryDomain.P2, purpose="bridge peer")
    graph.add_node(root)
    graph.add_node(other)
    for index in range(6):
        child = _node("file", f"f{index}", f"pkg/f{index}.py")
        graph.add_node(child)
        graph.add_edge(GraphEdge(source=root.node_id, target=child.node_id, kind="contains"))
        for sub in range(3):
            leaf = _node("function", f"f{index}.g{sub}", f"pkg/f{index}.py", line=sub + 1)
            graph.add_node(leaf)
            graph.add_edge(GraphEdge(source=child.node_id, target=leaf.node_id, kind="contains"))
            if sub == 0:
                deeper = _node("method", f"f{index}.g0.h", f"pkg/f{index}.py", line=1)
                graph.add_node(deeper)
                graph.add_edge(GraphEdge(source=leaf.node_id, target=deeper.node_id, kind="contains"))
    graph.add_bridge(root.node_id, other.node_id, weight="0.5", reason="fixture")
    # non-hierarchy edges sharing endpoints must never leak into hierarchy answers
    leaves = [n for n in graph.nodes() if n.kind == "function"]
    graph.add_edge(GraphEdge(source=leaves[0].node_id, target=leaves[1].node_id, kind="references"))
    graph.add_edge(GraphEdge(source=leaves[1].node_id, target=leaves[0].node_id, kind="calls"))
    return graph


def _scan_children(graph: UniversalGraph, node_id: str) -> tuple[str, ...]:
    return tuple(sorted(edge.target for edge in graph.edges() if edge.kind == "contains" and edge.source == node_id))


def _scan_parent(graph: UniversalGraph, node_id: str) -> str | None:
    for edge in graph.edges():
        if edge.kind == "contains" and edge.target == node_id:
            return edge.source
    return None


def test_hierarchy_accessors_match_brute_force_scan() -> None:
    graph = _mixed_fixture()
    for node in graph.nodes():
        assert graph.contains_children(node.node_id) == _scan_children(graph, node.node_id)
        assert graph.contains_parent(node.node_id) == _scan_parent(graph, node.node_id)
        # subtree = closure over the brute-force children, BFS order
        order: list[str] = [node.node_id]
        queue = [node.node_id]
        seen = {node.node_id}
        while queue:
            current = queue.pop(0)
            for child in _scan_children(graph, current):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
                    order.append(child)
        assert graph.subtree_ids(node.node_id) == tuple(order)


class _CountingEdgeMap(dict):  # type: ignore[type-arg]
    """Edge mapping that counts how many edge objects get iterated."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.iterations = 0

    def values(self) -> list[GraphEdge]:  # type: ignore[override]
        values = list(super().values())
        self.iterations += len(values)
        return values


def test_propose_primary_splits_scans_edges_constant_times() -> None:
    """One oversized single-domain subtree, size-driven split path.

    Old behavior: ``subtree_ids`` + ``contains_children`` re-scanned the full
    edge mapping per visited node -> O(N x E) edge iterations (measured:
    56.8M enum accesses in one ingest batch). Required: O(E) overall.
    """
    graph = UniversalGraph()
    root = _node("primary", "root", "", domain=PrimaryDomain.P1, purpose="fixture root")
    graph.add_node(root)
    n_children = 60
    for index in range(n_children):
        child = _node("file", f"f{index}", f"pkg/f{index}.py")
        graph.add_node(child)
        graph.add_edge(GraphEdge(source=root.node_id, target=child.node_id, kind="contains"))
        for sub in range(4):
            leaf = _node("function", f"f{index}.g{sub}", f"pkg/f{index}.py", line=sub + 1)
            graph.add_node(leaf)
            graph.add_edge(GraphEdge(source=child.node_id, target=leaf.node_id, kind="contains"))
    edge_count = graph.edge_count
    counting = _CountingEdgeMap(graph._edges)  # noqa: SLF001 -- measuring internal scan behavior
    graph._edges = counting  # noqa: SLF001 -- swapped after construction; only the call below is measured
    proposals = propose_primary_splits(graph, root.node_id, SplitThresholds(max_subtree_nodes=10))
    assert proposals  # the split path must actually trigger (size-driven)
    # O(E) bound with generous constant; the old O(N x E) shape is ~300x over.
    assert counting.iterations <= 6 * edge_count, (
        f"propose_primary_splits iterated {counting.iterations} edge objects for "
        f"{edge_count} edges — containment must be indexed, not re-scanned per node"
    )
