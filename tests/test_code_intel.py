"""Tests for analytics.code_intel — deep code-intelligence graph ops (W15, SUB-130).

Covers: snapshot canonicalization, symbol resolution (typed errors),
transitive callers/callees, dependency_path, impact_analysis, co_change,
boundary_violations — each with determinism and fail-closed guards.
"""

from __future__ import annotations

import pytest

from know_your_context.analytics.code_intel import (
    AmbiguousSymbolError,
    CodeIntelError,
    InvalidPolicyError,
    UnknownSymbolError,
    boundary_violations,
    build_code_graph,
    callees_of,
    callers_of,
    co_change,
    dependency_path,
    impact_analysis,
    resolve_symbol,
)

# ------------------------------------------------------------------ helpers


def _node(
    node_id: str, name: str, kind: str = "function", path: str = "a.py", line: object = 1, col: object = 0
) -> dict:
    return {"node_id": node_id, "name": name, "kind": kind, "path": path, "line": line, "col": col}


def _edge(source: str, target: str, kind: str = "calls", line: int = 1) -> dict:
    return {"source": source, "target": target, "kind": kind, "line": line}


@pytest.fixture()
def chain_graph():
    """a -> b -> c (calls); f.py contains a; plus an isolated node d."""
    return build_code_graph(
        [
            _node("n:a", "alpha", path="src/f.py", line=1),
            _node("n:b", "beta", path="src/f.py", line=5),
            _node("n:c", "gamma", path="src/g.py", line=2),
            _node("n:d", "delta", path="src/h.py", line=1),
            _node("n:file", "f.py", kind="file", path="src/f.py", line=1),
        ],
        [
            _edge("n:a", "n:b", "calls", 3),
            _edge("n:b", "n:c", "calls", 7),
            _edge("n:file", "n:a", "contains", 1),
            _edge("n:file", "n:b", "contains", 5),
        ],
    )


# ------------------------------------------------------------------ build_code_graph


class TestBuildCodeGraph:
    def test_nodes_sorted_canonically(self):
        graph = build_code_graph([_node("n:b", "b", path="z.py"), _node("n:a", "a", path="a.py")], [])
        assert [node.node_id for node in graph.nodes] == ["n:a", "n:b"]

    def test_edges_sorted_and_deduped(self):
        graph = build_code_graph(
            [_node("n:a", "a"), _node("n:b", "b")],
            [_edge("n:b", "n:a"), _edge("n:a", "n:b"), _edge("n:a", "n:b")],
        )
        assert len(graph.edges) == 2
        assert graph.dropped_edges == 1

    def test_malformed_node_rows_dropped(self):
        graph = build_code_graph([{"node_id": ""}, {"name": "x"}, _node("n:ok", "ok")], [])
        assert [node.node_id for node in graph.nodes] == ["n:ok"]

    def test_unknown_endpoint_edges_dropped_and_counted(self):
        graph = build_code_graph([_node("n:a", "a")], [_edge("n:a", "n:ghost")])
        assert graph.edges == ()
        assert graph.dropped_edges == 1

    def test_self_loops_dropped(self):
        graph = build_code_graph([_node("n:a", "a")], [_edge("n:a", "n:a")])
        assert graph.edges == ()
        assert graph.dropped_edges == 1

    def test_non_string_kind_dropped(self):
        graph = build_code_graph([_node("n:a", "a"), _node("n:b", "b")], [{"source": "n:a", "target": "n:b"}])
        assert graph.dropped_edges == 1

    def test_input_order_irrelevant(self):
        nodes = [_node("n:a", "a"), _node("n:b", "b"), _node("n:c", "c")]
        edges = [_edge("n:a", "n:b"), _edge("n:b", "n:c")]
        first = build_code_graph(nodes, edges)
        second = build_code_graph(list(reversed(nodes)), list(reversed(edges)))
        assert first.nodes == second.nodes
        assert first.edges == second.edges

    def test_non_int_line_defaults_zero(self):
        graph = build_code_graph([_node("n:a", "a", line="x")], [])
        assert graph.nodes[0].line == 0

    def test_empty_graph(self):
        graph = build_code_graph([], [])
        assert graph.nodes == ()
        assert graph.edges == ()
        assert graph.dropped_edges == 0


