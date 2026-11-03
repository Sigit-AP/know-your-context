"""Store version migration and portable, hash-verified bundles (W12).

Two capabilities:

* **Version migration** — :func:`inspect_store_version` reads a store's
  ``schema_migration`` ledger strictly read-only (``mode=ro``, no WAL
  sidecars) and classifies it against the migrations on disk:
  ``current`` / ``upgradeable`` / ``newer-than-build`` / ``not-a-store`` /
  ``absent``. :func:`plan_migration` turns that into a deterministic step
  list; :func:`migrate_store` executes it through the existing
  :class:`~know_your_context.store.EventStore` migrator and verifies the
  hash chain afterwards. A store newer than this build is refused *before*
  it is opened — never a partial upgrade, never a silent downgrade.

* **Portable bundles** — :func:`export_bundle` writes a directory holding a
  verified online-backup copy of the store plus a canonical
  ``bundle-manifest.json`` carrying the sha256, size, event count, chain
  head and applied migrations of the payload. :func:`verify_bundle`
  re-checks every hash (a tampered or corrupt payload is detected even when
  the manifest itself is edited — the payload must pass
  :func:`~know_your_context.backup.inspect_backup`, not just the recorded
  hash). :func:`import_bundle` verifies first (fail-closed), then installs
  through :func:`~know_your_context.backup.restore_store`, reusing the F-1
  live-store coordination. Bundles are machine-portable: SQLite is
  endian-stable across supported platforms and the manifest pins the exact
  bytes that must arrive.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from know_your_context import store as store_module
from know_your_context.backup import BackupError, backup_store, inspect_backup, restore_store
from know_your_context.domain import canonical_json
from know_your_context.observability import available_migration_versions
from know_your_context.store import DEFAULT_MIGRATIONS_DIR, EventStore

__all__ = [
    "BUNDLE_FORMAT_VERSION",
    "BUNDLE_MANIFEST_FILENAME",
    "BUNDLE_STORE_FILENAME",
    "BundleManifest",
    "BundleVerification",
    "ImportReport",
    "MigrateError",
    "MigrationPlan",
    "MigrationReport",
    "MigrationStep",
    "StoreVersionInfo",
    "export_bundle",
    "import_bundle",
    "inspect_store_version",
    "migrate_store",
    "plan_migration",
    "verify_bundle",
]

BUNDLE_FORMAT_VERSION = 1
BUNDLE_MANIFEST_FILENAME = "bundle-manifest.json"
BUNDLE_STORE_FILENAME = "store.sqlite3"

STATUSES = ("current", "upgradeable", "newer-than-build", "not-a-store", "absent")


class MigrateError(RuntimeError):
    """A migration/bundle contract violation (typed, fail-closed)."""


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha256(path: Path) -> str:
    # SUB-135: the read buffer follows the disk profile via the single
    # HardwarePlan (SSD/UNKNOWN keep the historical 1 MiB; HDD scales up).
    from know_your_context.hardware.profile import io_buffer_bytes

    buffer = io_buffer_bytes(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(buffer), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _migration_names(migrations_dir: str | Path | None) -> dict[int, str]:
    root = Path(migrations_dir) if migrations_dir is not None else DEFAULT_MIGRATIONS_DIR
    names: dict[int, str] = {}
    for path in sorted(root.glob("*.sql")):
        names[int(path.name.split("_", 1)[0])] = path.name
    return names


# ------------------------------------------------------------------ inspection


@dataclass(frozen=True)
class StoreVersionInfo:
    """Read-only classification of one store file against this build."""

    path: str
    present: bool
    is_sqlite: bool
    applied_versions: tuple[int, ...]
    available_versions: tuple[int, ...]
    pending_versions: tuple[int, ...]
    status: str  # one of STATUSES


def _read_applied_versions(path: Path) -> tuple[bool, tuple[int, ...]]:
    """Open *path* read-only; return ``(is_sqlite, applied versions)``.

    Both ledgers are consulted: ``schema_migration`` (file-based v1/v2) and
    ``schema_versions`` (in-code v3+). The applied set is their union.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return False, ()
    try:
        try:
            file_rows = conn.execute("SELECT version FROM schema_migration ORDER BY version").fetchall()
        except sqlite3.DatabaseError as error:
            message = str(error).lower()
            if "no such table" in message:
                file_rows = []
            elif "file is not a database" in message or "malformed" in message:
                return False, ()
            else:
                return False, ()
        try:
            ledger_rows = conn.execute("SELECT version FROM schema_versions ORDER BY version").fetchall()
        except sqlite3.DatabaseError as error:
            if "no such table" in str(error).lower():
                ledger_rows = []
            else:
                return False, ()
        if not file_rows and not ledger_rows:
            # A SQLite database with neither ledger is not one of our stores,
            # but it is a valid database that migration 0001 would initialize.
            tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()
            if tables is not None and int(tables[0]) > 0:
                return True, ()
        return True, tuple(sorted({int(row[0]) for row in file_rows} | {int(row[0]) for row in ledger_rows}))
    finally:
        conn.close()


