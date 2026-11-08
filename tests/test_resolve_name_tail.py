"""Resolver tail tier via the materialized ``symbols.name_tail`` index (TASK2 WS-B R1, SUB-160).

The v6 index path (``name_tail = ?``) is used for separator-free tails and is
provably equivalent to the legacy ``name = ? OR name LIKE '%.tail' OR name
LIKE '%::tail'`` scan; tails containing '.' or '::' keep the legacy LIKE path
byte-for-byte. Also covers the R2 ``files.path_rev`` suffix index for
``_module_file``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from _retrieval_v2_helpers import add_file, add_symbol
from know_your_context.resolve.resolver import ReferenceResolver, _escape_like
from know_your_context.store import EventStore, SymbolRow

TAIL_SQL = "SELECT * FROM symbols WHERE name_tail = ? ORDER BY path, line, col, node_id"
MODULE_SQL = "SELECT * FROM files WHERE lower(path_rev) GLOB ? ORDER BY path"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _legacy_tail_candidates(store: EventStore, tail: str) -> list[SymbolRow]:
    """The pre-v6 implementation, kept here as the reference oracle."""
    found: dict[str, SymbolRow] = {symbol.node_id: symbol for symbol in store.symbols_named(tail)}
    like = _escape_like(tail)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' "
            "ORDER BY path, line, col, node_id",
            (f"%.{like}", f"%::{like}"),
        ).fetchall()
    for row in rows:
        symbol = SymbolRow(
            node_id=str(row["node_id"]),
            name=str(row["name"]),
            kind=str(row["kind"]),
            path=str(row["path"]),
            line=int(row["line"]),
            col=int(row["col"]),
        )
        found.setdefault(symbol.node_id, symbol)
    return sorted(found.values(), key=lambda symbol: (symbol.path, symbol.line, symbol.col, symbol.node_id))


def _explain(store: EventStore, sql: str, params: tuple[str, ...]) -> str:
    with store.read() as conn:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(str(row[-1]) for row in rows)


class TestTailIndexPath:
    def test_index_path_matches_legacy_oracle(self, store: EventStore) -> None:
        add_symbol(store, "pkg.mod.func", "function", "a.py", 1)
        add_symbol(store, "other.func", "function", "b.py", 2)
        add_symbol(store, "func", "function", "c.py", 3)
        add_symbol(store, "pkg::func", "function", "d.py", 4)
        add_symbol(store, "unrelated", "function", "e.py", 5)
        resolver = ReferenceResolver()
        assert resolver._tail_candidates(store, "func") == _legacy_tail_candidates(store, "func")
        assert [symbol.name for symbol in resolver._tail_candidates(store, "func")] == [
            "pkg.mod.func",
            "other.func",
            "func",
            "pkg::func",
        ]

    def test_miss_is_empty(self, store: EventStore) -> None:
        add_symbol(store, "alpha", "function", "a.py", 1)
        resolver = ReferenceResolver()
        assert resolver._tail_candidates(store, "zzz_missing") == []

    def test_tail_query_uses_name_tail_index(self, store: EventStore) -> None:
        add_symbol(store, "alpha", "function", "a.py", 1)
        detail = _explain(store, TAIL_SQL, ("alpha",))
        assert "SEARCH" in detail
        assert "idx_symbols_name_tail" in detail

    def test_exotic_tail_with_double_colon_uses_legacy_path(self, store: EventStore) -> None:
        """A tail containing '::' cannot be a single name_tail segment; the
        resolver must keep the legacy LIKE path for it (byte-identical)."""
        nid = add_symbol(store, "x.a::b", "function", "a.py", 1)
        resolver = ReferenceResolver()
        candidates = resolver._tail_candidates(store, "a::b")
        assert [symbol.node_id for symbol in candidates] == [nid]
        assert candidates == _legacy_tail_candidates(store, "a::b")

    def test_exotic_tail_with_dot_uses_legacy_path(self, store: EventStore) -> None:
        nid = add_symbol(store, "x.README.md", "variable", "a.py", 1)
        resolver = ReferenceResolver()
        candidates = resolver._tail_candidates(store, "README.md")
        assert [symbol.node_id for symbol in candidates] == [nid]
        assert candidates == _legacy_tail_candidates(store, "README.md")


class TestModuleFileSuffixIndex:
    def test_suffix_match_unchanged(self, store: EventStore) -> None:
        add_file(store, "pkg/mod.py")
        add_file(store, "other/deep/pkg/mod.py")
        add_file(store, "unrelated.py")
        resolver = ReferenceResolver()
        status, payload = resolver._module_file(store, "pkg/mod.py")
        assert status == "hit"
        assert getattr(payload, "path", None) == "pkg/mod.py"
        status, payload = resolver._module_file(store, "mod.py")
        assert status == "ambiguous"
        assert payload == 2
        assert resolver._module_file(store, "absent.py") == ("miss", None)

    def test_suffix_query_uses_path_rev_index(self, store: EventStore) -> None:
        add_file(store, "pkg/mod.py")
        detail = _explain(store, MODULE_SQL, ("dom/gkp/*",))
        assert "SEARCH" in detail
        assert "idx_files_path_rev" in detail

    def test_upsert_file_populates_path_rev(self, store: EventStore) -> None:
        add_file(store, "pkg/mod.py")
        with store.read() as conn:
            row = conn.execute("SELECT path_rev FROM files WHERE path = 'pkg/mod.py'").fetchone()
        assert row is not None
        assert str(row[0]) == "pkg/mod.py"[::-1]

    def test_suffix_match_with_glob_metacharacters(self, store: EventStore) -> None:
        """GLOB wildcards in a module name are literal (bracket-escaped)."""
        add_file(store, "pkg/we*rd.py")
        add_file(store, "pkg/weXrd.py")
        resolver = ReferenceResolver()
        status, payload = resolver._module_file(store, "we*rd.py")
        assert status == "hit"
        assert getattr(payload, "path", None) == "pkg/we*rd.py"
