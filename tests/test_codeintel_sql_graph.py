"""SQL-backed codeintel graph tests (SUB-176, failure-learning F-A).

Pins that :class:`SqlCodeGraph` reproduces :func:`build_code_graph` /
:class:`CodeGraph` semantics **byte-for-byte** while reading only the rows a
query touches — the graphify lesson (``load_graph`` re-reads the whole
``graph.json`` per query) applied to our own ``codeintel`` adapter, which did
the same via two unbounded ``fetchall()`` calls.

RED first: the module under test does not exist until the GREEN step.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.analytics.code_intel import (
    AmbiguousSymbolError,
    CodeGraph,
    CodeIntelError,
    UnknownSymbolError,
    callees_of,
    callers_of,
    dependency_path,
    impact_analysis,
    resolve_symbol,
)
from know_your_context.analytics.code_intel_sql import SqlCodeGraph
from know_your_context.cli import main
from know_your_context.domain import canonical_json
from know_your_context.ops.codeintel_cli import (
    codeintel_graph,
    load_code_graph,
    open_sql_code_graph,
)
from know_your_context.store import EventStore


def _seed_rich_store(path: Path) -> Path:
    """A store with the dirty rows the in-memory builder guards against.

    Contains: a calls chain, a self-loop, a dangling-target edge, an
    empty-kind edge, an edge whose endpoint id is the empty string, a NULL
    line edge plus a same-key row that differs only in ``col`` (one build
    key, two identity rows) and duplicate names. ASCII-only: non-ASCII names
    route to the fallback by design (separate fixture below).
    """
    store = EventStore(path)
    store.upsert_symbol(node_id="n:a", name="alpha", kind="function", path="core/f.py", line=1, col=0)
    store.upsert_symbol(node_id="n:b", name="beta", kind="function", path="core/f.py", line=5, col=0)
    store.upsert_symbol(node_id="n:c", name="gamma", kind="function", path="ui/g.py", line=2, col=0)
    store.upsert_symbol(node_id="n:d", name="delta", kind="function", path="ui/g.py", line=9, col=0)
    store.upsert_symbol(node_id="n:dup1", name="dup", kind="function", path="a/x.py", line=1, col=0)
    store.upsert_symbol(node_id="n:dup2", name="dup", kind="function", path="b/y.py", line=1, col=0)
    store.upsert_symbol(node_id="n:file", name="f.py", kind="file", path="core/f.py", line=1, col=0)
    store.add_graph_edge("n:a", "n:b", "calls", line=3)
    store.add_graph_edge("n:b", "n:c", "calls", line=7)
    store.add_graph_edge("n:c", "n:d", "calls", line=1)
    store.add_graph_edge("n:file", "n:a", "contains", line=1)
    # Dirty rows the builder drops; insert via raw SQL (the public API guards).
    store.execute_raw("INSERT INTO graph_edges (source, target, kind, line) VALUES ('n:a', 'n:a', 'calls', 9)")
    store.execute_raw("INSERT INTO graph_edges (source, target, kind, line) VALUES ('n:a', 'n:ghost', 'calls', 9)")
    store.execute_raw("INSERT INTO graph_edges (source, target, kind, line) VALUES ('n:a', 'n:b', '', 9)")
    store.execute_raw("INSERT INTO graph_edges (source, target, kind, line) VALUES ('', 'n:b', 'calls', 9)")
    store.execute_raw("INSERT INTO graph_edges (source, target, kind, line) VALUES ('n:b', 'n:c', 'calls', NULL)")
    store.execute_raw(
        "INSERT INTO graph_edges (source, target, kind, line, col) VALUES ('n:b', 'n:c', 'calls', NULL, 5)"
    )
    store.close()
    return path


def _graphs(store_path: Path):
    """(in-memory graph, sql graph, stats) sharing one store file."""
    store = EventStore(store_path)
    try:
        memory_graph = load_code_graph(store)
        stats: dict[str, int] = {"node_rows": 0, "edge_rows": 0, "queries": 0}
        sql_graph = open_sql_code_graph(store, stats=stats)
    finally:
        store.close()
    assert sql_graph is not None
    return memory_graph, sql_graph, stats


def _both(fn, memory_graph, sql_graph, *args, **kwargs):
    """Run one op against both backends; return (memory, sql) outcomes.

    An outcome is ("ok", canonical payload) or ("error", type, message), so
    exception parity is compared exactly too.
    """

    def capture(graph):
        try:
            return ("ok", canonical_json(fn(graph, *args, **kwargs)))
        except CodeIntelError as error:
            return ("error", type(error).__name__, str(error))

    return capture(memory_graph), capture(sql_graph)


@pytest.fixture()
def rich_store(tmp_path: Path) -> Path:
    return _seed_rich_store(tmp_path / "rich.sqlite3")


# ------------------------------------------------------------------ counts


def test_counts_match_in_memory_build(rich_store: Path) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        assert sql_graph.node_count == len(memory_graph.nodes)
        assert sql_graph.edge_count == len(memory_graph.edges)
        assert sql_graph.dropped_edges == memory_graph.dropped_edges
    finally:
        sql_graph.close()


def test_node_lookup_matches_including_absent(rich_store: Path) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        for node_id in ("n:a", "n:dup2", "n:missing", ""):
            assert sql_graph.node(node_id) == memory_graph.node(node_id)
    finally:
        sql_graph.close()


def test_named_lookup_matches_case_insensitive(rich_store: Path) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        for name in ("alpha", "ALPHA", "dup", "absent"):
            assert sql_graph.named(name) == memory_graph.named(name)
    finally:
        sql_graph.close()


# ------------------------------------------------------------------ ops parity


@pytest.mark.parametrize("symbol", ["alpha", "beta", "gamma", "delta", "f.py"])
@pytest.mark.parametrize("depth", [1, 2, 8])
@pytest.mark.parametrize("limit", [1, 3, 200])
def test_callers_of_byte_identical(rich_store: Path, symbol: str, depth: int, limit: int) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        memory, sql = _both(callers_of, memory_graph, sql_graph, symbol, max_depth=depth, max_results=limit)
        assert sql == memory
    finally:
        sql_graph.close()


@pytest.mark.parametrize("symbol", ["alpha", "beta", "gamma", "delta"])
@pytest.mark.parametrize("depth", [1, 2, 8])
def test_callees_of_byte_identical(rich_store: Path, symbol: str, depth: int) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        memory, sql = _both(callees_of, memory_graph, sql_graph, symbol, max_depth=depth, max_results=200)
        assert sql == memory
    finally:
        sql_graph.close()


def test_callers_of_file_filter_byte_identical(rich_store: Path) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        for file in (None, "core/f.py", "ui/g.py", "nowhere.py"):
            memory, sql = _both(callers_of, memory_graph, sql_graph, "beta", max_depth=2, max_results=50, file=file)
            assert sql == memory
    finally:
        sql_graph.close()


def test_error_parity_unknown_ambiguous(rich_store: Path) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        for symbol in ("nope", "dup", "CAFÉ", "café", "ALPHA"):
            memory, sql = _both(callers_of, memory_graph, sql_graph, symbol)
            assert sql == memory
        # sanity: the fixture really exercises the unknown-symbol error class
        with pytest.raises(UnknownSymbolError):
            callers_of(memory_graph, "nope")
        # SUB-177: traversal ops answer an ambiguous name with the union
        # (measured gap: 14 ``parse_args`` definitions in the L4 stdlib
        # corpus errored AMBIGUOUS_SYMBOL while both competitors answered);
        # the strict single-node contract survives in ``resolve_symbol``.
        union = callers_of(memory_graph, "dup")
        assert union["ambiguous"] is True
        assert {c["node_id"] for c in union["candidates"]} == {"n:dup1", "n:dup2"}
        with pytest.raises(AmbiguousSymbolError):
            resolve_symbol(memory_graph, "dup")
    finally:
        sql_graph.close()


@pytest.mark.parametrize(
    ("source", "target"),
    [("alpha", "delta"), ("delta", "alpha"), ("alpha", "alpha"), ("alpha", "café"), ("nope", "alpha")],
)
def test_dependency_path_byte_identical(rich_store: Path, source: str, target: str) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        memory, sql = _both(dependency_path, memory_graph, sql_graph, source, target)
        assert sql == memory
    finally:
        sql_graph.close()


@pytest.mark.parametrize("symbol", ["alpha", "gamma", "café"])
@pytest.mark.parametrize("depth", [1, 8])
def test_impact_analysis_byte_identical(rich_store: Path, symbol: str, depth: int) -> None:
    memory_graph, sql_graph, _ = _graphs(rich_store)
    try:
        memory, sql = _both(impact_analysis, memory_graph, sql_graph, symbol, max_depth=depth)
        assert sql == memory
    finally:
        sql_graph.close()


# ------------------------------------------------------------------ the point


def test_reads_are_bounded_by_answer_not_by_store(rich_store: Path) -> None:
    """The failure being fixed: a depth-1 query must not read the whole graph.

    The in-memory adapter reads every symbol and every edge row, always.
    The SQL graph must return rows proportional to the answer, not to
    ``node_count``/``edge_count``: two aggregate count queries (one row
    each) plus the rows the traversal actually visits.
    """
    memory_graph, sql_graph, stats = _graphs(rich_store)
    try:
        total_nodes = len(memory_graph.nodes)
        total_edges = len(memory_graph.edges) + memory_graph.dropped_edges
        result = callers_of(sql_graph, "gamma", max_depth=1, max_results=200)
        assert result["entry_count"] > 0
        assert stats["queries"] > 0
        # Bounded: node rows = seed candidates + visited entries; edge rows =
        # raw neighbour rows of visited nodes. Both far below full tables even
        # on this tiny fixture; the bound scales with the answer.
        assert stats["node_rows"] <= 2 + result["entry_count"] + result["total"]
        assert stats["edge_rows"] <= total_edges  # never more than raw edge rows
        assert stats["node_rows"] < total_nodes + total_edges
    finally:
        sql_graph.close()


def test_high_fanout_queries_are_batched_not_per_entry(tmp_path: Path) -> None:
    """A hub node with N callers must not cost N point queries (SUB-178).

    Measured at L3 scale (t3_L3b.sqlite, 59 910 symbols): ``callers-of
    fn_0_0 --depth 1`` visits 14 916 callers and issued 14 917 single-row
    ``node()`` lookups — 64% of the op's CPU (0.58 s of 0.90 s), and the
    difference between surviving and collapsing in the 50-agent storm. Node
    payloads for the entry list must be fetched in bounded ``IN (...)``
    chunks, so the query count stays ~constant while the row count scales
    with the answer. Byte-parity with the in-memory build is pinned by the
    existing corpus-parity suite.
    """
    path = tmp_path / "hub.sqlite3"
    store = EventStore(path)
    fan = 1200
    store.upsert_symbol(node_id="n:hub", name="hub", kind="function", path="hub.py", line=1, col=0)
    for i in range(fan):
        store.upsert_symbol(node_id=f"n:c{i}", name=f"caller_{i}", kind="function", path=f"m{i % 40}.py", line=i, col=0)
        store.add_graph_edge(f"n:c{i}", "n:hub", "calls", line=1)
    try:
        memory_graph = load_code_graph(store)
        stats: dict[str, int] = {"node_rows": 0, "edge_rows": 0, "queries": 0}
        sql_graph = open_sql_code_graph(store, stats=stats)
        assert sql_graph is not None
        try:
            expected = callers_of(memory_graph, "hub", max_depth=1, max_results=200)
            result = callers_of(sql_graph, "hub", max_depth=1, max_results=200)
            assert result["total"] == fan == expected["total"]
            assert result["entries"] == expected["entries"], "byte-parity with the in-memory build"
            # Batched: counts (2) + named (1) + non-ASCII scan (1) + neighbour
            # rounds (1 batched) + ceil(fan / chunk) node fetches — never one
            # query per entry. 1200 callers must stay under 20 queries.
            assert stats["queries"] <= 20, (
                f"per-entry point queries are back: {stats['queries']} queries for {fan} callers"
            )
        finally:
            sql_graph.close()
    finally:
        store.close()


def test_snapshot_isolated_within_one_graph_instance(rich_store: Path) -> None:
    """One SqlCodeGraph = one snapshot, even if a writer lands mid-op."""
    store = EventStore(rich_store)
    try:
        sql_graph = open_sql_code_graph(store)
        assert sql_graph is not None
        try:
            before = sql_graph.node_count
            store.upsert_symbol(node_id="n:late", name="late", kind="function", path="z.py", line=1, col=0)
            assert sql_graph.node_count == before  # snapshot pinned at open
            assert sql_graph.node("n:late") is None
        finally:
            sql_graph.close()
    finally:
        store.close()


# ------------------------------------------------------------------ fallback


def test_factory_falls_back_for_memory_store() -> None:
    store = EventStore(":memory:")
    try:
        assert open_sql_code_graph(store) is None
    finally:
        store.close()


def test_factory_falls_back_when_store_has_non_ascii_names(tmp_path: Path) -> None:
    """A non-ASCII NAME in the store must not disable the SQL backend.

    Baseline (stress3 L4, SUB-177): one symbol named ``cassé`` in a 163k-row
    store made ``open_sql_code_graph`` refuse, so *every* ``codeintel
    callers-of`` fell back to the in-memory build — 19.5 s and ~2 GB per
    query, the whole 50-agent storm collapsing. SQLite ``lower()`` folds
    ASCII only while Python folds Unicode (U+212A KELVIN → ``k``), so the
    backend now answers ``named()`` as SQL-ASCII-CI **plus** a Python CI
    pass over the (tiny) non-ASCII name set — same answer, bounded cost.
    """
    path = tmp_path / "unicode.sqlite3"
    store = EventStore(path)
    store.upsert_symbol(node_id="n:cafe", name="café", kind="function", path="b/y.py", line=7, col=0)
    store.upsert_symbol(node_id="n:cafe_up", name="CAFÉ", kind="function", path="b/z.py", line=2, col=0)
    store.upsert_symbol(node_id="n:plain", name="plain", kind="function", path="b/y.py", line=9, col=0)
    store.upsert_symbol(node_id="n:kelvin", name="\u212aelvin", kind="function", path="b/k.py", line=3, col=0)
    store.add_graph_edge("n:plain", "n:cafe", "calls", line=1)
    try:
        sql_graph = open_sql_code_graph(store)
        assert sql_graph is not None, "a non-ASCII name must not disable the SQL backend"
        memory_graph = load_code_graph(store)
        try:
            for name in ("café", "CAFÉ", "cafe", "plain", "kelvin", "KELVIN", "absent"):
                assert sql_graph.named(name) == memory_graph.named(name), name
            assert callers_of(sql_graph, "café") == callers_of(memory_graph, "café")
            assert callers_of(sql_graph, "plain") == callers_of(memory_graph, "plain")
        finally:
            sql_graph.close()
        # A non-ASCII *query* symbol keeps the in-memory path (dispatch guard).
        with codeintel_graph(store, "café") as graph:
            assert type(graph) is CodeGraph
            assert callers_of(graph, "café") == callers_of(memory_graph, "café")
        with codeintel_graph(store, "plain") as graph:
            assert type(graph) is SqlCodeGraph
            assert callers_of(graph, "plain") == callers_of(memory_graph, "plain")
    finally:
        store.close()


def test_cli_callers_of_end_to_end(rich_store: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI dispatch uses the SQL backend and keeps the envelope contract."""
    store = EventStore(rich_store)
    try:
        assert open_sql_code_graph(store) is not None  # fixture qualifies
        memory_graph = load_code_graph(store)
        expected = json.loads(canonical_json(callers_of(memory_graph, "beta", max_depth=1, max_results=50)))
    finally:
        store.close()
    code = main(["--store", str(rich_store), "codeintel", "callers-of", "beta", "--depth", "1", "--limit", "50"])
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == expected


def test_cli_unknown_action_rejected_by_argparse(rich_store: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Unknown codeintel actions never reach the dispatch (argparse, exit 2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--store", str(rich_store), "codeintel", "frobnicate", "x"])
    assert excinfo.value.code == 2
