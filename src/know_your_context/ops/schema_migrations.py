"""Explicit schema migrations with verified rollback (W13).

The store's live migration path (``EventStore.migrate``) applies v3/v4 from
Python constants and stays untouched. This module adds the missing half of the
story: the *canonical SQL artifacts* for v3/v4 (``migrations/versions/``) plus
an explicit, verified **rollback** path:

* ``plan_rollback(path, target)`` — read-only: the down steps, in order, with
  the data-loss guard verdict per step;
* ``rollback_to(path, target, allow_data_loss=False)`` — one
  ``BEGIN IMMEDIATE`` transaction per run: data-loss guard, down statements,
  ledger update, post-rollback verification. Any failure aborts the whole
  transaction (atomic — never a half-rolled-back store);
* ``verify_schema(path)`` — ledger ↔ object consistency (mirrors the store's
  fail-closed checks, read-only).

Safety rules (fail closed):

* targets below v2 are refused (the file-migration era has no down scripts);
* dropping v4 with recorded failure reasons, or v3 with graph data, requires
  an explicit ``allow_data_loss=True``;
* ``ALTER TABLE ... DROP COLUMN`` needs SQLite ≥ 3.35 — checked at runtime;
* the rollback never touches ``event_log``/``node_revision``/``evidence``
  (append-only, trigger-protected) or the ``schema_migration`` file ledger.

The up-artifacts are pinned byte-equivalent to the store's in-code statements
by tests, so the two cannot drift.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from know_your_context.enums import MigrationDirection

__all__ = [
    "MIGRATIONS",
    "MIN_SQLITE_FOR_DROP_COLUMN",
    "SUPPORTED_DOWN_TARGETS",
    "VERSIONS_DIR",
    "Migration",
    "RollbackReport",
    "SchemaMigrationError",
    "plan_rollback",
    "recorded_schema_version",
    "rollback_to",
    "verify_schema",
]

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "versions"

#: ALTER TABLE ... DROP COLUMN landed in SQLite 3.35.
MIN_SQLITE_FOR_DROP_COLUMN = (3, 35, 0)

#: Rollback stops at the file-migration era (v1/v2 have no down scripts).
SUPPORTED_DOWN_TARGETS: frozenset[int] = frozenset({2, 3, 4, 5})

#: Tables the v3 migration owns (dropped by its down script).
_V3_TABLES: tuple[str, ...] = ("files", "symbols", "graph_edges", "unresolved_refs")


class SchemaMigrationError(RuntimeError):
    """A schema migration/rollback contract violation (typed, fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


_TRIGGER_START_RE = re.compile(r"^\s*CREATE\s+TRIGGER\b", re.IGNORECASE)
_TRIGGER_END_RE = re.compile(r"\bEND\s*$", re.IGNORECASE)


def _split_statements(sql_text: str) -> tuple[str, ...]:
    """Split a canonical SQL artifact into statements (``;``-separated,
    ``--`` comments stripped first). Only for our own controlled artifacts.

    Trigger-aware (v6): a ``CREATE TRIGGER ... BEGIN <stmts>; END`` block
    contains inner semicolons and must stay ONE statement — the body ends at
    the ``END`` keyword.
    """
    lines = [line for line in sql_text.splitlines() if not line.strip().startswith("--")]
    statements: list[str] = []
    buffer = ""
    in_trigger = False
    for chunk in "\n".join(lines).split(";"):
        buffer = f"{buffer};{chunk}" if buffer else chunk
        stripped = buffer.strip()
        if not in_trigger and _TRIGGER_START_RE.match(stripped):
            in_trigger = True
        if in_trigger and not _TRIGGER_END_RE.search(stripped):
            continue
        in_trigger = False
        if stripped:
            statements.append(stripped)
        buffer = ""
    if buffer.strip():
        statements.append(buffer.strip())
    return tuple(statements)


def _normalize(statement: str) -> str:
    """Whitespace-insensitive comparison form (parity with store constants)."""
    return " ".join(statement.split()).replace(" )", ")").rstrip(";").strip()


@dataclass(frozen=True)
class Migration:
    """One explicit schema migration: up/down SQL artifacts + guards."""

    version: int
    name: str
    description: str
    up_file: str
    down_file: str
    #: SQL counting rows whose loss the down script must guard against.
    data_guard_sql: str | None = None

    def __post_init__(self) -> None:
        if self.version < 3:
            raise SchemaMigrationError("INVALID_SPEC", "explicit migrations start at v3 (v1/v2 are file-era)")
        if not self.name or not self.description:
            raise SchemaMigrationError("INVALID_SPEC", f"migration v{self.version} needs name and description")

    def up_sql(self) -> tuple[str, ...]:
        return _split_statements(_read_artifact(self.up_file))

    def down_sql(self) -> tuple[str, ...]:
        return _split_statements(_read_artifact(self.down_file))


def _read_artifact(filename: str) -> str:
    path = VERSIONS_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise SchemaMigrationError("ARTIFACT_MISSING", f"migration artifact {path} unreadable: {error}") from error


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=3,
        name="universal_graph",
        description="universal knowledge graph: files, symbols, graph_edges, unresolved_refs",
        up_file="0003_up.sql",
        down_file="0003_down.sql",
        data_guard_sql=(
            "SELECT (SELECT COUNT(*) FROM files) + (SELECT COUNT(*) FROM symbols) "
            "+ (SELECT COUNT(*) FROM graph_edges) + (SELECT COUNT(*) FROM unresolved_refs)"
        ),
    ),
    Migration(
        version=4,
        name="unresolved_refs_failure_reason",
        description="unresolved_refs.failure_reason: persisted resolver failure reasons (audit F4)",
        up_file="0004_up.sql",
        down_file="0004_down.sql",
        data_guard_sql="SELECT COUNT(*) FROM unresolved_refs WHERE failure_reason IS NOT NULL",
    ),
    Migration(
        version=5,
        name="node_leases",
        description="node leases: multi-agent write coordination table (W17)",
        up_file="0005_up.sql",
        down_file="0005_down.sql",
        data_guard_sql="SELECT COUNT(*) FROM node_leases",
    ),
    Migration(
        version=6,
        name="retrieval_scale_indexes",
        description=(
            "symbols.name_tail + files.path_rev materialized columns with indexes, the trigger-driven "
            "symbols_watch change counter, and the invocation_journal effective-idempotency-key index "
            "(TASK2 WS-B)"
        ),
        up_file="0006_up.sql",
        down_file="0006_down.sql",
        # Every v6 object is derived/recomputable (columns) or structural
        # (indexes/triggers/counter): rollback loses no user data.
        data_guard_sql=None,
    ),
)

