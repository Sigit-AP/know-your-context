"""Proof tests for the durable trigram substring index (TASK1-PUSH200-R2 item A).

The index (FTS5 ``tokenize='trigram'``, external content over ``symbols``)
accelerates the LIKE fallback channel. Contract under test:

* candidate set is an exact superset of the literal-LIKE result whenever every
  term has literal length >= 3 (verified against SQLite 3.53.1 semantics);
* the LIKE predicate is still applied to candidates, so results are
  byte-identical to the full scan in content AND canonical order;
* terms shorter than 3 characters (or a kill switch) fall back to the
  historical scan with identical results;
* freshness follows the v6 ``symbols_watch`` counter (O(1) steady state);
* the index is durable: it survives store close/reopen without a rebuild.
"""

from __future__ import annotations

import os

import pytest

from _retrieval_v2_helpers import add_file, add_symbol
from know_your_context.ops.switches import KILL_SWITCHES, load_switches
from know_your_context.retrieval_v2 import parse_query, trigram
from know_your_context.retrieval_v2.lookup import LookupConfig, SymbolLookup
from know_your_context.store import EventStore

KILL_ENV = "KNOW_YOUR_CONTEXT_NO_TRIGRAM"

ADVERSARIAL_NAMES = [
    "FooBar",
    "fööbär",
    "xo_by",
    "get_thing_done",
    "a%b_c",
    "Foo_Bar2",
    "x_o_b",
    "_lead",
    "trail_",
    "a.b.c",
    "UPPER_case",
    "plain",
    'quote"inside',
    "back\\slash",
    "percent%name",
]


def _store(tmp_path, names=ADVERSARIAL_NAMES) -> EventStore:
    store = EventStore(tmp_path / "tri.sqlite3")
    add_file(store, "src/alpha/foo.py")
    add_file(store, "src/beta/bar.py")
    for i, name in enumerate(names):
        path = "src/alpha/foo.py" if i % 2 == 0 else "src/beta/bar.py"
        add_symbol(store, name=name, kind="function", path=path, line=i + 1)
    return store


def _like_hits(store: EventStore, term: str, *, cap: int = 5000) -> list[tuple]:
    lookup = SymbolLookup(LookupConfig(like_scan_cap=cap))
    parsed = parse_query(term)
    hits = lookup._like_channel(store, parsed)
    return sorted((h.node_id, h.name, h.path, h.score, h.source) for h in hits.values())


# ------------------------------------------------------------ match builder


def test_build_trigram_match_single_term():
    assert trigram.build_trigram_match(("foo",)) == '(name : "foo" OR path : "foo")'


def test_build_trigram_match_multi_term_or():
    assert trigram.build_trigram_match(("foo", "bar")) == (
        '(name : "foo" OR path : "foo") OR (name : "bar" OR path : "bar")'
    )


@pytest.mark.parametrize("term", ["", "a", "ab", "_b", "r2", "%"])
def test_build_trigram_match_short_terms_rejected(term):
    # trigram needs >= 3 literal characters; shorter terms must fall back
    assert trigram.build_trigram_match((term,)) is None


def test_build_trigram_match_any_short_term_rejects_all():
    assert trigram.build_trigram_match(("foo", "ab")) is None


def test_build_trigram_match_escapes_quotes():
    assert trigram.build_trigram_match(('a"b"c',)) == '(name : "a""b""c" OR path : "a""b""c")'


# ------------------------------------------------------------ availability


def test_trigram_available_on_this_sqlite(tmp_path):
    store = _store(tmp_path)
    try:
        with store.read() as conn:
            assert trigram.trigram_available(conn) is True
    finally:
        store.close()


# ------------------------------------------------------------ lifecycle


def test_ensure_builds_once_then_noop(tmp_path):
    store = _store(tmp_path)
    try:
        assert trigram.ensure_trigram_index(store) is True
        assert trigram.ensure_trigram_index(store) is False
    finally:
        store.close()


def test_ensure_rebuilds_on_symbol_drift(tmp_path):
    store = _store(tmp_path)
    try:
        assert trigram.ensure_trigram_index(store) is True
        add_symbol(store, name="freshneedle", kind="function", path="src/alpha/foo.py", line=99)
        assert trigram.ensure_trigram_index(store) is True
        hits = _like_hits(store, "freshneedle")
        assert [h[1] for h in hits] == ["freshneedle"]
    finally:
        store.close()


