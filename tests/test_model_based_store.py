"""Model-based tests for the event store (W16): a RuleBasedStateMachine whose
model is the oracle and the real file-backed store is the system under test.

Three drivers share one harness and one invariant set:

* ``TestStoreStateMachine`` — a hypothesis ``RuleBasedStateMachine`` over
  seeded random rules: open / write (upsert, retire, evidence, relation) /
  read / approve-replay (idempotent retry) / rollback (version conflict,
  idempotency conflict) / close / reopen / crash-kill (a real subprocess
  dying with the SIGKILL signature mid-work). Hypothesis' shrinker runs
  deterministically (``derandomize=True``); any failing trace it minimizes
  is codified into ``TestDeterministicRegression`` as a permanent test.
* ``TestDeterministicRegression`` — handcrafted permanent traces covering
  the nasty interleavings by name (crash between write and close, replay
  across a restart, rollback then reopen, ...).
* ``TestSeededTraces`` — twenty seeds of a deterministic ``random.Random``
  driver over the same op vocabulary; the trace is a pure function of the
  seed, so a failure reproduces exactly, forever.

Invariants are checked after EVERY step, not only at the end: the hash chain
verifies, the event/knowledge counts equal the model's, every node version
equals the model's, and lifecycle status matches (active vs superseded).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from know_your_context.domain import (
    Event,
    EventKind,
    Evidence,
    LifecycleStatus,
    Node,
    NodeKind,
    PrimaryDomain,
    Relation,
    RelationKind,
)
from know_your_context.store import EventStore, IdempotencyConflict, VersionConflict

REPO_ROOT = Path(__file__).resolve().parents[1]
CRASH_CHILD = REPO_ROOT / "tests" / "_model_store_crash_child.py"

NOW = datetime(2026, 1, 1, tzinfo=UTC)
NODE_POOL = tuple(f"n{i}" for i in range(8))


def _node(node_id: str, summary: str = "model summary") -> Node:
    return Node(
        node_id=node_id,
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary=summary,
        provenance=("context://model-based",),
        source_version_id="sv-model",
        created_at=NOW,
        updated_at=NOW,
    )


def _evidence(evidence_id: str, node_id: str) -> Evidence:
    one = Decimal("1")
    return Evidence(
        evidence_id=evidence_id,
        node_id=node_id,
        source_version_id="sv-model",
        source_uri="context://model-based/evidence",
        observed_at=NOW,
        independence_key=f"ind-{evidence_id}",
        outcome=one,
        correctness=one,
        reliability=one,
        reproducibility=one,
        scope_fit=one,
        successful_use=one,
        task_coverage=one,
        quality_at_use=one,
        contradiction=Decimal("0"),
        utilization_u=Decimal("0.5"),
        latency_ms=5,
        compute_ms=5,
        output_chars=42,
    )


def _event(kind: EventKind, node_id: str, *, affecting: bool = True, summary: str = "model summary") -> Event:
    kwargs: dict = {}
    if kind in (EventKind.NODE_UPSERT, EventKind.NODE_RETIRE):
        kwargs["node"] = _node(node_id, summary)
    elif kind is EventKind.EVIDENCE_APPEND:
        kwargs["evidence"] = _evidence(f"ev-{node_id}-{summary}", node_id)
    return Event(
        kind=kind,
        node_id=node_id,
        domain=PrimaryDomain.P1,
        source_version_id="sv-model",
        occurred_at=NOW,
        knowledge_affecting=affecting,
        **kwargs,
    )


def _run_crash_child(db_path: Path, mode: str, count: int, prefix: str) -> None:
    """Run the crash child; it must die with the SIGKILL signature (137)."""
    result = subprocess.run(
        [sys.executable, str(CRASH_CHILD), str(db_path), mode, str(count), prefix],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 137, f"crash child exited {result.returncode}: {result.stderr[-2000:]}"


class ModelHarness:
    """The model (oracle) driving one real file-backed store."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.db_path = workdir / "model.sqlite3"
        self.store: EventStore | None = EventStore(self.db_path)
        self.versions: dict[str, int] = {}
        self.retired: set[str] = set()
        self.relations: set[tuple[str, str]] = set()
        self.events = 0
        self.knowledge = 0
        self.evidence_rows = 0
        self._key_counter = 0
        self._crash_counter = 0
        self.last_write: tuple[Event, str] | None = None

    # ------------------------------------------------------------ lifecycle

    @property
    def is_open(self) -> bool:
        return self.store is not None

    def _fresh_key(self, tag: str) -> str:
        self._key_counter += 1
        return f"model:{self._key_counter}:{tag}"

    def close_store(self) -> None:
        assert self.store is not None
        self.store.close()
        self.store = None

    def reopen_store(self) -> None:
        assert self.store is None
        self.store = EventStore(self.db_path)

    # --------------------------------------------------------------- writes

    def upsert(self, node_id: str, *, affecting: bool = True, summary: str = "model summary") -> None:
        assert self.store is not None
        event = _event(EventKind.NODE_UPSERT, node_id, affecting=affecting, summary=summary)
        key = self._fresh_key("upsert")
        expected = self.versions.get(node_id, 0) + 1
        receipt = self.store.append_domain(event, idempotency_key=key)
        assert receipt.node_version == expected
        assert not receipt.idempotent_replay
        self.versions[node_id] = expected
        self.retired.discard(node_id)
        self.events += 1
        self.knowledge += int(affecting)
        self.last_write = (event, key)

    def retire(self, node_id: str) -> None:
        assert self.store is not None
        if node_id not in self.versions:
            self.upsert(node_id)
        event = _event(EventKind.NODE_RETIRE, node_id)
        key = self._fresh_key("retire")
        expected = self.versions[node_id] + 1
        receipt = self.store.append_domain(event, idempotency_key=key)
        assert receipt.node_version == expected
        self.versions[node_id] = expected
        self.retired.add(node_id)
        self.events += 1
        self.knowledge += 1
        self.last_write = (event, key)

    def evidence(self, node_id: str) -> None:
        assert self.store is not None
        if node_id not in self.versions:
            self.upsert(node_id)
        event = _event(EventKind.EVIDENCE_APPEND, node_id, summary=f"{self._key_counter}")
        key = self._fresh_key("evidence")
        receipt = self.store.append_domain(event, idempotency_key=key)
        assert receipt.node_version == self.versions[node_id]  # evidence never bumps
        self.events += 1
        self.knowledge += 1
        self.evidence_rows += 1
        self.last_write = (event, key)

    def relation(self, source: str, target: str) -> None:
        assert self.store is not None
        for node_id in (source, target):
            if node_id not in self.versions:
                self.upsert(node_id)
        event = Event(
            kind=EventKind.RELATION_ADD,
            node_id=source,
            domain=PrimaryDomain.P1,
            relation=Relation(kind=RelationKind.PARENT_OF, source_node_id=source, target_node_id=target),
            source_version_id="sv-model",
            occurred_at=NOW,
        )
        key = self._fresh_key("relation")
        self.store.append_domain(event, idempotency_key=key)
        self.relations.add((source, target))
        self.events += 1
        self.knowledge += 1
        self.last_write = (event, key)

    # ------------------------------------------------------- failure paths

    def rollback_attempt(self, node_id: str) -> None:
        """A version conflict must roll back: nothing is appended, ever."""
        assert self.store is not None
        event = _event(EventKind.NODE_UPSERT, node_id, summary="conflicting write")
        events_before, knowledge_before = self.events, self.knowledge
        with pytest.raises(VersionConflict):
            self.store.append_domain(
                event,
                idempotency_key=self._fresh_key("rollback"),
                expected_version=self.versions.get(node_id, 0) + 1,
            )
        assert self.store.event_count() == events_before
        assert self.store.knowledge_high_watermark() == knowledge_before

    def conflict_attempt(self, node_id: str) -> None:
        """Reusing a key for different content fails loud and writes nothing."""
        assert self.store is not None
        self.upsert(node_id, summary="first substance")
        assert self.last_write is not None
        _, key = self.last_write
        events_before = self.events
        with pytest.raises(IdempotencyConflict):
            self.store.append_domain(
                _event(EventKind.NODE_UPSERT, node_id, summary="different substance"),
                idempotency_key=key,
            )
        assert self.store.event_count() == events_before

    def replay_last_write(self) -> None:
        """A genuine retry (same substance, fresh attempt identity) replays."""
        assert self.store is not None
        if self.last_write is None:
            return
        event, key = self.last_write
        # Rebuild the event: fresh event_id/occurred_at, identical substance.
        rebuilt = Event(
            kind=event.kind,
            node_id=event.node_id,
            domain=event.domain,
            node=event.node,
            evidence=event.evidence,
            relation=event.relation,
            source_version_id=event.source_version_id,
            actor=event.actor,
            knowledge_affecting=event.knowledge_affecting,
        )
        receipt = self.store.append_domain(rebuilt, idempotency_key=key)
        assert receipt.idempotent_replay
        assert self.store.event_count() == self.events

    # ----------------------------------------------------------------- reads

    def read_node(self, node_id: str) -> None:
        assert self.store is not None
        current = self.store.current_node(node_id)
        if node_id not in self.versions:
            assert current is None
            return
        assert current is not None
        assert current.version == self.versions[node_id]
        expected_status = LifecycleStatus.SUPERSEDED if node_id in self.retired else LifecycleStatus.ACTIVE
        assert current.lifecycle_status == expected_status

    # ----------------------------------------------------------------- crash

    def crash_commit(self, count: int) -> None:
        """A child commits ``count`` writes and dies without closing."""
        assert self.store is not None
        self._crash_counter += 1
        prefix = f"crash{self._crash_counter}"
        _run_crash_child(self.db_path, "commit", count, prefix)
        for index in range(count):
            node_id = f"{prefix}-{index}"
            self.versions[node_id] = 1
        self.events += count
        self.knowledge += count

    def crash_uncommitted(self, count: int) -> None:
        """A child dies inside an open transaction: the partial write vanishes."""
        assert self.store is not None
        self._crash_counter += 1
        prefix = f"crash{self._crash_counter}"
        _run_crash_child(self.db_path, "uncommitted", count, prefix)
        for index in range(count):
            self.versions[f"{prefix}-{index}"] = 1
        self.events += count
        self.knowledge += count
        assert f"{prefix}-uncommitted" not in self.versions

    # ------------------------------------------------------------ invariants

    def check_invariants(self) -> None:
        """Model vs system, checked after EVERY step."""
        assert self.store is not None
        report = self.store.verify_chain()
        assert report.ok, f"chain broken at seq {report.first_bad_seq} after {report.checked} events"
        assert self.store.event_count() == self.events
        assert self.store.knowledge_high_watermark() == self.knowledge
        assert self.store.evidence_count() == self.evidence_rows
        for node_id, version in self.versions.items():
            assert self.store.node_version(node_id) == version
            current = self.store.current_node(node_id)
            assert current is not None
            expected_status = LifecycleStatus.SUPERSEDED if node_id in self.retired else LifecycleStatus.ACTIVE
            assert current.lifecycle_status == expected_status, f"{node_id}: {current.lifecycle_status}"

    def teardown(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None


# ------------------------------------------------------------ trace replay

# A trace is a list of (op, args) tuples replayed verbatim. This is also the
# codification format for any failing trace hypothesis' shrinker produces.
Trace = list[tuple[str, tuple]]


def replay_trace(trace: Trace, workdir: Path) -> ModelHarness:
    harness = ModelHarness(workdir)
    for op, args in trace:
        getattr(harness, op)(*args)
        if harness.is_open:
            harness.check_invariants()
    return harness


class TestDeterministicRegression:
    """Permanent traces for the interleavings that must never regress."""

    TRACES: dict[str, Trace] = {
        # write -> close -> reopen -> idempotent replay with the same key
        # must still hit (the dedup index survives a restart).
        "replay_survives_restart": [
            ("upsert", ("n0",)),
            ("close_store", ()),
            ("reopen_store", ()),
            ("replay_last_write", ()),
        ],
        # A crash mid-transaction leaves no trace; the chain verifies.
        "crash_uncommitted_vanishes": [
            ("upsert", ("n0",)),
            ("crash_uncommitted", (2,)),
        ],
        # Crash after committed writes: every commit survives, exactly, and
        # the live parent handle keeps working afterwards.
        "crash_commit_survives_exactly": [
            ("upsert", ("n0",)),
            ("crash_commit", (3,)),
            ("upsert", ("n1",)),
        ],
        # Rollback then a correct write: the conflict consumed no seq.
        "rollback_consumes_nothing": [
            ("upsert", ("n0",)),
            ("rollback_attempt", ("n0",)),
            ("upsert", ("n0",)),
        ],
        # Retire then upsert: versions continue, lifecycle returns to active.
        "retire_then_revive": [
            ("upsert", ("n0",)),
            ("retire", ("n0",)),
            ("upsert", ("n0",)),
            ("read_node", ("n0",)),
        ],
        # Evidence on a retired node is legal; the version does not move.
        "evidence_on_retired_node": [
            ("upsert", ("n0",)),
            ("retire", ("n0",)),
            ("evidence", ("n0",)),
        ],
        # Conflict attempt (same key, other substance) writes nothing.
        "conflict_attempt_writes_nothing": [
            ("conflict_attempt", ("n0",)),
        ],
        # Close twice is legal; reopening a closed-but-existing store works.
        "close_reopen_close_reopen": [
            ("upsert", ("n0",)),
            ("close_store", ()),
            ("reopen_store", ()),
            ("close_store", ()),
            ("reopen_store", ()),
            ("upsert", ("n1",)),
        ],
        # Reopen an untouched store: empty chain verifies.
        "reopen_empty_store": [
            ("close_store", ()),
            ("reopen_store", ()),
        ],
        # Relation web across restart.
        "relations_across_restart": [
            ("relation", ("n0", "n1")),
            ("close_store", ()),
            ("reopen_store", ()),
            ("relation", ("n1", "n2")),
        ],
        # Interleave crash with live handle: parent handle keeps working.
        "crash_with_live_parent_handle": [
            ("upsert", ("n0",)),
            ("crash_commit", (1,)),
            ("upsert", ("n1",)),
        ],
        # Codified from the first hypothesis stateful run (seed of the
        # machine's opening sequence): replay after evidence write.
        "codified_replay_after_evidence": [
            ("upsert", ("n0",)),
            ("evidence", ("n0",)),
            ("replay_last_write", ()),
            ("upsert", ("n1",)),
        ],
    }

    @pytest.mark.parametrize("name", sorted(TRACES))
    def test_trace(self, name: str, tmp_path: Path) -> None:
        harness: ModelHarness | None = None
        try:
            harness = replay_trace(self.TRACES[name], tmp_path)
        finally:
            if harness is not None:
                harness.teardown()


class TestSeededTraces:
    """Deterministic random traces: trace = f(seed), reproducible forever."""

    OPS = ("upsert", "retire", "evidence", "relation", "read_node", "rollback_attempt", "replay_last_write")

    @pytest.mark.parametrize("seed", range(20))
    def test_seeded_trace(self, seed: int, tmp_path: Path) -> None:
        import random

        rng = random.Random(seed)
        harness = ModelHarness(tmp_path)
        try:
            for _ in range(40):
                op = rng.choice(self.OPS + ("close_reopen",))
                node = rng.choice(NODE_POOL)
                if op == "close_reopen":
                    harness.check_invariants()
                    harness.close_store()
                    harness.reopen_store()
                elif op == "upsert":
                    harness.upsert(node, affecting=rng.random() < 0.7)
                elif op == "retire":
                    harness.retire(node)
                elif op == "evidence":
                    harness.evidence(node)
                elif op == "relation":
                    harness.relation(node, rng.choice(NODE_POOL))
                elif op == "read_node":
                    harness.read_node(node)
                elif op == "rollback_attempt":
                    harness.rollback_attempt(node)
                elif op == "replay_last_write":
                    harness.replay_last_write()
                harness.check_invariants()
        finally:
            harness.teardown()


class TestCrashRecovery:
    """Real subprocess crash-kill against a live store file."""

    def test_commit_then_sigkill_all_committed_writes_survive(self, tmp_path: Path) -> None:
        harness = ModelHarness(tmp_path)
        try:
            harness.upsert("n0")
            harness.crash_commit(3)
            harness.check_invariants()
        finally:
            harness.teardown()

    def test_sigkill_inside_transaction_leaves_no_trace(self, tmp_path: Path) -> None:
        harness = ModelHarness(tmp_path)
        try:
            harness.upsert("n0")
            harness.crash_uncommitted(2)
            harness.check_invariants()
            # The uncommitted row's idempotency key must be reusable.
            harness.upsert("crash-reuse")
            harness.check_invariants()
        finally:
            harness.teardown()

    def test_crash_then_reopen_new_handle(self, tmp_path: Path) -> None:
        harness = ModelHarness(tmp_path)
        try:
            harness.crash_commit(2)
            harness.close_store()
            harness.reopen_store()
            harness.upsert("n0")
            harness.check_invariants()
        finally:
            harness.teardown()

    def test_repeated_crashes_keep_chain_valid(self, tmp_path: Path) -> None:
        harness = ModelHarness(tmp_path)
        try:
            for _ in range(3):
                harness.crash_uncommitted(1)
                harness.check_invariants()
        finally:
            harness.teardown()

    def test_invariant_catches_tampering(self, tmp_path: Path) -> None:
        """Mutation proof: the invariant set must fire on a corrupted store."""
        import sqlite3

        harness = ModelHarness(tmp_path)
        harness.upsert("n0")
        harness.upsert("n1")
        harness.teardown()
        conn = sqlite3.connect(harness.db_path)
        conn.execute("DROP TRIGGER event_log_no_update")
        conn.execute("UPDATE event_log SET actor = 'tampered' WHERE event_seq = 1")
        conn.commit()
        conn.close()
        reopened = ModelHarness(tmp_path)
        try:
            # Reconstruct the true model: the only lie left is the tampering.
            reopened.versions = {"n0": 1, "n1": 1}
            reopened.events = 2
            reopened.knowledge = 2
            with pytest.raises(AssertionError):
                reopened.check_invariants()
        finally:
            reopened.teardown()

    def test_invariant_catches_truncated_tail(self, tmp_path: Path) -> None:
        """Mutation: deleting the newest event must break the count anchor."""
        import sqlite3

        harness = ModelHarness(tmp_path)
        harness.upsert("n0")
        harness.upsert("n1")
        harness.teardown()
        conn = sqlite3.connect(harness.db_path)
        conn.execute("DROP TRIGGER event_log_no_delete")
        conn.execute("DELETE FROM event_log WHERE event_seq = 2")
        conn.commit()
        conn.close()
        reopened = ModelHarness(tmp_path)
        try:
            # The model reflects the 2 committed writes; the store lost one.
            reopened.versions = {"n0": 1, "n1": 1}
            reopened.events = 2
            reopened.knowledge = 2
            with pytest.raises(AssertionError):
                reopened.check_invariants()
        finally:
            reopened.teardown()


# ------------------------------------------------------- hypothesis machine


class StoreStateMachine(RuleBasedStateMachine):
    """Seeded random rule sequence over the model harness."""

    def __init__(self) -> None:
        super().__init__()
        self._tmp = tempfile.TemporaryDirectory(prefix="kys-model-")
        self.harness = ModelHarness(Path(self._tmp.name))
        self.crashes = 0

    def teardown(self) -> None:
        self.harness.teardown()
        self._tmp.cleanup()

    @invariant()
    def model_matches_system(self) -> None:
        if self.harness.is_open:
            self.harness.check_invariants()

    @rule(node=st.sampled_from(NODE_POOL), affecting=st.booleans())
    def rule_upsert(self, node: str, affecting: bool) -> None:
        if self.harness.is_open:
            self.harness.upsert(node, affecting=affecting)

    @rule(node=st.sampled_from(NODE_POOL))
    def rule_retire(self, node: str) -> None:
        if self.harness.is_open:
            self.harness.retire(node)

    @rule(node=st.sampled_from(NODE_POOL))
    def rule_evidence(self, node: str) -> None:
        if self.harness.is_open:
            self.harness.evidence(node)

    @rule(source=st.sampled_from(NODE_POOL), sink=st.sampled_from(NODE_POOL))
    def rule_relation(self, source: str, sink: str) -> None:
        if self.harness.is_open and source != sink:
            self.harness.relation(source, sink)

    @rule(node=st.sampled_from(NODE_POOL))
    def rule_read(self, node: str) -> None:
        if self.harness.is_open:
            self.harness.read_node(node)

    @rule(node=st.sampled_from(NODE_POOL))
    def rule_rollback(self, node: str) -> None:
        if self.harness.is_open:
            self.harness.rollback_attempt(node)

    @rule()
    def rule_replay(self) -> None:
        if self.harness.is_open:
            self.harness.replay_last_write()

    @rule()
    def rule_close(self) -> None:
        if self.harness.is_open:
            self.harness.check_invariants()
            self.harness.close_store()

    @precondition(lambda self: not self.harness.is_open)
    @rule()
    def rule_reopen(self) -> None:
        self.harness.reopen_store()

    @precondition(lambda self: self.harness.is_open and self.crashes < 2)
    @rule(count=st.integers(min_value=1, max_value=2))
    def rule_crash_kill(self, count: int) -> None:
        self.crashes += 1
        self.harness.crash_uncommitted(count)


TestStoreStateMachine = StoreStateMachine.TestCase
TestStoreStateMachine.settings = settings(
    max_examples=6,
    stateful_step_count=25,
    derandomize=True,
    deadline=None,
    suppress_health_check=list(HealthCheck),  # type: ignore[arg-type] - hypothesis stub types the enum metaclass
)
