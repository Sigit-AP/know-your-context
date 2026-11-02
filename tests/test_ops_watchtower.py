"""Tests for ``know_your_context.ops.watchtower`` (W12 tools surface).

Pins the multi-store status aggregator contract: each store is probed in
isolation (never creating a missing store file, never raising on a broken
one), the fleet report is deterministic (sorted by name), and the exit code
is 1 iff any store is unreachable, errored, or fails its chain/migrations.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from know_your_context.ops.watchtower import (
    StoreTarget,
    WatchtowerError,
    WatchtowerReport,
    aggregate_status,
    exit_code,
    probe_store,
    render_markdown,
)
from know_your_context.projection import GraphProjector
from know_your_context.store import EventStore


def _store(tmp_path: Path, name: str = "fleet.sqlite3") -> EventStore:
    return EventStore(tmp_path / name)


def _event(store: EventStore, seq: int) -> None:
    with store.transaction() as conn:
        store.insert_event(
            conn,
            event_id=f"{seq:064x}",
            kind="audit",
            event_type="audit.test",
            knowledge_affecting=False,
            actor="tester",
            occurred_at="2026-01-01T00:00:00+00:00",
            payload={"probe": seq},
        )


def _target(tmp_path: Path, name: str, store_name: str | None = None) -> StoreTarget:
    return StoreTarget(name=name, store_path=str(tmp_path / (store_name or f"{name}.sqlite3")))


# ------------------------------------------------------------------ targets


def test_target_rejects_blank_name(tmp_path: Path) -> None:
    with pytest.raises(WatchtowerError):
        StoreTarget(name="  ", store_path=str(tmp_path / "x.sqlite3"))


def test_target_rejects_blank_path() -> None:
    with pytest.raises(WatchtowerError):
        StoreTarget(name="x", store_path="")


def test_target_frozen(tmp_path: Path) -> None:
    target = _target(tmp_path, "a")
    with pytest.raises(Exception):  # noqa: B017
        target.name = "b"  # type: ignore[misc]


# ------------------------------------------------------------------ probing


def test_probe_healthy_store(tmp_path: Path) -> None:
    store = _store(tmp_path, "a.sqlite3")
    try:
        _event(store, 1)
    finally:
        store.close()
    status = probe_store(_target(tmp_path, "alpha", "a.sqlite3"))
    assert status.reachable is True
    assert status.error is None
    assert status.chain_ok is True
    assert status.migrations_ok is True
    assert status.event_count == 1
    assert status.health_score is not None
    assert status.health_max_score is not None
    assert status.health_score <= status.health_max_score
    assert status.size_bytes is not None and status.size_bytes > 0


def test_probe_missing_store_never_creates_it(tmp_path: Path) -> None:
    path = tmp_path / "ghost.sqlite3"
    status = probe_store(StoreTarget(name="ghost", store_path=str(path)))
    assert status.reachable is False
    assert status.error is not None
    assert not path.exists()  # probing must not create the store file
    assert not os.path.exists(f"{path}-wal")


def test_probe_garbage_file_is_contained(tmp_path: Path) -> None:
    path = tmp_path / "junk.sqlite3"
    path.write_bytes(b"junk junk junk junk junk junk junk junk")
    status = probe_store(StoreTarget(name="junk", store_path=str(path)))
    assert status.reachable is False
    assert status.error is not None
    assert status.chain_ok is None


def test_probe_records_stale_journals(tmp_path: Path) -> None:
    store = _store(tmp_path, "stale.sqlite3")
    try:
        store.execute_raw(
            "INSERT INTO invocation_journal (invocation_id, audit_event_id, operation, request_hash, actor, "
            "idempotency_key, started_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'in_progress')",
            (
                "stale-1",
                "audit-stale-1",
                "knowledge.query",
                "request-hash",
                "tester",
                None,
                "2026-01-01T00:00:00+00:00",
            ),
        )
    finally:
        store.close()
    status = probe_store(_target(tmp_path, "stale", "stale.sqlite3"), stale_after_seconds=0.0)
    assert status.stale_journals == 1
    assert status.reachable is True  # stale journals degrade, they don't break


def test_probe_projection_fields(tmp_path: Path) -> None:
    store = _store(tmp_path, "proj.sqlite3")
    try:
        projector = GraphProjector(store, output_root=tmp_path / "proj")
        projector.rebuild()
    finally:
        store.close()
    target = StoreTarget(name="proj", store_path=str(tmp_path / "proj.sqlite3"), projection_root=str(tmp_path / "proj"))
    status = probe_store(target)
    assert status.projection_present is True
    assert status.projection_watermark_matches is True


def test_probe_without_projection_root_reports_none(tmp_path: Path) -> None:
    store = _store(tmp_path, "plain.sqlite3")
    store.close()
    status = probe_store(_target(tmp_path, "plain", "plain.sqlite3"))
    assert status.projection_present is None
    assert status.projection_watermark_matches is None


def test_probe_health_perfect_on_fresh_store(tmp_path: Path) -> None:
    store = _store(tmp_path, "fresh.sqlite3")
    store.close()
    status = probe_store(_target(tmp_path, "fresh", "fresh.sqlite3"))
    assert status.health_score == status.health_max_score


# ------------------------------------------------------------------ aggregation


def test_aggregate_sorts_by_name(tmp_path: Path) -> None:
    for name in ("zeta", "alpha", "mid"):
        _store(tmp_path, f"{name}.sqlite3").close()
    report = aggregate_status(
        (
            _target(tmp_path, "zeta"),
            _target(tmp_path, "alpha"),
            _target(tmp_path, "mid"),
        )
    )
    assert [status.name for status in report.stores] == ["alpha", "mid", "zeta"]


def test_aggregate_all_healthy(tmp_path: Path) -> None:
    for name in ("a", "b"):
        store = _store(tmp_path, f"{name}.sqlite3")
        _event(store, 1)
        store.close()
    report = aggregate_status((_target(tmp_path, "a"), _target(tmp_path, "b")))
    assert report.ok is True
    assert report.total_stores == 2
    assert report.reachable_count == 2
    assert report.unreachable_names == ()
    assert report.total_events == 2
    assert exit_code(report) == 0


def test_aggregate_with_unreachable_store(tmp_path: Path) -> None:
    _store(tmp_path, "good.sqlite3").close()
    report = aggregate_status(
        (
            _target(tmp_path, "good"),
            StoreTarget(name="missing", store_path=str(tmp_path / "missing.sqlite3")),
        )
    )
    assert report.ok is False
    assert report.reachable_count == 1
    assert report.unreachable_names == ("missing",)
    assert exit_code(report) == 1


def test_aggregate_rejects_duplicate_names(tmp_path: Path) -> None:
    with pytest.raises(WatchtowerError):
        aggregate_status((_target(tmp_path, "dup", "a.sqlite3"), _target(tmp_path, "dup", "b.sqlite3")))


def test_aggregate_rejects_duplicate_paths(tmp_path: Path) -> None:
    with pytest.raises(WatchtowerError):
        aggregate_status((_target(tmp_path, "a", "same.sqlite3"), _target(tmp_path, "b", "same.sqlite3")))


def test_aggregate_empty_fleet_is_ok() -> None:
    report = aggregate_status(())
    assert report.ok is True
    assert report.total_stores == 0
    assert exit_code(report) == 0


def test_aggregate_is_deterministic(tmp_path: Path) -> None:
    for name in ("a", "b"):
        _store(tmp_path, f"{name}.sqlite3").close()
    targets = (_target(tmp_path, "a"), _target(tmp_path, "b"))
    assert aggregate_status(targets) == aggregate_status(targets)


def test_report_frozen() -> None:
    report = WatchtowerReport(stores=())
    with pytest.raises(Exception):  # noqa: B017
        report.stores = ()  # type: ignore[misc]


def test_status_frozen(tmp_path: Path) -> None:
    store = _store(tmp_path, "s.sqlite3")
    store.close()
    status = probe_store(_target(tmp_path, "s", "s.sqlite3"))
    with pytest.raises(Exception):  # noqa: B017
        status.reachable = False  # type: ignore[misc]


def test_watchtower_error_is_runtime_error() -> None:
    assert issubclass(WatchtowerError, RuntimeError)


def test_aggregate_counts_events_across_fleet(tmp_path: Path) -> None:
    store_a = _store(tmp_path, "a.sqlite3")
    _event(store_a, 1)
    _event(store_a, 2)
    store_a.close()
    _store(tmp_path, "b.sqlite3").close()
    report = aggregate_status((_target(tmp_path, "a"), _target(tmp_path, "b")))
    assert report.total_events == 2


def test_aggregate_flags_broken_chain_via_stub(tmp_path: Path) -> None:
    """A store whose chain fails drives exit 1 even when reachable."""
    store = _store(tmp_path, "x.sqlite3")
    store.close()
    # Simulate by making the file unreadable-as-store after creation:
    path = tmp_path / "x.sqlite3"
    original = path.read_bytes()
    path.write_bytes(b"\x00" * len(original))  # zeroed pages: not a valid store
    report = aggregate_status((_target(tmp_path, "x"),))
    assert report.ok is False
    assert exit_code(report) == 1


# ------------------------------------------------------------------ rendering


def test_render_markdown_table(tmp_path: Path) -> None:
    store = _store(tmp_path, "a.sqlite3")
    _event(store, 1)
    store.close()
    report = aggregate_status((_target(tmp_path, "alpha", "a.sqlite3"),))
    markdown = render_markdown(report)
    assert markdown.startswith("# Watchtower Report")
    assert "| name |" in markdown
    assert "| alpha |" in markdown
    assert "yes" in markdown


def test_render_marks_unreachable(tmp_path: Path) -> None:
    report = aggregate_status((StoreTarget(name="ghost", store_path=str(tmp_path / "ghost.sqlite3")),))
    markdown = render_markdown(report)
    assert "ghost" in markdown
    assert "unreachable" in markdown.lower() or "no" in markdown


def test_render_is_deterministic(tmp_path: Path) -> None:
    _store(tmp_path, "a.sqlite3").close()
    report = aggregate_status((_target(tmp_path, "a"),))
    assert render_markdown(report) == render_markdown(report)


def test_render_empty_fleet() -> None:
    markdown = render_markdown(WatchtowerReport(stores=()))
    assert "No stores" in markdown


def test_render_includes_summary_counts(tmp_path: Path) -> None:
    _store(tmp_path, "a.sqlite3").close()
    markdown = render_markdown(aggregate_status((_target(tmp_path, "a"),)))
    assert "1/1" in markdown  # reachable/total