_MIGRATION_BY_VERSION = {migration.version: migration for migration in MIGRATIONS}
_MIN_EXPLICIT_VERSION = 3
_FILE_ERA_VERSION = 2


@dataclass(frozen=True)
class RollbackReport:
    """One executed rollback; ``verified`` is the post-verify verdict."""

    path: str
    from_version: int
    to_version: int
    direction: MigrationDirection
    statements_executed: int
    verified: bool


# ------------------------------------------------------------------ plumbing


def _connect_rw(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    if not target.is_file():
        raise SchemaMigrationError("STORE_MISSING", f"store {target} does not exist")
    conn = sqlite3.connect(f"file:{target}?mode=rw", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(str(row["name"]) == column for row in conn.execute(f"PRAGMA table_info({table})"))  # noqa: S608


def recorded_schema_version(path: str | Path) -> int:
    """The highest version in ``schema_versions`` (0 when the ledger is absent)."""
    conn = _connect_rw(path)
    try:
        if not _table_exists(conn, "schema_versions"):
            return 0
        row = conn.execute("SELECT MAX(version) AS v FROM schema_versions").fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0
    finally:
        conn.close()


# ------------------------------------------------------------------ verify


def verify_schema(path: str | Path) -> dict[str, Any]:
    """Read-only ledger ↔ object consistency report (fail-closed mirror)."""
    conn = _connect_rw(path)
    try:
        problems: list[str] = []
        ledger_exists = _table_exists(conn, "schema_versions")
        versions: list[int] = []
        if ledger_exists:
            versions = sorted(int(row["version"]) for row in conn.execute("SELECT version FROM schema_versions"))
        recorded = versions[-1] if versions else 0
        if recorded >= _MIN_EXPLICIT_VERSION:
            missing_tables = [name for name in _V3_TABLES if not _table_exists(conn, name)]
            if missing_tables:
                problems.append(f"schema_versions records v3+ but tables are missing: {missing_tables}")
        else:
            present = [name for name in _V3_TABLES if _table_exists(conn, name)]
            if present:
                problems.append(f"v3 tables exist without a schema_versions record: {present}")
        has_failure_reason = _table_exists(conn, "unresolved_refs") and _column_exists(
            conn, "unresolved_refs", "failure_reason"
        )
        if recorded >= 4 and not has_failure_reason:
            problems.append("schema_versions records v4 but unresolved_refs.failure_reason is missing")
        if _MIN_EXPLICIT_VERSION <= recorded < 4 and has_failure_reason:
            problems.append("unresolved_refs.failure_reason exists without a v4 record")
        has_node_leases = _table_exists(conn, "node_leases")
        if recorded >= 5 and not has_node_leases:
            problems.append("schema_versions records v5 but the node_leases table is missing")
        if _MIN_EXPLICIT_VERSION <= recorded < 5 and has_node_leases:
            problems.append("node_leases exists without a v5 schema_versions record")
        has_name_tail = _table_exists(conn, "symbols") and _column_exists(conn, "symbols", "name_tail")
        has_watch = _table_exists(conn, "symbols_watch")
        if recorded >= 6 and not has_name_tail:
            problems.append("schema_versions records v6 but symbols.name_tail is missing")
        if recorded >= 6 and not has_watch:
            problems.append("schema_versions records v6 but the symbols_watch table is missing")
        if _MIN_EXPLICIT_VERSION <= recorded < 6 and has_name_tail:
            problems.append("symbols.name_tail exists without a v6 schema_versions record")
        if _MIN_EXPLICIT_VERSION <= recorded < 6 and has_watch:
            problems.append("symbols_watch exists without a v6 schema_versions record")
        return {
            "recorded_version": recorded,
            "versions": versions,
            "consistent": not problems,
            "problems": problems,
        }
    finally:
        conn.close()


# ------------------------------------------------------------------ rollback


def _steps_for(recorded: int, target: int) -> list[Migration]:
    if target not in SUPPORTED_DOWN_TARGETS:
        raise SchemaMigrationError(
            "UNSUPPORTED_TARGET",
            f"rollback target must be one of {sorted(SUPPORTED_DOWN_TARGETS)}, got {target}",
        )
    if recorded < _MIN_EXPLICIT_VERSION:
        raise SchemaMigrationError(
            "NOTHING_TO_ROLLBACK", f"recorded schema version is {recorded}; nothing explicit to roll back"
        )
    if target >= recorded:
        raise SchemaMigrationError("UNSUPPORTED_TARGET", f"target v{target} must be below the recorded v{recorded}")
    return [_MIGRATION_BY_VERSION[v] for v in range(recorded, target, -1) if v in _MIGRATION_BY_VERSION]


def plan_rollback(path: str | Path, target: int) -> list[dict[str, object]]:
    """The down steps for ``rollback_to(path, target)`` — read-only."""
    recorded = recorded_schema_version(path)
    conn = _connect_rw(path)
    try:
        steps: list[dict[str, object]] = []
        for migration in _steps_for(recorded, target):
            guarded_rows = 0
            if migration.data_guard_sql is not None:
                guarded_rows = int(conn.execute(migration.data_guard_sql).fetchone()[0])
            steps.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "direction": MigrationDirection.DOWN,
                    "statements": list(migration.down_sql()),
                    "data_guard_rows": guarded_rows,
                }
            )
        return steps
    finally:
        conn.close()