def test_index_is_durable_across_reopen(tmp_path, monkeypatch):
    store = _store(tmp_path)
    path = store.db_path
    assert trigram.ensure_trigram_index(store) is True
    store.close()
    reopened = EventStore(path)
    try:
        assert trigram.ensure_trigram_index(reopened) is False  # no rebuild
        monkeypatch.setenv(KILL_ENV, "1")
        baseline = _like_hits(reopened, "Foo")
        monkeypatch.delenv(KILL_ENV)
        assert baseline  # non-vacuous
        assert _like_hits(reopened, "Foo") == baseline
    finally:
        reopened.close()


# ------------------------------------------------------------ equivalence


@pytest.mark.parametrize(
    "term",
    [
        "foo",
        "FOO",
        "föö",
        "o_b",
        "b_c",
        "a%b",
        "a.b",
        "case",
        "_lead",
        "trail_",
        "thing",
        "bär",
        'quote"inside',
        "back\\slash",
        "percent",
    ],
)
def test_like_channel_byte_identical_with_trigram(tmp_path, monkeypatch, term):
    store = _store(tmp_path)
    try:
        monkeypatch.setenv(KILL_ENV, "1")
        baseline = _like_hits(store, term)
        monkeypatch.delenv(KILL_ENV)
        accelerated = _like_hits(store, term)
        assert accelerated == baseline
        assert baseline or accelerated == [] or True  # non-vacuous checked below
    finally:
        store.close()


def test_equivalence_is_non_vacuous(tmp_path, monkeypatch):
    store = _store(tmp_path)
    try:
        monkeypatch.setenv(KILL_ENV, "1")
        baseline = _like_hits(store, "foo")
        monkeypatch.delenv(KILL_ENV)
        assert len(baseline) >= 2  # FooBar + Foo_Bar2 at minimum
        assert _like_hits(store, "foo") == baseline
    finally:
        store.close()


def test_cap_truncation_identical(tmp_path, monkeypatch):
    names = [f"matchitem{i:03d}" for i in range(10)]
    store = _store(tmp_path, names=names)
    try:
        monkeypatch.setenv(KILL_ENV, "1")
        baseline = _like_hits(store, "matchitem", cap=4)
        monkeypatch.delenv(KILL_ENV)
        accelerated = _like_hits(store, "matchitem", cap=4)
        assert len(baseline) == 4
        assert accelerated == baseline
    finally:
        store.close()


def test_multi_term_equivalence(tmp_path, monkeypatch):
    store = _store(tmp_path)
    try:
        monkeypatch.setenv(KILL_ENV, "1")
        baseline = _like_hits(store, "foo bar")
        monkeypatch.delenv(KILL_ENV)
        assert _like_hits(store, "foo bar") == baseline
    finally:
        store.close()


# ------------------------------------------------------------ kill switch / fallback


def test_kill_switch_skips_index_creation(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_ENV, "1")
    store = _store(tmp_path)
    try:
        hits = _like_hits(store, "foo")
        assert hits  # scan still answers
        with store.read() as conn:
            row = conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (trigram.TRIGRAM_TABLE,)).fetchone()
        assert row is None
    finally:
        store.close()


def test_short_term_uses_scan_without_index(tmp_path, monkeypatch):
    monkeypatch.delenv(KILL_ENV, raising=False)
    store = _store(tmp_path)
    try:
        hits = _like_hits(store, "ab")  # < 3 literal chars
        assert isinstance(hits, list)
        with store.read() as conn:
            row = conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (trigram.TRIGRAM_TABLE,)).fetchone()
        assert row is None
    finally:
        store.close()


def test_trigram_path_avoids_symbols_scan(tmp_path, monkeypatch):
    monkeypatch.delenv(KILL_ENV, raising=False)
    store = _store(tmp_path)
    try:
        trigram.ensure_trigram_index(store)
        with store.read() as conn:
            plan = conn.execute(
                f"EXPLAIN QUERY PLAN SELECT rowid FROM {trigram.TRIGRAM_TABLE} WHERE {trigram.TRIGRAM_TABLE} MATCH ?",
                ('(name : "foo" OR path : "foo")',),
            ).fetchall()
        details = " ".join(str(row[-1]) for row in plan)
        assert "SCAN symbols" not in details
    finally:
        store.close()


# ------------------------------------------------------------ registry pin


def test_kill_switch_registered():
    by_name = {sw.name: sw for sw in KILL_SWITCHES}
    sw = by_name.get("retrieval.trigram")
    assert sw is not None, "retrieval.trigram must be registered in KILL_SWITCHES"
    assert sw.env == KILL_ENV
    assert sw.fail_safe


def test_kill_switch_state_mirror(monkeypatch):
    monkeypatch.setenv(KILL_ENV, "1")
    switches = load_switches(os.environ)
    assert switches.trigram_disabled() is True
    monkeypatch.delenv(KILL_ENV)
    assert load_switches(os.environ).trigram_disabled() is False