def _full_ladder(migrations_dir: str | Path | None) -> tuple[int, ...]:
    """Every version this build can apply: file migrations + in-code v3..N."""
    file_versions = set(available_migration_versions(migrations_dir))
    code_versions = set(range(3, store_module.SCHEMA_VERSION + 1))
    return tuple(sorted(file_versions | code_versions))


def inspect_store_version(
    path: str | Path,
    *,
    migrations_dir: str | Path | None = None,
) -> StoreVersionInfo:
    """Classify a store file; strictly read-only, never raises."""
    target = Path(path)
    available = _full_ladder(migrations_dir)
    if not target.is_file():
        return StoreVersionInfo(
            path=str(target),
            present=False,
            is_sqlite=False,
            applied_versions=(),
            available_versions=available,
            pending_versions=(),
            status="absent",
        )
    is_sqlite, applied = _read_applied_versions(target)
    if not is_sqlite:
        return StoreVersionInfo(
            path=str(target),
            present=True,
            is_sqlite=False,
            applied_versions=(),
            available_versions=available,
            pending_versions=(),
            status="not-a-store",
        )
    max_available = max(available, default=0)
    pending = tuple(version for version in available if version not in applied)
    if applied and max(applied) > max_available:
        status = "newer-than-build"
        pending = ()
    elif pending:
        status = "upgradeable"
    else:
        status = "current"
    return StoreVersionInfo(
        path=str(target),
        present=True,
        is_sqlite=True,
        applied_versions=applied,
        available_versions=available,
        pending_versions=pending,
        status=status,
    )


# ------------------------------------------------------------------ planning


@dataclass(frozen=True)
class MigrationStep:
    """One pending migration: its version and the on-disk file name."""

    version: int
    name: str


@dataclass(frozen=True)
class MigrationPlan:
    """The deterministic plan to bring a store to a target version."""

    path: str
    current_version: int
    target_version: int
    steps: tuple[MigrationStep, ...]
    up_to_date: bool


def plan_migration(
    path: str | Path,
    *,
    target_version: int | None = None,
    migrations_dir: str | Path | None = None,
) -> MigrationPlan:
    """Plan the pending migrations; typed errors for anything unsafe."""
    info = inspect_store_version(path, migrations_dir=migrations_dir)
    if info.status == "absent":
        raise MigrateError(f"store {info.path!r} does not exist")
    if info.status == "not-a-store":
        raise MigrateError(f"store {info.path!r} is not a SQLite database")
    if info.status == "newer-than-build":
        raise MigrateError(
            f"store {info.path!r} is at schema v{max(info.applied_versions)}, newer than this build supports "
            f"(v{max(info.available_versions, default=0)}); refusing to open"
        )
    available = info.available_versions
    target = max(available, default=0) if target_version is None else int(target_version)
    if target not in available and available:
        raise MigrateError(f"target version {target} is not among the available migrations {list(available)}")
    names = _migration_names(migrations_dir)
    steps = tuple(
        MigrationStep(version=version, name=names.get(version, f"in-code-v{version}"))
        for version in info.pending_versions
        if version <= target
    )
    current = max(info.applied_versions, default=0)
    return MigrationPlan(
        path=info.path,
        current_version=current,
        target_version=target,
        steps=steps,
        up_to_date=not steps,
    )


# ------------------------------------------------------------------ applying


@dataclass(frozen=True)
class MigrationReport:
    """The verified outcome of one migration run."""

    path: str
    before_version: int
    after_version: int
    applied: tuple[int, ...]
    chain_ok: bool
    already_current: bool


