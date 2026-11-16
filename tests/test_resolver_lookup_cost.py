"""B7 (SUB-173): symbol lookups must cost O(1) rows per ref, not O(corpus).

Measured baseline (stress-3-products §6 B7): ingest L1→L3 (50× files) took
4.6→802 s (175×) while the reference tool stayed linear. Profiled at L2
(1,000 files, 38,371 refs): ``resolve_pending`` 55.0 s of 64.5 s wall, and
``store.symbols_named`` alone built **3,058,564** ``SymbolRow`` objects —
~80 rows per call, because every module defines ``__init__``/``add``/
``summary``/``json``/``os`` and the resolver materialised *every* same-named
row only to decide "exactly one?" or "ambiguous, how many?".

The fix: ask for the count (index-only), fetch the single row only when the
count is 1. These tests pin the cost model, not a wall-clock number: the
resolver may materialise at most one row per ref regardless of how many
symbols share that name.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from know_your_context.resolve import ReferenceResolver
from know_your_context.store import EventStore

SEEN = "2026-10-26T00:00:00+00:00"
SOURCE = "function:source"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _file(store: EventStore, path: str) -> None:
    store.upsert_file(
        path=path,
        sha256=hashlib.sha256(path.encode()).hexdigest(),
        size=10,
        mtime=100,
        language="python",
        generated_hint=False,
        extraction_version=1,
    )


def _seed_common_name(store: EventStore, name: str, count: int) -> None:
    """*count* symbols that share *name* — the corpus's ``json``/``add`` shape."""
    for index in range(count):
        path = f"pkg/m{index}.py"
        _file(store, path)
        store.upsert_symbol(node_id=f"function:{name}-{index}", name=name, kind="function", path=path, line=1)


def _source(store: EventStore) -> None:
    _file(store, "pkg/main.py")
    store.upsert_symbol(node_id=SOURCE, name="main", kind="function", path="pkg/main.py", line=1)


def _ref(store: EventStore, target: str) -> int:
    return store.insert_unresolved_ref(
        source_node_id=SOURCE, target_name=target, kind="calls", name_tail=None, first_seen=SEEN
    )


@contextmanager
def _count_symbol_rows(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Count ``SymbolRow`` constructions (one per fetched ``symbols`` row)."""
    calls = [0]
    original = EventStore._symbol_row

    def counting(row):  # noqa: ANN001, ANN202 - mirrors the staticmethod signature
        calls[0] += 1
        return original(row)

    monkeypatch.setattr(EventStore, "_symbol_row", staticmethod(counting))
    yield calls


def test_ambiguous_lookup_materialises_at_most_one_row_per_ref(
    store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = 300
    _seed_common_name(store, "json", common)
    _source(store)
    refs = [_ref(store, "json") for _ in range(50)]
    with _count_symbol_rows(monkeypatch) as rows:
        report = ReferenceResolver().resolve_pending(store)
    # Constant per ref (the source symbol row from ``_source_context``), never
    # proportional to how many symbols share the name: the old code fetched
    # 50 x 300 = 15,000 target rows here.
    assert rows[0] <= 2 * len(refs), f"materialised {rows[0]} rows for {len(refs)} refs (was {len(refs) * common})"
    assert report.resolved == 0
    assert all(outcome.reason == f"ambiguous_exact:{common}" for outcome in report.outcomes)
    assert [outcome.ref_id for outcome in report.outcomes] == refs


def test_unique_lookup_still_resolves_the_single_symbol(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _source(store)
    store.upsert_symbol(node_id="function:only", name="only_fn", kind="function", path="pkg/only.py", line=1)
    _file(store, "pkg/only.py")
    ref = _ref(store, "only_fn")
    with _count_symbol_rows(monkeypatch) as rows:
        report = ReferenceResolver().resolve_pending(store)
    # Two rows, both constant per ref: the source symbol and the one match.
    assert rows[0] <= 2
    assert report.resolved == 1
    assert report.outcomes[0].target_node_id == "function:only"
    assert report.outcomes[0].ref_id == ref


def test_tail_lookup_is_also_count_first(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dotted ref resolves through the tail; ambiguity is counted, not listed."""
    for index in range(120):
        path = f"pkg/t{index}.py"
        _file(store, path)
        store.upsert_symbol(node_id=f"function:dup-{index}", name="mod.Dup", kind="function", path=path, line=1)
    _source(store)
    ref = _ref(store, "mod.Dup")
    with _count_symbol_rows(monkeypatch) as rows:
        report = ReferenceResolver().resolve_pending(store)
    assert rows[0] <= 2, f"tail lookup materialised {rows[0]} rows"
    assert report.outcomes[0].reason is not None
    assert report.outcomes[0].reason.startswith("ambiguous")
    assert report.outcomes[0].ref_id == ref


def test_resolution_attempts_are_written_in_one_transaction(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """38k per-ref transactions (each its own fsync) were 14.6 s of L2's wall."""
    _seed_common_name(store, "json", 5)
    _source(store)
    for _ in range(40):
        _ref(store, "json")
    commits = [0]
    original = EventStore.transaction

    @contextmanager
    def counting(self):  # noqa: ANN001, ANN202 - mirrors the method signature
        commits[0] += 1
        with original(self) as conn:
            yield conn

    monkeypatch.setattr(EventStore, "transaction", counting)
    ReferenceResolver().resolve_pending(store)
    assert commits[0] <= 2, f"{commits[0]} write transactions for one resolve pass (edges + attempts expected)"


def test_pending_remaining_is_counted_not_materialised(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_common_name(store, "json", 3)
    _source(store)
    for _ in range(60):
        _ref(store, "json")
    materialised = [0]
    original = EventStore._unresolved_ref_row

    def counting(row):  # noqa: ANN001, ANN202 - mirrors the staticmethod signature
        materialised[0] += 1
        return original(row)

    monkeypatch.setattr(EventStore, "_unresolved_ref_row", staticmethod(counting))
    report = ReferenceResolver().resolve_pending(store)
    assert report.pending_remaining == 0
    # Constant per ref: once for the pending read, once for the recorded
    # outcome the report reads back. The old ``len(pending_unresolved_refs())``
    # added a *third* full materialisation of the queue on every pass.
    assert materialised[0] <= 2 * 60, f"{materialised[0]} unresolved-ref rows materialised for 60 refs"
