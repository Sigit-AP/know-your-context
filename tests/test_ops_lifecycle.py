"""Tests for ``know_your_context.ops.lifecycle`` (W12 tools surface).

Pins the lifecycle contract: append-only hash-chained health history with
deterministic trend analysis, a declarative retention policy engine (age and
count rules, protected items, deterministic plans), and safe vacuum/compact
of the SQLite store (staging copy -> verification -> coordinated atomic swap
through the same live-store discipline as restore).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from know_your_context.domain import canonical_json
from know_your_context.observability import check_health
from know_your_context.ops.lifecycle import (
    DEFAULT_HISTORY_FILENAME,
    FragmentationReport,
    HealthSnapshot,
    HealthTrend,
    LifecycleError,
    PruneReport,
    RetentionDecision,
    RetentionItem,
    RetentionRule,
    VacuumReport,
    analyze_fragmentation,
    analyze_health_trend,
    history_path_for_store,
    load_health_history,
    plan_generic_retention,
    prune_health_history,
    record_health_snapshot,
    vacuum_store,
)
from know_your_context.store import EventStore

# ------------------------------------------------------------------ helpers

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return EPOCH + timedelta(minutes=minutes)


def _store(tmp_path: Path, name: str = "lifecycle.sqlite3") -> EventStore:
    return EventStore(tmp_path / name)


def _snapshot(
    *,
    minutes: int = 0,
    score: int = 100,
    max_score: int = 100,
    prev_hash: str = "0" * 64,
) -> HealthSnapshot:
    components = (("hash_chain", True), ("store_select", True))
    payload = {
        "schema_version": 1,
        "recorded_at": _at(minutes).isoformat(),
        "score": score,
        "max_score": max_score,
        "ok_components": sum(1 for _, ok in components if ok),
        "total_components": len(components),
        "components": [[name, ok] for name, ok in components],
        "knowledge_high_watermark": 0,
        "event_count": 0,
        "prev_hash": prev_hash,
    }
    import hashlib

    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return HealthSnapshot(
        schema_version=1,
        recorded_at=payload["recorded_at"],
        score=score,
        max_score=max_score,
        ok_components=payload["ok_components"],
        total_components=payload["total_components"],
        components=components,
        knowledge_high_watermark=0,
        event_count=0,
        prev_hash=prev_hash,
        hash=digest,
    )


def _write_history(path: Path, snapshots: list[HealthSnapshot]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for snap in snapshots:
            handle.write(canonical_json(snap.to_payload()) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


# ------------------------------------------------------------------ snapshot model


def test_snapshot_is_frozen() -> None:
    snap = _snapshot()
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass refuses assignment
        snap.score = 0  # type: ignore[misc]


def test_snapshot_rejects_negative_score() -> None:
    with pytest.raises(LifecycleError):
        HealthSnapshot(
            schema_version=1,
            recorded_at=EPOCH.isoformat(),
            score=-1,
            max_score=100,
            ok_components=0,
            total_components=0,
            components=(),
            knowledge_high_watermark=0,
            event_count=0,
            prev_hash="0" * 64,
            hash="0" * 64,
        )


def test_snapshot_rejects_score_above_max() -> None:
    with pytest.raises(LifecycleError):
        HealthSnapshot(
            schema_version=1,
            recorded_at=EPOCH.isoformat(),
            score=101,
            max_score=100,
            ok_components=0,
            total_components=0,
            components=(),
            knowledge_high_watermark=0,
            event_count=0,
            prev_hash="0" * 64,
            hash="0" * 64,
        )


def test_snapshot_rejects_bad_hash_shape() -> None:
    with pytest.raises(LifecycleError):
        HealthSnapshot(
            schema_version=1,
            recorded_at=EPOCH.isoformat(),
            score=1,
            max_score=100,
            ok_components=0,
            total_components=0,
            components=(),
            knowledge_high_watermark=0,
            event_count=0,
            prev_hash="not-hex",
            hash="0" * 64,
        )


def test_snapshot_rejects_ok_components_above_total() -> None:
    with pytest.raises(LifecycleError):
        HealthSnapshot(
            schema_version=1,
            recorded_at=EPOCH.isoformat(),
            score=1,
            max_score=100,
            ok_components=3,
            total_components=2,
            components=(),
            knowledge_high_watermark=0,
            event_count=0,
            prev_hash="0" * 64,
            hash="0" * 64,
        )


def test_snapshot_payload_round_trip() -> None:
    snap = _snapshot(minutes=5, score=42)
    loaded = HealthSnapshot.from_payload(snap.to_payload())
    assert loaded == snap


def test_snapshot_from_payload_rejects_unknown_schema() -> None:
    payload = _snapshot().to_payload()
    payload["schema_version"] = 999
    with pytest.raises(LifecycleError):
        HealthSnapshot.from_payload(payload)


def test_snapshot_from_payload_rejects_tampered_hash() -> None:
    payload = _snapshot().to_payload()
    payload["score"] = payload["score"] + 1
    with pytest.raises(LifecycleError):
        HealthSnapshot.from_payload(payload)


def test_snapshot_from_payload_rejects_non_object() -> None:
    with pytest.raises(LifecycleError):
        HealthSnapshot.from_payload([1, 2, 3])


def test_snapshot_from_payload_rejects_missing_field() -> None:
    payload = _snapshot().to_payload()
    del payload["recorded_at"]
    with pytest.raises(LifecycleError):
        HealthSnapshot.from_payload(payload)


# ------------------------------------------------------------------ recording


def test_record_creates_history_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        snap = record_health_snapshot(store, now=EPOCH)
        path = history_path_for_store(store)
        assert path.name == DEFAULT_HISTORY_FILENAME
        assert path.is_file()
        assert snap.max_score > 0
        assert 0 <= snap.score <= snap.max_score
        assert snap.prev_hash == "0" * 64
    finally:
        store.close()


def test_record_chains_prev_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = record_health_snapshot(store, now=_at(0))
        second = record_health_snapshot(store, now=_at(1))
        assert second.prev_hash == first.hash
        history = load_health_history(history_path_for_store(store))
        assert [snap.hash for snap in history] == [first.hash, second.hash]
    finally:
        store.close()


def test_record_uses_explicit_history_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    explicit = tmp_path / "custom" / "health.jsonl"
    try:
        record_health_snapshot(store, history_path=explicit, now=EPOCH)
        assert explicit.is_file()
        assert not history_path_for_store(store).exists()
    finally:
        store.close()


def test_record_refuses_memory_store_without_explicit_path(tmp_path: Path) -> None:
    store = EventStore(":memory:")
    try:
        with pytest.raises(LifecycleError):
            record_health_snapshot(store, now=EPOCH)
    finally:
        store.close()


def test_record_detects_history_corruption_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        record_health_snapshot(store, now=EPOCH)
        path = history_path_for_store(store)
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text(lines[0] + "\n" + lines[0] + "\n", encoding="utf-8")  # broken chain
        with pytest.raises(LifecycleError):
            record_health_snapshot(store, now=_at(1))
    finally:
        store.close()


def test_record_components_sorted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        snap = record_health_snapshot(store, now=EPOCH)
        names = [name for name, _ in snap.components]
        assert names == sorted(names)
    finally:
        store.close()


def test_record_counts_match_components(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        snap = record_health_snapshot(store, now=EPOCH)
        assert snap.total_components == len(snap.components)
        assert snap.ok_components == sum(1 for _, ok in snap.components if ok)
    finally:
        store.close()


def test_record_is_deterministic_for_fixed_clock(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    store_a = EventStore(dir_a / "store.sqlite3")
    store_b = EventStore(dir_b / "store.sqlite3")
    try:
        snap_a = record_health_snapshot(store_a, now=EPOCH)
        snap_b = record_health_snapshot(store_b, now=EPOCH)
        assert snap_a.to_payload() == snap_b.to_payload()
    finally:
        store_a.close()
        store_b.close()


# ------------------------------------------------------------------ loading


def test_load_missing_history_is_empty(tmp_path: Path) -> None:
    assert load_health_history(tmp_path / "nope.jsonl") == ()


def test_load_rejects_malformed_json_line(tmp_path: Path) -> None:
    path = tmp_path / "health.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(LifecycleError):
        load_health_history(path)


def test_load_rejects_truncated_last_line(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        record_health_snapshot(store, now=EPOCH)
        record_health_snapshot(store, now=_at(1))
        path = history_path_for_store(store)
        raw = path.read_bytes()
        path.write_bytes(raw[: len(raw) - 10])  # torn write
        with pytest.raises(LifecycleError):
            load_health_history(path)
    finally:
        store.close()


def test_load_rejects_genesis_with_nonzero_prev(tmp_path: Path) -> None:
    snap = _snapshot(prev_hash="1" * 64)
    path = tmp_path / "health.jsonl"
    _write_history(path, [snap])
    with pytest.raises(LifecycleError):
        load_health_history(path)


# ------------------------------------------------------------------ trend analysis


def test_trend_of_empty_history_is_insufficient_data() -> None:
    trend = analyze_health_trend(())
    assert trend.direction == "insufficient-data"
    assert trend.snapshots == 0
    assert trend.first_score is None
    assert trend.last_score is None


def test_trend_single_snapshot_is_insufficient_data() -> None:
    trend = analyze_health_trend((_snapshot(score=90),))
    assert trend.direction == "insufficient-data"
    assert trend.snapshots == 1


def test_trend_stable_scores() -> None:
    snaps = [_snapshot(minutes=i, score=100) for i in range(3)]
    # re-chain
    trend = analyze_health_trend(tuple(snaps))
    assert trend.direction == "stable"
    assert trend.first_score == 100
    assert trend.last_score == 100
    assert trend.current_drop_streak == 0
    assert trend.worst_drop_streak == 0


def test_trend_improving() -> None:
    snaps = [_snapshot(minutes=i, score=60 + i * 10) for i in range(4)]
    trend = analyze_health_trend(tuple(snaps))
    assert trend.direction == "improving"
    assert trend.min_score == 60
    assert trend.max_score_observed == 90


def test_trend_degrading_with_streaks() -> None:
    scores = [100, 90, 80, 80, 70]
    snaps = [_snapshot(minutes=i, score=s) for i, s in enumerate(scores)]
    trend = analyze_health_trend(tuple(snaps))
    assert trend.direction == "degrading"
    assert trend.worst_drop_streak == 2
    assert trend.current_drop_streak == 1


def test_trend_flaky_components_counted() -> None:
    good = _snapshot(minutes=0, score=100)
    bad_payload = _snapshot(minutes=1, score=50).to_payload()
    bad_payload["components"] = [["hash_chain", False], ["store_select", True]]
    bad_payload["ok_components"] = 1
    import hashlib

    bad_payload["hash"] = hashlib.sha256(
        canonical_json({k: v for k, v in bad_payload.items() if k != "hash"}).encode("utf-8")
    ).hexdigest()
    bad = HealthSnapshot.from_payload(bad_payload)
    trend = analyze_health_trend((good, bad))
    flaky = dict(trend.flaky_components)
    assert flaky.get("hash_chain") == 1
    assert "store_select" not in flaky
    assert "hash_chain" in trend.regressions


def test_trend_is_deterministic() -> None:
    snaps = tuple(_snapshot(minutes=i, score=100 - i) for i in range(6))
    assert analyze_health_trend(snaps) == analyze_health_trend(snaps)


def test_trend_frozen() -> None:
    trend = analyze_health_trend(())
    with pytest.raises(Exception):  # noqa: B017
        trend.direction = "stable"  # type: ignore[misc]


# ------------------------------------------------------------------ retention rules


def test_rule_requires_at_least_one_constraint() -> None:
    with pytest.raises(LifecycleError):
        RetentionRule(name="empty")


def test_rule_rejects_negative_age() -> None:
    with pytest.raises(LifecycleError):
        RetentionRule(name="age", max_age_seconds=-1)


def test_rule_rejects_negative_entries() -> None:
    with pytest.raises(LifecycleError):
        RetentionRule(name="count", max_entries=-1)


def test_rule_rejects_empty_name() -> None:
    with pytest.raises(LifecycleError):
        RetentionRule(name="  ", max_entries=1)


def test_item_rejects_blank_key() -> None:
    with pytest.raises(LifecycleError):
        RetentionItem(key="", created_at=EPOCH.isoformat())


def test_retention_age_rule_evicts_old_items() -> None:
    items = (
        RetentionItem(key="old", created_at=_at(0).isoformat()),
        RetentionItem(key="new", created_at=_at(100).isoformat()),
    )
    plan = plan_generic_retention(items, (RetentionRule(name="age", max_age_seconds=60 * 60),), now=_at(120))
    decisions = {d.key: d for d in plan}
    assert decisions["old"].keep is False
    assert decisions["old"].rule == "age"
    assert decisions["new"].keep is True


def test_retention_count_rule_keeps_newest() -> None:
    items = tuple(RetentionItem(key=f"i{i}", created_at=_at(i).isoformat()) for i in range(5))
    plan = plan_generic_retention(items, (RetentionRule(name="count", max_entries=2),), now=_at(10))
    kept = sorted(d.key for d in plan if d.keep)
    evicted = sorted(d.key for d in plan if not d.keep)
    assert kept == ["i3", "i4"]
    assert evicted == ["i0", "i1", "i2"]
    assert all(d.rule == "count" for d in plan if not d.keep)


def test_retention_protected_items_never_evicted() -> None:
    items = (
        RetentionItem(key="pinned", created_at=_at(0).isoformat(), protected=True),
        RetentionItem(key="old", created_at=_at(0).isoformat()),
    )
    plan = plan_generic_retention(items, (RetentionRule(name="age", max_age_seconds=1),), now=_at(500))
    decisions = {d.key: d for d in plan}
    assert decisions["pinned"].keep is True
    assert decisions["pinned"].reason == "protected"
    assert decisions["old"].keep is False


def test_retention_plan_is_deterministic_and_sorted() -> None:
    items = tuple(RetentionItem(key=f"k{i}", created_at=_at(i).isoformat()) for i in range(6))
    rules = (RetentionRule(name="count", max_entries=3),)
    plan_a = plan_generic_retention(items, rules, now=_at(100))
    plan_b = plan_generic_retention(tuple(reversed(items)), rules, now=_at(100))
    assert plan_a == plan_b
    assert [d.key for d in plan_a] == sorted(d.key for d in plan_a)


def test_retention_age_and_count_rules_compose() -> None:
    items = tuple(RetentionItem(key=f"k{i}", created_at=_at(i).isoformat()) for i in range(4))
    rules = (
        RetentionRule(name="age", max_age_seconds=98 * 60),
        RetentionRule(name="count", max_entries=2),
    )
    plan = plan_generic_retention(items, rules, now=_at(100))
    decisions = {d.key: d for d in plan}
    assert decisions["k0"].keep is False  # over age (100 min > 98 min)
    assert decisions["k0"].rule == "age"
    assert decisions["k1"].keep is False
    assert decisions["k2"].keep is True  # exactly at the age boundary survives
    assert decisions["k3"].keep is True


def test_retention_age_rule_uses_strictly_older_than() -> None:
    items = (RetentionItem(key="edge", created_at=_at(0).isoformat()),)
    plan = plan_generic_retention(items, (RetentionRule(name="age", max_age_seconds=60),), now=_at(1))
    assert plan[0].keep is True  # exactly at the boundary survives


def test_retention_rejects_invalid_item_timestamp() -> None:
    items = (RetentionItem(key="bad", created_at="not-a-timestamp"),)
    with pytest.raises(LifecycleError):
        plan_generic_retention(items, (RetentionRule(name="age", max_age_seconds=1),), now=EPOCH)


def test_retention_decisions_carry_reasons() -> None:
    items = (RetentionItem(key="fresh", created_at=_at(99).isoformat()),)
    plan = plan_generic_retention(items, (RetentionRule(name="age", max_age_seconds=60),), now=_at(100))
    assert plan[0].keep is True
    assert plan[0].reason == "within-policy"


# ------------------------------------------------------------------ history pruning


def test_prune_health_history_keeps_newest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for i in range(5):
            record_health_snapshot(store, now=_at(i))
        path = history_path_for_store(store)
        report = prune_health_history(path, keep=2, now=_at(10))
        assert isinstance(report, PruneReport)
        assert report.removed == 3
        assert report.kept == 2
        history = load_health_history(path)
        assert len(history) == 2
        assert history[0].recorded_at == _at(3).isoformat()
    finally:
        store.close()


def test_prune_rechains_after_removal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for i in range(4):
            record_health_snapshot(store, now=_at(i))
        path = history_path_for_store(store)
        previous_head = load_health_history(path)[-1].hash
        report = prune_health_history(path, keep=2, now=_at(10))
        history = load_health_history(path)
        assert history[0].prev_hash == "0" * 64
        assert report.previous_head_hash == previous_head
        assert report.new_head_hash == history[-1].hash
    finally:
        store.close()


def test_prune_dry_run_changes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for i in range(3):
            record_health_snapshot(store, now=_at(i))
        path = history_path_for_store(store)
        before = path.read_bytes()
        report = prune_health_history(path, keep=1, dry_run=True)
        assert report.dry_run is True
        assert report.removed == 2
        assert path.read_bytes() == before
    finally:
        store.close()


def test_prune_keep_zero_empties_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        record_health_snapshot(store, now=EPOCH)
        path = history_path_for_store(store)
        report = prune_health_history(path, keep=0)
        assert report.removed == 1
        assert load_health_history(path) == ()
    finally:
        store.close()


def test_prune_rejects_negative_keep(tmp_path: Path) -> None:
    with pytest.raises(LifecycleError):
        prune_health_history(tmp_path / "h.jsonl", keep=-1)


def test_prune_missing_file_is_noop(tmp_path: Path) -> None:
    report = prune_health_history(tmp_path / "absent.jsonl", keep=1)
    assert report.removed == 0
    assert report.kept == 0
    assert report.new_head_hash is None


def test_prune_refuses_corrupt_history(tmp_path: Path) -> None:
    path = tmp_path / "health.jsonl"
    path.write_text("garbage\n", encoding="utf-8")
    with pytest.raises(LifecycleError):
        prune_health_history(path, keep=1)


# ------------------------------------------------------------------ fragmentation


def test_fragmentation_report_shape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = analyze_fragmentation(store)
        assert isinstance(report, FragmentationReport)
        assert report.page_count > 0
        assert report.freelist_count >= 0
        assert report.page_size > 0
        assert 0 <= report.fragmentation_permille <= 1000
        assert report.file_bytes > 0
    finally:
        store.close()


def test_fragmentation_fresh_store_not_recommended(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert analyze_fragmentation(store).vacuum_recommended is False
    finally:
        store.close()


def test_fragmentation_memory_store_reports_zero_bytes() -> None:
    store = EventStore(":memory:")
    try:
        report = analyze_fragmentation(store)
        assert report.file_bytes == 0
        assert report.wal_bytes == 0
    finally:
        store.close()


# ------------------------------------------------------------------ vacuum


def _fill_and_delete(store: EventStore, count: int = 3) -> None:
    for index in range(count):
        with store.transaction() as conn:
            store.insert_event(
                conn,
                event_id=f"{index:064x}",
                kind="audit",
                event_type="audit.test",
                knowledge_affecting=False,
                actor="tester",
                occurred_at="2026-01-01T00:00:00+00:00",
                payload={"probe": index, "padding": "x" * 200},
            )


def test_vacuum_refuses_memory_store() -> None:
    store = EventStore(":memory:")
    try:
        with pytest.raises(LifecycleError):
            vacuum_store(store)
    finally:
        store.close()


def test_vacuum_reclaims_freelist(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        # Create freelist: grow then drop a scratch table through raw writes.
        store.execute_raw("CREATE TABLE scratch_vacuum (payload TEXT)")
        store.execute_raw("INSERT INTO scratch_vacuum (payload) VALUES (hex(randomblob(200000)))")
        store.execute_raw("DROP TABLE scratch_vacuum")
        fragmented = analyze_fragmentation(store)
        assert fragmented.freelist_count > 0
        report = vacuum_store(store)
        assert isinstance(report, VacuumReport)
        assert report.verified is True
        assert report.freelist_before == fragmented.freelist_count
        assert report.freelist_after == 0
        assert report.pages_after < report.pages_before
        # The pre-vacuum bytes live partly in the WAL; the compacted main file
        # must never exceed the pre-vacuum total footprint.
        assert report.after_bytes <= report.before_bytes + fragmented.wal_bytes
        assert store.verify_chain().ok
    finally:
        store.close()


def test_vacuum_preserves_all_data(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _fill_and_delete(store)
        events_before = store.event_count()
        audit_before = store.audit_event_count()
        head_before = store.verify_chain()
        report = vacuum_store(store)
        assert report.verified is True
        assert store.event_count() == events_before
        assert store.audit_event_count() == audit_before
        assert store.verify_chain().ok
        assert store.verify_chain().checked == head_before.checked
    finally:
        store.close()


def test_vacuum_leaves_no_staging_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        vacuum_store(store)
        leftovers = [p.name for p in tmp_path.iterdir() if ".vacuum-" in p.name]
        assert leftovers == []
    finally:
        store.close()


def test_vacuum_report_duration_nonnegative(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = vacuum_store(store)
        assert report.duration_ms >= 0
    finally:
        store.close()


def test_vacuum_store_still_serves_after_swap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _fill_and_delete(store)
        vacuum_store(store)
        # The coordinated swap reopens the live handle; health still verifies.
        assert check_health(store).ok is True
    finally:
        store.close()


def test_vacuum_on_second_store_object_same_path(tmp_path: Path) -> None:
    """A store reopened in a fresh process-shape (no live registry entry) vacuums too."""
    path = tmp_path / "s.sqlite3"
    store = EventStore(path)
    store.close()
    reopened = EventStore(path)
    try:
        report = vacuum_store(reopened)
        assert report.verified is True
    finally:
        reopened.close()


def test_vacuum_wraps_staging_failure_in_lifecycle_error(tmp_path: Path) -> None:
    """A failed VACUUM INTO surfaces as LifecycleError, never a raw sqlite error."""
    store = _store(tmp_path)
    staging = tmp_path / f".lifecycle.sqlite3.vacuum-{os.getpid()}"
    staging.mkdir()  # a directory at the staging path: VACUUM INTO must fail
    try:
        with pytest.raises(LifecycleError):
            vacuum_store(store)
        assert store.verify_chain().ok  # the original store was not touched
    finally:
        staging.rmdir()
        store.close()


# ------------------------------------------------------------------ misc types


def test_vacuum_report_frozen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = vacuum_store(store)
        with pytest.raises(Exception):  # noqa: B017
            report.verified = False  # type: ignore[misc]
    finally:
        store.close()


def test_fragmentation_report_frozen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = analyze_fragmentation(store)
        with pytest.raises(Exception):  # noqa: B017
            report.page_count = 0  # type: ignore[misc]
    finally:
        store.close()


def test_lifecycle_error_is_runtime_error() -> None:
    assert issubclass(LifecycleError, RuntimeError)


def test_history_filename_constant() -> None:
    assert DEFAULT_HISTORY_FILENAME.endswith(".jsonl")


def test_retention_decision_frozen() -> None:
    decision = RetentionDecision(key="k", keep=True, rule=None, reason="within-policy")
    with pytest.raises(Exception):  # noqa: B017
        decision.keep = False  # type: ignore[misc]


def test_health_trend_fields_default() -> None:
    trend = HealthTrend(snapshots=0)
    assert trend.direction == "insufficient-data"
    assert trend.flaky_components == ()
    assert trend.regressions == ()