def migrate_store(
    path: str | Path,
    *,
    migrations_dir: str | Path | None = None,
) -> MigrationReport:
    """Apply pending migrations and verify the hash chain afterwards."""
    plan = plan_migration(path, migrations_dir=migrations_dir)
    if plan.up_to_date:
        store = EventStore(path, migrations_dir=migrations_dir)
        try:
            chain_ok = bool(store.verify_chain().ok)
        finally:
            store.close()
        return MigrationReport(
            path=plan.path,
            before_version=plan.current_version,
            after_version=plan.current_version,
            applied=(),
            chain_ok=chain_ok,
            already_current=True,
        )
    try:
        store = EventStore(path, migrations_dir=migrations_dir)
    except sqlite3.Error as error:
        raise MigrateError(f"migration of {plan.path!r} failed to open the store: {error}") from error
    try:
        applied_now = tuple(version for version in _full_ladder(migrations_dir) if version > plan.current_version)
        chain_ok = bool(store.verify_chain().ok)
        after = max(_full_ladder(migrations_dir), default=plan.current_version)
    finally:
        store.close()
    return MigrationReport(
        path=plan.path,
        before_version=plan.current_version,
        after_version=after,
        applied=applied_now,
        chain_ok=chain_ok,
        already_current=False,
    )


# ------------------------------------------------------------------ bundles