# ------------------------------------------------------------------ resolve_symbol


class TestResolveSymbol:
    def test_by_node_id(self, chain_graph):
        assert resolve_symbol(chain_graph, "n:a").name == "alpha"

    def test_by_name_case_insensitive(self, chain_graph):
        assert resolve_symbol(chain_graph, "ALPHA").node_id == "n:a"

    def test_unknown_raises(self, chain_graph):
        with pytest.raises(UnknownSymbolError):
            resolve_symbol(chain_graph, "nope")

    def test_empty_symbol_raises(self, chain_graph):
        with pytest.raises(UnknownSymbolError):
            resolve_symbol(chain_graph, "")

    def test_ambiguous_raises_with_candidates(self):
        graph = build_code_graph(
            [_node("n:1", "dup", path="a.py", line=1), _node("n:2", "dup", path="b.py", line=1)],
            [],
        )
        with pytest.raises(AmbiguousSymbolError) as info:
            resolve_symbol(graph, "dup")
        assert len(info.value.candidates) == 2

    def test_file_filter_disambiguates(self):
        graph = build_code_graph(
            [_node("n:1", "dup", path="a.py", line=1), _node("n:2", "dup", path="b.py", line=1)],
            [],
        )
        assert resolve_symbol(graph, "dup", file="b.py").node_id == "n:2"

    def test_node_id_with_wrong_file_raises(self, chain_graph):
        with pytest.raises(UnknownSymbolError):
            resolve_symbol(chain_graph, "n:a", file="other.py")


# ------------------------------------------------------------------ callers_of / callees_of


class TestCallersCallees:
    def test_callers_transitive(self, chain_graph):
        result = callers_of(chain_graph, "gamma")
        names = [entry["name"] for entry in result["entries"]]
        assert names == ["beta", "alpha"]  # depth 1 first
        depths = {entry["name"]: entry["depth"] for entry in result["entries"]}
        assert depths == {"beta": 1, "alpha": 2}

    def test_callees_transitive(self, chain_graph):
        result = callees_of(chain_graph, "alpha")
        names = [entry["name"] for entry in result["entries"]]
        assert names == ["beta", "gamma"]

    def test_callers_of_root_is_empty(self, chain_graph):
        result = callers_of(chain_graph, "alpha")
        assert result["entries"] == []
        assert result["total"] == 0

    def test_cycle_terminates(self):
        graph = build_code_graph(
            [_node("n:a", "a"), _node("n:b", "b")],
            [_edge("n:a", "n:b"), _edge("n:b", "n:a")],
        )
        result = callees_of(graph, "a")
        assert [entry["name"] for entry in result["entries"]] == ["b"]

    def test_kind_filter(self, chain_graph):
        result = callees_of(chain_graph, "f.py", kinds=("contains",))
        names = {entry["name"] for entry in result["entries"]}
        assert names == {"alpha", "beta"}

    def test_max_depth_bounds(self, chain_graph):
        result = callees_of(chain_graph, "alpha", max_depth=1)
        assert [entry["name"] for entry in result["entries"]] == ["beta"]

    def test_max_depth_zero_rejected(self, chain_graph):
        with pytest.raises(InvalidPolicyError):
            callees_of(chain_graph, "alpha", max_depth=0)

    def test_max_depth_too_large_rejected(self, chain_graph):
        with pytest.raises(InvalidPolicyError):
            callees_of(chain_graph, "alpha", max_depth=33)

    def test_max_results_guard(self, chain_graph):
        result = callees_of(chain_graph, "alpha", max_results=1)
        assert result["entry_count"] == 1
        assert result["total"] == 2
        assert result["guards"] == ["max_results_reached"]

    def test_max_results_zero_rejected(self, chain_graph):
        with pytest.raises(InvalidPolicyError):
            callers_of(chain_graph, "gamma", max_results=0)

    def test_bool_knob_rejected(self, chain_graph):
        with pytest.raises(InvalidPolicyError):
            callees_of(chain_graph, "alpha", max_depth=True)

    def test_empty_kinds_rejected(self, chain_graph):
        with pytest.raises(InvalidPolicyError):
            callers_of(chain_graph, "gamma", kinds=())

    def test_bad_kind_rejected(self, chain_graph):
        with pytest.raises(InvalidPolicyError):
            callers_of(chain_graph, "gamma", kinds=("CALLS!",))

    def test_artifact_shape(self, chain_graph):
        result = callers_of(chain_graph, "gamma")
        assert result["schema_version"] == 1
        assert result["kind"] == "analytics.code_intel.callers_of"
        assert result["root"]["name"] == "gamma"
        assert result["node_count"] == 5
        assert result["parameters"]["direction"] == "reverse"

    def test_entries_sorted_by_depth_then_path(self):
        graph = build_code_graph(
            [
                _node("n:t", "target"),
                _node("n:1", "x", path="b.py", line=2),
                _node("n:2", "y", path="a.py", line=9),
            ],
            [_edge("n:1", "n:t"), _edge("n:2", "n:t")],
        )
        result = callers_of(graph, "target")
        assert [entry["path"] for entry in result["entries"]] == ["a.py", "b.py"]

    def test_via_edge_recorded(self, chain_graph):
        result = callers_of(chain_graph, "gamma")
        beta = next(entry for entry in result["entries"] if entry["name"] == "beta")
        assert beta["via_edge_kind"] == "calls"
        assert beta["via_line"] == 7
        assert beta["from_node_id"] == "n:c"

    def test_determinism_repeated_runs(self, chain_graph):
        first = callers_of(chain_graph, "gamma")
        second = callers_of(chain_graph, "gamma")
        assert first == second

    def test_unknown_symbol_raises(self, chain_graph):
        with pytest.raises(UnknownSymbolError):
            callers_of(chain_graph, "ghost")


