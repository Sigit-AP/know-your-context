"""Deep diagnostics with structured repair suggestions (W12 tools surface).

:func:`run_doctor` goes beyond the read-only self-check of
:mod:`know_your_context.ops.diagnose`: where ``diagnose`` verifies the store
and projection *state*, ``doctor`` probes the *environment the store runs
in* — the Python/SQLite runtime floors, the installed dependency set, the
filesystem the store lives on (writability probe, free-space headroom), the
permission posture (file modes, process umask), the deeper integrity drills
(``quick_check``, optional full ``integrity_check``, ``foreign_key_check``)
and live performance probes (read latency distribution, fragmentation, WAL
growth, stale journals).

Every finding is a :class:`DoctorFinding` with a tiered severity and a
``suggestions`` tuple of concrete, actionable repair steps, so the report
doubles as a runbook. Every check is fail-closed: a crashing check becomes a
``CHECK_CRASHED`` error finding and never escapes as an exception. The
report's :func:`exit_code` is ``0`` when nothing exceeds ``warning`` and
``1`` when any ``error`` finding exists, so ``doctor`` can gate scripts and
CI directly.

Probes are non-destructive: the writability check creates and removes a
0600 probe file next to the store; the latency probe runs ``SELECT 1``;
integrity drills are read-only PRAGMAs. Runtime facts (Python version,
SQLite version, umask, dependency versions) are injectable for deterministic
tests.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from know_your_context.observability import available_migration_versions
from know_your_context.ops.lifecycle import analyze_fragmentation
from know_your_context.ops.metrics import latency_summary
from know_your_context.security import redact_value
from know_your_context.store import EventStore

__all__ = [
    "CATEGORIES",
    "EXIT_ISSUES",
    "EXIT_OK",
    "RUNTIME_DEPENDENCIES",
    "SEVERITY_ORDER",
    "SQLITE_MIN_VERSION",
    "SUPPORTED_PYTHON",
    "WAL_WARN_BYTES",
    "DoctorFinding",
    "DoctorReport",
    "exit_code",
    "render_markdown",
    "run_doctor",
]

SEVERITY_ORDER = ("info", "warning", "error")
CATEGORIES = ("environment", "dependencies", "permissions", "integrity", "performance")
EXIT_OK = 0
EXIT_ISSUES = 1

#: The supported Python window (mirrors ``requires-python`` in pyproject.toml).
SUPPORTED_PYTHON = ((3, 14), (3, 15))
#: SQLite floor: VACUUM INTO with an expression argument (3.27+) backs the
#: lifecycle vacuum; older engines cannot run the W12 tooling.
SQLITE_MIN_VERSION = (3, 27, 0)
#: WAL sizes beyond this many bytes warrant a checkpoint warning.
WAL_WARN_BYTES = 32 * 1024 * 1024
#: Free-space floor: at least this many bytes, or twice the store file.
DISK_FREE_FLOOR_BYTES = 16 * 1024 * 1024

#: Runtime dependencies as (distribution, import module) pairs.
RUNTIME_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("graphifyy", "graphify"),
    ("mcp", "mcp"),
    ("networkx", "networkx"),
    ("numpy", "numpy"),
    ("pydantic", "pydantic"),
    ("pypdf", "pypdf"),
    ("starlette", "starlette"),
    ("tree-sitter-language-pack", "tree_sitter_language_pack"),
    ("uvicorn", "uvicorn"),
)


@dataclass(frozen=True)
class DoctorFinding:
    """One tiered deep-diagnostic observation with repair suggestions."""

    code: str
    category: str
    severity: str
    message: str
    suggestions: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}")


@dataclass(frozen=True)
class DoctorReport:
    """The fail-closed verdict of a full deep diagnostic."""

    findings: tuple[DoctorFinding, ...] = ()
    checked: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "warning")

    @property
    def worst_severity(self) -> str:
        for severity in reversed(SEVERITY_ORDER):
            if any(finding.severity == severity for finding in self.findings):
                return severity
        return "info"

    @property
    def category_counts(self) -> dict[str, int]:
        return {
            category: sum(1 for finding in self.findings if finding.category == category) for category in CATEGORIES
        }


def exit_code(report: DoctorReport) -> int:
    """The process exit code for a report: 1 iff any error finding exists."""
    return EXIT_ISSUES if report.error_count else EXIT_OK


# ------------------------------------------------------------------ internals


def _finding(
    code: str,
    category: str,
    severity: str,
    message: str,
    *,
    suggestions: tuple[str, ...] = (),
    **details: Any,
) -> DoctorFinding:
    redacted = redact_value({"message": message, "details": details, "suggestions": list(suggestions)})
    assert isinstance(redacted, dict)
    return DoctorFinding(
        code=code,
        category=category,
        severity=severity,
        message=str(redacted["message"]),
        suggestions=tuple(str(s) for s in redacted["suggestions"]),
        details=dict(redacted["details"]),
    )


class _Checks:
    """Accumulator: runs each check, contains crashes as CHECK_CRASHED findings."""

    def __init__(self) -> None:
        self.findings: list[DoctorFinding] = []
        self.checked: list[str] = []

    def run(self, name: str, body: Any) -> None:
        self.checked.append(name)
        try:
            body()
        except Exception as error:  # noqa: BLE001 - diagnostics never raise; they report
            self.findings.append(
                _finding(
                    "CHECK_CRASHED",
                    "integrity",
                    "error",
                    f"check {name} failed to run: {type(error).__name__}: {error}",
                    suggestions=("re-run doctor; if the crash persists, file an issue with this finding",),
                )
            )

    def add(self, finding: DoctorFinding) -> None:
        self.findings.append(finding)


def _parse_version(text: str) -> tuple[int, ...] | None:
    parts: list[int] = []
    for piece in text.split("."):
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _default_version_lookup(distribution: str) -> str:
    from importlib import metadata

    return metadata.version(distribution)


# ------------------------------------------------------------------ report


def render_markdown(report: DoctorReport) -> str:
    """Render a report as a stable markdown runbook, errors first."""
    lines: list[str] = ["# Doctor Report", ""]
    status = "ok" if report.ok else "issues"
    lines.append(f"Status: **{status}** — {report.error_count} error(s), {report.warning_count} warning(s).")
    lines.append("")
    for category in CATEGORIES:
        lines.append(f"## {category.capitalize()}")
        lines.append("")
        findings = sorted(
            (f for f in report.findings if f.category == category),
            key=lambda f: (-SEVERITY_ORDER.index(f.severity), f.code),
        )
        if not findings:
            lines.append("No findings.")
        else:
            lines.append("| severity | code | message |")
            lines.append("|---|---|---|")
            for finding in findings:
                lines.append(f"| {finding.severity} | {finding.code} | {finding.message} |")
            for finding in findings:
                if finding.suggestions:
                    lines.append("")
                    lines.append(f"**{finding.code} — suggestions:**")
                    for suggestion in finding.suggestions:
                        lines.append(f"- {suggestion}")
        lines.append("")
    if not report.findings:
        lines.append("No findings.")
        lines.append("")
    lines.append("## Checks")
    lines.append("")
    for name in report.checked:
        lines.append(f"- {name}")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ checks
#
# One module-level function per check (TASK2 WS-F / F2C): run_doctor is a
# pure dispatch table over these — the pre-refactor nested closures put the
# whole module's cyclomatic weight (C=38) on one function.


def _python_version_check(checks: _Checks, python_version: tuple[int, ...] | None) -> None:
    version = python_version if python_version is not None else tuple(sys.version_info[:3])
    low, high = SUPPORTED_PYTHON
    if not (low <= tuple(version[:2]) < high):
        checks.add(
            _finding(
                "PYTHON_VERSION_UNSUPPORTED",
                "environment",
                "warning",
                f"Python {'.'.join(str(p) for p in version)} is outside the supported window "
                f"[{'.'.join(map(str, low))}, {'.'.join(map(str, high))})",
                suggestions=(f"run on Python >= {'.'.join(map(str, low))} and < {'.'.join(map(str, high))}",),
                python_version=list(version),
            )
        )


def _platform_check(checks: _Checks) -> None:
    if os.name != "posix":
        checks.add(
            _finding(
                "PLATFORM_LIMITED",
                "environment",
                "warning",
                f"platform os.name={os.name!r}: POSIX permission and fsync semantics are not guaranteed",
                suggestions=("prefer a POSIX host for production stores",),
                os_name=os.name,
            )
        )


def _sqlite_version_check(checks: _Checks, sqlite_version: str | None) -> None:
    version_text = sqlite_version if sqlite_version is not None else sqlite3.sqlite_version
    parsed = _parse_version(version_text)
    if parsed is None or parsed < SQLITE_MIN_VERSION:
        checks.add(
            _finding(
                "SQLITE_VERSION_TOO_OLD",
                "environment",
                "error",
                f"SQLite {version_text} is below the supported floor {'.'.join(map(str, SQLITE_MIN_VERSION))}",
                suggestions=(
                    "upgrade the Python build or the system SQLite to >= "
                    f"{'.'.join(map(str, SQLITE_MIN_VERSION))} (VACUUM INTO expressions require it)",
                ),
                sqlite_version=version_text,
            )
        )


def _store_dir_writable(checks: _Checks, store: EventStore) -> None:
    directory = Path(store.db_path).resolve().parent
    probe = directory / f".doctor-probe-{os.getpid()}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, b"probe")
        finally:
            os.close(fd)
    except OSError as error:
        checks.add(
            _finding(
                "STORE_DIR_NOT_WRITABLE",
                "environment",
                "error",
                f"the store directory {directory} refused a write probe: {error}",
                suggestions=(
                    "restore write permission on the store directory",
                    f"check the mount flags of {directory} (read-only mounts reject writes)",
                ),
                directory=str(directory),
            )
        )
    finally:
        if probe.is_file():
            probe.unlink()


def _disk_free(checks: _Checks, store: EventStore) -> None:
    directory = Path(store.db_path).resolve().parent
    usage = shutil.disk_usage(directory)
    store_bytes = Path(store.db_path).stat().st_size if Path(store.db_path).is_file() else 0
    floor = max(DISK_FREE_FLOOR_BYTES, store_bytes * 2)
    if usage.free < floor:
        checks.add(
            _finding(
                "DISK_FREE_LOW",
                "environment",
                "warning",
                f"free space {usage.free} bytes is below the safety floor {floor} bytes",
                suggestions=(
                    "free disk space or move the store to a larger volume",
                    "run `know-your-context lifecycle vacuum` to reclaim freelist pages",
                ),
                free_bytes=usage.free,
                floor_bytes=floor,
            )
        )


def _dependencies(checks: _Checks, lookup: Callable[[str], str]) -> None:
    import importlib.util

    for distribution, module in RUNTIME_DEPENDENCIES:
        installed: str | None = None
        if importlib.util.find_spec(module) is not None:
            try:
                installed = lookup(distribution)
            except Exception:  # noqa: BLE001 - a failing lookup means "not installed"
                installed = None
        if installed is None:
            checks.add(
                _finding(
                    "DEPENDENCY_MISSING",
                    "dependencies",
                    "error",
                    f"runtime dependency {distribution!r} (module {module!r}) is not importable",
                    suggestions=("run `uv sync` to install the locked dependency set",),
                    distribution=distribution,
                    module=module,
                )
            )
        else:
            checks.add(
                _finding(
                    "DEPENDENCY_VERSION",
                    "dependencies",
                    "info",
                    f"{distribution} {installed} installed",
                    distribution=distribution,
                    installed=installed,
                )
            )


def _store_file_permissions(checks: _Checks, store: EventStore) -> None:
    path = Path(store.db_path)
    if not path.is_file():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        checks.add(
            _finding(
                "STORE_FILE_PERMISSIONS_OPEN",
                "permissions",
                "warning",
                f"the store file is group/other-accessible (mode {oct(mode)})",
                suggestions=(f"chmod 600 {path}",),
                path=str(path),
                mode=oct(mode),
            )
        )


def _process_umask(checks: _Checks, umask: int | None) -> None:
    if umask is not None:
        mask = umask
    else:
        mask = os.umask(0o022)
        os.umask(mask)  # restore immediately: the read must never leak
    if (0o777 & ~mask) & 0o002:
        checks.add(
            _finding(
                "UMASK_TOO_OPEN",
                "permissions",
                "warning",
                f"process umask {oct(mask)} lets new files default to world-writable",
                suggestions=("set umask 022 or stricter (077) before running the service",),
                umask=oct(mask),
            )
        )


def _quick_check(checks: _Checks, store: EventStore) -> None:
    with store.read() as conn:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    results = [str(row[0]) for row in rows]
    if results != ["ok"]:
        checks.add(
            _finding(
                "QUICK_CHECK_FAILED",
                "integrity",
                "error",
                f"PRAGMA quick_check reported {len(results)} problem(s)",
                suggestions=(
                    "restore from the newest verified backup (`know-your-context restore`)",
                    "inspect the store with `sqlite3 <store> 'PRAGMA integrity_check'`",
                ),
                results=results[:10],
            )
        )


def _integrity_check(checks: _Checks, store: EventStore) -> None:
    with store.read() as conn:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    results = [str(row[0]) for row in rows]
    if results != ["ok"]:
        checks.add(
            _finding(
                "INTEGRITY_CHECK_FAILED",
                "integrity",
                "error",
                f"PRAGMA integrity_check reported {len(results)} problem(s)",
                suggestions=("restore from the newest verified backup",),
                results=results[:10],
            )
        )


def _foreign_keys(checks: _Checks, store: EventStore) -> None:
    with store.read() as conn:
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    if rows:
        checks.add(
            _finding(
                "FOREIGN_KEY_VIOLATIONS",
                "integrity",
                "error",
                f"PRAGMA foreign_key_check reported {len(rows)} violation(s)",
                suggestions=("restore from the newest verified backup; foreign-key violations indicate torn writes",),
                violations=len(rows),
            )
        )


def _hash_chain(checks: _Checks, store: EventStore) -> None:
    report = store.verify_chain()
    if not report.ok:
        checks.add(
            _finding(
                "CHAIN_BROKEN",
                "integrity",
                "error",
                "the event-log hash chain does not verify",
                suggestions=(
                    "restore from the newest verified backup (`know-your-context restore`)",
                    "do not accept further writes until the chain verifies again",
                ),
                checked=report.checked,
                first_bad_seq=report.first_bad_seq,
            )
        )


def _migrations(checks: _Checks, store: EventStore) -> None:
    applied = store.applied_migrations()
    available = available_migration_versions(None)
    if applied != available:
        checks.add(
            _finding(
                "MIGRATIONS_DRIFT",
                "integrity",
                "error",
                "applied migrations differ from the migrations on disk",
                suggestions=("run `know-your-context migrate plan` then `know-your-context migrate apply`",),
                applied=list(applied),
                available=list(available),
            )
        )


def _read_latency(checks: _Checks, store: EventStore, probe_iterations: int, slow_probe_ms: int) -> None:
    if probe_iterations <= 0:
        return
    durations: list[int] = []
    for _ in range(probe_iterations):
        started = time.monotonic_ns()
        with store.read() as conn:
            conn.execute("SELECT 1").fetchone()
        durations.append((time.monotonic_ns() - started) // 1_000_000)
    summary = latency_summary(durations)
    checks.add(
        _finding(
            "READ_LATENCY",
            "performance",
            "info",
            f"read probe p95 {summary['p95']} ms over {probe_iterations} iterations",
            iterations=probe_iterations,
            min_ms=summary["min"],
            mean_ms=summary["mean"],
            p50_ms=summary["p50"],
            p95_ms=summary["p95"],
            max_ms=summary["max"],
        )
    )
    p95 = summary["p95"]
    if p95 is not None and p95 > slow_probe_ms:
        checks.add(
            _finding(
                "READ_LATENCY_SLOW",
                "performance",
                "warning",
                f"read probe p95 {p95} ms exceeds the {slow_probe_ms} ms threshold",
                suggestions=(
                    "run `know-your-context lifecycle vacuum` to defragment the store",
                    "check disk health and I/O contention on the store volume",
                    "raise the file-descriptor and memory limits of the service process",
                ),
                p95_ms=p95,
                threshold_ms=slow_probe_ms,
            )
        )


def _fragmentation(checks: _Checks, store: EventStore) -> None:
    report = analyze_fragmentation(store)
    if report.vacuum_recommended:
        checks.add(
            _finding(
                "FRAGMENTATION_HIGH",
                "performance",
                "warning",
                f"{report.freelist_count} of {report.page_count} pages "
                f"({report.fragmentation_permille} permille) sit on the freelist",
                suggestions=("run `know-your-context lifecycle vacuum` to reclaim freelist pages",),
                page_count=report.page_count,
                freelist_count=report.freelist_count,
                fragmentation_permille=report.fragmentation_permille,
            )
        )
    if report.wal_bytes > WAL_WARN_BYTES:
        checks.add(
            _finding(
                "WAL_LARGE",
                "performance",
                "warning",
                f"the write-ahead log holds {report.wal_bytes} bytes",
                suggestions=("let a checkpoint drain the WAL, or run `know-your-context lifecycle vacuum`",),
                wal_bytes=report.wal_bytes,
            )
        )


def _stale_journals(checks: _Checks, store: EventStore, stale_after_seconds: float) -> None:
    stale = store.incomplete_journals(stale_after_seconds)
    if stale:
        checks.add(
            _finding(
                "STALE_JOURNALS",
                "performance",
                "warning",
                f"{len(stale)} in-progress journal(s) are stale beyond {stale_after_seconds}s",
                suggestions=(
                    "restart the service so crash recovery closes the stale journals",
                    "inspect `know-your-context metrics --stale-after` for the affected operations",
                ),
                count=len(stale),
            )
        )


# ------------------------------------------------------------------ main


def run_doctor(
    store: EventStore,
    *,
    projection_root: str | Path | None = None,  # noqa: ARG001 - parity with diagnose; reserved
    full_integrity: bool = False,
    stale_after_seconds: float = 300.0,
    probe_iterations: int = 32,
    slow_probe_ms: int = 500,
    python_version: tuple[int, ...] | None = None,
    sqlite_version: str | None = None,
    umask: int | None = None,
    version_lookup: Callable[[str], str] | None = None,
) -> DoctorReport:
    """Run the deep diagnostic; non-destructive, fail-closed per check."""
    checks = _Checks()
    lookup = version_lookup if version_lookup is not None else _default_version_lookup
    is_memory = getattr(store, "db_path", ":memory:") == ":memory:"

    # -------------------------------------------------------- environment
    checks.run("python_version", lambda: _python_version_check(checks, python_version))
    checks.run("platform", lambda: _platform_check(checks))
    checks.run("sqlite_version", lambda: _sqlite_version_check(checks, sqlite_version))
    if not is_memory:
        checks.run("store_dir_writable", lambda: _store_dir_writable(checks, store))
        checks.run("disk_free", lambda: _disk_free(checks, store))

    # ------------------------------------------------------- dependencies
    checks.run("dependencies", lambda: _dependencies(checks, lookup))

    # ------------------------------------------------------- permissions
    if not is_memory:
        checks.run("store_file_permissions", lambda: _store_file_permissions(checks, store))
    checks.run("process_umask", lambda: _process_umask(checks, umask))

    # --------------------------------------------------------- integrity
    checks.run("quick_check", lambda: _quick_check(checks, store))
    if full_integrity:
        checks.run("integrity_check", lambda: _integrity_check(checks, store))
    checks.run("foreign_keys", lambda: _foreign_keys(checks, store))
    checks.run("hash_chain", lambda: _hash_chain(checks, store))
    checks.run("migrations", lambda: _migrations(checks, store))

    # -------------------------------------------------------- performance
    checks.run("read_latency", lambda: _read_latency(checks, store, probe_iterations, slow_probe_ms))
    checks.run("fragmentation", lambda: _fragmentation(checks, store))
    checks.checked.append("wal_size")  # the WAL probe rides the fragmentation check
    checks.run("stale_journals", lambda: _stale_journals(checks, store, stale_after_seconds))

    return DoctorReport(findings=tuple(checks.findings), checked=tuple(sorted(set(checks.checked))))
