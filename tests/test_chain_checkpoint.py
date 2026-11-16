"""Incremental chain verification via sidecar checkpoints (TASK1-PUSH200 P2).

``EventStore.verify_chain()`` re-derives the whole hash chain on every call —
O(N) wall time for what is usually an unchanged history. The checkpoint
sidecar turns routine re-audits into O(tail): a successful full verify pins
``(seq, hash, checked)`` to a sidecar file (0600, atomic rename, same
discipline as the capability registry), and ``verify_chain(incremental=True)``
re-derives the single checkpoint row (O(1) tamper anchor) plus the tail.

Fail-closed discipline:
- default stays FULL; incremental is opt-in per call;
- any sidecar inconsistency (missing, corrupt, hash mismatch, seq drift)
  falls back to a full verify — never to a skipped audit;
- KNOW_YOUR_CONTEXT_NO_CHAIN_CHECKPOINT=1 disables the mechanism entirely;
- the disclosed scope limit is pinned by test: tampering strictly BEFORE the
  checkpoint is invisible to the incremental pass (the checkpoint attests to
  it) and caught by the full pass. Forensic audits must stay full.

References have no hash chain at all (scorecard integrity: codegraph 7.6%,
graphify 9.8%), so the checkpointed re-audit is a depth step beyond the
industry baseline (full re-walk, git-fsck style), not a parity catch-up.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain
from know_your_context.store import EventStore

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
_NO_UPDATE_DDL = (
    "CREATE TRIGGER IF NOT EXISTS event_log_no_update "
    "BEFORE UPDATE ON event_log BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END;"
)


def _append_events(store: EventStore, count: int, *, actor: str = "agent-t", start: int = 0) -> None:
    for i in range(start, start + count):
        node = Node.model_validate(
            {
                "node_id": f"n-{i % 7}",
                "kind": NodeKind.LEAF,
                "domain": PrimaryDomain.P1,
                "source_version_id": "sv-ck",
                "payload": {"fact": f"checkpoint {i}"},
                "summary": f"checkpoint test event {i}",
                "provenance": ("context://checkpoint-test",),
                "context_tags": ("checkpoint",),
                "scope": "global",
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        event = Event.model_validate(
            {
                "kind": EventKind.NODE_UPSERT,
                "node_id": node.node_id,
                "domain": node.domain,
                "node": node,
                "source_version_id": node.source_version_id,
                "occurred_at": NOW,
            }
        )
        store.append_domain(event, idempotency_key=f"ck-{actor}-{i}")


def _tamper_row(db_path: Path, seq: int | str) -> None:
    """Rewrite one event row the way a raw-file attacker would (trigger dropped)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TRIGGER event_log_no_update")
        where = f"event_seq = {seq}" if isinstance(seq, int) else f"event_seq = ({seq})"
        payload = '{"tampered": 1}'
        conn.execute(f"UPDATE event_log SET payload_json = '{payload}' WHERE {where}")  # noqa: S608
        conn.execute(_NO_UPDATE_DDL)
        conn.commit()
    finally:
        conn.close()


def _checkpoint_path(db_path: Path) -> Path:
    return db_path.with_suffix(db_path.suffix + ".chain-checkpoint.json")


class TestCheckpointWrittenByFullVerify:
    def test_full_verify_writes_sidecar(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 12)
        assert store.verify_chain().ok
        sidecar = _checkpoint_path(tmp_path / "s.sqlite3")
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert data["checked"] == 12
        assert data["seq"] >= 12
        assert len(data["hash"]) == 64
        # 0600 like the capability registry
        assert (sidecar.stat().st_mode & 0o777) == 0o600

    def test_failed_verify_never_writes_sidecar(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 4)
        with store.transaction() as conn:  # corrupt the anchor directly
            conn.execute("UPDATE system_state SET value = 'deadbeef' WHERE key = 'chain_head_hash'")
        assert not store.verify_chain().ok
        assert not _checkpoint_path(tmp_path / "s.sqlite3").exists()


