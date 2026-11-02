"""CLI wiring tests for the W12 tools surface (SUB-122).

Pins the ``lifecycle``, ``doctor``, ``migrate`` and ``watchtower``
subcommands end-to-end through ``cli.main``: exit codes, canonical-JSON
stdout payloads, typed error envelopes on stderr, and the OPS-only rule
(none of the new commands joins the frozen knowledge/OPS operations).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.cli import build_parser, main
from know_your_context.http_api import KNOWLEDGE_OPERATIONS, OPS_OPERATIONS
from know_your_context.ops.lifecycle import DEFAULT_HISTORY_FILENAME, load_health_history
from know_your_context.ops.migrate import BUNDLE_MANIFEST_FILENAME, BUNDLE_STORE_FILENAME
from know_your_context.store import EventStore


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _out_json(raw: str) -> dict:
    return json.loads(raw)


def _store(tmp_path: Path, name: str = "cli.sqlite3") -> EventStore:
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


# ------------------------------------------------------------------ OPS-only rule


def test_w12_commands_are_not_knowledge_or_ops_operations() -> None:
    """The frozen surfaces must not grow: the W12 verbs are CLI-only."""
    frozen = set(KNOWLEDGE_OPERATIONS) | set(OPS_OPERATIONS)
    for verb in ("lifecycle", "doctor", "migrate", "watchtower"):
        assert verb not in frozen


def test_w12_commands_parse() -> None:
    parser = build_parser()
    for argv in (
        ["lifecycle", "record"],
        ["lifecycle", "trend"],
        ["lifecycle", "prune", "--keep", "3"],
        ["lifecycle", "vacuum"],
        ["lifecycle", "fragmentation"],
        ["doctor"],
        ["doctor", "--format", "markdown", "--full-integrity"],
        ["migrate", "plan"],
        ["migrate", "apply"],
        ["migrate", "export", "--dest", "/tmp/x"],
        ["migrate", "import", "--bundle", "/tmp/x"],
        ["migrate", "verify", "--bundle", "/tmp/x"],
        ["watchtower"],
        ["watchtower", "a.sqlite3", "--target", "prod=/tmp/p.sqlite3"],
    ):
        parser.parse_args(argv)  # raises SystemExit on a wiring mistake


# ------------------------------------------------------------------ lifecycle


def test_cli_lifecycle_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "lifecycle", "record"], capsys)
    assert code == 0
    payload = _out_json(out)
    assert payload["schema_version"] == 1
    assert payload["prev_hash"] == "0" * 64
    assert (tmp_path / DEFAULT_HISTORY_FILENAME).is_file()


def test_cli_lifecycle_record_chains(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    base = ["--store", str(tmp_path / "cli.sqlite3")]
    _invoke(base + ["lifecycle", "record"], capsys)
    code, out, _ = _invoke(base + ["lifecycle", "record"], capsys)
    assert code == 0
    history = load_health_history(tmp_path / DEFAULT_HISTORY_FILENAME)
    assert len(history) == 2
    assert _out_json(out)["prev_hash"] == history[0].hash


def test_cli_lifecycle_trend(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    base = ["--store", str(tmp_path / "cli.sqlite3")]
    _invoke(base + ["lifecycle", "record"], capsys)
    _invoke(base + ["lifecycle", "record"], capsys)
    code, out, _ = _invoke(base + ["lifecycle", "trend"], capsys)
    assert code == 0
    payload = _out_json(out)
    assert payload["direction"] == "stable"
    assert payload["snapshots"] == 2


def test_cli_lifecycle_trend_empty_history(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "lifecycle", "trend"], capsys)
    assert code == 0
    assert _out_json(out)["direction"] == "insufficient-data"


def test_cli_lifecycle_prune_dry_run_then_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    base = ["--store", str(tmp_path / "cli.sqlite3")]
    for _ in range(3):
        _invoke(base + ["lifecycle", "record"], capsys)
    ledger = tmp_path / DEFAULT_HISTORY_FILENAME
    before = ledger.read_bytes()
    code, out, _ = _invoke(base + ["lifecycle", "prune", "--keep", "1", "--dry-run"], capsys)
    assert code == 0
    assert _out_json(out)["removed"] == 2
    assert ledger.read_bytes() == before  # dry run changed nothing
    code, out, _ = _invoke(base + ["lifecycle", "prune", "--keep", "1"], capsys)
    assert code == 0
    assert _out_json(out)["kept"] == 1
    assert len(load_health_history(ledger)) == 1


def test_cli_lifecycle_prune_rejects_negative_keep(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _invoke(["--store", str(tmp_path / "x.sqlite3"), "lifecycle", "prune", "--keep", "-1"], capsys)
    assert code == 1
    assert "error" in json.loads(err)


def test_cli_lifecycle_fragmentation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "lifecycle", "fragmentation"], capsys)
    assert code == 0
    payload = _out_json(out)
    assert payload["page_count"] > 0
    assert "vacuum_recommended" in payload


def test_cli_lifecycle_vacuum(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    _event(store, 1)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "lifecycle", "vacuum"], capsys)
    assert code == 0
    payload = _out_json(out)
    assert payload["verified"] is True
    assert payload["freelist_after"] == 0
    reopened = EventStore(tmp_path / "cli.sqlite3")
    try:
        assert reopened.event_count() == 1
        assert reopened.verify_chain().ok
    finally:
        reopened.close()


# ------------------------------------------------------------------ doctor


def test_cli_doctor_healthy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "doctor"], capsys)
    assert code == 0
    payload = _out_json(out)
    assert payload["ok"] is True
    assert "category_counts" in payload
    assert payload["checked"]


def test_cli_doctor_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "doctor", "--format", "markdown"], capsys)
    assert code == 0
    assert out.startswith("# Doctor Report")


def test_cli_doctor_full_integrity_runs_extra_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "doctor", "--full-integrity"], capsys)
    assert code == 0
    assert "integrity_check" in _out_json(out)["checked"]


def test_cli_doctor_broken_store_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    path = tmp_path / "cli.sqlite3"
    data = bytearray(path.read_bytes())
    data[8192:12288] = b"\xff" * 4096
    path.write_bytes(bytes(data))
    code, _, err = _invoke(["--store", str(path), "doctor"], capsys)
    assert code == 1  # either the typed envelope (unopenable) or an error report


# ------------------------------------------------------------------ migrate


def test_cli_migrate_plan_current(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "migrate", "plan"], capsys)
    assert code == 0
    payload = _out_json(out)
    assert payload["up_to_date"] is True
    assert payload["steps"] == []


def test_cli_migrate_plan_absent_store_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _invoke(["--store", str(tmp_path / "ghost.sqlite3"), "migrate", "plan"], capsys)
    assert code == 1
    assert "error" in json.loads(err)


def test_cli_migrate_apply_current(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    code, out, _ = _invoke(["--store", str(tmp_path / "cli.sqlite3"), "migrate", "apply"], capsys)
    assert code == 0
    payload = _out_json(out)
    assert payload["already_current"] is True
    assert payload["chain_ok"] is True


def test_cli_migrate_export_verify_import_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    for seq in range(2):
        _event(store, seq + 1)
    store.close()
    base = ["--store", str(tmp_path / "cli.sqlite3")]
    code, out, _ = _invoke(base + ["migrate", "export", "--dest", str(tmp_path / "bundle")], capsys)
    assert code == 0
    manifest = _out_json(out)
    assert manifest["format_version"] == 1
    assert (tmp_path / "bundle" / BUNDLE_STORE_FILENAME).is_file()
    assert (tmp_path / "bundle" / BUNDLE_MANIFEST_FILENAME).is_file()

    code, out, _ = _invoke(base + ["migrate", "verify", "--bundle", str(tmp_path / "bundle")], capsys)
    assert code == 0
    assert _out_json(out)["ok"] is True

    dest = tmp_path / "imported.sqlite3"
    code, out, _ = _invoke(
        base + ["migrate", "import", "--bundle", str(tmp_path / "bundle"), "--dest", str(dest)], capsys
    )
    assert code == 0
    assert _out_json(out)["event_count"] == 2
    imported = EventStore(dest)
    try:
        assert imported.event_count() == 2
        assert imported.verify_chain().ok
    finally:
        imported.close()


def test_cli_migrate_verify_tampered_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    base = ["--store", str(tmp_path / "cli.sqlite3")]
    _invoke(base + ["migrate", "export", "--dest", str(tmp_path / "bundle")], capsys)
    target = tmp_path / "bundle" / BUNDLE_STORE_FILENAME
    with open(target, "r+b") as handle:
        handle.seek(0, 2)
        handle.write(b"tampered")
    code, out, _ = _invoke(base + ["migrate", "verify", "--bundle", str(tmp_path / "bundle")], capsys)
    assert code == 1
    assert _out_json(out)["ok"] is False


def test_cli_migrate_import_refuses_existing_dest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    base = ["--store", str(tmp_path / "cli.sqlite3")]
    _invoke(base + ["migrate", "export", "--dest", str(tmp_path / "bundle")], capsys)
    dest = tmp_path / "occupied.sqlite3"
    EventStore(dest).close()
    code, _, err = _invoke(
        base + ["migrate", "import", "--bundle", str(tmp_path / "bundle"), "--dest", str(dest)], capsys
    )
    assert code == 1
    assert "error" in json.loads(err)


# ------------------------------------------------------------------ watchtower


def test_cli_watchtower_fleet_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("a", "b"):
        _store(tmp_path, f"{name}.sqlite3").close()
    code, out, _ = _invoke(["watchtower", str(tmp_path / "a.sqlite3"), str(tmp_path / "b.sqlite3")], capsys)
    assert code == 0
    payload = _out_json(out)
    assert [store["name"] for store in payload["stores"]] == ["a", "b"]
    assert all(store["reachable"] for store in payload["stores"])


def test_cli_watchtower_missing_store_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _invoke(["watchtower", str(tmp_path / "ghost.sqlite3")], capsys)
    assert code == 1
    payload = _out_json(out)
    assert payload["stores"][0]["reachable"] is False
    assert not (tmp_path / "ghost.sqlite3").exists()  # probing never creates


def test_cli_watchtower_named_targets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _store(tmp_path, "prod.sqlite3").close()
    code, out, _ = _invoke(["watchtower", "--target", f"production={tmp_path / 'prod.sqlite3'}"], capsys)
    assert code == 0
    assert _out_json(out)["stores"][0]["name"] == "production"


def test_cli_watchtower_rejects_malformed_target(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _invoke(["watchtower", "--target", "no-equals-sign"], capsys)
    assert code == 2
    assert json.loads(err)["error"]["code"] == "CLI_USAGE"


def test_cli_watchtower_duplicate_names_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    EventStore(dir_a / "same.sqlite3").close()
    EventStore(dir_b / "same.sqlite3").close()
    code, _, err = _invoke(["watchtower", str(dir_a / "same.sqlite3"), str(dir_b / "same.sqlite3")], capsys)
    assert code == 1
    assert "error" in json.loads(err)


def test_cli_watchtower_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _store(tmp_path, "a.sqlite3").close()
    code, out, _ = _invoke(["watchtower", str(tmp_path / "a.sqlite3"), "--format", "markdown"], capsys)
    assert code == 0
    assert out.startswith("# Watchtower Report")
    assert "| a |" in out


def test_cli_watchtower_empty_fleet_ok(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _invoke(["watchtower"], capsys)
    assert code == 0
    assert _out_json(out)["stores"] == []


# ------------------------------------------------------------------ payloads stay canonical


def test_cli_outputs_are_canonical_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path)
    store.close()
    base = ["--store", str(tmp_path / "cli.sqlite3")]
    for argv in (
        base + ["lifecycle", "record"],
        base + ["lifecycle", "fragmentation"],
        base + ["doctor"],
        base + ["migrate", "plan"],
        ["watchtower", str(tmp_path / "cli.sqlite3")],
    ):
        code, out, _ = _invoke(list(argv), capsys)
        assert code == 0
        assert out.strip() == json.dumps(json.loads(out), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
