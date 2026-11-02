"""Tests for ``know_your_context.ops.doctor`` (W12 tools surface).

Pins the deep-diagnostics contract: environment, dependency, permission,
integrity and performance probes, every finding tiered and carrying
structured repair suggestions, every check fail-closed (a crashing check is
a CHECK_CRASHED error finding, never an exception), and the exit code
derived from the worst finding.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from know_your_context.ops.doctor import (
    CATEGORIES,
    SEVERITY_ORDER,
    DoctorFinding,
    DoctorReport,
    exit_code,
    render_markdown,
    run_doctor,
)
from know_your_context.store import EventStore

# ------------------------------------------------------------------ helpers


def _store(tmp_path: Path, name: str = "doctor.sqlite3") -> EventStore:
    return EventStore(tmp_path / name)


def _findings(report: DoctorReport, code: str) -> list[DoctorFinding]:
    return [finding for finding in report.findings if finding.code == code]


def _versions(pinned: dict[str, str]) -> Any:
    def lookup(dist: str) -> str:
        if dist not in pinned:
            raise KeyError(dist)
        return pinned[dist]

    return lookup


# ------------------------------------------------------------------ model


def test_finding_is_frozen() -> None:
    finding = DoctorFinding(code="X", category="environment", severity="info", message="m")
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass refuses assignment
        finding.code = "Y"  # type: ignore[misc]


def test_finding_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        DoctorFinding(code="X", category="environment", severity="fatal", message="m")


def test_finding_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="category"):
        DoctorFinding(code="X", category="cosmic", severity="info", message="m")


def test_categories_cover_report_sections() -> None:
    assert set(CATEGORIES) == {"environment", "dependencies", "permissions", "integrity", "performance"}


def test_report_ok_only_without_errors() -> None:
    report = DoctorReport(
        findings=(DoctorFinding(code="A", category="environment", severity="warning", message="m"),),
        checked=("x",),
    )
    assert report.ok is True
    assert report.warning_count == 1
    assert report.error_count == 0
    assert report.worst_severity == "warning"


def test_report_empty_is_info() -> None:
    report = DoctorReport()
    assert report.ok is True
    assert report.worst_severity == "info"
    assert exit_code(report) == 0


def test_report_error_drives_exit_code() -> None:
    report = DoctorReport(
        findings=(DoctorFinding(code="A", category="integrity", severity="error", message="m"),),
    )
    assert report.ok is False
    assert exit_code(report) == 1


def test_report_category_counts() -> None:
    report = DoctorReport(
        findings=(
            DoctorFinding(code="A", category="environment", severity="info", message="m"),
            DoctorFinding(code="B", category="environment", severity="warning", message="m"),
            DoctorFinding(code="C", category="integrity", severity="error", message="m"),
        ),
    )
    counts = report.category_counts
    assert counts["environment"] == 2
    assert counts["integrity"] == 1
    assert counts["performance"] == 0


# ------------------------------------------------------------------ healthy store


def test_healthy_store_has_no_error_findings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, projection_root=None)
        assert report.error_count == 0
        assert exit_code(report) == 0
        assert report.ok is True
    finally:
        store.close()


def test_healthy_report_lists_every_category_checked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store)
        checked = set(report.checked)
        for probe in (
            "python_version",
            "platform",
            "sqlite_version",
            "store_dir_writable",
            "disk_free",
            "dependencies",
            "store_file_permissions",
            "process_umask",
            "quick_check",
            "hash_chain",
            "migrations",
            "foreign_keys",
            "read_latency",
            "fragmentation",
            "wal_size",
            "stale_journals",
        ):
            assert probe in checked, probe
    finally:
        store.close()


def test_severity_order_is_canonical() -> None:
    assert SEVERITY_ORDER == ("info", "warning", "error")


# ------------------------------------------------------------------ environment


def test_python_version_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, python_version=(3, 12, 0))
        findings = _findings(report, "PYTHON_VERSION_UNSUPPORTED")
        assert findings and findings[0].severity == "warning"
        assert any("3.14" in suggestion for suggestion in findings[0].suggestions)
    finally:
        store.close()


def test_python_version_ok_is_silent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, python_version=(3, 14, 0))
        assert _findings(report, "PYTHON_VERSION_UNSUPPORTED") == []
    finally:
        store.close()


def test_python_version_above_window_warns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, python_version=(3, 15, 0))
        assert _findings(report, "PYTHON_VERSION_UNSUPPORTED")
    finally:
        store.close()


def test_sqlite_version_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, sqlite_version="3.20.0")
        findings = _findings(report, "SQLITE_VERSION_TOO_OLD")
        assert findings and findings[0].severity == "error"
        assert findings[0].suggestions
    finally:
        store.close()


def test_sqlite_version_modern_is_silent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, sqlite_version="3.45.1")
        assert _findings(report, "SQLITE_VERSION_TOO_OLD") == []
    finally:
        store.close()


def test_sqlite_version_unparsable_is_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, sqlite_version="banana")
        assert _findings(report, "SQLITE_VERSION_TOO_OLD")
    finally:
        store.close()


def test_store_dir_writable_probe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store)
        assert _findings(report, "STORE_DIR_NOT_WRITABLE") == []
        # the probe file is cleaned up
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".doctor-probe")]
        assert leftovers == []
    finally:
        store.close()


def test_store_dir_probe_skipped_for_memory_store() -> None:
    store = EventStore(":memory:")
    try:
        report = run_doctor(store)
        assert _findings(report, "STORE_DIR_NOT_WRITABLE") == []
        assert "store_dir_writable" not in report.checked
    finally:
        store.close()


def test_disk_free_reports_info_detail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store)
        # a healthy tmp fs yields no DISK_FREE_LOW finding
        assert _findings(report, "DISK_FREE_LOW") == []
    finally:
        store.close()


# ------------------------------------------------------------------ dependencies


def test_dependencies_present_are_silent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store)  # real environment: runtime deps are installed
        assert _findings(report, "DEPENDENCY_MISSING") == []
    finally:
        store.close()


def test_dependency_missing_is_error_with_suggestion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, version_lookup=_versions({}))  # nothing installed
        findings = _findings(report, "DEPENDENCY_MISSING")
        assert findings, "every runtime dependency should be reported missing"
        assert all(f.severity == "error" for f in findings)
        assert any("uv sync" in s for f in findings for s in f.suggestions)
    finally:
        store.close()


def test_dependency_versions_recorded_in_info(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, version_lookup=_versions({"networkx": "3.7"}))
        info = [f for f in report.findings if f.code == "DEPENDENCY_VERSION"]
        assert any(f.details.get("installed") == "3.7" for f in info)
    finally:
        store.close()


# ------------------------------------------------------------------ permissions


def test_permissions_open_store_file_warns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        os.chmod(store.db_path, 0o644)
        report = run_doctor(store)
        findings = _findings(report, "STORE_FILE_PERMISSIONS_OPEN")
        assert findings and findings[0].severity == "warning"
        assert any("chmod" in s for s in findings[0].suggestions)
    finally:
        store.close()


def test_process_umask_world_writable_warns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, umask=0o000)
        findings = _findings(report, "UMASK_TOO_OPEN")
        assert findings and findings[0].severity == "warning"
    finally:
        store.close()


def test_process_umask_restrictive_is_silent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, umask=0o077)
        assert _findings(report, "UMASK_TOO_OPEN") == []
        assert _findings(report, "UMASK_TOO_OPEN") == _findings(report, "UMASK_WORLD_WRITABLE")
    finally:
        store.close()


# ------------------------------------------------------------------ integrity


def test_broken_chain_is_error_with_restore_suggestion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:

        class _BrokenChain:
            def __init__(self, inner: EventStore) -> None:
                self._inner = inner
                self.db_path = inner.db_path

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

            def verify_chain(self) -> Any:
                from know_your_context.store import ChainReport

                return ChainReport(ok=False, checked=1, first_bad_seq=1)

        report = run_doctor(cast("EventStore", _BrokenChain(store)))
        findings = _findings(report, "CHAIN_BROKEN")
        assert findings and findings[0].severity == "error"
        assert any("restore" in s.lower() for s in findings[0].suggestions)
        assert exit_code(report) == 1
    finally:
        store.close()


class _RawConnStore:
    """A store-shaped wrapper over a raw SQLite connection (corruption probe)."""

    def __init__(self, path: Path) -> None:
        self.db_path = str(path)
        self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def read(self) -> Any:
        from contextlib import contextmanager

        @contextmanager
        def _ctx() -> Any:
            yield self._conn

        return _ctx()


def test_quick_check_failure_is_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.close()
    with open(store.db_path, "r+b") as handle:
        handle.seek(8192)  # page 3: past the schema page, inside table data
        handle.write(b"\xff" * 4096)  # corrupt the page
    damaged = _RawConnStore(Path(store.db_path))
    report = run_doctor(cast("EventStore", damaged))
    # corruption must surface as an error finding from some integrity probe
    errors = [f for f in report.findings if f.severity == "error"]
    assert errors, "corrupted pages must yield at least one error finding"
    assert _findings(report, "QUICK_CHECK_FAILED") or _findings(report, "CHECK_CRASHED")


def test_foreign_key_check_runs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store)
        assert "foreign_keys" in report.checked
        assert _findings(report, "FOREIGN_KEY_VIOLATIONS") == []
    finally:
        store.close()


def test_full_integrity_opt_in(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quick = run_doctor(store)
        full = run_doctor(store, full_integrity=True)
        assert "integrity_check" not in quick.checked
        assert "integrity_check" in full.checked
    finally:
        store.close()


def test_migrations_drift_is_error_with_suggestion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with store.read() as conn:
            conn.execute("DELETE FROM schema_migration WHERE version = (SELECT MAX(version) FROM schema_migration)")
            conn.commit()
        report = run_doctor(store)
        findings = _findings(report, "MIGRATIONS_DRIFT")
        assert findings and findings[0].severity == "error"
        assert any("migrate" in s for s in findings[0].suggestions)
    finally:
        store.close()


# ------------------------------------------------------------------ performance


def test_read_latency_probe_records_stats(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, probe_iterations=8)
        latency = [f for f in report.findings if f.code == "READ_LATENCY"]
        assert latency and latency[0].severity == "info"
        details = latency[0].details
        assert details["iterations"] == 8
        assert details["p95_ms"] >= details["min_ms"] >= 0
    finally:
        store.close()


def test_slow_probe_threshold_warns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, probe_iterations=4, slow_probe_ms=-1)  # everything is slow
        findings = _findings(report, "READ_LATENCY_SLOW")
        assert findings and findings[0].severity == "warning"
        assert any("vacuum" in s.lower() or "lifecycle" in s.lower() for s in findings[0].suggestions)
    finally:
        store.close()


def test_fragmentation_warning_suggests_vacuum(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.execute_raw("CREATE TABLE scratch (payload TEXT)")
        store.execute_raw("INSERT INTO scratch (payload) VALUES (hex(randomblob(400000)))")
        store.execute_raw("DROP TABLE scratch")
        report = run_doctor(store)
        findings = _findings(report, "FRAGMENTATION_HIGH")
        assert findings and findings[0].severity == "warning"
        assert any("vacuum" in s for s in findings[0].suggestions)
    finally:
        store.close()


def test_stale_journals_warn(tmp_path: Path) -> None:
    store = _store(tmp_path)
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
        report = run_doctor(store, stale_after_seconds=0.0)
        findings = _findings(report, "STALE_JOURNALS")
        assert findings and findings[0].severity == "warning"
        assert findings[0].suggestions
    finally:
        store.close()


# ------------------------------------------------------------------ fail-closed containment


class _ExplodingStore:
    db_path = ":memory:"

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"simulated failure in {name}")


def test_exploding_store_is_contained_as_check_crashed() -> None:
    report = run_doctor(_ExplodingStore())  # type: ignore[arg-type]
    crashed = _findings(report, "CHECK_CRASHED")
    assert crashed, "every store-touching check must degrade to a finding"
    assert report.ok is False
    assert exit_code(report) == 1


def test_doctor_never_raises_on_closed_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.close()
    report = run_doctor(store)  # reads on a closed handle must be contained
    assert report.error_count >= 1
    assert _findings(report, "CHECK_CRASHED")


# ------------------------------------------------------------------ rendering


def test_render_markdown_groups_by_category(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store, version_lookup=_versions({}))
        markdown = render_markdown(report)
        assert markdown.startswith("# Doctor Report")
        for category in CATEGORIES:
            assert f"## {category.capitalize()}" in markdown
        assert "DEPENDENCY_MISSING" in markdown
    finally:
        store.close()


def test_render_markdown_shows_suggestions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        os.chmod(store.db_path, 0o644)
        markdown = render_markdown(run_doctor(store))
        assert "Suggestions" in markdown or "suggestion" in markdown.lower()
        assert "chmod" in markdown
    finally:
        store.close()


def test_render_markdown_is_deterministic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        os.chmod(store.db_path, 0o644)
        report = run_doctor(store, probe_iterations=0)
        assert render_markdown(report) == render_markdown(report)
    finally:
        store.close()


def test_render_empty_report() -> None:
    markdown = render_markdown(DoctorReport())
    assert "No findings" in markdown


def test_report_checked_sorted_unique(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        report = run_doctor(store)
        assert list(report.checked) == sorted(set(report.checked))
    finally:
        store.close()


def test_doctor_report_frozen() -> None:
    report = DoctorReport()
    with pytest.raises(Exception):  # noqa: B017
        report.findings = ()  # type: ignore[misc]
