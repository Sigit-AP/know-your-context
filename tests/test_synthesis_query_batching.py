"""WS-D / TASK2-PLAN S1B: heavy synthesis passes must batch their store reads.

Baseline (cluster-b verdict, S1): 12 passes took 114.4s on a 14.896-symbol
store; ``module_member_calls`` alone probed 13.410 candidates with per-ref
``symbol_by_node`` + ``outgoing_edges`` + ``symbols_in_path`` — three (or
more) store round-trips per candidate. The pass must join its input sets
once per pass (the SUB-135 prefetch pattern), not per ref.

Pinned deterministically (query-call counts, never wall clock):

* ``module_member_calls`` issues O(1) ``EventStore`` lookups regardless of
  candidate count;
* results are unchanged: the same edges minted, the same refs resolved,
  ambiguity still skipped whole.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from know_your_context.resolve.synthesis import (
    PASS_MODULE_MEMBER_CALLS,
    SynthesisConfig,
    run_synthesis,
)
from know_your_context.store import EventStore

SEEN = "2026-11-08T00:00:00+00:00"
PARSE = "ingest:0123456789abcdef"


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


def _build_fanout_store(store: EventStore, count: int) -> list[str]:
    """``count`` caller functions, each importing two modules; the first
    module holds exactly one symbol named ``helper{i}`` — one unambiguous
    module-member hit per caller."""
    expected_targets: list[str] = []
    for index in range(count):
        caller_path = f"src/caller{index}.py"
        mod_a = f"pkg/mod_a{index}.py"
        mod_b = f"pkg/mod_b{index}.py"
        _add_file(store, caller_path)
        _add_file(store, mod_a)
        _add_file(store, mod_b)
        caller = f"fn:caller{index}"
        helper = f"fn:helper{index}"
        store.upsert_symbol(node_id=caller, name=f"caller{index}", kind="function", path=caller_path, line=1)
        store.upsert_symbol(node_id=helper, name=f"helper{index}", kind="function", path=mod_a, line=1)
        store.upsert_symbol(node_id=f"fn:other{index}", name=f"other{index}", kind="function", path=mod_b, line=1)
        store.add_graph_edge(caller, f"file:{mod_a}", "imports", provenance=PARSE)
        store.add_graph_edge(caller, f"file:{mod_b}", "imports", provenance=PARSE)
        ref_id = store.insert_unresolved_ref(
            source_node_id=caller, target_name=f"helper{index}", kind="calls", name_tail=None, first_seen=SEEN
        )
        store.record_resolution_attempt(ref_id, outcome="failed", reason="no_resolution")
        expected_targets.append(helper)
    return expected_targets


def _count_lookups(store: EventStore) -> dict[str, int]:
    """Wrap the per-candidate lookup methods with call counters (instance-level)."""
    counts = {"symbol_by_node": 0, "outgoing_edges": 0, "symbols_in_path": 0}
    for name in counts:
        original = getattr(store, name)

        def make_wrapper(name: str, fn):  # type: ignore[no-untyped-def]
            def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
                counts[name] += 1
                return fn(*args, **kwargs)

            return wrapper

        setattr(store, name, make_wrapper(name, original))  # noqa: B010 -- test probe on a fixture store
    return counts


def test_module_member_calls_batched_regardless_of_candidate_count(store: EventStore) -> None:
    expected_targets = _build_fanout_store(store, 30)
    counts = _count_lookups(store)
    report = run_synthesis(store, config=SynthesisConfig(enabled_passes=frozenset({PASS_MODULE_MEMBER_CALLS})))
    entry = [p for p in report.passes if p.name == PASS_MODULE_MEMBER_CALLS][0]
    # behavior pin: every caller resolved to its own helper, refs resolved
    assert entry.inserted == 30
    with store.read() as conn:
        edges = conn.execute(
            "SELECT source, target, kind FROM graph_edges WHERE provenance = 'synthesis:module_member_calls' "
            "ORDER BY source"
        ).fetchall()
        statuses = conn.execute("SELECT status FROM unresolved_refs").fetchall()
    assert {(str(s), str(t), str(k)) for s, t, k in edges} == {
        (f"fn:caller{index}", expected_targets[index], "calls") for index in range(30)
    }
    assert [str(row[0]) for row in statuses] == ["resolved"] * 30
    # performance pin: O(1) store lookups per pass, not O(candidates x paths).
    # Old shape: 30 symbol_by_node + 30 outgoing_edges + 60 symbols_in_path.
    for name, value in counts.items():
        assert value <= 3, f"{name} called {value}x for 30 candidates — pass must batch its reads"
