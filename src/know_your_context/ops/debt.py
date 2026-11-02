"""Operational surface for the knowledge debt queue (I4, W14 SUB-128).

Three pieces, all fail-closed:

* **Read-only store adapter** — :func:`load_debt_inputs` lifts the latest
  non-superseded node revisions, the ``node_relation``/``parent_of`` links
  and the unresolved-reference rows out of the store with strictly read-only
  SQL (the pattern documented in ``docs/recovery/atm-wave-c2.md``), so
  :mod:`know_your_context.analytics.debt` stays pure and store-agnostic.

* **Burn-down ledger** — an append-only, hash-chained JSONL ledger of
  per-cycle queue snapshots (``knowledge-debt.jsonl`` next to the store).
  :func:`load_debt_ledger` verifies every line's hash and every
  ``prev_hash`` link and raises :class:`DebtLedgerError` on any break, so
  silent truncation or tampering is detected before trend numbers are read.

* **CLI handler** — :func:`run_debt_command` backs ``debt list|report|propose``.
  ``list``/``report`` never write to the store (the ledger is a sidecar
  file); ``propose`` turns a queue item's draft into a real
  :func:`know_your_context.improvement.propose` call — the existing
  proposal-gated path, so high-impact retire/merge drafts still require an
  operator approval before anything is applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from know_your_context.analytics.debt import (
    KILL_SWITCH_ENV,
    DebtConfig,
    DebtError,
    build_debt_queue,
    build_debt_report,
    queue_snapshot,
    render_debt_markdown,
)
from know_your_context.domain import canonical_json
from know_your_context.store import EventStore

__all__ = [
    "DEFAULT_LEDGER_FILENAME",
    "LEDGER_SCHEMA_VERSION",
    "DebtLedgerError",
    "append_debt_snapshot",
    "ledger_path_for_store",
    "load_debt_inputs",
    "load_debt_ledger",
    "run_debt_command",
]

DEFAULT_LEDGER_FILENAME = "knowledge-debt.jsonl"
LEDGER_SCHEMA_VERSION = 1
_GENESIS_PREV_HASH = "0" * 64

_LATEST_REVISIONS_SQL = (
    "SELECT nr.node_id AS node_id, nr.payload_json AS payload_json FROM node_revision nr "
    "JOIN (SELECT node_id, MAX(version) AS v FROM node_revision GROUP BY node_id) latest "
    "ON latest.node_id = nr.node_id AND latest.v = nr.version "
    "WHERE json_extract(nr.payload_json, '$.lifecycle_status') != 'superseded' "
    "ORDER BY nr.node_id"
)
_RELATIONS_SQL = "SELECT source_node_id, target_node_id, relation FROM node_relation ORDER BY relation_seq"
_UNRESOLVED_SQL = (
    "SELECT ref_id, source_node_id, target_name, status, first_seen FROM unresolved_refs "
    "WHERE status IN ('pending', 'failed') ORDER BY first_seen, ref_id"
)


class DebtLedgerError(RuntimeError):
    """The burn-down ledger is corrupt or was tampered with (fail-closed)."""


# ------------------------------------------------------------------ adapter


def load_debt_inputs(store: EventStore) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read-only SQL adapter: (nodes, links, unresolved refs) for the detector."""
    nodes: list[dict[str, Any]] = []
    with store.read() as conn:
        rows = conn.execute(_LATEST_REVISIONS_SQL).fetchall()
        relation_rows = conn.execute(_RELATIONS_SQL).fetchall()
        ref_rows = conn.execute(_UNRESOLVED_SQL).fetchall()
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        nodes.append(
            {
                "id": str(payload["node_id"]),
                "version": int(payload.get("version", 1)),
                "domain": str(payload.get("domain", "")),
                "scores": dict(payload.get("scores") or {}),
                "last_used_at": payload.get("last_used_at"),
                "updated_at": payload.get("updated_at"),
                "created_at": payload.get("created_at"),
                "lifecycle_status": str(payload.get("lifecycle_status", "active")),
                "parent_id": payload.get("parent_id"),
            }
        )
    node_ids = {node["id"] for node in nodes}
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in relation_rows:
        source, target = str(row["source_node_id"]), str(row["target_node_id"])
        relation = str(row["relation"])
        if source in node_ids and target in node_ids and (source, target, relation) not in seen:
            seen.add((source, target, relation))
            links.append({"source": source, "target": target, "relation": relation})
    for node in nodes:
        parent = node.pop("parent_id")
        if isinstance(parent, str) and parent in node_ids:
            key = (parent, node["id"], "parent_of")
            if key not in seen:
                seen.add(key)
                links.append({"source": parent, "target": node["id"], "relation": "parent_of"})
    refs = [
        {
            "ref_id": int(row["ref_id"]),
            "source_node_id": str(row["source_node_id"]),
            "target_name": str(row["target_name"]),
            "status": str(row["status"]),
            "first_seen": str(row["first_seen"]),
        }
        for row in ref_rows
    ]
    return nodes, links, refs


# ------------------------------------------------------------------ ledger


def ledger_path_for_store(store_path: str | Path) -> Path:
    """The default ledger location: alongside the store file."""
    path = Path(store_path)
    if str(store_path) == ":memory:":
        raise DebtLedgerError("a :memory: store has no ledger location")
    return path.parent / DEFAULT_LEDGER_FILENAME


def _snapshot_hash(entry: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in entry.items() if key != "hash"}
    return hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()