# ------------------------------------------------------------------ dependency_path


class TestDependencyPath:
    def test_direct_path(self, chain_graph):
        result = dependency_path(chain_graph, "alpha", "beta")
        assert result["path_count"] == 1
        assert result["paths"][0]["node_ids"] == ["n:a", "n:b"]
        assert result["paths"][0]["cost"] == 1

    def test_transitive_path(self, chain_graph):
        result = dependency_path(chain_graph, "alpha", "gamma")
        assert result["paths"][0]["node_ids"] == ["n:a", "n:b", "n:c"]

    def test_no_path_is_valid_result(self, chain_graph):
        result = dependency_path(chain_graph, "gamma", "alpha")
        assert result["path_count"] == 0
        assert result["paths"] == []
        assert result["guards"] == []

    def test_same_node_rejected(self, chain_graph):
        with pytest.raises(CodeIntelError):
            dependency_path(chain_graph, "alpha", "alpha")

    def test_unknown_source_rejected(self, chain_graph):
        with pytest.raises(UnknownSymbolError):
            dependency_path(chain_graph, "ghost", "beta")

    def test_unknown_target_rejected(self, chain_graph):
        with pytest.raises(UnknownSymbolError):
            dependency_path(chain_graph, "alpha", "ghost")

    def test_lexicographic_tie_break(self):
        graph = build_code_graph(
            [_node("n:s", "s"), _node("n:t", "t"), _node("n:m", "m"), _node("n:z", "z")],
            [_edge("n:s", "n:z"), _edge("n:s", "n:m"), _edge("n:m", "n:t"), _edge("n:z", "n:t")],
        )
        result = dependency_path(graph, "s", "t")
        assert result["paths"][0]["node_ids"] == ["n:s", "n:m", "n:t"]

    def test_kind_filter_excludes(self, chain_graph):
        result = dependency_path(chain_graph, "alpha", "gamma", kinds=("imports",))
        assert result["path_count"] == 0

    def test_expansion_budget_guard(self):
        # Long chain so a tiny budget fires.
        nodes = [_node(f"n:{index}", f"f{index}") for index in range(50)]
        edges = [_edge(f"n:{index}", f"n:{index + 1}") for index in range(49)]
        graph = build_code_graph(nodes, edges)
        result = dependency_path(graph, "f0", "f49", max_expansions=3)
        assert "max_expansions_reached" in result["guards"]

    def test_max_expansions_validation(self, chain_graph):
        with pytest.raises(InvalidPolicyError):
            dependency_path(chain_graph, "alpha", "beta", max_expansions=0)

    def test_artifact_shape(self, chain_graph):
        result = dependency_path(chain_graph, "alpha", "gamma")
        assert result["kind"] == "analytics.code_intel.dependency_path"
        assert result["source"]["node_id"] == "n:a"
        assert result["target"]["node_id"] == "n:c"
        nodes = result["paths"][0]["nodes"]
        assert nodes[0]["name"] == "alpha" and nodes[-1]["name"] == "gamma"