def rollback_to(path: str | Path, target: int, *, allow_data_loss: bool = False) -> RollbackReport:
    """Roll the schema back to *target*, atomically, with verification.

    One ``BEGIN IMMEDIATE`` transaction: guards, down statements, ledger
    updates. Any failure rolls the whole thing back and raises
    :class:`SchemaMigrationError`. Post-commit, :func:`verify_schema` must
    agree or the report's ``verified`` flag is False (announced, never hidden).
    """
    if sqlite3.sqlite_version_info < MIN_SQLITE_FOR_DROP_COLUMN:
        raise SchemaMigrationError(
            "SQLITE_TOO_OLD",
            f"rollback needs SQLite >= {'.'.join(map(str, MIN_SQLITE_FOR_DROP_COLUMN))} "
            f"(DROP COLUMN), have {sqlite3.sqlite_version}",
        )
    recorded = recorded_schema_version(path)
    steps = _steps_for(recorded, target)
    conn = _connect_rw(path)
    executed = 0
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for migration in steps:
                if migration.data_guard_sql is not None and not allow_data_loss:
                    guarded = int(conn.execute(migration.data_guard_sql).fetchone()[0])
                    if guarded > 0:
                        raise SchemaMigrationError(
                            "DATA_LOSS_GUARD",
                            f"rollback of v{migration.version} ({migration.name}) would lose {guarded} row(s) "
                            "of recorded data; pass allow_data_loss=True to proceed",
                        )
                for statement in migration.down_sql():
                    conn.execute(statement)
                    executed += 1
                if migration.version > _MIN_EXPLICIT_VERSION:
                    conn.execute("DELETE FROM schema_versions WHERE version = ?", (migration.version,))
                # v3's down drops the schema_versions ledger itself (pre-v3 state).
            conn.execute("COMMIT")
        except SchemaMigrationError:
            conn.execute("ROLLBACK")
            raise
        except sqlite3.Error as error:
            conn.execute("ROLLBACK")
            raise SchemaMigrationError(
                "ROLLBACK_FAILED", f"rollback to v{target} failed and was rolled back: {error}"
            ) from error
    finally:
        conn.close()
    verified = bool(verify_schema(path)["consistent"])
    return RollbackReport(
        path=str(path),
        from_version=recorded,
        to_version=target,
        direction=MigrationDirection.DOWN,
        statements_executed=executed,
        verified=verified,
    )