def append_debt_snapshot(path: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Append one hash-chained snapshot line; returns the stored entry."""
    existing = load_debt_ledger(path) if path.exists() else []
    prev_hash = existing[-1]["hash"] if existing else _GENESIS_PREV_HASH
    entry = {**snapshot, "prev_hash": prev_hash}
    entry["hash"] = _snapshot_hash(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(entry) + "\n")
    return entry


def load_debt_ledger(path: Path) -> list[dict[str, Any]]:
    """Load and verify the hash chain; any break raises DebtLedgerError."""
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    prev_hash = _GENESIS_PREV_HASH
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise DebtLedgerError(f"ledger line {line_number} is not JSON: {error}") from error
        if entry.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise DebtLedgerError(f"ledger line {line_number} has an unsupported schema version")
        if entry.get("prev_hash") != prev_hash:
            raise DebtLedgerError(f"ledger line {line_number} breaks the prev_hash chain")
        if entry.get("hash") != _snapshot_hash(entry):
            raise DebtLedgerError(f"ledger line {line_number} fails its hash check")
        entries.append(entry)
        prev_hash = str(entry["hash"])
    return entries


# ------------------------------------------------------------------ CLI


def _usage_error(message: str) -> int:
    envelope = {"error": {"code": "CLI_USAGE", "message": message, "retryable": False, "audit_event_id": None}}
    print(canonical_json(envelope), file=sys.stderr)
    return 2


def _operation_error(code: str, message: str) -> int:
    envelope = {"error": {"code": code, "message": message, "retryable": False, "audit_event_id": None}}
    print(canonical_json(envelope), file=sys.stderr)
    return 1


def _config_from_args(args: argparse.Namespace) -> DebtConfig:
    try:
        return DebtConfig(
            low_score_threshold=str(args.low_score_threshold),
            stale_days=int(args.stale_days),
            unresolved_ref_days=int(args.unresolved_days),
            budget=int(args.budget),
        )
    except DebtError as error:
        raise _CliUsage(error) from error


class _CliUsage(Exception):
    def __init__(self, error: DebtError) -> None:
        super().__init__(str(error))


def _build_queue(store: EventStore, config: DebtConfig) -> dict[str, Any]:
    nodes, links, refs = load_debt_inputs(store)
    return build_debt_queue(nodes, links, now=datetime.now(UTC), unresolved_refs=refs, config=config)


def run_debt_command(args: argparse.Namespace) -> int:
    """Run ``debt list|report|propose``; return the exit code."""
    try:
        config = _config_from_args(args)
    except _CliUsage as error:
        return _usage_error(str(error.__cause__ or error))

    store = EventStore(args.store)
    try:
        queue = _build_queue(store, config)
        if queue["disabled"]:
            print(
                f"debt queue disabled by {KILL_SWITCH_ENV}: the queue is empty by operator decision",
                file=sys.stderr,
            )
        action = str(args.debt_action)
        if action == "list":
            print(canonical_json(queue))
            return 0
        if action == "report":
            return _report(store, args, queue)
        if action == "propose":
            return _propose(store, args, queue)
        return _usage_error(f"unknown debt action {action!r}")
    finally:
        store.close()


def _report(store: EventStore, args: argparse.Namespace, queue: dict[str, Any]) -> int:
    ledger = Path(args.ledger) if getattr(args, "ledger", None) else None
    try:
        ledger = ledger or ledger_path_for_store(store.db_path)
    except DebtLedgerError as error:
        return _operation_error("DEBT_LEDGER", str(error))
    try:
        history = load_debt_ledger(ledger)
    except DebtLedgerError as error:
        return _operation_error("DEBT_LEDGER", str(error))

    if getattr(args, "record", False) and not queue["disabled"]:
        cycle = (history[-1]["cycle"] + 1) if history else 1
        snapshot = queue_snapshot(queue, cycle=cycle, recorded_at=datetime.now(UTC).isoformat())
        append_debt_snapshot(ledger, snapshot)
        history = load_debt_ledger(ledger)

    report = build_debt_report(queue, snapshots=history)
    if str(getattr(args, "format", "markdown")) == "json":
        print(canonical_json(report))
    else:
        print(render_debt_markdown(report), end="")
    return 0


def _propose(store: EventStore, args: argparse.Namespace, queue: dict[str, Any]) -> int:
    if queue["disabled"]:
        return _operation_error(
            "DEBT_DISABLED", f"the debt queue is disabled by {KILL_SWITCH_ENV}; refusing to propose"
        )
    node_id = str(args.node_id)
    item = next((entry for entry in queue["items"] if entry["node_id"] == node_id), None)
    if item is None:
        # Not in the budget window: re-check without the budget so a real
        # debt item beyond the cap can still be proposed explicitly.
        nodes, links, refs = load_debt_inputs(store)
        from know_your_context.analytics.debt import MAX_BUDGET

        full = build_debt_queue(
            nodes,
            links,
            now=datetime.now(UTC),
            unresolved_refs=refs,
            config=DebtConfig(budget=MAX_BUDGET),
        )
        item = next((entry for entry in full["items"] if entry["node_id"] == node_id), None)
    if item is None:
        return _operation_error("NO_DEBT_ITEM", f"node {node_id!r} has no debt item in the queue")

    from know_your_context.improvement import DiffOperation, propose

    diff = [DiffOperation.model_validate(op) for op in item["proposal"]["diff"]]
    try:
        record = propose(
            store,
            node_id=node_id,
            diff=diff,
            rationale=item["proposal"]["rationale"],
            actor=str(getattr(args, "actor", "operator") or "operator"),
        )
    except Exception as error:  # typed store errors land in the envelope
        return _operation_error(type(error).__name__.upper(), str(error))
    proposal_payload = json.loads(record.proposal.model_dump_json())
    proposal_payload["audit_event_id"] = record.audit_event_id
    proposal_payload["audit_event_seq"] = record.audit_event_seq
    print(canonical_json({"debt_item": item, "proposal": proposal_payload}))
    return 0
