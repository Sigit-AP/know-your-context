"""Durable trigram substring index for retrieval v2 (TASK1-PUSH200-R2 item A).

The LIKE fallback channel (``retrieval_v2.lookup._like_channel``) answers
``name/path LIKE '%term%'`` with a full ``symbols`` scan — measured at
896,6 ms cold @1.847.104 symbols (task2-audit-wave1). Both references lack a
durable answer: graphify has no substring index at all, and codegraph's
``name-substring-index.ts`` (#2620) is **in-memory, rebuilt in full per
change-stamp** (``memoizedRead`` + ``readNameSubstringIndex``,
queries.ts:477/2340) — O(N) reads per invalidation, gone at process exit.

This module instead keeps a **durable** FTS5 trigram index
(``tokenize='trigram'``, external content over ``symbols(name, path)``)
whose freshness follows the v6 ``symbols_watch`` counter — O(1) steady
state, no rebuild on reopen, no per-change full re-read.

Correctness contract (pinned by ``tests/test_retrieval_v2_trigram.py``):
an FTS5 trigram MATCH is an exact **superset** of the literal-LIKE result
iff every term has literal length >= 3 (the trigram minimum; verified
against SQLite 3.53.1 with adversarial case/unicode/punctuation needles).
The LIKE predicate is therefore still applied to the candidate rows, which
makes the accelerated path byte-identical to the scan in content and
canonical order. Terms shorter than 3 characters (or the kill switch
``KNOW_YOUR_CONTEXT_NO_TRIGRAM=1``) fall back to the historical scan.
"""

from __future__ import annotations

import sqlite3

from know_your_context.retrieval_v2.fts import META_TABLE
from know_your_context.store import EventStore

__all__ = [
    "TRIGRAM_TABLE",
    "TrigramUnavailableError",
    "build_trigram_match",
    "drop_trigram_index",
    "ensure_trigram_index",
    "rebuild_trigram_index",
    "trigram_available",
]

TRIGRAM_TABLE = "retrieval_v2_symbols_trigram"

#: FTS5 trigram MATCH only covers needles of >= 3 characters.
MIN_NEEDLE_LEN = 3


class TrigramUnavailableError(RuntimeError):
    """Raised when the SQLite build lacks FTS5 trigram support (fail-closed)."""


def trigram_available(conn: sqlite3.Connection) -> bool:
    """Probe FTS5 trigram support with a throwaway temp virtual table."""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._retrieval_v2_trigram_probe USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE temp._retrieval_v2_trigram_probe")
    except sqlite3.OperationalError:
        return False
    return True


def build_trigram_match(terms: tuple[str, ...]) -> str | None:
    """Build the FTS5 MATCH expression covering both ``name`` and ``path``.

    Returns ``None`` when any term has literal length < 3 — the caller must
    fall back to the scan, because the trigram index cannot guarantee the
    superset property for such needles.
    """
    if not terms:
        return None
    parts: list[str] = []
    for term in terms:
        if len(term) < MIN_NEEDLE_LEN:
            return None
        escaped = term.replace('"', '""')
        parts.append(f'(name : "{escaped}" OR path : "{escaped}")')
    return " OR ".join(parts)


def _watch_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT seq FROM symbols_watch WHERE id = 1").fetchone()
    return int(row[0]) if row is not None else 0


def _fingerprint(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT count(*), IFNULL(sum(length(node_id) + length(name) + length(kind) + length(path)"
        " + abs(line) + abs(col)), 0) FROM symbols"
    ).fetchone()
    return f"{int(row[0])}:{int(row[1])}"


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
    if not trigram_available(conn):
        raise TrigramUnavailableError("this SQLite build lacks FTS5 trigram; LIKE fallback stays a scan")
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {TRIGRAM_TABLE} "
        f"USING fts5(name, path, content='symbols', content_rowid='rowid', tokenize='trigram')"
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS {META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def rebuild_trigram_index(store: EventStore) -> int:
    """Drop and re-index every symbol row; returns the indexed row count."""
    with store.transaction() as conn:
        _ensure_schema(conn)
        # external-content tables reject raw DELETE; the FTS5 'rebuild'
        # command atomically clears the index and repopulates from symbols
        conn.execute(
            f"INSERT INTO {TRIGRAM_TABLE}({TRIGRAM_TABLE}) VALUES('rebuild')"  # nosec B608 -- module-owned constant
        )
        count = int(conn.execute("SELECT count(*) FROM symbols").fetchone()[0])
        _write_meta(conn, "trigram_fingerprint", _fingerprint(conn))
        _write_meta(conn, "trigram_watch_seq", str(_watch_seq(conn)))
    return count


def ensure_trigram_index(store: EventStore) -> bool:
    """Create the index when missing; rebuild when ``symbols`` really changed.

    Same freshness discipline as :func:`retrieval_v2.fts.ensure_fts_index`:
    the v6 ``symbols_watch`` counter is the O(1) freshness token; the legacy
    fingerprint scan is only the arbiter once the counter moved. Returns
    True when a (re)build happened.
    """
    with store.transaction() as conn:
        _ensure_schema(conn)
        recorded_fp = _read_meta(conn, "trigram_fingerprint")
        recorded_seq = _read_meta(conn, "trigram_watch_seq")
        current_seq = _watch_seq(conn)
        try:
            seq_current = recorded_seq is not None and int(recorded_seq) == current_seq
        except ValueError:
            seq_current = False  # corrupt meta is a freshness event, never a crash
        if recorded_fp is not None and seq_current:
            return False
        if recorded_fp is not None and _fingerprint(conn) == recorded_fp:
            _write_meta(conn, "trigram_watch_seq", str(current_seq))
            return False
    rebuild_trigram_index(store)
    return True


def drop_trigram_index(store: EventStore) -> None:
    """Remove the index and its metadata; idempotent."""
    with store.transaction() as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TRIGRAM_TABLE}")
        conn.execute(f"DELETE FROM {META_TABLE} WHERE key IN ('trigram_fingerprint', 'trigram_watch_seq')")
