"""CLI tests for the knowledge debt queue (I4, W14 SUB-128).

Pins ``know-your-context debt list|report|propose`` end-to-end through
``cli.main``: exit codes, canonical-JSON stdout, typed error envelopes on
stderr, the budget cap, the kill switch announcement, the hash-chained
burn-down ledger, the proposal-gated action path (a high-impact retire
proposal is recorded but the node stays untouched until an operator
approves), and the OPS-only rule (``debt`` never joins the frozen
knowledge/OPS operations).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from know_your_context.cli import build_parser, main
from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain, ScoreVector
from know_your_context.http_api import KNOWLEDGE_OPERATIONS, OPS_OPERATIONS
from know_your_context.improvement import get_proposal
from know_your_context.ops.debt import (
    DEFAULT_LEDGER_FILENAME,
    DebtLedgerError,
    ledger_path_for_store,
    load_debt_ledger,
)
from know_your_context.store import EventStore

NOW = datetime(2026, 2, 1, tzinfo=UTC)
OLD = datetime(2025, 12, 1, tzinfo=UTC)
OLD_ISO = "2025-12-01T00:00:00+00:00"


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict | None, str]:
    code = main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else None
    return code, payload, captured.err


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "debt.sqlite3")


def _fresh() -> datetime:
    return datetime.now(UTC)


def _upsert(
    store: EventStore,
    node_id: str,
    *,
    confidence: str = "0.600000000000",
    last_used_at: datetime | None = None,
    domain: PrimaryDomain = PrimaryDomain.P1,
) -> None:
    fresh = _fresh()
    node = Node(
        node_id=node_id,
        kind=NodeKind.ROOT,
        domain=domain,
        summary=node_id,
        provenance=("context://corpus/lesson.md",),
        source_version_id="sv-debt",
        scores=ScoreVector.model_validate(
            {"p": "0.5", "m": "0.5", "e": "0.5", "uncertainty": "0.5", "confidence": confidence}
        ),
        created_at=OLD,
        updated_at=fresh,
        last_used_at=last_used_at if last_used_at is not None else fresh,
    )
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id=node.node_id,
            domain=node.domain,
            node=node,
            source_version_id=node.source_version_id,
            occurred_at=NOW,
        ),
        idempotency_key=f"debt:upsert:{node.node_id}:{store.node_version(node.node_id)}",
    )


def _seed_debt(store: EventStore) -> None:
    """One low-score node, one stale node, one clean node."""
    _upsert(store, "n-low", confidence="0.100000000000")
    _upsert(store, "n-stale", last_used_at=OLD)
    _upsert(store, "n-ok")


# ------------------------------------------------------------------ OPS-only rule


def test_debt_is_not_a_knowledge_or_ops_operation() -> None:
    frozen = set(KNOWLEDGE_OPERATIONS) | set(OPS_OPERATIONS)
    assert "debt" not in frozen


def test_debt_subcommands_parse() -> None:
    parser = build_parser()
    for argv in (
        ["debt", "list"],
        ["debt", "list", "--budget", "5"],
        ["debt", "report"],
        ["debt", "report", "--format", "markdown", "--record"],
        ["debt", "propose", "n-low"],
    ):
        parser.parse_args(argv)


# ------------------------------------------------------------------------- list


class TestList:
    def test_empty_store_is_an_empty_typed_queue(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        store.close()
        code, payload, _ = _invoke(["--store", str(store.db_path), "debt", "list"], capsys)
        assert payload is not None
        assert code == 0
        assert payload["disabled"] is False
        assert payload["items"] == []
        assert payload["candidate_count"] == 0

    def test_debt_nodes_are_listed(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        code, payload, _ = _invoke(["--store", str(store.db_path), "debt", "list"], capsys)
        assert payload is not None
        assert code == 0
        node_ids = {item["node_id"] for item in payload["items"]}
        assert {"n-low", "n-stale"} <= node_ids
        assert "n-ok" not in node_ids

    def test_output_has_the_contract_keys(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        _, payload, _ = _invoke(["--store", str(store.db_path), "debt", "list"], capsys)
        assert payload is not None
        for key in (
            "schema_version",
            "disabled",
            "generated_at",
            "config",
            "signal_counts",
            "candidate_count",
            "budget",
            "truncated",
            "overflow_count",
            "items",
        ):
            assert key in payload, key

    def test_budget_caps_the_listing(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        _, payload, _ = _invoke(["--store", str(store.db_path), "debt", "list", "--budget", "1"], capsys)
        assert payload is not None
        assert len(payload["items"]) == 1
        assert payload["truncated"] is True
        assert payload["overflow_count"] == payload["candidate_count"] - 1

    def test_budget_zero_is_a_usage_error(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        store.close()
        code, _, err = _invoke(["--store", str(store.db_path), "debt", "list", "--budget", "0"], capsys)
        assert code == 2
        assert "CLI_USAGE" in err

    def test_budget_above_max_is_a_usage_error(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        store.close()
        code, _, err = _invoke(["--store", str(store.db_path), "debt", "list", "--budget", "101"], capsys)
        assert code == 2

    def test_kill_switch_disables_and_announces(self, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        monkeypatch.setenv("KYS_DEBT_QUEUE", "0")
        code, payload, err = _invoke(["--store", str(store.db_path), "debt", "list"], capsys)
        assert code == 0
        assert payload is not None
        assert payload["disabled"] is True
        assert payload["items"] == []
        assert "KYS_DEBT_QUEUE" in err  # the kill switch announces its action

    def test_old_unresolved_ref_is_listed(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _upsert(store, "n-ok")
        store.upsert_file(
            path="a.py",
            sha256="a" * 64,
            size=10,
            mtime=1,
            language="python",
            generated_hint=False,
            extraction_version=1,
        )
        store.upsert_symbol(node_id="n-ok", name="alpha_fn", kind="function", path="a.py", line=1)
        store.insert_unresolved_ref(source_node_id="n-ok", target_name="gamma_fn", kind="calls", first_seen=OLD_ISO)
        store.close()
        code, payload, _ = _invoke(["--store", str(store.db_path), "debt", "list"], capsys)
        assert payload is not None
        assert code == 0
        item = next(i for i in payload["items"] if i["node_id"] == "n-ok")
        assert "unresolved_ref_old" in item["signals"]

    def test_list_is_read_only(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        event_count = store.event_count()
        store.close()
        raw_before = Path(tmp_path / "debt.sqlite3").read_bytes()
        code, _, _ = _invoke(["--store", str(tmp_path / "debt.sqlite3"), "debt", "list"], capsys)
        assert code == 0
        assert Path(tmp_path / "debt.sqlite3").read_bytes() == raw_before
        store = EventStore(tmp_path / "debt.sqlite3")
        assert store.event_count() == event_count
        store.close()


# ------------------------------------------------------------------------ report


class TestReport:
    def test_report_json_carries_burndown(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        code, payload, _ = _invoke(["--store", str(store.db_path), "debt", "report", "--format", "json"], capsys)
        assert payload is not None
        assert code == 0
        assert "queue" in payload
        assert "burndown" in payload
        assert payload["burndown"]["trend"] == "insufficient_data"

    def test_report_markdown_is_the_default(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        code = main(["--store", str(store.db_path), "debt", "report"])
        captured = capsys.readouterr()
        assert code == 0
        assert captured.out.startswith("# Knowledge Debt Report")

    def test_record_appends_to_the_ledger(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        code, _, _ = _invoke(["--store", str(store.db_path), "debt", "report", "--record", "--format", "json"], capsys)
        assert code == 0
        ledger = ledger_path_for_store(tmp_path / "debt.sqlite3")
        assert ledger.name == DEFAULT_LEDGER_FILENAME
        snapshots = load_debt_ledger(ledger)
        assert len(snapshots) == 1
        assert snapshots[0]["cycle"] == 1
        assert snapshots[0]["candidate_count"] >= 2

    def test_two_records_produce_a_burndown_delta(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        argv = ["--store", str(store.db_path), "debt", "report", "--record", "--format", "json"]
        _invoke(argv, capsys)
        code, payload, _ = _invoke(argv, capsys)
        assert payload is not None
        assert code == 0
        assert payload["burndown"]["cycles"] == 2
        assert len(payload["burndown"]["deltas"]) == 1
        # Nothing changed between the two cycles: flat, honest zero.
        assert payload["burndown"]["trend"] == "flat"
        assert payload["burndown"]["net_count_delta"] == 0

    def test_burndown_improves_after_a_node_leaves(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        argv = ["--store", str(store.db_path), "debt", "report", "--record", "--format", "json"]
        _invoke(argv, capsys)
        # Operator fixes the stale node: it is used again (a fresh revision
        # with a recent last_used_at makes the stale signal go silent).
        store = EventStore(tmp_path / "debt.sqlite3")
        _upsert(store, "n-stale")
        store.close()
        code, payload, _ = _invoke(argv, capsys)
        assert payload is not None
        assert code == 0
        assert payload["burndown"]["trend"] == "improving"
        assert payload["burndown"]["burned_total"] >= 1

    def test_tampered_ledger_fails_closed(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        argv = ["--store", str(store.db_path), "debt", "report", "--record", "--format", "json"]
        _invoke(argv, capsys)
        ledger = ledger_path_for_store(tmp_path / "debt.sqlite3")
        lines = ledger.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[0])
        tampered["candidate_count"] = 999
        ledger.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        with pytest.raises(DebtLedgerError):
            load_debt_ledger(ledger)
        code, _, err = _invoke(argv, capsys)
        assert code == 1
        assert "DEBT_LEDGER" in err

    def test_record_without_debt_still_records_a_zero_snapshot(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        store.close()
        code, _, _ = _invoke(["--store", str(store.db_path), "debt", "report", "--record", "--format", "json"], capsys)
        assert code == 0
        snapshots = load_debt_ledger(ledger_path_for_store(tmp_path / "debt.sqlite3"))
        assert snapshots[0]["candidate_count"] == 0

    def test_kill_switch_blocks_recording(self, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        monkeypatch.setenv("KYS_DEBT_QUEUE", "off")
        code, payload, _ = _invoke(
            ["--store", str(store.db_path), "debt", "report", "--record", "--format", "json"],
            capsys,
        )
        assert payload is not None
        assert code == 0
        assert payload["queue"]["disabled"] is True
        assert not ledger_path_for_store(tmp_path / "debt.sqlite3").exists()


# ----------------------------------------------------------------------- propose


class TestPropose:
    def test_improve_proposal_is_recorded_via_the_gated_path(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        code, payload, _ = _invoke(["--store", str(store.db_path), "debt", "propose", "n-low"], capsys)
        assert payload is not None
        assert code == 0
        proposal_id = payload["proposal"]["proposal_id"]
        store = EventStore(tmp_path / "debt.sqlite3")
        record = get_proposal(store, proposal_id)
        assert record is not None
        assert record.node_id == "n-low"
        assert record.impact == "low"
        assert record.rationale
        store.close()

    def test_retire_gate(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _upsert(
            store,
            "n-dead",
            confidence="0.100000000000",
            last_used_at=OLD,
        )
        store.close()
        code, payload, _ = _invoke(["--store", str(store.db_path), "debt", "propose", "n-dead"], capsys)
        assert payload is not None
        assert code == 0
        assert payload["proposal"]["impact"] == "high"
        store = EventStore(tmp_path / "debt.sqlite3")
        node = store.current_node("n-dead")
        assert node is not None
        assert node.lifecycle_status.value == "active"  # proposal-only: nothing applied
        store.close()

    def test_unknown_node_is_a_typed_error(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        store.close()
        code, _, err = _invoke(["--store", str(store.db_path), "debt", "propose", "ghost"], capsys)
        assert code == 1
        assert "NO_DEBT_ITEM" in err

    def test_clean_node_has_no_debt_item(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        code, _, err = _invoke(["--store", str(store.db_path), "debt", "propose", "n-ok"], capsys)
        assert code == 1
        assert "NO_DEBT_ITEM" in err

    def test_propose_refuses_when_killed(self, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        monkeypatch.setenv("KYS_DEBT_QUEUE", "0")
        code, _, err = _invoke(["--store", str(store.db_path), "debt", "propose", "n-low"], capsys)
        assert code == 1
        assert "DEBT_DISABLED" in err

    def test_propose_matches_existing_queue_item(self, tmp_path: Path, capsys) -> None:
        store = _store(tmp_path)
        _seed_debt(store)
        store.close()
        _, listed, _ = _invoke(["--store", str(store.db_path), "debt", "list"], capsys)
        assert listed is not None
        item = next(i for i in listed["items"] if i["node_id"] == "n-stale")
        assert item["action"] == "improve"
        code, payload, _ = _invoke(["--store", str(store.db_path), "debt", "propose", "n-stale"], capsys)
        assert payload is not None
        assert code == 0
        assert payload["debt_item"]["action"] == item["action"]


def test_no_repo_env_leakage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests above never rely on ambient KYS_DEBT_QUEUE state."""
    monkeypatch.delenv("KYS_DEBT_QUEUE", raising=False)
    assert os.environ.get("KYS_DEBT_QUEUE") is None