def _require_hex64(value: str, field_name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise MigrateError(f"{field_name} must be 64 lowercase hex characters, got {value!r}")


@dataclass(frozen=True)
class BundleManifest:
    """The canonical manifest of one portable bundle."""

    format_version: int
    created_at: str
    store_file: str
    sha256: str
    size_bytes: int
    event_count: int
    chain_head_hash: str | None
    applied_migrations: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.format_version != BUNDLE_FORMAT_VERSION:
            raise MigrateError(
                f"bundle format v{self.format_version} is not supported by this build (v{BUNDLE_FORMAT_VERSION})"
            )
        if not self.store_file or "/" in self.store_file or "\\" in self.store_file or self.store_file in {".", ".."}:
            raise MigrateError(f"bundle store file name {self.store_file!r} is not a plain file name")
        _require_hex64(self.sha256, "sha256")
        if self.chain_head_hash is not None:
            _require_hex64(self.chain_head_hash, "chain_head_hash")
        if self.size_bytes < 0 or self.event_count < 0:
            raise MigrateError("bundle sizes and counts must be >= 0")

    def to_payload(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "created_at": self.created_at,
            "store_file": self.store_file,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "event_count": self.event_count,
            "chain_head_hash": self.chain_head_hash,
            "applied_migrations": list(self.applied_migrations),
        }

    @classmethod
    def from_payload(cls, payload: Any) -> BundleManifest:
        if not isinstance(payload, dict):
            raise MigrateError(f"bundle manifest must be an object, got {type(payload).__name__}")
        try:
            return cls(
                format_version=int(payload["format_version"]),
                created_at=str(payload["created_at"]),
                store_file=str(payload["store_file"]),
                sha256=str(payload["sha256"]),
                size_bytes=int(payload["size_bytes"]),
                event_count=int(payload["event_count"]),
                chain_head_hash=(None if payload.get("chain_head_hash") is None else str(payload["chain_head_hash"])),
                applied_migrations=tuple(int(v) for v in payload["applied_migrations"]),
            )
        except KeyError as error:
            raise MigrateError(f"bundle manifest is missing field {error}") from error
        except (TypeError, ValueError) as error:
            raise MigrateError(f"bundle manifest field types are invalid: {error}") from error


def _store_applied_versions(store: EventStore) -> tuple[int, ...]:
    """The store's full applied version set: file migrations + in-code ledger."""
    with store.read() as conn:
        file_versions = {int(row["version"]) for row in conn.execute("SELECT version FROM schema_migration")}
        try:
            ledger_versions = {int(row["version"]) for row in conn.execute("SELECT version FROM schema_versions")}
        except sqlite3.DatabaseError:
            ledger_versions = set()
    return tuple(sorted(file_versions | ledger_versions))


def export_bundle(
    store: EventStore,
    dest_dir: str | Path,
    *,
    now: datetime | None = None,
) -> BundleManifest:
    """Export a verified, hash-manifested portable bundle of *store*.

    The destination directory must not exist or be empty. The store copy is
    produced by :func:`~know_your_context.backup.backup_store` (verified
    online backup), the manifest is written canonically, and both file and
    directory are fsynced before the manifest is returned.
    """
    if store.db_path == ":memory:":
        raise MigrateError("cannot export an in-memory store")
    dest = Path(dest_dir)
    if dest.exists() and any(dest.iterdir()):
        raise MigrateError(f"bundle destination {str(dest)!r} exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    if now is None:
        now = datetime.now(UTC)
    try:
        receipt = backup_store(store, dest / BUNDLE_STORE_FILENAME)
        manifest = BundleManifest(
            format_version=BUNDLE_FORMAT_VERSION,
            created_at=now.isoformat(),
            store_file=BUNDLE_STORE_FILENAME,
            sha256=_sha256(dest / BUNDLE_STORE_FILENAME),
            size_bytes=receipt.size_bytes,
            event_count=receipt.event_count,
            chain_head_hash=receipt.chain_head_hash,
            applied_migrations=_store_applied_versions(store),
        )
        manifest_path = dest / BUNDLE_MANIFEST_FILENAME
        manifest_path.write_text(canonical_json(manifest.to_payload()) + "\n", encoding="utf-8")
        _fsync_file(manifest_path)
        _fsync_dir(dest)
        return manifest
    except BackupError as error:
        raise MigrateError(f"bundle export failed at the verified-copy step: {error}") from error


@dataclass(frozen=True)
class BundleVerification:
    """The fail-closed verdict of one bundle verification."""

    ok: bool
    errors: tuple[str, ...]
    manifest: BundleManifest | None


def verify_bundle(bundle_dir: str | Path) -> BundleVerification:
    """Verify a bundle's manifest, hashes and payload; never raises."""
    bundle = Path(bundle_dir)
    errors: list[str] = []
    if not bundle.is_dir():
        return BundleVerification(ok=False, errors=(f"bundle {str(bundle)!r} does not exist",), manifest=None)
    manifest_path = bundle / BUNDLE_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return BundleVerification(ok=False, errors=(f"bundle is missing {BUNDLE_MANIFEST_FILENAME}",), manifest=None)
    try:
        manifest = BundleManifest.from_payload(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, MigrateError) as error:
        return BundleVerification(ok=False, errors=(f"bundle manifest is invalid: {error}",), manifest=None)
    store_path = bundle / manifest.store_file
    if not store_path.is_file():
        errors.append(f"bundle payload {manifest.store_file!r} is missing")
    else:
        actual = _sha256(store_path)
        if actual != manifest.sha256:
            errors.append("bundle payload sha256 does not match the manifest")
        elif int(store_path.stat().st_size) != manifest.size_bytes:
            errors.append("bundle payload size does not match the manifest")
        else:
            # The hash matches but the manifest itself could have been edited
            # to bless a corrupt payload: the payload must still pass the
            # backup inspector (quick_check + chain anchor).
            try:
                info = inspect_backup(store_path)
            except BackupError as error:
                errors.append(f"bundle payload failed inspection: {error}")
            else:
                if not info["ok"]:
                    errors.append("bundle payload failed quick_check")
                elif int(info["event_count"]) != manifest.event_count:
                    errors.append(
                        f"bundle payload holds {info['event_count']} events, manifest claims {manifest.event_count}"
                    )
    return BundleVerification(ok=not errors, errors=tuple(errors), manifest=manifest)


@dataclass(frozen=True)
class ImportReport:
    """The verified outcome of installing a bundle onto a destination."""

    bundle: str
    dest: str
    event_count: int
    chain_head_hash: str | None
    sha256: str
    format_version: int


def import_bundle(
    bundle_dir: str | Path,
    dest_path: str | Path,
    *,
    overwrite: bool = False,
) -> ImportReport:
    """Install a verified bundle at *dest_path* through the restore discipline.

    Verification runs first and is fail-closed: any manifest, hash or
    payload problem raises :class:`MigrateError` before the destination is
    touched.
    """
    bundle = Path(bundle_dir)
    verification = verify_bundle(bundle)
    if not verification.ok or verification.manifest is None:
        raise MigrateError(f"refusing to import an unverified bundle: {'; '.join(verification.errors)}")
    manifest = verification.manifest
    try:
        receipt = restore_store(bundle / manifest.store_file, dest_path, overwrite=overwrite)
    except BackupError as error:
        raise MigrateError(f"bundle import failed at the install step: {error}") from error
    return ImportReport(
        bundle=str(bundle),
        dest=str(dest_path),
        event_count=receipt.event_count,
        chain_head_hash=receipt.chain_head_hash,
        sha256=receipt.sha256,
        format_version=manifest.format_version,
    )
