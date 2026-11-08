"""Capability audit-log scaling guards (TASK2 WS-F: finding F1C).

F1C (cluster-c verdict §47/§60): ``CapabilityStore.audit()`` read the WHOLE
log on every append to learn ``(seq, prev_hash)`` — O(n) per append, O(n^2)
over the log's lifetime (measured: 2.8 -> 8.9 -> 98.1 ms/op at 50/500/5000
events on this host). The append path must use the head marker as an O(1)
shortcut, with the log itself as the source of truth on any disagreement:

* head marker matches the log tail -> append from the head (O(1));
* the log is one line AHEAD of the head (crash between the log append and
  the head-marker replace) -> follow the log;
* any other disagreement, a tail line longer than the read window, or a
  missing head -> full-scan fallback (the pre-fix semantics);
* a corrupt tail line stays fail-closed (``CapabilityStoreError``).

The chain must verify from genesis after every one of these paths.
"""

from __future__ import annotations

import hashlib
import json
import tracemalloc
from pathlib import Path

import pytest

from know_your_context.security.capabilities import (
    AUDIT_HEAD_NAME,
    AUDIT_LOG_NAME,
    CapabilityStore,
    CapabilityStoreError,
)

# Memory budget for ONE append over a pre-seeded log. The old full-read
# implementation materialized the entire log (4000 lines x ~250 B payload ->
# >1 MiB of bytes plus a 4000-entry list) on every append; the head-shortcut
# path reads only the small tail window. The budget sits between the two
# with >2x margin (pattern: tests/test_store_scaling_guards.py S2).
_APPEND_PEAK_BUDGET_BYTES = 384 * 1024
_SEED_EVENTS = 4000


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pytest.fixture
def store(tmp_path: Path) -> CapabilityStore:
    instance = CapabilityStore(tmp_path)
    instance.ensure_secret()
    return instance


def _seed(store: CapabilityStore, count: int) -> None:
    for i in range(count):
        store.audit({"event": "seed", "i": i}, now=1_700_000_000.0 + i)


def _log_lines(store: CapabilityStore) -> list[str]:
    return store.audit_path.read_text(encoding="utf-8").splitlines()


def _last_record(store: CapabilityStore) -> dict:
    return json.loads(_log_lines(store)[-1])


def _manual_line(seq: int, prev: str, event: dict, *, at: float) -> str:
    record: dict = {"seq": seq, "prev": prev, "at": at, **event}
    record["hash"] = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
    return _canonical(record) + "\n"


def test_audit_append_memory_is_bounded_in_log_length(store: CapabilityStore) -> None:
    _seed(store, _SEED_EVENTS)
    tracemalloc.start()
    try:
        store.audit({"event": "measured"}, now=1_700_100_000.0)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert store.verify_audit_chain()
    assert peak < _APPEND_PEAK_BUDGET_BYTES, (
        f"one audit append peaked at {peak / 1024:.0f} KiB over a {_SEED_EVENTS}-event log; "
        f"budget {_APPEND_PEAK_BUDGET_BYTES / 1024:.0f} KiB — the append must not re-read the whole log"
    )


def test_audit_append_follows_log_when_ahead_of_head(store: CapabilityStore) -> None:
    """Crash between the log append and the head-marker replace: the next
    append must follow the LOG (the pre-fix semantics), not the stale head."""
    _seed(store, 3)
    last = _last_record(store)
    # Simulate the crash: line 4 lands in the log, the head marker still says 3.
    with store.audit_path.open("a", encoding="utf-8") as handle:
        handle.write(_manual_line(4, str(last["hash"]), {"event": "crashed-append"}, at=1_700_000_100.0))
    assert store.audit_head_path.read_text(encoding="utf-8").split()[0] == "3"
    store.audit({"event": "after-crash"}, now=1_700_000_100.0)
    head_seq, head_hash = store.audit_head_path.read_text(encoding="utf-8").split()
    appended = _last_record(store)
    assert (head_seq, head_hash) == ("5", appended["hash"])
    assert appended["seq"] == 5
    assert appended["prev"] == json.loads(_log_lines(store)[-2])["hash"]
    assert store.verify_audit_chain()


