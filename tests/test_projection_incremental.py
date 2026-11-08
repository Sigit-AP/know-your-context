"""Incremental publish guards (TASK2 WS-E: finding D3).

D3 (cluster-a verdict): every publish re-replayed ALL knowledge events
(``_knowledge_events`` full scan + pydantic re-validation of every event) —
O(K) per publish, measured 300–540 ms @K=2010 (79% of it event replay) and
growing without bound in K. The projector now folds only the events since
its last replayed ``event_seq`` into an accumulated replay state; a
regression (watermark move backwards) or any fold inconsistency resets to a
full replay, and a validation failure on a delta publish retries once as a
full replay before failing (the plan's fallback).

The pinned invariant: an incrementally published generation is
BYTE-IDENTICAL to a from-scratch full rebuild at the same watermark.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain
from know_your_context.projection import ARTIFACT_NAMES, GraphProjector
from know_your_context.store import EventStore

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _node(node_id: str, *, summary: str | None = None) -> Node:
    return Node.model_validate(
        {
            "node_id": node_id,
            "kind": NodeKind.LEAF,
            "domain": PrimaryDomain.P1,
            "summary": summary or f"summary of {node_id}",
            "provenance": ("context://incremental",),
            "source_version_id": "sv-inc",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def _upsert(store: EventStore, node: Node, key: str) -> None:
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
    store.append_domain(event, idempotency_key=key)


def _retire(store: EventStore, node_id: str, key: str) -> None:
    store.append_domain(
        Event(kind=EventKind.NODE_RETIRE, node_id=node_id, domain=PrimaryDomain.P1),
        idempotency_key=key,
    )


def _seed(store: EventStore, count: int, *, start: int = 0) -> None:
    for i in range(start, start + count):
        _upsert(store, _node(f"n-{i}"), key=f"seed-{i}")


def _artifacts(projector: GraphProjector, receipt: Any) -> dict[str, bytes]:
    generation = Path(receipt.generation_dir)
    return {name: (generation / name).read_bytes() for name in ARTIFACT_NAMES}


def _counting_validate(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count pydantic event re-validations — the dominant O(K) replay cost."""
    counter = {"validations": 0}
    original = Event.model_validate

    def counting(cls: type[Event], *args: Any, **kwargs: Any) -> Event:
        counter["validations"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(Event, "model_validate", classmethod(counting))
    return counter


def test_delta_publish_replays_only_new_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After priming at K events, a publish over d new events must parse
    d events — not K. (The pre-fix projector re-validated all K.)"""
    store = EventStore(tmp_path / "soul.sqlite3")
    projector = GraphProjector(store, output_root=tmp_path / "out")
    _seed(store, 60)
    projector.rebuild()
    _seed(store, 3, start=60)
    counter = _counting_validate(monkeypatch)
    projector.rebuild()
    assert counter["validations"] <= 3, (
        f"delta publish re-validated {counter['validations']} events for a 3-event delta at K=63"
    )
    store.close()


def test_incremental_publish_is_byte_identical_to_full_rebuild(tmp_path: Path) -> None:
    """The semantic pin: incremental publishes over several rounds produce
    exactly the generation a fresh full rebuild produces (every artifact)."""
    store = EventStore(tmp_path / "soul.sqlite3")
    _seed(store, 20)
    incremental = GraphProjector(store, output_root=tmp_path / "inc")
    incremental.rebuild()
    # Round 2: churn — upserts to existing nodes plus new ones.
    for i in range(5):
        _upsert(store, _node(f"n-{i}", summary=f"revised {i}"), key=f"rev-{i}")
    _seed(store, 5, start=20)
    incremental.rebuild()
    # Round 3: a retirement (shrink guard accounted explicitly).
    _retire(store, "n-7", key="retire-7")
    receipt_inc = incremental.rebuild(allow_shrink=True)
    fresh = GraphProjector(store, output_root=tmp_path / "full")
    receipt_full = fresh.rebuild(allow_shrink=True)
    assert receipt_inc.content_hash == receipt_full.content_hash
    assert receipt_inc.projection_version == receipt_full.projection_version
    assert _artifacts(incremental, receipt_inc) == _artifacts(fresh, receipt_full)
    store.close()


def test_regression_to_an_earlier_watermark_replays_fully(tmp_path: Path) -> None:
    """rebuild(upto=K1) after publishing K2 > K1 must equal a fresh full
    rebuild at K1 — the state reset keeps fixed-mark probes deterministic."""
    store = EventStore(tmp_path / "soul.sqlite3")
    _seed(store, 10)
    watermark_mid = store.knowledge_high_watermark()
    _seed(store, 10, start=10)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    projector.rebuild()  # incremental state now sits at K=20
    # Stepping back is a deliberate shrink of the published view, and a
    # fixed-mark probe never activates over the newer pointer (the store
    # CAS refuses a watermark regression — that protection is untouched).
    receipt_back = projector.rebuild(upto_knowledge_seq=watermark_mid, allow_shrink=True, activate=False)
    fresh = GraphProjector(store, output_root=tmp_path / "full")
    receipt_fresh = fresh.rebuild(upto_knowledge_seq=watermark_mid, allow_shrink=True, activate=False)
    assert receipt_back.content_hash == receipt_fresh.content_hash
    assert _artifacts(projector, receipt_back) == _artifacts(fresh, receipt_fresh)
    store.close()


def test_validation_failure_on_delta_retries_as_full_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan's fallback: a delta publish that fails validation retries
    once as a FULL replay before the error may escape."""
    store = EventStore(tmp_path / "soul.sqlite3")
    _seed(store, 10)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    projector.rebuild()
    _seed(store, 2, start=10)

    counter = _counting_validate(monkeypatch)
    original_build = GraphProjector._build_generation  # noqa: SLF001
    attempts = {"count": 0}

    def fail_first(self: GraphProjector, *args: Any, **kwargs: Any) -> Any:
        attempts["count"] += 1
        if attempts["count"] == 1:
            from know_your_context.projection import ProjectionValidationError

            raise ProjectionValidationError("injected validation failure (D3 drill)")
        return original_build(self, *args, **kwargs)

    monkeypatch.setattr(GraphProjector, "_build_generation", fail_first)
    receipt = projector.rebuild()
    assert receipt.activated is True
    assert attempts["count"] == 2
    # The retry re-validated the FULL prefix: 12 events (10 primed + 2 new),
    # not the 2-event delta the first attempt folded.
    assert counter["validations"] >= 12, (
        f"the fallback re-validated only {counter['validations']} events — a full replay re-reads all 12"
    )
    store.close()


def test_watermark_only_moves_forward_in_state(tmp_path: Path) -> None:
    """Sanity: consecutive publishes keep moving the receipt watermark with
    the store's, and the publish content hash is stable for a stable store."""
    store = EventStore(tmp_path / "soul.sqlite3")
    projector = GraphProjector(store, output_root=tmp_path / "out")
    _seed(store, 4)
    first = projector.rebuild()
    again = projector.rebuild()
    assert again.content_hash == first.content_hash
    assert again.activated is True
    _seed(store, 2, start=4)
    third = projector.rebuild()
    assert third.source_knowledge_high_watermark == store.knowledge_high_watermark()
    assert third.source_knowledge_high_watermark > first.source_knowledge_high_watermark
    store.close()