# ------------------------------------------------------------------ impact_analysis


class TestImpactAnalysis:
    def test_reverse_closure(self, chain_graph):
        result = impact_analysis(chain_graph, "gamma")
        names = {entry["name"] for entry in result["entries"]}
        assert {"beta", "alpha"} <= names

    def test_contains_ancestors_included(self, chain_graph):
        result = impact_analysis(chain_graph, "beta")
        names = {entry["name"] for entry in result["entries"]}
        assert "f.py" in names  # the owning file is impacted

    def test_direct_dependents_count(self, chain_graph):
        result = impact_analysis(chain_graph, "gamma")
        assert result["direct_dependents"] == 1
        assert result["total_impacted"] == 3  # beta (d1) + alpha, f.py (d2)

    def test_depth_histogram(self, chain_graph):
        result = impact_analysis(chain_graph, "gamma")
        assert result["depth_histogram"] == {"1": 1, "2": 2}

    def test_via_kind_counts(self, chain_graph):
        result = impact_analysis(chain_graph, "beta")
        assert result["via_kind_counts"].get("contains") == 1
        assert result["via_kind_counts"].get("calls") == 1

    def test_impacted_files(self, chain_graph):
        result = impact_analysis(chain_graph, "gamma")
        assert result["impacted_files"] == ["src/f.py"]
        assert result["impacted_file_count"] == 1

    def test_leaf_impact_is_empty(self, chain_graph):
        result = impact_analysis(chain_graph, "delta")
        assert result["total_impacted"] == 0

    def test_guard_on_truncation(self, chain_graph):
        result = impact_analysis(chain_graph, "gamma", max_results=1)
        assert result["guards"] == ["max_results_reached"]
        assert result["entry_count"] == 1

    def test_artifact_shape(self, chain_graph):
        result = impact_analysis(chain_graph, "gamma")
        assert result["kind"] == "analytics.code_intel.impact_analysis"
        assert result["root"]["node_id"] == "n:c"

    def test_unknown_symbol_raises(self, chain_graph):
        with pytest.raises(UnknownSymbolError):
            impact_analysis(chain_graph, "ghost")


# ------------------------------------------------------------------ co_change