def test_audit_append_falls_back_when_head_marker_disagrees(store: CapabilityStore) -> None:
    """A tampered/garbage head marker must never steer the chain: append
    follows the log and rewrites the head from the log's truth."""
    _seed(store, 3)
    store.audit_head_path.write_text("999 " + "f" * 64 + "\n", encoding="utf-8")
    store.audit({"event": "after-tamper"}, now=1_700_000_100.0)
    appended = _last_record(store)
    assert appended["seq"] == 4
    head_seq, head_hash = store.audit_head_path.read_text(encoding="utf-8").split()
    assert (head_seq, head_hash) == ("4", appended["hash"])
    assert store.verify_audit_chain()


def test_audit_append_without_head_marker_bootstraps_from_scan(store: CapabilityStore) -> None:
    _seed(store, 3)
    store.audit_head_path.unlink()
    store.audit({"event": "no-head"}, now=1_700_000_100.0)
    assert _last_record(store)["seq"] == 4
    assert store.verify_audit_chain()


def test_audit_append_corrupt_tail_line_stays_fail_closed(store: CapabilityStore) -> None:
    _seed(store, 3)
    with store.audit_path.open("a", encoding="utf-8") as handle:
        handle.write("this-is-not-json\n")
    with pytest.raises(CapabilityStoreError):
        store.audit({"event": "doomed"}, now=1_700_000_100.0)


def test_audit_append_after_oversized_line(store: CapabilityStore) -> None:
    """A line longer than the tail window must degrade to the full-scan
    fallback with identical semantics (the log stays the source of truth)."""
    store.audit({"event": "big", "blob": "y" * 200_000}, now=1_700_000_000.0)
    store.audit({"event": "after-big"}, now=1_700_000_100.0)
    appended = _last_record(store)
    assert appended["seq"] == 2
    assert appended["prev"] == json.loads(_log_lines(store)[0])["hash"]
    assert store.verify_audit_chain()


def test_audit_chain_intact_across_mixed_paths(store: CapabilityStore) -> None:
    """Issue/revoke traffic over the fast path keeps the chain verifiable
    from genesis to the head marker at every step."""
    token_records = []
    for i in range(25):
        token, capability = store.issue(
            actor=f"agent-{i}", scope="read", verbs=["knowledge_get_node"], now=1_700_000_000.0 + i
        )
        token_records.append((token, capability))
        assert store.verify_audit_chain()
    for _token, capability in token_records[:5]:
        store.revoke(capability.nonce, now=1_700_000_100.0)
        assert store.verify_audit_chain()
    events = store.audit_events()
    assert len(events) == 30
    assert events[-1]["seq"] == 30


def test_audit_log_and_head_names_unchanged(store: CapabilityStore) -> None:
    """The on-disk contract (file names) is untouched by the fix."""
    assert store.audit_path.name == AUDIT_LOG_NAME == "capability-audit.jsonl"
    assert store.audit_head_path.name == AUDIT_HEAD_NAME == "capability-audit.head"


def test_registry_load_isolates_the_cache(store: CapabilityStore) -> None:
    """The cached registry must never be mutated through a loaded view.

    Pins the defensive-copy contract while the copy gets cheaper (F1C
    residual: the json round-trip deep copy is O(n) with a serializer
    constant; a two-level copy keeps the same isolation for every mutation
    the store itself performs).
    """
    store.issue(actor="agent-a", scope="read", verbs=["knowledge_get_node"], now=1_700_000_000.0)
    registry = store._load_registry()
    registry["issued"]["injected"] = {"nonce": "injected"}
    registry["revoked"]["injected"] = {"revoked_at": 0.0}
    listed = store.list_registry()
    assert all(entry["nonce"] != "injected" for entry in listed["issued"])
    assert all(entry["nonce"] != "injected" for entry in listed["revoked"])
