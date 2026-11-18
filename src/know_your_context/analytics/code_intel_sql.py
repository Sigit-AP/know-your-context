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
  Aggregate values (counts and the non-ASCII name set) are pure content
  functions, so they are memoized across instances *per snapshot*: the key
  is an exact content fingerprint read inside the instance's own read
  transaction (symbol count, edge count, ``MAX(edge_id)``, the v6
  ``symbols_watch`` sequence — which moves on ANY symbols write — plus
  db/wal stat tokens), so a write landing between instances moves the key
  and forces recomputation, while N instances of one immutable snapshot pay
  the full edge-stream aggregation once. Measured why (SUB-188S, L3 store,
  59 911 symbols / 125 599 edges): the aggregation is ~500 ms of a ~1 s
  ``callers-of``; every op payload carries the counts, so a one-shot CLI
  query paid it every time. The edge-count statement itself is shaped so
  endpoint validity runs as ``EXISTS`` point probes of the ``symbols``
  PRIMARY KEY per candidate group, never as a ``SCAN`` of the symbol table.

One instance = one snapshot: the connection holds a single read transaction
(WAL), so a writer landing mid-op cannot change what an op sees. The memo
serves exactly that snapshot to later instances — never a newer or older
one (a replaced/in-place store file with identical cardinalities AND the
same watch sequence is not detected; the store is append-only by design).

Cross-process cache (SUB-193): the memo is process-local, so the 50-agent
storm paid the valid-edge aggregation once PER PROCESS — measured on the L4
store (2 cores): ~78 s of an ~82 s instrumented ``callers-of`` sat in that
aggregation, and the CLI's p50 hit 115 s, kissing the 120 s timeout. A
sidecar file (``<store>.codeintel-cache.json``, atomic
``mkstemp`` + ``os.replace``, flock-guarded so one winner computes) shares
the snapshot's pure content functions (``node_count``/``edge_count``/
``raw_edges`` and, for v6 stores, the non-ASCII name set) across processes.
The exact fingerprint is part of the sidecar's key: a moved key (any write)
forces recomputation, a corrupt/mismatched file is never trusted, and every
I/O failure fails open to today's per-process compute — the cache can only
change cost, never answers.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import quote

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only fallback
    fcntl = None  # type: ignore[assignment]

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
    "SELECT DISTINCT e.source, e.target, e.kind, IFNULL(e.line, 0)"
    " FROM graph_edges e"
    " JOIN symbols s ON s.node_id = e.source"
    " JOIN symbols t ON t.node_id = e.target"
    " WHERE e.source <> '' AND e.target <> '' AND e.kind <> ''"
    "   AND e.source <> e.target"
    ")"
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

# ------------------------------------------------------------ aggregate memo
#: Content fingerprint of one snapshot -> lazily computed aggregates, shared
#: across :class:`SqlCodeGraph` instances of the SAME snapshot (docstring).
#: ``raw_edges`` is derivable from the fingerprint itself (COUNT(*) of
#: ``graph_edges`` is part of the key), so it is stored but never computed
#: twice. ``nonascii`` is only memoized when the store carries the v6
#: ``symbols_watch`` counter — without it, name-content changes between
#: instances could not be detected, so the per-instance scan stays.
_STORE_MEMO: dict[tuple[object, ...], dict[str, object]] = {}
_MEMO_LOCK = threading.Lock()
_MEMO_MAX_ENTRIES = 64
_FINGERPRINT_SQL = (
    "SELECT "
    "(SELECT COUNT(*) FROM symbols WHERE node_id <> ''), "
    "(SELECT COUNT(*) FROM graph_edges), "
    "(SELECT COALESCE(MAX(edge_id), 0) FROM graph_edges), "
    "EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'symbols_watch')"
)
_WATCH_SEQ_SQL = "SELECT seq FROM symbols_watch WHERE id = 1"

