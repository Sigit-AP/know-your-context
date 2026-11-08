"""WS-D / TASK2-PLAN S1B (corrected attribution): the name-match tier must
batch its store reads.

Measured on the pinned 14.896-symbol store (20x vendored corpus): the 12
synthesis passes cost 3.7s total, while ``resolve_and_synthesize`` wall was
144-200s — the N+1 sits in ``NameMatchResolver.resolve_unmatched``:
61.816 SQL executions (103.6s) + 346.786 ``score_name_pair`` calls (73.7s)
for 13.426 refs, resolving 0. Per ref it issues ``symbol_by_node`` +
``get_file`` + 1 exact-name query + up to ``max_query_segments`` LIKE scans.

Pinned deterministically (statement counts via sqlite trace, never wall):

* ``resolve_unmatched`` issues O(distinct names + distinct segments)
  SELECTs, not O(refs x segments);
* outcomes are byte-identical: same resolutions, same edges, same reasons
  (the 54-test name_matcher suite pins behavior; this pins the batching).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from know_your_context.resolve.name_matcher import NameMatchResolver
from know_your_context.store import EventStore

SEEN = "2026-11-08T00:00:00+00:00"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _add_file(store: EventStore, path: str) -> None:
    store.upsert_file(
        path=path,
        sha256=hashlib.sha256(path.encode()).hexdigest(),
        size=10,
        mtime=100,
        language="python",
        generated_hint=False,
        extraction_version=1,
    )


def test_resolve_unmatched_batched_regardless_of_ref_count(store: EventStore) -> None:
    """25 failed refs but only 5 distinct tails — one query round per
    distinct lookup, not per ref. Old shape: >=4 SELECTs per ref (100+)."""
    for index in range(5):
        _add_file(store, f"src/mod{index}.py")
        store.upsert_symbol(
            node_id=f"fn:helper{index}", name=f"helper{index}", kind="function", path=f"src/mod{index}.py", line=1
        )
    for copy in range(5):
        for index in range(5):
            caller_path = f"src/caller{copy}_{index}.py"
            _add_file(store, caller_path)
            ref_id = store.insert_unresolved_ref(
                source_node_id=f"file:{caller_path}",
                target_name=f"helper{index}",
                kind="calls",
                name_tail=None,
                first_seen=SEEN,
            )
            store.record_resolution_attempt(ref_id, outcome="failed", reason="no_resolution")
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)  # noqa: SLF001 -- test probe on a fixture store
    try:
        report = NameMatchResolver().resolve_unmatched(store)
    finally:
        store._conn.set_trace_callback(None)  # noqa: SLF001
    # Read phase = the SELECT prefix before the first write (decisions are
    # computed from reads, then edges/ref-updates flush — in BOTH the old
    # per-ref code and the batched one; the per-ref write-path SELECTs of
    # retry_failed_ref/record_resolution_attempt are out of scope here).
    read_selects = 0
    for sql in statements:
        if not sql.lstrip().upper().startswith("SELECT"):
            break
        read_selects += 1
    # behavior pin: every ref resolved to its distinct helper
    assert report.processed == 25
    assert report.resolved == 25
    # performance pin: batched reads are O(distinct lookups), not O(refs).
    # Old shape: 1 + 25 refs x (1 symbol + 1 file + 1 exact + 2 segments) = 126.
    assert read_selects <= 40, (
        f"{read_selects} read-phase SELECTs for 25 refs / 5 distinct tails — reads must be batched per distinct lookup"
    )
