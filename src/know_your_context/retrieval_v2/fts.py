"""FTS5 index management for retrieval v2 (wave W4).

The full-text index is a module-owned virtual table
(``retrieval_v2_symbols_fts``) — an external-content FTS5 view over the
frozen v3 ``symbols`` table (columns ``name``, ``kind``, ``path``). The
store schema is not touched; the index lives next to it and can be
dropped/rebuilt at any time (RESEARCH-QUERY §2.1 channel 1, MASTER-PLAN
pillar 4).

Freshness (TASK2 WS-B Q1): event-driven via the v6 ``symbols_watch``
counter — one O(1) read of a trigger-maintained row that moves on ANY
``symbols`` write (helpers, pipeline batch SQL, future paths). The legacy
content fingerprint (``count(*)`` + total column bytes = a full ``symbols``
SCAN) only runs as the *arbiter* when the counter moved, and a rebuild only
happens when the arbiter confirms real drift — a no-op rewrite bumps the
counter but not the content. Before v6 this SCAN ran per cold query (~97%
of cold-query time at 1.85M symbols). Callers that need guaranteed freshness
after a length-preserving update call :func:`rebuild_fts_index` explicitly
(the ingestion pipeline does).
"""

from __future__ import annotations

import re
import sqlite3

from know_your_context.store import EventStore

__all__ = [
    "FTS_TABLE",
    "META_TABLE",
    "FtsUnavailableError",
    "build_fts_query",
    "drop_fts_index",
    "ensure_fts_index",
    "fts_available",
    "rebuild_fts_index",
]

FTS_TABLE = "retrieval_v2_symbols_fts"
META_TABLE = "retrieval_v2_meta"

# characters stripped from FTS tokens (codegraph searchNodesFTS sanitiser)
_STRIP_RE = re.compile(r"['\"*():^]")
_FTS_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})


class FtsUnavailableError(RuntimeError):
    """Raised when the SQLite build lacks FTS5 (fail-closed)."""


def fts_available(conn: sqlite3.Connection) -> bool:
    """Probe FTS5 support with a throwaway temp virtual table."""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._retrieval_v2_fts_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._retrieval_v2_fts_probe")
    except sqlite3.OperationalError:
        return False
    return True


def build_fts_query(terms: tuple[str, ...]) -> str:
    """Build the FTS5 MATCH expression: sanitised prefix tokens joined by OR.

    Mirrors codegraph: ``::`` becomes a space, ``['"*():^]`` are stripped,
    FTS operator words are dropped, and every surviving token becomes a
    quoted prefix query ``"token"*``. Empty input → empty string.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = _STRIP_RE.sub("", term.replace("::", " "))
        for token in cleaned.split():
            if not token or token.upper() in _FTS_OPERATORS or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return " OR ".join(f'"{token}"*' for token in tokens)


def _fingerprint(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT count(*), IFNULL(sum(length(node_id) + length(name) + length(kind) + length(path)"
        " + abs(line) + abs(col)), 0) FROM symbols"
    ).fetchone()
    return f"{int(row[0])}:{int(row[1])}"


def _watch_seq(conn: sqlite3.Connection) -> int:
    """The trigger-maintained symbols change counter (schema v6)."""
    row = conn.execute("SELECT seq FROM symbols_watch WHERE id = 1").fetchone()
    return int(row[0]) if row is not None else 0


def _read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        f"SELECT value FROM {META_TABLE} WHERE key = ?",  # nosec B608 -- constant table name; key bound
        (key,),
    ).fetchone()
    return None if row is None else str(row[0])


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        f"INSERT INTO {META_TABLE}(key, value) VALUES(?, ?) "  # nosec B608 -- constant table name; values bound
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _ensure_schema(conn: sqlite3.Connection) -> None:
    if not fts_available(conn):
        raise FtsUnavailableError("this SQLite build lacks FTS5; retrieval v2 search is unavailable")
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
        f"USING fts5(name, kind, path, content='symbols', content_rowid='rowid')"
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS {META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def _build_cache_kib(store: EventStore) -> int:
    """Page cache for the FTS build, from the single HardwarePlan (SUB-135).

    The build is the heaviest scan retrieval_v2 performs; giving it the
    plan's aggressive cache (⅛ MemAvailable, capped) keeps the working set
    in memory. Kill switch / detection failure / small stores → 0: the
    connection keeps whatever the store recipe already applied.
    """
    try:
        from know_your_context.hardware.profile import hardware_plan
        from know_your_context.ops.switches import load_switches

        if load_switches().hw_tuning_disabled():
            return 0
        db_path = str(getattr(store, "db_path", ":memory:"))
        plan = hardware_plan(None if db_path == ":memory:" else db_path)
        return max(0, int(plan.storage.cache_kib))
    except Exception:  # noqa: BLE001 - a cache hint must never break the build
        return 0


def rebuild_fts_index(store: EventStore) -> int:
    """Drop and re-index every symbol row; returns the indexed row count."""
    with store.transaction() as conn:
        cache_kib = _build_cache_kib(store)
        if cache_kib > 0:
            # Negative cache_size = KiB units (SQLite contract); same pragma
            # shape the store recipe applies at open (SUB-134).
            conn.execute(f"PRAGMA cache_size = {-cache_kib}")
        _ensure_schema(conn)
        # external-content tables reject raw DELETE; the FTS5 'rebuild'
        # command atomically clears the index and repopulates from symbols
        conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")  # nosec B608 -- module-owned constant table name
        count = int(conn.execute("SELECT count(*) FROM symbols").fetchone()[0])
        _write_meta(conn, "fingerprint", _fingerprint(conn))
        _write_meta(conn, "watch_seq", str(_watch_seq(conn)))
    return count


def ensure_fts_index(store: EventStore) -> bool:
    """Create the index when missing; rebuild when ``symbols`` really changed.

    O(1) fast path (TASK2 WS-B Q1): the v6 ``symbols_watch`` counter is the
    freshness token. When it matches the recorded one and a fingerprint is on
    record, the index is current — no ``symbols`` SCAN at all. Only a moved
    counter pays the legacy fingerprint SCAN (the arbiter): real drift →
    rebuild; a no-op rewrite → just re-stamp the counter. Returns True when a
    (re)build happened, False when the index was already current.
    """
    with store.transaction() as conn:
        _ensure_schema(conn)
        recorded_fp = _read_meta(conn, "fingerprint")
        recorded_seq = _read_meta(conn, "watch_seq")
        current_seq = _watch_seq(conn)
        try:
            seq_current = recorded_seq is not None and int(recorded_seq) == current_seq
        except ValueError:
            seq_current = False  # corrupt meta is a freshness event, never a crash
        if recorded_fp is not None and seq_current:
            return False
        if recorded_fp is not None and _fingerprint(conn) == recorded_fp:
            _write_meta(conn, "watch_seq", str(current_seq))
            return False
    rebuild_fts_index(store)
    return True


def drop_fts_index(store: EventStore) -> None:
    """Remove the index and its metadata; idempotent."""
    with store.transaction() as conn:
        conn.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
        conn.execute(f"DROP TABLE IF EXISTS {META_TABLE}")
