"""Tests for ``know_your_context.ops.migrate`` (W12 tools surface).

Pins the migration/portability contract: read-only store version inspection
(current / upgradeable / newer-than-build / not-a-store / absent), migration
planning and execution through the existing versioned migrator, and portable
bundles — a directory containing a verified store copy plus a canonical
manifest whose sha256 hashes make tampering detectable — exported from a
live store and imported through the F-1 coordinated restore discipline.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from know_your_context.domain import canonical_json
from know_your_context.observability import available_migration_versions
from know_your_context.ops.migrate import (
    BUNDLE_FORMAT_VERSION,
    BUNDLE_MANIFEST_FILENAME,
    BUNDLE_STORE_FILENAME,
    BundleManifest,
    BundleVerification,
    ImportReport,
    MigrateError,
    MigrationPlan,
    MigrationReport,
    export_bundle,
    import_bundle,
    inspect_store_version,
    migrate_store,
    plan_migration,
    verify_bundle,
)
from know_your_context.store import EventStore

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path, name: str = "migrate.sqlite3") -> EventStore:
    return EventStore(tmp_path / name)


def _legacy_store(path: Path, versions: list[int]) -> None:
    """A store file with only the given schema_migration rows (no modern tables)."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for version in versions:
            conn.execute(
                "INSERT INTO schema_migration (version, name, applied_at) VALUES (?, ?, ?)",
                (version, f"legacy-{version}", "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()


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


# ------------------------------------------------------------------ inspection


def test_inspect_current_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.db_path
    store.close()
    info = inspect_store_version(path)
    assert info.present is True
    assert info.is_sqlite is True
    assert info.status == "current"
    assert info.pending_versions == ()
    assert tuple(info.applied_versions) == info.available_versions


def test_inspect_absent_store(tmp_path: Path) -> None:
    info = inspect_store_version(tmp_path / "nope.sqlite3")
    assert info.present is False
    assert info.status == "absent"
    assert info.applied_versions == ()


def test_inspect_not_a_store(tmp_path: Path) -> None:
    path = tmp_path / "garbage.bin"
    path.write_bytes(b"not a sqlite database at all, definitely")
    info = inspect_store_version(path)
    assert info.present is True
    assert info.is_sqlite is False
    assert info.status == "not-a-store"


def test_inspect_legacy_store_is_upgradeable(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    _legacy_store(path, [1])
    info = inspect_store_version(path)
    assert info.is_sqlite is True
    assert info.status == "upgradeable"
    assert info.pending_versions == tuple(v for v in info.available_versions if v != 1)


def test_inspect_v2_store_is_upgradeable_to_in_code_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine v2 store (both file migrations) is pending the in-code v3/v4."""
    from know_your_context import store as store_module

    path = tmp_path / "v2.sqlite3"
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 2)
    legacy = EventStore(path)
    legacy.close()
    monkeypatch.undo()
    info = inspect_store_version(path)
    assert info.status == "upgradeable"
    assert info.pending_versions == (3, 4, 5, 6)


def test_inspect_empty_sqlite_is_upgradeable(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    info = inspect_store_version(path)
    assert info.status == "upgradeable"
    assert info.applied_versions == ()
    assert info.pending_versions == info.available_versions


def test_inspect_newer_than_build(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    _legacy_store(path, [999])
    info = inspect_store_version(path)
    assert info.status == "newer-than-build"
    assert info.pending_versions == ()


def test_inspect_is_read_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.db_path
    store.close()
    before = Path(path).read_bytes()
    inspect_store_version(path)
    assert Path(path).read_bytes() == before


def test_inspect_does_not_create_wal_sidecars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.db_path
    store.close()
    for sidecar in (f"{path}-wal", f"{path}-shm"):
        if os.path.exists(sidecar):
            os.remove(sidecar)
    inspect_store_version(path)
    assert not os.path.exists(f"{path}-wal")
    assert not os.path.exists(f"{path}-shm")


def test_store_version_info_frozen(tmp_path: Path) -> None:
    info = inspect_store_version(tmp_path / "absent.sqlite3")
    with pytest.raises(Exception):  # noqa: B017
        info.status = "current"  # type: ignore[misc]


# ------------------------------------------------------------------ planning


def test_plan_current_store_is_up_to_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.db_path
    store.close()
    plan = plan_migration(path)
    assert isinstance(plan, MigrationPlan)
    assert plan.up_to_date is True
    assert plan.steps == ()
    assert plan.current_version == max(inspect_store_version(path).available_versions)


def test_plan_current_store_target_matches_build(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.db_path
    store.close()
    plan = plan_migration(path)
    info = inspect_store_version(path)
    assert plan.current_version == max(info.available_versions)
    assert plan.target_version == max(info.available_versions)


def test_plan_legacy_store_lists_pending_steps(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    _legacy_store(path, [1])
    plan = plan_migration(path)
    assert plan.up_to_date is False
    assert plan.current_version == 1
    assert [step.version for step in plan.steps] == sorted(step.version for step in plan.steps)
    assert all(step.name for step in plan.steps)


def test_plan_newer_than_build_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    _legacy_store(path, [999])
    with pytest.raises(MigrateError):
        plan_migration(path)


def test_plan_absent_store_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(MigrateError):
        plan_migration(tmp_path / "absent.sqlite3")


def test_plan_not_a_store_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "junk.db"
    path.write_bytes(b"junk junk junk junk junk junk")
    with pytest.raises(MigrateError):
        plan_migration(path)


def test_plan_target_version_validation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.db_path
    store.close()
    with pytest.raises(MigrateError):
        plan_migration(path, target_version=999)


# ------------------------------------------------------------------ applying


def test_migrate_store_already_current(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _event(store, 1)
    path = store.db_path
    store.close()
    report = migrate_store(path)
    assert isinstance(report, MigrationReport)
    assert report.already_current is True
    assert report.applied == ()
    assert report.chain_ok is True
    assert report.before_version == report.after_version


def test_migrate_store_applies_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from know_your_context import store as store_module

    path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 2)
    legacy = EventStore(path)  # a genuine v2 store
    legacy.close()
    monkeypatch.undo()
    report = migrate_store(path)
    assert report.already_current is False
    assert report.before_version == 2
    assert report.after_version == 6
    assert report.applied == (3, 4, 5, 6)
    # the migrated store opens as a fully current EventStore
    store = EventStore(path)
    try:
        assert store.applied_migrations() == available_migration_versions(None)
        assert inspect_store_version(path).status == "current"
    finally:
        store.close()


def test_migrate_store_newer_than_build_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    _legacy_store(path, [999])
    before = path.read_bytes()
    with pytest.raises(MigrateError):
        migrate_store(path)
    assert path.read_bytes() == before  # refused before touching the file


def test_migrate_store_garbage_refused(tmp_path: Path) -> None:
    path = tmp_path / "garbage.db"
    path.write_bytes(b"garbage garbage garbage garbage")
    with pytest.raises(MigrateError):
        migrate_store(path)


def test_migrate_error_is_runtime_error() -> None:
    assert issubclass(MigrateError, RuntimeError)


# ------------------------------------------------------------------ bundle export


def test_export_bundle_layout(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _event(store, 1)
        manifest = export_bundle(store, tmp_path / "bundle", now=EPOCH)
        assert isinstance(manifest, BundleManifest)
        bundle = tmp_path / "bundle"
        assert (bundle / BUNDLE_STORE_FILENAME).is_file()
        assert (bundle / BUNDLE_MANIFEST_FILENAME).is_file()
        assert manifest.format_version == BUNDLE_FORMAT_VERSION
        assert manifest.created_at == EPOCH.isoformat()
        assert manifest.event_count == 1
        payload = json.loads((bundle / BUNDLE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert payload["sha256"] == hashlib.sha256((bundle / BUNDLE_STORE_FILENAME).read_bytes()).hexdigest()
    finally:
        store.close()


def test_export_bundle_refuses_nonempty_destination(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "stray").write_text("x", encoding="utf-8")
    try:
        with pytest.raises(MigrateError):
            export_bundle(store, dest)
    finally:
        store.close()


def test_export_bundle_records_full_version_ladder(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        manifest = export_bundle(store, tmp_path / "bundle", now=EPOCH)
        assert manifest.applied_migrations == (1, 2, 3, 4, 5, 6)  # file migrations + in-code ledger
    finally:
        store.close()


def test_export_bundle_refuses_memory_store(tmp_path: Path) -> None:
    store = EventStore(":memory:")
    try:
        with pytest.raises(MigrateError):
            export_bundle(store, tmp_path / "bundle")
    finally:
        store.close()


def test_export_bundle_is_deterministic_for_fixed_clock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _event(store, 1)
        export_bundle(store, tmp_path / "b1", now=EPOCH)
        export_bundle(store, tmp_path / "b2", now=EPOCH)
        m1 = (tmp_path / "b1" / BUNDLE_MANIFEST_FILENAME).read_bytes()
        m2 = (tmp_path / "b2" / BUNDLE_MANIFEST_FILENAME).read_bytes()
        assert m1 == m2
    finally:
        store.close()


def test_export_bundle_records_chain_head(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _event(store, 1)
        _event(store, 2)
        chain = store.verify_chain()
        manifest = export_bundle(store, tmp_path / "bundle", now=EPOCH)
        assert manifest.chain_head_hash is not None
        assert chain.ok
    finally:
        store.close()


# ------------------------------------------------------------------ bundle verify


def test_verify_bundle_ok(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _event(store, 1)
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    verification = verify_bundle(tmp_path / "bundle")
    assert isinstance(verification, BundleVerification)
    assert verification.ok is True
    assert verification.errors == ()
    assert verification.manifest is not None


def test_verify_bundle_detects_tampered_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _event(store, 1)
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    target = tmp_path / "bundle" / BUNDLE_STORE_FILENAME
    with open(target, "r+b") as handle:
        handle.seek(0, 2)
        handle.write(b"tampered")
    verification = verify_bundle(tmp_path / "bundle")
    assert verification.ok is False
    assert any("sha256" in error for error in verification.errors)


def test_verify_bundle_detects_missing_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    (tmp_path / "bundle" / BUNDLE_STORE_FILENAME).unlink()
    verification = verify_bundle(tmp_path / "bundle")
    assert verification.ok is False
    assert any("missing" in error.lower() or "absent" in error for error in verification.errors)


def test_verify_bundle_detects_tampered_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    manifest_path = tmp_path / "bundle" / BUNDLE_MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["event_count"] = 999
    manifest_path.write_text(canonical_json(payload), encoding="utf-8")
    verification = verify_bundle(tmp_path / "bundle")
    assert verification.ok is False


def test_verify_bundle_rejects_wrong_format_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    manifest_path = tmp_path / "bundle" / BUNDLE_MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["format_version"] = 999
    manifest_path.write_text(canonical_json(payload), encoding="utf-8")
    verification = verify_bundle(tmp_path / "bundle")
    assert verification.ok is False
    assert any("format" in error.lower() for error in verification.errors)


def test_verify_bundle_missing_dir_never_raises(tmp_path: Path) -> None:
    verification = verify_bundle(tmp_path / "ghost")
    assert verification.ok is False
    assert verification.manifest is None


def test_verify_bundle_rejects_malformed_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / BUNDLE_MANIFEST_FILENAME).write_text("not json", encoding="utf-8")
    verification = verify_bundle(bundle)
    assert verification.ok is False


# ------------------------------------------------------------------ bundle import


def test_import_bundle_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for seq in range(3):
            _event(store, seq + 1)
        head = store.verify_chain()
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
        source_events = store.event_count()
    finally:
        store.close()
    dest = tmp_path / "imported.sqlite3"
    report = import_bundle(tmp_path / "bundle", dest)
    assert isinstance(report, ImportReport)
    assert report.event_count == source_events
    imported = EventStore(dest)
    try:
        assert imported.event_count() == source_events
        assert imported.verify_chain().ok
        assert imported.verify_chain().checked == head.checked
    finally:
        imported.close()


def test_import_bundle_refuses_existing_dest_without_overwrite(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    with pytest.raises(Exception, match="overwrite"):
        import_bundle(tmp_path / "bundle", store.db_path)


def test_import_bundle_overwrite_replaces(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _event(store, 1)
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    report = import_bundle(tmp_path / "bundle", store.db_path, overwrite=True)
    assert report.event_count == 1
    reopened = EventStore(store.db_path)
    try:
        assert reopened.event_count() == 1
    finally:
        reopened.close()


def test_import_bundle_refuses_tampered_bundle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    target = tmp_path / "bundle" / BUNDLE_STORE_FILENAME
    with open(target, "r+b") as handle:
        handle.seek(0, 2)
        handle.write(b"tampered")
    dest = tmp_path / "dest.sqlite3"
    with pytest.raises(MigrateError):
        import_bundle(tmp_path / "bundle", dest)
    assert not dest.exists()  # nothing was installed


def test_import_bundle_refuses_corrupt_store_payload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    target = tmp_path / "bundle" / BUNDLE_STORE_FILENAME
    data = bytearray(target.read_bytes())
    data[8192:12288] = b"\xff" * 4096  # corrupt an interior page
    target.write_bytes(bytes(data))
    manifest_path = tmp_path / "bundle" / BUNDLE_MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()  # re-sign the corruption
    manifest_path.write_text(canonical_json(payload), encoding="utf-8")
    with pytest.raises(MigrateError):
        import_bundle(tmp_path / "bundle", tmp_path / "dest.sqlite3")


def test_bundle_manifest_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _event(store, 1)
        manifest = export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    loaded = BundleManifest.from_payload(json.loads(json.dumps(manifest.to_payload())))
    assert loaded == manifest


def test_bundle_manifest_rejects_bad_format() -> None:
    manifest = BundleManifest(
        format_version=BUNDLE_FORMAT_VERSION,
        created_at=EPOCH.isoformat(),
        store_file=BUNDLE_STORE_FILENAME,
        sha256="0" * 64,
        size_bytes=1,
        event_count=0,
        chain_head_hash=None,
        applied_migrations=(1,),
    )
    payload = manifest.to_payload()
    payload["format_version"] = 42
    with pytest.raises(MigrateError):
        BundleManifest.from_payload(payload)


def test_bundle_manifest_rejects_bad_hash_shape() -> None:
    with pytest.raises(MigrateError):
        BundleManifest(
            format_version=BUNDLE_FORMAT_VERSION,
            created_at=EPOCH.isoformat(),
            store_file=BUNDLE_STORE_FILENAME,
            sha256="xyz",
            size_bytes=1,
            event_count=0,
            chain_head_hash=None,
            applied_migrations=(1,),
        )


def test_bundle_manifest_rejects_traversal_store_name() -> None:
    with pytest.raises(MigrateError):
        BundleManifest(
            format_version=BUNDLE_FORMAT_VERSION,
            created_at=EPOCH.isoformat(),
            store_file="../escape.sqlite3",
            sha256="0" * 64,
            size_bytes=1,
            event_count=0,
            chain_head_hash=None,
            applied_migrations=(1,),
        )


def test_bundle_manifest_frozen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        manifest = export_bundle(store, tmp_path / "bundle", now=EPOCH)
    finally:
        store.close()
    with pytest.raises(Exception):  # noqa: B017
        manifest.event_count = 5  # type: ignore[misc]
