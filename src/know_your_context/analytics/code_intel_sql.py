"""SQL-backed lazy code graph for the codeintel ops (SUB-176, failure-learning F-A).

The failure this fixes: the in-memory adapter
(:func:`know_your_context.ops.codeintel_cli.load_code_graph`) materialises
**every** row of ``symbols`` and ``graph_edges`` for every query — the same
design that made graphify re-read its full ``graph.json`` per query and time
out at scale. :class:`SqlCodeGraph` implements the exact :class:`CodeGraph`
read surface the analytics ops use, backed by the indexed v3 tables, so a
depth-1 ``callers-of`` reads rows proportional to the answer, not to the
store.

Parity contract (pinned byte-for-byte by ``tests/test_codeintel_sql_graph.py``):

* node lookup: ``symbols.node_id`` is the table PRIMARY KEY, so "first row in
  canonical scan order" is the only row — point lookup is exact.
* name lookup: ``lower(name) = lower(?)`` uses ``idx_symbols_lower_name``;
  ordering ``(path, line, col, node_id)`` mirrors the in-memory grouping.
  SQLite ``lower()`` folds ASCII only while Python ``str.lower()`` also folds
  Unicode (e.g. U+212A KELVIN SIGN → ``k``), so ``named()`` answers with the
  SQL ASCII-CI pass **plus** a Python CI pass over the store's non-ASCII
  names — a set fetched once per instance (one bounded scan, then cached).
  Measured why this matters (stress3 L4, SUB-177): one symbol named
  ``cassé`` in a 163k-row store made the old factory refuse the SQL backend,
  so *every* traversal op fell back to the in-memory build — 19.5 s and
  ~2 GB per query, collapsing the 50-agent storm. Same answer, bounded cost.
  The dispatch still routes non-ASCII *query* symbols to the in-memory
  build (the reference implementation), so the SQL path only ever folds
  ASCII query text against the cached non-ASCII name set.
* neighbours: ``DISTINCT (other, kind, IFNULL(line, 0))`` reproduces the
  builder's dedupe key ``(source, target, kind, line)`` with ``line`` already
  normalised (NULL → 0, mirroring ``_as_int``); self-loops, empty endpoints,
  empty kinds and dangling endpoints are filtered exactly like
  ``build_code_graph``. UTF-8 binary ordering equals code-point ordering, so
  SQL ``ORDER BY`` matches Python tuple sort.
* counts: ``node_count``/``edge_count``/``dropped_edges`` are computed with
  the builder's validity and dedupe rules, so the ``node_count``/
  ``edge_count`` fields in op payloads are identical to the in-memory path.

One instance = one snapshot: the connection holds a single read transaction
(WAL), so a writer landing mid-op cannot change what an op sees.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from know_your_context.analytics.code_intel import CodeNode

__all__ = ["SqlCodeGraph"]

_DIRECTION_COLUMN = {"forward": "target", "reverse": "source"}
_PIVOT_COLUMN = {"forward": "source", "reverse": "target"}

_NODE_SQL = "SELECT node_id, name, kind, path, line, col FROM symbols WHERE node_id = ? AND node_id <> ''"
_NODES_MANY_SQL = "SELECT node_id, name, kind, path, line, col FROM symbols WHERE node_id IN ({ids}) AND node_id <> ''"
_NAMED_SQL = (
    "SELECT node_id, name, kind, path, line, col FROM symbols "
    "WHERE lower(name) = lower(?) AND node_id <> '' ORDER BY path, line, col, node_id"
)
_NODE_COUNT_SQL = "SELECT COUNT(*) FROM symbols WHERE node_id <> ''"
_NONASCII_SQL = "SELECT node_id, name, kind, path, line, col FROM symbols WHERE name GLOB '*[^ -~]*' AND node_id <> ''"
_EDGE_COUNT_SQL = (
    "SELECT COUNT(*) FROM ("
    "SELECT e.source, e.target, e.kind, IFNULL(e.line, 0) AS ln FROM graph_edges e "
    "JOIN symbols s ON s.node_id = e.source AND s.node_id <> '' "
    "JOIN symbols t ON t.node_id = e.target AND t.node_id <> '' "
    "WHERE e.source <> '' AND e.target <> '' AND e.kind <> '' AND e.source <> e.target "
    "GROUP BY e.source, e.target, e.kind, ln)"
)
_RAW_EDGE_ROWS_SQL = "SELECT COUNT(*) FROM graph_edges"
_NEIGHBOURS_SQL = (
    "SELECT DISTINCT e.{other}, e.kind, IFNULL(e.line, 0) AS ln FROM graph_edges e "
    "JOIN symbols s ON s.node_id = e.{other} AND s.node_id <> '' "
    "WHERE e.{pivot} = ? AND e.kind IN ({{kinds}}) "
    "AND e.{other} <> '' AND e.{other} <> e.{pivot} "
    "ORDER BY e.{other}, e.kind, ln"
)
_NEIGHBOURS_MANY_SQL = (
    "SELECT DISTINCT e.{pivot}, e.{other}, e.kind, IFNULL(e.line, 0) AS ln FROM graph_edges e "
    "JOIN symbols s ON s.node_id = e.{other} AND s.node_id <> '' "
    "WHERE e.{pivot} IN ({{ids}}) AND e.kind IN ({{kinds}}) "
    "AND e.{other} <> '' AND e.{other} <> e.{pivot} "
    "ORDER BY e.{pivot}, e.{other}, e.kind, ln"
)

#: Bound for one ``IN (...)`` chunk (SQLite's variable limit is >= 999).
_CHUNK = 500


def _node_from_row(row: tuple[object, ...]) -> CodeNode:
    return CodeNode(
        node_id=str(row[0]),
        name=str(row[1]),
        kind=str(row[2]),
        path=str(row[3]),
        line=int(row[4]),  # type: ignore[arg-type]
        col=int(row[5]),  # type: ignore[arg-type]
    )


class SqlCodeGraph:
    """Lazy SQL-backed code graph with the :class:`CodeGraph` read surface.

    Owns its connection; :meth:`close` rolls the snapshot transaction back
    and closes it. ``stats`` (test hook) accumulates ``node_rows``,
    ``edge_rows`` and ``queries`` so tests can pin that reads stay bounded
    by the answer, not by the store.
    """

    __slots__ = ("_conn", "_counts", "_node_cache", "_nonascii", "_stats")

    def __init__(self, conn: sqlite3.Connection, *, stats: dict[str, int] | None = None) -> None:
        self._conn = conn
        self._stats = stats
        self._node_cache: dict[str, CodeNode | None] = {}
        self._nonascii: tuple[CodeNode, ...] | None = None
        self._counts: dict[str, int] = {}
        self._conn.execute("BEGIN")

    def close(self) -> None:
        self._conn.rollback()
        self._conn.close()

    # ------------------------------------------------------------ rows

    def _rows(self, sql: str, params: tuple[object, ...], bucket: str) -> list[tuple[object, ...]]:
        cursor = self._conn.execute(sql, params)
        rows = cursor.fetchall()
        if self._stats is not None:
            self._stats[bucket] = self._stats.get(bucket, 0) + len(rows)
            self._stats["queries"] = self._stats.get("queries", 0) + 1
        return rows

    def _count(self, key: str, sql: str) -> int:
        if sql not in self._counts:
            self._counts[sql] = int(self._rows(sql, (), "node_rows")[0][0])  # type: ignore[arg-type]
        return self._counts[sql]

    # ------------------------------------------------------------ CodeGraph surface

    @property
    def node_count(self) -> int:
        return self._count("nodes", _NODE_COUNT_SQL)

    @property
    def edge_count(self) -> int:
        return self._count("edges", _EDGE_COUNT_SQL)

    @property
    def dropped_edges(self) -> int:
        return self._count("raw_edges", _RAW_EDGE_ROWS_SQL) - self.edge_count

    def node(self, node_id: str) -> CodeNode | None:
        if node_id not in self._node_cache:
            rows = self._rows(_NODE_SQL, (node_id,), "node_rows")
            self._node_cache[node_id] = _node_from_row(rows[0]) if rows else None
        return self._node_cache[node_id]

    def nodes_many(self, node_ids: tuple[str, ...]) -> dict[str, CodeNode]:
        """Batched :meth:`node` — one indexed ``IN (...)`` query per ``_CHUNK`` ids.

        Measured why (SUB-178): a 14 916-caller hub query otherwise issued
        one point lookup per entry — 64% of the op's CPU. Warm cache entries
        are served without touching the database; misses are cached as
        ``None`` exactly like :meth:`node`.
        """
        out: dict[str, CodeNode] = {}
        missing: list[str] = []
        for node_id in dict.fromkeys(node_ids):
            if node_id in self._node_cache:
                cached = self._node_cache[node_id]
                if cached is not None:
                    out[node_id] = cached
            else:
                missing.append(node_id)
        for start in range(0, len(missing), _CHUNK):
            chunk = missing[start : start + _CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self._rows(_NODES_MANY_SQL.format(ids=placeholders), tuple(chunk), "node_rows")
            found: set[str] = set()
            for row in rows:
                node = _node_from_row(row)
                self._node_cache[node.node_id] = node
                out[node.node_id] = node
                found.add(node.node_id)
            for node_id in chunk:
                if node_id not in found:
                    self._node_cache[node_id] = None
        return out

    def _nonascii_nodes(self) -> tuple[CodeNode, ...]:
        """The store's non-ASCII-named symbols, fetched once per instance.

        One bounded ``GLOB '*[^ -~]*'`` scan (no index can serve it), then
        cached — the parity pass in :meth:`named` iterates this set, so the
        per-query cost is proportional to the store's non-ASCII names
        (measured: a handful in a 163k-row real corpus), not to the store.
        """
        if self._nonascii is None:
            rows = self._rows(_NONASCII_SQL, (), "node_rows")
            self._nonascii = tuple(_node_from_row(row) for row in rows)
        return self._nonascii

    def named(self, name: str) -> tuple[CodeNode, ...]:
        """Case-insensitive name lookup, byte-identical to ``CodeGraph.named``.

        SQL ``lower()`` folds ASCII only; the Python pass over the cached
        non-ASCII set restores Unicode folds (KELVIN SIGN → ``k``, ``É`` →
        ``é``). Results merge by ``node_id`` and keep the canonical
        ``(path, line, col, node_id)`` order.
        """
        rows = self._rows(_NAMED_SQL, (name,), "node_rows")
        found: dict[str, CodeNode] = {str(row[0]): _node_from_row(row) for row in rows}
        lowered = name.lower()
        for node in self._nonascii_nodes():
            if node.name.lower() == lowered:
                found.setdefault(node.node_id, node)
        return tuple(sorted(found.values(), key=lambda node: (node.path, node.line, node.col, node.node_id)))

    def neighbours(
        self,
        node_id: str,
        direction: Literal["forward", "reverse"],
        kinds: frozenset[str],
    ) -> tuple[tuple[str, str, int], ...]:
        sql = _NEIGHBOURS_SQL.format(other=_DIRECTION_COLUMN[direction], pivot=_PIVOT_COLUMN[direction])
        sql = sql.format(kinds=", ".join("?" for _ in kinds))
        params: tuple[object, ...] = (node_id, *sorted(kinds))
        rows = self._rows(sql, params, "edge_rows")
        return tuple((str(row[0]), str(row[1]), int(row[2])) for row in rows)  # type: ignore[arg-type]

    def neighbours_many(
        self,
        node_ids: tuple[str, ...],
        direction: Literal["forward", "reverse"],
        kinds: frozenset[str],
    ) -> dict[str, tuple[tuple[str, str, int], ...]]:
        """Batched neighbours: one indexed query per ``_CHUNK`` ids.

        ``ORDER BY pivot, other, kind, line`` keeps each id's list sorted
        exactly as :meth:`neighbours` returns it.
        """
        grouped: dict[str, list[tuple[str, str, int]]] = {node_id: [] for node_id in node_ids}
        if not node_ids:
            return {}
        template = _NEIGHBOURS_MANY_SQL.format(other=_DIRECTION_COLUMN[direction], pivot=_PIVOT_COLUMN[direction])
        kind_ph = ", ".join("?" for _ in kinds)
        for start in range(0, len(node_ids), _CHUNK):
            chunk = node_ids[start : start + _CHUNK]
            id_ph = ", ".join("?" for _ in chunk)
            sql = template.format(ids=id_ph, kinds=kind_ph)
            params: tuple[object, ...] = (*chunk, *sorted(kinds))
            for row in self._rows(sql, params, "edge_rows"):
                grouped[str(row[0])].append((str(row[1]), str(row[2]), int(row[3])))  # type: ignore[arg-type]
        return {node_id: tuple(entries) for node_id, entries in grouped.items()}


def store_has_only_ascii_names(conn: sqlite3.Connection) -> bool:
    """True when no symbol name carries a byte outside printable ASCII.

    Byte-level argument: UTF-8 non-ASCII characters are made of bytes >= 0x80,
    all above '~' (0x7E), so the GLOB catches exactly the names SQLite's
    ASCII-only ``lower()`` could diverge on. ASCII control bytes (< 0x20)
    also match — a deliberate conservative fallback, never a wrong answer.
    """
    row = conn.execute("SELECT EXISTS(SELECT 1 FROM symbols WHERE name GLOB '*[^ -~]*')").fetchone()
    return not bool(row[0])


def open_readonly_connection(db_path: str) -> sqlite3.Connection | None:
    """Open ``db_path`` read-only; ``None`` when the graph tables are absent."""
    if db_path == ":memory:" or not Path(db_path).exists():
        return None
    uri = f"file:{quote(str(Path(db_path).resolve()))}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 15000")
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('symbols', 'graph_edges')"
        ).fetchone()
        if int(tables[0]) != 2:  # type: ignore[arg-type]
            conn.close()
            return None
        return conn
    except sqlite3.Error:
        return None