class TestIncrementalVerify:
    def test_incremental_checks_only_the_tail(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 20)
        assert store.verify_chain().ok  # writes checkpoint at 20
        _append_events(store, 5, start=20)
        report = store.verify_chain(incremental=True)
        assert report.ok
        assert report.checked == 5

    def test_incremental_no_new_events_checks_zero(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 9)
        assert store.verify_chain().ok
        report = store.verify_chain(incremental=True)
        assert report.ok
        assert report.checked == 0

    def test_incremental_without_sidecar_runs_full(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 11)
        report = store.verify_chain(incremental=True)
        assert report.ok
        assert report.checked == 11

    def test_corrupt_sidecar_falls_back_to_full(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 11)
        assert store.verify_chain().ok
        _checkpoint_path(tmp_path / "s.sqlite3").write_text("{not json")
        report = store.verify_chain(incremental=True)
        assert report.ok
        assert report.checked == 11  # full ran

    def test_sidecar_from_another_history_falls_back_to_full(self, tmp_path: Path) -> None:
        store_a = EventStore(tmp_path / "a.sqlite3")
        store_b = EventStore(tmp_path / "b.sqlite3")
        _append_events(store_a, 8, actor="agent-a")
        _append_events(store_b, 8, actor="agent-b")
        assert store_a.verify_chain().ok
        assert store_b.verify_chain().ok
        # replay A's checkpoint onto B: the anchor row re-derivation must
        # reject it (different history) and a full verify must run instead
        (tmp_path / "b.sqlite3.chain-checkpoint.json").write_text(
            (tmp_path / "a.sqlite3.chain-checkpoint.json").read_text()
        )
        report = store_b.verify_chain(incremental=True)
        assert report.ok
        assert report.checked == 8

    def test_kill_switch_env_pinned_to_registry(self) -> None:
        from know_your_context import store as store_mod
        from know_your_context.ops import switches

        assert switches.ENV_NO_CHAIN_CHECKPOINT == store_mod._NO_CHAIN_CHECKPOINT_ENV  # noqa: SLF001
        entry = next(e for e in switches.KILL_SWITCHES if e.env == switches.ENV_NO_CHAIN_CHECKPOINT)
        assert entry.name == "store.chain_checkpoint"

    def test_uptodate_checkpoint_is_not_rewritten(self, tmp_path: Path) -> None:
        # polling verifiers must not churn the sidecar when nothing moved
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 6)
        assert store.verify_chain().ok
        sidecar = _checkpoint_path(tmp_path / "s.sqlite3")
        before = sidecar.read_bytes()
        assert store.verify_chain().ok
        assert store.verify_chain(incremental=True).ok
        assert sidecar.read_bytes() == before

    def test_write_checkpoint_false_writes_nothing(self, tmp_path: Path) -> None:
        # pure probes (watchtower/metrics) audit without leaving artifacts
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 6)
        assert store.verify_chain(write_checkpoint=False).ok
        assert not _checkpoint_path(tmp_path / "s.sqlite3").exists()

    def test_kill_switch_forces_full(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_NO_CHAIN_CHECKPOINT", "1")
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 7)
        assert store.verify_chain().ok
        assert not _checkpoint_path(tmp_path / "s.sqlite3").exists()  # never written
        report = store.verify_chain(incremental=True)
        assert report.ok
        assert report.checked == 7

    def test_post_checkpoint_tamper_is_caught(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 10)
        assert store.verify_chain().ok  # checkpoint at 10
        _append_events(store, 3, start=10)
        # tamper with a tail row, going around the append-only triggers the
        # way a raw-file attacker would
        _tamper_row(tmp_path / "s.sqlite3", "SELECT MAX(event_seq) FROM event_log")
        report = store.verify_chain(incremental=True)
        assert not report.ok
        assert report.first_bad_seq is not None

    def test_incremental_scope_limit_is_disclosed(self, tmp_path: Path) -> None:
        """Pin the honest trade-off: pre-checkpoint tampering is attested by
        the checkpoint (incremental trusts it) and caught only by a full
        verify. This test documents WHY forensic audits stay full."""
        store = EventStore(tmp_path / "s.sqlite3")
        _append_events(store, 10)
        assert store.verify_chain().ok  # checkpoint at 10
        _tamper_row(tmp_path / "s.sqlite3", 2)
        incremental = store.verify_chain(incremental=True)
        assert incremental.ok  # checkpoint attests seq<=10; scope disclosed
        assert incremental.checked == 0
        full = store.verify_chain()
        assert not full.ok  # the full pass still catches the broken link
        assert full.first_bad_seq == 2