class TestCoChange:
    def test_basic_pair(self):
        result = co_change([["a.py", "b.py"], ["b.py", "a.py"], ["a.py", "b.py"], ["a.py", "c.py"]])
        assert result["pair_count"] == 1
        pair = result["pairs"][0]
        assert (pair["file_a"], pair["file_b"]) == ("a.py", "b.py")
        assert pair["support"] == 3

    def test_confidence_directional(self):
        result = co_change([["a", "b"], ["a", "b"], ["a", "c"], ["b", "d"], ["b", "d"]])
        pair = next(p for p in result["pairs"] if {p["file_a"], p["file_b"]} == {"a", "b"})
        assert pair["confidence_a_to_b"] == "0.666666666667"  # ab=2, a in 3 sets
        assert pair["confidence_b_to_a"] == "0.500000000000"  # ab=2, b in 4 sets

    def test_confidence_asymmetric(self):
        result = co_change([["a", "b"], ["a", "b"], ["a", "c"]])
        pair = result["pairs"][0]
        assert pair["confidence_a_to_b"] == "0.666666666667"  # a seen 3x
        assert pair["confidence_b_to_a"] == "1.000000000000"  # b seen 2x

    def test_min_support_filters(self):
        result = co_change([["a", "b"], ["a", "c"]], min_support=2)
        assert result["pair_count"] == 0
        assert result["below_support_pairs"] == 2

    def test_min_support_one_keeps_singletons_pairs(self):
        result = co_change([["a", "b"]], min_support=1)
        assert result["pair_count"] == 1

    def test_singleton_sets_discarded(self):
        result = co_change([["a"], ["a", "b"], ["a", "b"]])
        assert result["discarded_sets"] == 1
        assert result["change_set_count"] == 3

    def test_duplicate_paths_within_set_deduped(self):
        result = co_change([["a", "a", "b"], ["a", "b"]])
        assert result["pair_count"] == 1
        assert result["pairs"][0]["support"] == 2

    def test_min_confidence_filters(self):
        result = co_change([["a", "b"], ["a", "c"], ["a", "c"], ["a", "c"]], min_support=1, min_confidence="0.9")
        # a-b confidence 0.25 both ways? a:4 sets, ab support 1 -> 0.25/1.0 b side 1/1=1.0 keeps
        pairs = {(p["file_a"], p["file_b"]) for p in result["pairs"]}
        assert ("a", "b") in pairs  # b->a confidence is 1.0

    def test_min_confidence_out_of_range(self):
        with pytest.raises(InvalidPolicyError):
            co_change([], min_confidence="1.5")

    def test_min_support_validation(self):
        with pytest.raises(InvalidPolicyError):
            co_change([], min_support=0)

    def test_limit_guard(self):
        sets = [[f"f{i}.py", f"f{i + 1}.py", f"f{i + 2}.py"] for i in range(10)]
        result = co_change(sets, min_support=1, limit=3)
        assert result["pair_count"] == 3
        assert result["total_pairs"] > 3
        assert result["guards"] == ["limit_reached"]

    def test_sorted_by_support_then_names(self):
        result = co_change(
            [["b", "c"], ["a", "b"], ["b", "c"], ["a", "b"], ["b", "c"]],
            min_support=1,
        )
        assert [(p["file_a"], p["file_b"]) for p in result["pairs"]] == [("b", "c"), ("a", "b")]

    def test_empty_history(self):
        result = co_change([])
        assert result["pair_count"] == 0
        assert result["change_set_count"] == 0

    def test_non_string_entries_dropped(self):
        messy: list[list[str]] = [["a", "b"], ["a", "b"]]
        messy[0].append(None)  # type: ignore[arg-type] — deliberately malformed input
        result = co_change(messy)
        assert result["pair_count"] == 1

    def test_artifact_shape(self):
        result = co_change([["a", "b"], ["a", "b"]])
        assert result["kind"] == "analytics.code_intel.co_change"
        assert result["file_count"] == 2
        assert result["parameters"]["min_support"] == 2

    def test_determinism_input_order(self):
        first = co_change([["a", "b", "c"], ["a", "b"], ["b", "c"], ["a", "b"]])
        second = co_change([["b", "c"], ["a", "b"], ["a", "b", "c"], ["a", "b"]])
        assert first["pairs"] == second["pairs"]


# ------------------------------------------------------------------ boundary_violations


def _layered_graph():
    return build_code_graph(
        [
            _node("n:ui", "view", path="ui/view.py", line=1),
            _node("n:core", "engine", path="core/engine.py", line=1),
            _node("n:data", "repo", path="data/repo.py", line=1),
            _node("n:free", "misc", path="misc/x.py", line=1),
        ],
        [
            _edge("n:ui", "n:core", "calls", 3),  # ui -> core
            _edge("n:data", "n:ui", "calls", 4),  # data -> ui  (violation)
            _edge("n:core", "n:data", "calls", 5),  # core -> data
            _edge("n:free", "n:ui", "calls", 6),  # unlayered source, skipped
            _edge("n:ui", "n:ui2", "calls", 7),
        ]
        + [],
    )