# ----------------------------------------------------- cross-process cache
#: Filename suffix of the per-store aggregate sidecar (SUB-193, module docstring).
#: One JSON document per store, written atomically; its key is the exact
#: fingerprint tuple of one snapshot, so a stale or corrupt file is never
#: served. ``db_path`` appears both inside the document and as ``key[0]`` —
#: a file copied next to another store self-invalidates on both fields.
_SIDECAR_SUFFIX = ".codeintel-cache.json"
#: Lock-file suffix for the sidecar's single-writer flock.
_SIDECAR_LOCK_SUFFIX = ".codeintel-cache.lock"
#: How long a reader waits for the compute lock before failing open to the
#: per-process compute (today's cost). Writers hold it for the duration of
#: one aggregation — measured ~3 s solo on the L4 store.
_SIDECAR_LOCK_WAIT_S = 15.0
#: Wait between flock retries while the lock is held (blocked writers sleep,
#: they do not spin the CPU).
_SIDECAR_LOCK_POLL_S = 0.05


def _sidecar_path(db_path: str) -> Path:
    """The aggregate sidecar file for one store file (SUB-193)."""
    return Path(db_path + _SIDECAR_SUFFIX)


def _sidecar_read(db_path: str, key: tuple[object, ...]) -> dict[str, object] | None:
    """The cached pure-content aggregates for ``key``, else ``None``.

    ``None`` means "absent, corrupt, or for a different snapshot" — never a
    partial result. The key embeds the exact fingerprint of the snapshot the
    writer computed from, so a served document is pinned to identical
    content by the same argument the in-process memo relies on.
    """
    try:
        payload = json.loads(_sidecar_path(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("db_path") != db_path:
        return None
    if payload.get("key") != list(key):
        return None
    return payload


def _nonascii_rows_from_payload(payload: dict[str, object]) -> tuple[CodeNode, ...] | None:
    """Deserialize the cached non-ASCII name set; ``None`` when absent or malformed."""
    rows = payload.get("nonascii")
    if rows is None:
        return None
    if not isinstance(rows, list):
        return None
    nodes: list[CodeNode] = []
    try:
        for row in rows:
            if not isinstance(row, list) or len(row) != 6:
                return None
            nodes.append(_node_from_row(tuple(row)))
    except (TypeError, ValueError, IndexError):
        return None
    return tuple(nodes)


def _fill_from_payload(entry: dict[str, object], payload: dict[str, object], has_watch: bool) -> bool:
    """Copy cached values into an in-process memo entry; True when any landed."""
    filled = False
    edge = payload.get("edge_count")
    if entry["edge_count"] is None and type(edge) is int:
        entry["edge_count"] = edge
        filled = True
    if has_watch and entry["nonascii"] is None:
        nonascii = _nonascii_rows_from_payload(payload)
        if nonascii is not None:
            entry["nonascii"] = nonascii
            filled = True
    return filled


def _sidecar_write(
    db_path: str,
    key: tuple[object, ...],
    *,
    node_count: int,
    edge_count: int,
    raw_edges: int,
    nonascii: tuple[CodeNode, ...] | None,
) -> None:
    """Atomically publish one snapshot's pure-content aggregates (SUB-193).

    ``mkstemp`` in the store's directory + ``os.replace`` (the webview
    sidecar pattern): a concurrent reader sees either the old or the new
    complete document, and the key check decides which is usable. Raises
    ``OSError`` on failure — callers fail open to per-process compute.
    """
    payload = {
        "db_path": db_path,
        "key": list(key),
        "node_count": int(node_count),
        "edge_count": int(edge_count),
        "raw_edges": int(raw_edges),
        "nonascii": (
            [[node.node_id, node.name, node.kind, node.path, node.line, node.col] for node in nonascii]
            if nonascii is not None
            else None
        ),
    }
    path = _sidecar_path(db_path)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _acquire_shared_cache_lock(db_path: str) -> int | None:
    """Exclusive flock on the sidecar lock file; ``None`` when unavailable.

    Blocking with a bounded poll wait: the 49 losers sleep (no CPU spin)
    while the single winner runs the ~3 s aggregation, then re-read the
    freshened sidecar. ``None`` (no flock support, wait expiry, unwritable
    dir) sends the caller to today's per-process compute.
    """
    if fcntl is None:  # pragma: no cover - POSIX-only fallback
        return None
    lock_path = Path(db_path + _SIDECAR_LOCK_SUFFIX)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return None
    deadline = time.monotonic() + _SIDECAR_LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(_SIDECAR_LOCK_POLL_S)


def _release_shared_cache_lock(fd: int | None) -> None:
    """Release the flock (auto-released on process death too) and close."""
    if fd is None:
        return
    with contextlib.suppress(OSError):
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


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

    __slots__ = ("_conn", "_counts", "_node_cache", "_nonascii", "_stats", "_token")

    def __init__(self, conn: sqlite3.Connection, *, stats: dict[str, int] | None = None) -> None:
        self._conn = conn
        self._stats = stats
        self._node_cache: dict[str, CodeNode | None] = {}
        self._nonascii: tuple[CodeNode, ...] | None = None
        self._counts: dict[str, int] = {}
        self._token: tuple[tuple[object, ...], int, int, bool] | None = None
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

    def _snapshot_token(self) -> tuple[tuple[object, ...], int, int, bool]:
        """(key, symbol count, raw edge count, has_watch) — computed once per instance.

        The first call issues this instance's FIRST reads, pinning the read
        snapshot — the key (and the two counts reused as answers) can never
        describe a different store state than the reads that follow. Later
        calls (node_count, edge_count and the non-ASCII lookup used to run
        the fingerprint three times per instance) serve straight from memory
        (SUB-193: those redundant full-table scans added up under the storm).
        """
        if self._token is None:
            conn = self._conn
            row = conn.execute(_FINGERPRINT_SQL).fetchone()
            watch_seq: int | None = None
            has_watch = bool(row[3])
            if has_watch:
                watch = conn.execute(_WATCH_SEQ_SQL).fetchone()
                watch_seq = int(watch[0]) if watch is not None else None
            db_path = str(conn.execute("PRAGMA database_list").fetchone()[2])
            db_stat = os.stat(db_path)
            wal_path = db_path + "-wal"
            try:
                wal_stat = os.stat(wal_path)
            except OSError:
                wal_stat = None
            key: tuple[object, ...] = (
                db_path,
                int(row[0]),
                int(row[1]),
                int(row[2]),
                watch_seq,
                db_stat.st_size,
                db_stat.st_mtime_ns,
                wal_stat.st_size if wal_stat is not None else 0,
            )
            self._token = (key, int(row[0]), int(row[1]), has_watch)  # type: ignore[assignment]
        return self._token  # type: ignore[return-value]

    def _memo_entry(self) -> tuple[dict[str, object], int, int, bool]:
        """The shared memo entry for this instance's snapshot, plus the
        fingerprint's symbol count and raw edge count (both reused as
        answers), and whether the v6 ``symbols_watch`` counter exists.

        The fingerprint queries are this instance's FIRST reads, so they pin
        the read snapshot; every aggregate served from the memo was computed
        in a transaction that saw the identical snapshot. Missing values are
        filled from the cross-process sidecar first (SUB-193).
        """
        key, fingerprint_syms, fingerprint_edges, has_watch = self._snapshot_token()
        with _MEMO_LOCK:
            entry = _STORE_MEMO.get(key)
            if entry is None:
                if len(_STORE_MEMO) >= _MEMO_MAX_ENTRIES:
                    _STORE_MEMO.clear()
                entry = {"node_count": None, "edge_count": None, "raw_edges": None, "nonascii": None}
                _STORE_MEMO[key] = entry  # type: ignore[arg-type]
        self._ensure_shared_aggregates(entry, key, fingerprint_syms, fingerprint_edges, has_watch)
        return entry, fingerprint_syms, fingerprint_edges, has_watch  # type: ignore[return-value]

    def _ensure_shared_aggregates(
        self,
        entry: dict[str, object],
        key: tuple[object, ...],
        fingerprint_syms: int,
        fingerprint_edges: int,
        has_watch: bool,
    ) -> None:
        """Fill the entry's expensive values from the cross-process cache.

        Only the valid-edge aggregation is expensive (measured: ~78 s of an
        ~82 s instrumented ``callers-of`` under the 50-way L4 storm; the
        non-ASCII GLOB scan rides along on v6 stores). The sidecar publishes
        both once per snapshot; every other process reads them with zero
        SQL. Any failure — no flock, wait expiry, unwritable or corrupt
        file, store moved on — fails open: ``_count``/``_nonascii_nodes``
        then compute in-process, today's exact cost and answer.
        """
        if entry["edge_count"] is not None:
            return  # the expensive value is warm; non-ASCII fills on its own path
        db_path = str(key[0])
        payload = _sidecar_read(db_path, key)
        if payload is not None and _fill_from_payload(entry, payload, has_watch):
            return
        fd = _acquire_shared_cache_lock(db_path)
        if fd is None:
            return  # fail open: the caller computes in-process (today's cost)
        try:
            # A winner may have published while we waited for the lock.
            payload = _sidecar_read(db_path, key)
            if payload is not None and _fill_from_payload(entry, payload, has_watch):
                return
            edge_count = int(self._rows(_EDGE_COUNT_SQL, (), "node_rows")[0][0])  # type: ignore[arg-type]
            entry["edge_count"] = edge_count
            nonascii: tuple[CodeNode, ...] | None = None
            if has_watch:
                rows = self._rows(_NONASCII_SQL, (), "node_rows")
                nonascii = tuple(_node_from_row(row) for row in rows)
                entry["nonascii"] = nonascii
            with contextlib.suppress(OSError):
                _sidecar_write(
                    db_path,
                    key,
                    node_count=fingerprint_syms,
                    edge_count=edge_count,
                    raw_edges=fingerprint_edges,
                    nonascii=nonascii,
                )
        finally:
            _release_shared_cache_lock(fd)

    def _count(self, key: str, sql: str) -> int:
        if key in self._counts:
            return self._counts[key]
        entry, fingerprint_syms, fingerprint_edges, _has_watch = self._memo_entry()
        with _MEMO_LOCK:
            edge_count = entry["edge_count"]
        if edge_count is None:
            # Cold: fingerprint reads already carried the symbol count and the
            # raw edge count; only the valid-edge aggregation is expensive.
            edge_count = int(self._rows(_EDGE_COUNT_SQL, (), "node_rows")[0][0])  # type: ignore[arg-type]
            with _MEMO_LOCK:
                if entry["edge_count"] is None:
                    entry["node_count"] = fingerprint_syms
                    entry["edge_count"] = edge_count
                    entry["raw_edges"] = fingerprint_edges
        value = {"nodes": fingerprint_syms, "edges": edge_count, "raw_edges": fingerprint_edges}[key]
        self._counts[key] = value
        return value

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
            entry, _syms, _edges, has_watch = self._memo_entry()
            cached: object = None
            if has_watch:
                with _MEMO_LOCK:
                    cached = entry["nonascii"]
                if cached is None:
                    # The sidecar may carry the set even when the in-process
                    # memo was warmed before its publication (SUB-193).
                    key = self._snapshot_token()[0]
                    payload = _sidecar_read(str(key[0]), key)
                    if payload is not None:
                        cached = _nonascii_rows_from_payload(payload)
                        if cached is not None:
                            entry["nonascii"] = cached
            if cached is None:
                rows = self._rows(_NONASCII_SQL, (), "node_rows")
                self._nonascii = tuple(_node_from_row(row) for row in rows)
                if has_watch:
                    with _MEMO_LOCK:
                        if entry["nonascii"] is None:
                            entry["nonascii"] = self._nonascii
            else:
                self._nonascii = cached  # type: ignore[assignment]
        assert self._nonascii is not None
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
