"""B2 (SUB-173): a concurrently-building projection is not a stale projection.

Measured baseline (stress-3-products §6 B2): 20 agents reading a store right
after ingest produced 9/40 (22.5 %) — and in this session's re-run 18/40 —
``STALE_PROJECTION`` errors whose message ("the active projection does not
cover knowledge watermark 0") was logically impossible. The cause is not
corruption: the projector's rebuild lock is ``flock(LOCK_EX | LOCK_NB)``, so
every reader that lost the race to the *first builder* got ``ProjectorBusy``,
the repair helper swallowed it, and the reader reported "stale" instead of
"a build is in progress, wait for it". A second run of 30 parallel queries
after the projection existed was 30/30 green.

The fix: treat ``ProjectorBusy`` as "another process is publishing — wait for
it (bounded)" instead of "your read failed". A projection that genuinely does
not cover the watermark after the wait still fails typed and retryable: the
contract's failure mode is preserved, only the false positive is removed.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from know_your_context.contracts import QueryRequest, StaleProjectionUnavailable
from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain
from know_your_context.projection import GraphProjector, ProjectorBusy
from know_your_context.service import KnowledgeService
from know_your_context.store import EventStore


def _upsert(store: EventStore, node: Node) -> None:
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id=node.node_id,
            domain=node.domain,
            node=node,
            source_version_id=node.source_version_id,
        ),
        idempotency_key=f"race:upsert:{node.node_id}",
    )


def _store_with_unbuilt_projection(tmp_path: Path) -> tuple[EventStore, GraphProjector, KnowledgeService]:
    """Knowledge exists (watermark > 0) but no projection has ever been built.

    This is exactly the "everyone arrives first" state after an ingest: the
    first reader must build, the rest must wait for it — not fail.
    """
    store = EventStore(tmp_path / "soul.sqlite3")
    projector = GraphProjector(store, output_root=tmp_path / "out")
    service = KnowledgeService(store, projector)
    _upsert(
        store,
        Node(
            node_id="P1",
            kind=NodeKind.PRIMARY,
            domain=PrimaryDomain.P1,
            summary="Pembelajaran",
            provenance=("context://roots",),
            source_version_id="sv-race",
        ),
    )
    _upsert(
        store,
        Node(
            node_id="n-race",
            parent_id="P1",
            kind=NodeKind.CHILD,
            domain=PrimaryDomain.P1,
            summary="race lesson about gates",
            context_tags=("gates",),
            provenance=("context://corpus/race.md",),
            source_version_id="sv-race",
        ),
    )
    return store, projector, service


def test_reader_waits_for_the_concurrent_builder_instead_of_reporting_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, projector, service = _store_with_unbuilt_projection(tmp_path)
    monkeypatch.setattr("know_your_context.service._PROJECTION_WAIT_SECONDS", 5.0)

    real_repair = projector.repair_if_stale
    state = {"calls": 0}

    def busy_then_publish() -> object:
        """First repair attempt loses the lock; a background builder publishes."""
        state["calls"] += 1
        if state["calls"] == 1:

            def builder() -> None:
                time.sleep(0.05)  # the competing process finishing its build
                real_repair()

            thread = threading.Thread(target=builder, daemon=True)
            thread.start()
            raise ProjectorBusy("another projector holds the rebuild lock")
        return real_repair()

    monkeypatch.setattr(projector, "repair_if_stale", busy_then_publish)
    response = service.query(QueryRequest(purpose="gates", domain="P1", token_budget=1024))
    assert response.stale is False
    assert response.source_knowledge_high_watermark == store.knowledge_high_watermark()
    assert "n-race" in {node.node_id for node in response.results}
    store.close()


def test_a_projection_that_never_covers_still_fails_typed_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, projector, service = _store_with_unbuilt_projection(tmp_path)
    monkeypatch.setattr("know_your_context.service._PROJECTION_WAIT_SECONDS", 0.2)

    def always_busy() -> object:
        raise ProjectorBusy("another projector holds the rebuild lock")

    monkeypatch.setattr(projector, "repair_if_stale", always_busy)
    started = time.monotonic()
    with pytest.raises(StaleProjectionUnavailable) as excinfo:
        service.query(QueryRequest(purpose="gates", domain="P1", token_budget=512))
    elapsed = time.monotonic() - started
    assert excinfo.value.code == "STALE_PROJECTION"
    assert excinfo.value.retryable is True
    # Bounded wait, not an unbounded hang, and not an instant lie either.
    assert 0.15 <= elapsed < 3.0, elapsed
    assert "rebuild" in str(excinfo.value).lower(), "the message must name the real condition"
    store.close()


def test_the_wait_does_not_mask_a_real_repair_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repair that runs and does not help (no busy peer) still fails at once."""
    store, projector, service = _store_with_unbuilt_projection(tmp_path)
    monkeypatch.setattr("know_your_context.service._PROJECTION_WAIT_SECONDS", 5.0)
    monkeypatch.setattr(projector, "repair_if_stale", lambda: None)
    started = time.monotonic()
    with pytest.raises(StaleProjectionUnavailable):
        service.query(QueryRequest(purpose="gates", domain="P1", token_budget=512))
    assert time.monotonic() - started < 1.0, "no busy peer: the read must fail fast, not wait"
    store.close()


def test_reader_rides_out_a_peer_rebuild_that_outlasts_the_wait_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SUB-198: a reader must not fail while a peer is demonstrably still building.

    The L4 storm baseline measured err=49/50 on the ``query`` lane (and 27s
    p50 on the codeintel lane) because the fixed ``_PROJECTION_WAIT_SECONDS``
    budget expired while the peer's rebuild — slowed by 50-way store-open and
    audit-write contention — was still in flight. The rebuild lock is flock(2),
    so a held lock is a *liveness* signal: the peer is alive and building. The
    reader should ride out the peer's publish instead of failing typed with a
    read that would have succeeded seconds later. This test holds the real
    rebuild flock for longer than a tiny budget and publishes under the lock,
    exactly like the storm's winning builder; the reader must wait and succeed
    (elapsed >= the hold), not raise STALE_PROJECTION at the budget.
    """
    store, projector, service = _store_with_unbuilt_projection(tmp_path)
    monkeypatch.setattr("know_your_context.service._PROJECTION_WAIT_SECONDS", 0.05)
    monkeypatch.setattr("know_your_context.service._PROJECTION_WAIT_POLL_SECONDS", 0.005)

    peer_locked = threading.Event()

    def peer_builder() -> None:
        # The competing builder: wins the flock, builds for 0.4 s (past the
        # 0.05 s budget), publishes while still holding the lock (rebuild()
        # holds flock(2) through _publish), then releases on context exit.
        with projector.hold_lock():
            peer_locked.set()
            time.sleep(0.4)
            watermark = store.knowledge_high_watermark()
            state, _ = projector._replay_upto(watermark)  # noqa: SLF001 - race-mechanism test
            extraction, quarantined = projector._extraction(state)
            projector._publish(extraction, quarantined, watermark, allow_shrink=False, activate=True)

    thread = threading.Thread(target=peer_builder, daemon=True)
    thread.start()
    assert peer_locked.wait(timeout=2.0), "peer must win the rebuild lock before the read"

    started = time.monotonic()
    response = service.query(QueryRequest(purpose="gates", domain="P1", token_budget=1024))
    elapsed = time.monotonic() - started
    thread.join(timeout=2.0)
    assert response.stale is False
    assert elapsed >= 0.3, f"reader must ride out the peer build, got {elapsed:.2f}s"
    assert "n-race" in {node.node_id for node in response.results}
    store.close()