class TestBoundaryViolations:
    LAYERS = {"ui": ("ui",), "core": ("core",), "data": ("data",)}

    def _graph(self):
        nodes = [
            _node("n:ui", "view", path="ui/view.py", line=1),
            _node("n:ui2", "view2", path="ui/view2.py", line=1),
            _node("n:core", "engine", path="core/engine.py", line=1),
            _node("n:data", "repo", path="data/repo.py", line=1),
            _node("n:free", "misc", path="misc/x.py", line=1),
        ]
        edges = [
            _edge("n:ui", "n:core", "calls", 3),
            _edge("n:data", "n:ui", "calls", 4),
            _edge("n:core", "n:data", "calls", 5),
            _edge("n:free", "n:ui", "calls", 6),
            _edge("n:ui", "n:ui2", "calls", 7),
        ]
        return build_code_graph(nodes, edges)

    def test_detects_violations(self):
        result = boundary_violations(
            self._graph(), layers=self.LAYERS, allowed_flows=[("ui", "core"), ("core", "data")]
        )
        assert result["total_violations"] == 1
        violation = result["violations"][0]
        assert violation["from_layer"] == "data"
        assert violation["to_layer"] == "ui"
        assert violation["source"] == "n:data"

    def test_internal_edges_not_violations(self):
        result = boundary_violations(self._graph(), layers=self.LAYERS, allowed_flows=[])
        sources = {violation["source"] for violation in result["violations"]}
        assert "n:ui2" not in {violation["target"] for violation in result["violations"]}
        assert "n:ui" in sources  # ui->core cross-layer not allowed here

    def test_unlayered_skipped_and_counted(self):
        result = boundary_violations(self._graph(), layers=self.LAYERS, allowed_flows=[])
        assert result["skipped_unlayered_edges"] == 1
        assert result["unlayered_nodes"] == 1

    def test_flow_summary(self):
        result = boundary_violations(self._graph(), layers=self.LAYERS, allowed_flows=[])
        summary = {(f["from_layer"], f["to_layer"]): f["count"] for f in result["flow_summary"]}
        assert summary == {("ui", "core"): 1, ("data", "ui"): 1, ("core", "data"): 1}

    def test_allowed_flow_unknown_layer_rejected(self):
        with pytest.raises(InvalidPolicyError):
            boundary_violations(self._graph(), layers=self.LAYERS, allowed_flows=[("ui", "ghost")])

    def test_empty_layers_rejected(self):
        with pytest.raises(InvalidPolicyError):
            boundary_violations(self._graph(), layers={})

    def test_layer_without_prefix_rejected(self):
        with pytest.raises(InvalidPolicyError):
            boundary_violations(self._graph(), layers={"ui": ()})

    def test_absolute_prefix_rejected(self):
        with pytest.raises(InvalidPolicyError):
            boundary_violations(self._graph(), layers={"ui": ("/abs",)})

    def test_dotdot_prefix_rejected(self):
        with pytest.raises(InvalidPolicyError):
            boundary_violations(self._graph(), layers={"ui": ("../x",)})

    def test_longest_prefix_wins(self):
        graph = build_code_graph(
            [_node("n:a", "x", path="core/deep/x.py", line=1), _node("n:b", "y", path="ui/y.py", line=1)],
            [_edge("n:a", "n:b")],
        )
        result = boundary_violations(graph, layers={"core": ("core",), "deep": ("core/deep",), "ui": ("ui",)})
        assert result["violations"][0]["from_layer"] == "deep"

    def test_edge_kind_filter(self):
        result = boundary_violations(self._graph(), layers=self.LAYERS, edge_kinds=("imports",))
        assert result["total_violations"] == 0

    def test_limit_guard(self):
        result = boundary_violations(self._graph(), layers=self.LAYERS, limit=1)
        assert result["violation_count"] == 1
        assert result["guards"] == ["limit_reached"]

    def test_limit_validation(self):
        with pytest.raises(InvalidPolicyError):
            boundary_violations(self._graph(), layers=self.LAYERS, limit=0)

    def test_artifact_shape(self):
        result = boundary_violations(self._graph(), layers=self.LAYERS)
        assert result["kind"] == "analytics.code_intel.boundary_violations"
        assert result["parameters"]["layers"] == {"core": ["core"], "data": ["data"], "ui": ["ui"]}

    def test_determinism(self):
        first = boundary_violations(self._graph(), layers=self.LAYERS)
        second = boundary_violations(self._graph(), layers=self.LAYERS)
        assert first == second

    def test_prefix_boundary_not_substring(self):
        # "ui" must not match "uix/foo.py"
        graph = build_code_graph(
            [_node("n:a", "x", path="uix/x.py", line=1), _node("n:b", "y", path="ui/y.py", line=1)],
            [_edge("n:a", "n:b")],
        )
        result = boundary_violations(graph, layers={"ui": ("ui",)})
        assert result["total_violations"] == 0
        assert result["skipped_unlayered_edges"] == 1
