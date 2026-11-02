"""Verifiable provenance chain tests (W14 I2, SUB-127).

Every node applied from a contract-v4 extraction carries a five-link
hash-chained provenance: source sha256 -> payload hash -> capability actor ->
submission event hash -> node event hash. ``verify_provenance`` re-derives
every link from the artifacts that already exist (node payload, extraction
queue sidecar, audit event, apply event) and reports exactly which link broke;
legacy nodes without a provenance block report an honest ``unverified``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from _extraction_helpers import (
    SOURCE_SHA256,
    SOURCE_SHA256_B,
    audit_events,
    make_args,
    make_payload,
    seed_engine,
    seed_root,
)
from know_your_context.cli import main as cli_main
from know_your_context.domain import Node, NodeKind, PrimaryDomain, canonical_json
from know_your_context.extraction.queue import sidecar_path
from know_your_context.security import SecurityError
from know_your_context.security.provenance import (  # noqa: E402 (RED until provenance.py lands)
    LINK_NAMES,
    CapabilityActorLinkError,
    LinkReport,
    NodeEventLinkError,
    PayloadHashLinkError,
    ProvenanceChainError,
    ProvenanceNodeNotFoundError,
    ProvenanceReport,
    SourceHashLinkError,
    SubmissionEventLinkError,
    verify_provenance,
    verify_provenance_strict,
)

OPERATOR = "operator"
APPLY_SOURCE_VERSION_PREFIX = "extraction:"

# --------------------------------------------------------------------- setup


def _seed_applied(tmp_path: Path, *, with_source_path: bool = False, actor: str = "agent-7"):
    """One submitted + approved extraction; returns the applied node ids."""
    engine, service, store, queue, actor = seed_engine(tmp_path, actor=actor)
    root = seed_root(tmp_path)
    args = make_args(source_path="scan-a.txt" if with_source_path else None)
    receipt = engine.submit(args, actor=actor, extraction_root=root)
    decision = engine.approve(receipt["receipt_id"], operator=OPERATOR, service=service)
    return {
        "engine": engine,
        "service": service,
        "store": store,
        "queue": queue,
        "actor": actor,
        "root": root,
        "receipt": receipt,
        "receipt_id": receipt["receipt_id"],
        "node_ids": list(decision["applied_nodes"]),
        "node_id": decision["applied_nodes"][0],
    }


def _file_fingerprints(*paths: Path) -> dict[str, str]:
    """sha256 of every existing file (db, -wal, -shm) — the read-only proof."""
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.exists()}


def _db_family(db_path: Path) -> list[Path]:
    # -shm is transient reader-mark bookkeeping, not durable content; the
    # read-only proof covers the database and its WAL only.
    return [db_path, Path(f"{db_path}-wal")]


def _seed_legacy_node(tmp_path: Path):
    """A plain pre-v4 node with no provenance block at all."""
    engine, service, store, queue, actor = seed_engine(tmp_path)
    node = Node(
        node_id="legacy:note-1",
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary="a note written before contract v4",
        provenance=("manual:operator",),
        source_version_id="legacy:v1",
    )
    service.log_event(node, actor=actor, idempotency_key="legacy:note-1")
    return {"service": service, "store": store, "queue": queue, "node_id": node.node_id}


# ------------------------------------------------------------------- tampers

_TRIGGER_SQL = {
    "event_log": (
        "event_log_no_update",
        "CREATE TRIGGER event_log_no_update BEFORE UPDATE ON event_log"
        " BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END",
    ),
    "node_revision": (
        "node_revision_no_update",
        "CREATE TRIGGER node_revision_no_update BEFORE UPDATE ON node_revision"
        " BEGIN SELECT RAISE(ABORT, 'node revisions are immutable'); END",
    ),
}


def _tamper_store(store, table: str, set_sql: str, where_sql: str, params: tuple[Any, ...] = ()) -> None:
    """Simulate an attacker with raw file access: drop the append-only
    trigger, mutate, restore the trigger (the schema ends up unchanged)."""
    trigger, create_sql = _TRIGGER_SQL[table]
    with store.transaction() as conn:
        conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute(f"UPDATE {table} SET {set_sql} WHERE {where_sql}", params)
        conn.execute(create_sql)


def _delete_store_rows(store, table: str, where_sql: str, params: tuple[Any, ...] = ()) -> None:
    trigger = f"{table}_no_delete"
    message = "event_log is append-only" if table == "event_log" else "node revisions are immutable"
    with store.transaction() as conn:
        conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute(f"DELETE FROM {table} WHERE {where_sql}", params)
        conn.execute(f"CREATE TRIGGER {trigger} BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '{message}'); END")


def _tamper_queue(store, set_sql: str, where_sql: str, params: tuple[Any, ...] = ()) -> None:
    """Mutate the extraction sidecar with a raw second connection."""
    conn = sqlite3.connect(sidecar_path(store.db_path))
    try:
        conn.execute(f"UPDATE extraction_submissions SET {set_sql} WHERE {where_sql}", params)
        conn.commit()
    finally:
        conn.close()


def _submit_audit_where() -> tuple[str, tuple[Any, ...]]:
    return "kind = 'audit' AND event_type = 'knowledge.submit_extraction'", ()


def _apply_event_where(node_id: str, receipt_id: str) -> tuple[str, tuple[Any, ...]]:
    return "kind = 'domain' AND node_id = ? AND source_version_id = ?", (node_id, f"extraction:{receipt_id}")


def _rewrite_node_payload(store, node_id: str, mutate) -> None:
    """Rewrite the materialized node payload (attacker on the read model)."""
    with store.read() as conn:
        row = conn.execute(
            "SELECT version, payload_json FROM node_revision WHERE node_id = ? ORDER BY version DESC LIMIT 1",
            (node_id,),
        ).fetchone()
    assert row is not None
    payload = json.loads(str(row["payload_json"]))
    mutate(payload)
    trigger, create_sql = _TRIGGER_SQL["node_revision"]
    with store.transaction() as conn:
        conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute(
            "UPDATE node_revision SET payload_json = ? WHERE node_id = ? AND version = ?",
            (canonical_json(payload), node_id, int(row["version"])),
        )
        conn.execute(create_sql)


def _rewrite_event_payload(store, where_sql: str, params: tuple[Any, ...], mutate) -> None:
    with store.read() as conn:
        row = conn.execute(f"SELECT event_id, payload_json FROM event_log WHERE {where_sql}", params).fetchone()
    assert row is not None
    payload = json.loads(str(row["payload_json"]))
    mutate(payload)
    _tamper_store(
        store,
        "event_log",
        "payload_json = ?",
        "event_id = ?",
        (canonical_json(payload), str(row["event_id"])),
    )


# ============================================================== happy chain


class TestHappyChain:
    def test_full_chain_rederives_and_matches(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "verified"
        assert report.ok
        # The chain re-derives every id from first principles.
        row = env["queue"].get(env["receipt_id"])
        assert row is not None
        payload_hash = hashlib.sha256(canonical_json(row.payload).encode("utf-8")).hexdigest()
        assert payload_hash == row.payload_sha256
        fingerprint = hashlib.sha256(
            canonical_json(
                {"agent": row.method["agent"], "prompt_id": row.method["prompt_id"], "tool": row.method["tool"]}
            ).encode()
        ).hexdigest()
        idem = f"{row.source_sha256}|{row.actor}|{fingerprint}"
        assert idem == row.idem_key
        receipt = "ext-" + hashlib.sha256(f"{idem}|{row.payload_sha256}".encode()).hexdigest()[:32]
        assert receipt == env["receipt_id"]

    def test_report_has_exactly_five_links_in_order(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert tuple(link.name for link in report.links) == LINK_NAMES
        assert tuple(link.index for link in report.links) == (0, 1, 2, 3, 4)

    def test_every_link_ok_on_a_clean_chain(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert all(link.status == "ok" for link in report.links)
        assert report.broken_link is None
        assert all(link.error_type is None for link in report.links)

    def test_second_node_of_the_same_receipt_verifies(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        assert len(env["node_ids"]) == 2
        report = verify_provenance(env["store"], env["node_ids"][1])
        assert report.status == "verified"

    def test_chain_with_bound_source_path_verifies(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path, with_source_path=True)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "verified"

    def test_optional_source_file_rehash_with_root(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path, with_source_path=True)
        report = verify_provenance(env["store"], env["node_id"], extraction_root=env["root"])
        assert report.status == "verified"

    def test_verification_is_deterministic_across_runs(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        first = verify_provenance(env["store"], env["node_id"])
        second = verify_provenance(env["store"], env["node_id"])
        assert first == second

    def test_report_to_dict_is_canonical_and_complete(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        data = report.to_dict()
        assert data["node_id"] == env["node_id"]
        assert data["status"] == "verified"
        assert data["broken_link"] is None
        assert len(data["links"]) == 5
        assert json.loads(json.dumps(data)) == data  # JSON-round-trippable

    def test_custom_actor_is_the_actor_in_the_chain(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path, actor="vision-agent-12")
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "verified"
        row = env["queue"].get(env["receipt_id"])
        assert row is not None and row.actor == "vision-agent-12"

    def test_two_independent_submissions_both_verify(self, tmp_path: Path) -> None:
        engine, service, store, queue, actor = seed_engine(tmp_path)
        root = seed_root(tmp_path)
        first = engine.submit(make_args(source_path="scan-a.txt"), actor=actor, extraction_root=root)
        second = engine.submit(
            make_args(
                source_sha256=SOURCE_SHA256_B,
                source_path="scan-b.txt",
                payload=make_payload(text="the south graft failed twice"),
            ),
            actor=actor,
            extraction_root=root,
        )
        for receipt in (first, second):
            decision = engine.approve(receipt["receipt_id"], operator=OPERATOR, service=service)
            report = verify_provenance(store, decision["applied_nodes"][0])
            assert report.status == "verified"

    def test_report_reason_mentions_the_chain(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert "5" in report.reason or "chain" in report.reason.lower()


# ======================================================== unverified legacy


class TestUnverifiedLegacy:
    def test_legacy_node_is_unverified_not_verified(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "unverified"
        assert not report.ok

    def test_legacy_node_is_not_failed_either(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status != "failed"
        assert report.broken_link is None

    def test_legacy_links_are_all_unverified(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert len(report.links) == 5
        assert all(link.status == "unverified" for link in report.links)

    def test_legacy_reason_is_honest_about_the_missing_block(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert "no" in report.reason.lower() and "provenance" in report.reason.lower()

    def test_unverified_is_not_ok(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.ok is False

    def test_strict_does_not_raise_for_legacy_nodes(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        report = verify_provenance_strict(env["store"], env["node_id"])
        assert report.status == "unverified"

    def test_unknown_node_raises_typed_not_found(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        with pytest.raises(ProvenanceNodeNotFoundError):
            verify_provenance(env["store"], "agent:does-not-exist")

    def test_unknown_node_strict_raises_too(self, tmp_path: Path) -> None:
        env = _seed_legacy_node(tmp_path)
        with pytest.raises(ProvenanceNodeNotFoundError):
            verify_provenance_strict(env["store"], "agent:does-not-exist")

    def test_node_with_only_certainty_block_is_failed_not_unverified(self, tmp_path: Path) -> None:
        """A partial provenance block is a tamper signal, not a legacy node."""
        env = _seed_applied(tmp_path)

        def strip_extraction(payload: dict[str, Any]) -> None:
            payload["payload"].pop("extraction", None)

        _rewrite_node_payload(env["store"], env["node_id"], strip_extraction)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"

    def test_node_with_only_extraction_block_is_failed(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)

        def strip_certainty(payload: dict[str, Any]) -> None:
            payload["payload"].pop("certainty", None)

        _rewrite_node_payload(env["store"], env["node_id"], strip_certainty)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"


# ================================================================= link 0


class TestSourceHashLink:
    def test_node_side_source_hash_tamper_breaks_link_0(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)

        def mutate(payload: dict[str, Any]) -> None:
            payload["payload"]["certainty"]["source_sha256"] = SOURCE_SHA256_B

        _rewrite_node_payload(env["store"], env["node_id"], mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == 0
        assert report.links[0].error_type == "SourceHashLinkError"

    def test_node_side_tamper_leaves_other_links_ok(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)

        def mutate(payload: dict[str, Any]) -> None:
            payload["payload"]["certainty"]["source_sha256"] = SOURCE_SHA256_B

        _rewrite_node_payload(env["store"], env["node_id"], mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert [link.status for link in report.links[1:]] == ["ok", "ok", "ok", "ok"]

    def test_provenance_tuple_sha_entry_tamper_breaks_link_0(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)

        def mutate(payload: dict[str, Any]) -> None:
            payload["provenance"] = [
                f"sha256:{SOURCE_SHA256_B}" if entry.startswith("sha256:") else entry for entry in payload["provenance"]
            ]

        _rewrite_node_payload(env["store"], env["node_id"], mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 0

    def test_queue_side_source_hash_tamper_breaks_link_0(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        _tamper_queue(env["store"], "source_sha256 = ?", "receipt_id = ?", (SOURCE_SHA256_B, env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == 0
        assert report.links[0].error_type == "SourceHashLinkError"

    def test_receipt_source_hash_tamper_breaks_link_0(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        row = env["queue"].get(env["receipt_id"])
        assert row is not None
        receipt = dict(row.receipt)
        receipt["source_sha256"] = SOURCE_SHA256_B
        _tamper_queue(env["store"], "receipt_json = ?", "receipt_id = ?", (canonical_json(receipt), env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 0

    def test_deleted_source_file_with_root_breaks_link_0(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path, with_source_path=True)
        (env["root"] / "scan-a.txt").unlink()
        report = verify_provenance(env["store"], env["node_id"], extraction_root=env["root"])
        assert report.broken_link == 0

    def test_rewritten_source_file_with_root_breaks_link_0(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path, with_source_path=True)
        (env["root"] / "scan-a.txt").write_bytes(b"utterly different bytes\n")
        report = verify_provenance(env["store"], env["node_id"], extraction_root=env["root"])
        assert report.broken_link == 0


# ================================================================= link 1


class TestPayloadHashLink:
    def test_payload_json_tamper_breaks_link_1(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        row = env["queue"].get(env["receipt_id"])
        assert row is not None
        payload = dict(row.payload)
        payload["text"] = "forged: the graft needs NO witnesses"
        _tamper_queue(env["store"], "payload_json = ?", "receipt_id = ?", (canonical_json(payload), env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == 1
        assert report.links[1].error_type == "PayloadHashLinkError"

    def test_payload_json_tamper_leaves_links_0_and_2_ok(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        row = env["queue"].get(env["receipt_id"])
        assert row is not None
        payload = dict(row.payload)
        payload["text"] = "forged text"
        _tamper_queue(env["store"], "payload_json = ?", "receipt_id = ?", (canonical_json(payload), env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.links[0].status == "ok"
        assert report.links[2].status == "ok"

    def test_payload_sha_column_tamper_breaks_at_link_1(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        forged = hashlib.sha256(b"forged").hexdigest()
        _tamper_queue(env["store"], "payload_sha256 = ?", "receipt_id = ?", (forged, env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 1
        assert report.links[1].error_type == "PayloadHashLinkError"

    def test_node_side_payload_hash_tamper_breaks_only_link_1(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)

        def mutate(payload: dict[str, Any]) -> None:
            payload["payload"]["extraction"]["payload_sha256"] = hashlib.sha256(b"forged").hexdigest()

        _rewrite_node_payload(env["store"], env["node_id"], mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 1
        assert report.links[0].status == "ok"
        assert report.links[2].status == "ok"

    def test_receipt_payload_hash_tamper_breaks_link_1(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        row = env["queue"].get(env["receipt_id"])
        assert row is not None
        receipt = dict(row.receipt)
        receipt["payload_sha256"] = hashlib.sha256(b"forged").hexdigest()
        _tamper_queue(env["store"], "receipt_json = ?", "receipt_id = ?", (canonical_json(receipt), env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 1

    def test_payload_node_replacement_breaks_link_1(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        row = env["queue"].get(env["receipt_id"])
        assert row is not None
        payload = dict(row.payload)
        payload["nodes"] = [{"name": "Forged", "kind": "leaf", "domain": "P3", "summary": "injected"}]
        _tamper_queue(env["store"], "payload_json = ?", "receipt_id = ?", (canonical_json(payload), env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 1


# ================================================================= link 2


class TestCapabilityActorLink:
    def test_actor_column_tamper_breaks_at_link_2(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        _tamper_queue(env["store"], "actor = ?", "receipt_id = ?", ("mallory", env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == 2
        assert report.links[2].error_type == "CapabilityActorLinkError"

    def test_method_agent_tamper_breaks_link_2(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        row = env["queue"].get(env["receipt_id"])
        assert row is not None
        method = dict(row.method)
        method["agent"] = "mallory-9"
        _tamper_queue(env["store"], "method_json = ?", "receipt_id = ?", (canonical_json(method), env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 2

    def test_method_fingerprint_column_tamper_breaks_link_2(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        forged = hashlib.sha256(b"other-method").hexdigest()
        _tamper_queue(env["store"], "method_fingerprint = ?", "receipt_id = ?", (forged, env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 2

    def test_idem_key_column_tamper_breaks_link_2(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        _tamper_queue(env["store"], "idem_key = ?", "receipt_id = ?", ("forged|key|here", env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 2

    def test_node_provenance_agent_entry_tamper_breaks_only_link_2(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)

        def mutate(payload: dict[str, Any]) -> None:
            payload["provenance"] = [
                "agent:mallory" if entry.startswith("agent:") else entry for entry in payload["provenance"]
            ]

        _rewrite_node_payload(env["store"], env["node_id"], mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 2
        assert report.links[0].status == "ok"
        assert report.links[1].status == "ok"
        assert report.links[3].status == "ok"
        assert report.links[4].status == "ok"

    def test_receipt_id_column_tamper_breaks_link_2(self, tmp_path: Path) -> None:
        """Forging the receipt id breaks the re-derived identity binding."""
        env = _seed_applied(tmp_path)
        forged = "ext-" + hashlib.sha256(b"forged-receipt").hexdigest()[:32]
        _tamper_queue(env["store"], "receipt_id = ?", "receipt_id = ?", (forged, env["receipt_id"]))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        # The node names the original receipt, which no longer exists.
        assert report.broken_link is not None


# ================================================================= link 3


class TestSubmissionEventLink:
    def test_missing_link_is_typed_error(self, tmp_path: Path) -> None:
        """Deleting the submission audit event breaks link 3 with a typed error."""
        env = _seed_applied(tmp_path)
        where, params = _submit_audit_where()
        _delete_store_rows(env["store"], "event_log", where, params)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == 3
        assert report.links[3].error_type == "SubmissionEventLinkError"

    def test_audit_event_hash_tamper_breaks_link_3(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        forged = hashlib.sha256(b"forged-hash").hexdigest()
        where, params = _submit_audit_where()
        _tamper_store(env["store"], "event_log", "event_hash = ?", f"{where}", (forged,))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 3
        assert report.links[3].error_type == "SubmissionEventLinkError"

    def test_audit_event_prev_tamper_breaks_link_3(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        forged = hashlib.sha256(b"forged-prev").hexdigest()
        where, _ = _submit_audit_where()
        _tamper_store(env["store"], "event_log", "prev_event_hash = ?", where, (forged,))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 3

    def test_audit_payload_receipt_rewrite_breaks_link_3(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        where, params = _submit_audit_where()

        def mutate(payload: dict[str, Any]) -> None:
            payload["result_references"]["receipt_id"] = "ext-" + "0" * 32

        _rewrite_event_payload(env["store"], where, params, mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 3

    def test_audit_payload_source_hash_tamper_breaks_link_3(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        where, params = _submit_audit_where()

        def mutate(payload: dict[str, Any]) -> None:
            payload["result_references"]["source_sha256"] = SOURCE_SHA256_B

        _rewrite_event_payload(env["store"], where, params, mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 3

    def test_audit_event_actor_tamper_breaks_link_3(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        where, _ = _submit_audit_where()
        _tamper_store(env["store"], "event_log", "actor = ?", where, ("mallory",))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 3

    def test_clean_chain_has_exactly_one_submit_audit_event(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        events = audit_events(env["store"], "knowledge.submit_extraction")
        assert len(events) == 1
        assert events[0]["result_references"]["receipt_id"] == env["receipt_id"]


# ================================================================= link 4


class TestNodeEventLink:
    def test_deleted_apply_event_breaks_link_4(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        where, params = _apply_event_where(env["node_id"], env["receipt_id"])
        _delete_store_rows(env["store"], "event_log", where, params)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == 4
        assert report.links[4].error_type == "NodeEventLinkError"

    def test_apply_event_hash_tamper_breaks_link_4(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        forged = hashlib.sha256(b"forged-hash").hexdigest()
        where, params = _apply_event_where(env["node_id"], env["receipt_id"])
        _tamper_store(env["store"], "event_log", "event_hash = ?", where, (forged, *params))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 4

    def test_apply_event_prev_tamper_breaks_link_4(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        forged = hashlib.sha256(b"forged-prev").hexdigest()
        where, params = _apply_event_where(env["node_id"], env["receipt_id"])
        _tamper_store(env["store"], "event_log", "prev_event_hash = ?", where, (forged, *params))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 4

    def test_apply_event_payload_tamper_breaks_link_4(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        where, params = _apply_event_where(env["node_id"], env["receipt_id"])

        def mutate(payload: dict[str, Any]) -> None:
            payload["summary"] = "forged summary"

        _rewrite_event_payload(env["store"], where, params, mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 4
        assert report.links[4].error_type == "NodeEventLinkError"

    def test_apply_event_provenance_rewrite_breaks_link_4(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        where, params = _apply_event_where(env["node_id"], env["receipt_id"])

        def mutate(payload: dict[str, Any]) -> None:
            payload["node"]["payload"]["certainty"]["receipt_id"] = "ext-" + "f" * 32

        _rewrite_event_payload(env["store"], where, params, mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 4

    def test_apply_event_actor_tamper_breaks_link_4(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        where, params = _apply_event_where(env["node_id"], env["receipt_id"])
        _tamper_store(env["store"], "event_log", "actor = ?", where, ("mallory", *params))
        report = verify_provenance(env["store"], env["node_id"])
        assert report.broken_link == 4


# =========================================================== tamper matrix


def _tamper_link_0(env: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["payload"]["certainty"]["source_sha256"] = SOURCE_SHA256_B

    _rewrite_node_payload(env["store"], env["node_id"], mutate)


def _tamper_link_1(env: dict[str, Any]) -> None:
    row = env["queue"].get(env["receipt_id"])
    assert row is not None
    payload = dict(row.payload)
    payload["text"] = "forged"
    _tamper_queue(env["store"], "payload_json = ?", "receipt_id = ?", (canonical_json(payload), env["receipt_id"]))


def _tamper_link_2(env: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["provenance"] = [
            "agent:mallory" if entry.startswith("agent:") else entry for entry in payload["provenance"]
        ]

    _rewrite_node_payload(env["store"], env["node_id"], mutate)


def _tamper_link_3(env: dict[str, Any]) -> None:
    where, params = _submit_audit_where()
    _delete_store_rows(env["store"], "event_log", where, params)


def _tamper_link_4(env: dict[str, Any]) -> None:
    where, params = _apply_event_where(env["node_id"], env["receipt_id"])
    _delete_store_rows(env["store"], "event_log", where, params)


_TAMPERS = {
    0: (_tamper_link_0, "SourceHashLinkError"),
    1: (_tamper_link_1, "PayloadHashLinkError"),
    2: (_tamper_link_2, "CapabilityActorLinkError"),
    3: (_tamper_link_3, "SubmissionEventLinkError"),
    4: (_tamper_link_4, "NodeEventLinkError"),
}


class TestTamperDrillMatrix:
    @pytest.mark.parametrize("link_index", [0, 1, 2, 3, 4])
    def test_tamper_at_each_link_detected_with_index(self, tmp_path: Path, link_index: int) -> None:
        env = _seed_applied(tmp_path)
        tamper, error_name = _TAMPERS[link_index]
        tamper(env)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == link_index
        assert report.links[link_index].status == "broken"
        assert report.links[link_index].error_type == error_name

    @pytest.mark.parametrize("link_index", [0, 1, 2, 3, 4])
    def test_links_before_the_tampered_one_stay_ok(self, tmp_path: Path, link_index: int) -> None:
        env = _seed_applied(tmp_path)
        tamper, _ = _TAMPERS[link_index]
        tamper(env)
        report = verify_provenance(env["store"], env["node_id"])
        for link in report.links[:link_index]:
            assert link.status == "ok", f"link {link.index} should survive a tamper at {link_index}"

    @pytest.mark.parametrize("link_index", [0, 1, 2, 3, 4])
    def test_strict_raises_the_typed_error_of_the_tampered_link(self, tmp_path: Path, link_index: int) -> None:
        env = _seed_applied(tmp_path)
        tamper, error_name = _TAMPERS[link_index]
        tamper(env)
        with pytest.raises(ProvenanceChainError) as excinfo:
            verify_provenance_strict(env["store"], env["node_id"])
        assert type(excinfo.value).__name__ == error_name
        assert excinfo.value.link_index == link_index
        assert excinfo.value.link_name == LINK_NAMES[link_index]


# ================================================================ read-only


class TestReadOnly:
    def test_verification_is_read_only(self, tmp_path: Path) -> None:
        """Byte proof: the store and sidecar files are untouched by verify."""
        env = _seed_applied(tmp_path)
        store = env["store"]
        db_path = Path(str(store.db_path))
        watched = _db_family(db_path) + _db_family(Path(sidecar_path(db_path)))
        before = _file_fingerprints(*watched)
        report = verify_provenance(store, env["node_id"])
        assert report.status == "verified"
        assert _file_fingerprints(*watched) == before

    def test_verification_changes_no_logical_state(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        store = env["store"]
        chain_before = store.verify_chain()
        dump_before = env["queue"].canonical_dump()
        count_before = store.event_count()
        verify_provenance(store, env["node_id"])
        assert store.verify_chain() == chain_before
        assert env["queue"].canonical_dump() == dump_before
        assert store.event_count() == count_before

    def test_verification_after_tamper_is_also_read_only(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        _tamper_link_1(env)
        store = env["store"]
        db_path = Path(str(store.db_path))
        watched = _db_family(db_path) + _db_family(Path(sidecar_path(db_path)))
        before = _file_fingerprints(*watched)
        report = verify_provenance(store, env["node_id"])
        assert report.status == "failed"
        assert _file_fingerprints(*watched) == before

    def test_repeated_verification_on_memory_store_changes_nothing(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        store = env["store"]
        verify_provenance(store, env["node_id"])
        verify_provenance(store, env["node_id"])
        report = store.verify_chain()
        assert report.ok


# ============================================================ strict errors


class TestTypedErrors:
    def test_chain_errors_are_security_errors(self) -> None:
        for cls in (
            SourceHashLinkError,
            PayloadHashLinkError,
            CapabilityActorLinkError,
            SubmissionEventLinkError,
            NodeEventLinkError,
        ):
            assert issubclass(cls, ProvenanceChainError)
            assert issubclass(cls, SecurityError)

    def test_link_error_carries_index_and_name(self) -> None:
        error = PayloadHashLinkError("boom", link_index=1)
        assert error.link_index == 1
        assert error.link_name == "payload_hash"
        assert "boom" in str(error)

    def test_each_link_error_has_its_fixed_index(self) -> None:
        cases = [
            (SourceHashLinkError, 0),
            (PayloadHashLinkError, 1),
            (CapabilityActorLinkError, 2),
            (SubmissionEventLinkError, 3),
            (NodeEventLinkError, 4),
        ]
        for cls, index in cases:
            error = cls("x", link_index=index)
            assert error.link_index == index
            assert error.link_name == LINK_NAMES[index]

    def test_strict_passes_on_a_clean_chain(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance_strict(env["store"], env["node_id"])
        assert report.status == "verified"

    def test_missing_queue_row_fails_closed(self, tmp_path: Path) -> None:
        """A node naming a receipt the queue has never seen cannot verify."""
        env = _seed_applied(tmp_path)

        def mutate(payload: dict[str, Any]) -> None:
            forged = "ext-" + hashlib.sha256(b"ghost").hexdigest()[:32]
            payload["payload"]["certainty"]["receipt_id"] = forged
            payload["payload"]["extraction"]["receipt_id"] = forged

        _rewrite_node_payload(env["store"], env["node_id"], mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link is not None

    def test_node_not_found_message_names_the_node(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        with pytest.raises(ProvenanceNodeNotFoundError, match="ghost"):
            verify_provenance(env["store"], "ghost")


# ============================================================= report shape


class TestReportShape:
    def test_link_report_fields(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        link = report.links[0]
        assert isinstance(link, LinkReport)
        assert link.index == 0
        assert link.name == "source_sha256"
        assert link.status == "ok"
        assert link.detail

    def test_report_is_frozen(self, tmp_path: Path) -> None:
        import dataclasses

        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert isinstance(report, ProvenanceReport)
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.status = "hacked"  # type: ignore[misc]

    def test_failed_report_reason_names_the_broken_link(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        _tamper_link_3(env)
        report = verify_provenance(env["store"], env["node_id"])
        assert LINK_NAMES[3] in report.reason

    def test_broken_link_detail_explains_what_mismatched(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        _tamper_link_1(env)
        report = verify_provenance(env["store"], env["node_id"])
        detail = report.links[1].detail.lower()
        assert "payload" in detail

    def test_link_names_constant(self) -> None:
        assert LINK_NAMES == ("source_sha256", "payload_hash", "capability_actor", "submission_event", "node_event")


# ====================================================================== cli


def _cli(*argv: str) -> int:
    return cli_main(list(argv))


class TestCli:
    def test_verify_verified_node_exits_0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_applied(tmp_path)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", env["node_id"])
        assert code == 0
        out = capsys.readouterr().out
        assert "verified" in out.lower()

    def test_verify_prints_all_five_links(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_applied(tmp_path)
        _cli("--store", str(env["store"].db_path), "provenance", "verify", env["node_id"])
        out = capsys.readouterr().out
        for name in LINK_NAMES:
            assert name in out

    def test_verify_tampered_node_exits_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_applied(tmp_path)
        _tamper_link_1(env)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", env["node_id"])
        assert code == 1
        out = capsys.readouterr().out
        assert "payload_hash" in out

    def test_verify_legacy_node_exits_3_with_honest_status(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        env = _seed_legacy_node(tmp_path)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", env["node_id"])
        assert code == 3
        assert "unverified" in capsys.readouterr().out.lower()

    def test_verify_unknown_node_exits_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_applied(tmp_path)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", "agent:ghost")
        assert code == 1
        err = capsys.readouterr().err
        assert "ghost" in err
        assert "PROVENANCE_NODE_NOT_FOUND" in err

    def test_verify_json_output_shape(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_applied(tmp_path)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", "--json", env["node_id"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "verified"
        assert data["node_id"] == env["node_id"]
        assert len(data["links"]) == 5
        assert data["links"][0]["name"] == "source_sha256"

    def test_verify_json_on_failure_marks_the_broken_link(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        env = _seed_applied(tmp_path)
        _tamper_link_4(env)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", "--json", env["node_id"])
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "failed"
        assert data["broken_link"] == 4
        assert data["links"][4]["error_type"] == "NodeEventLinkError"

    def test_verify_json_on_legacy_node(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_legacy_node(tmp_path)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", "--json", env["node_id"])
        assert code == 3
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "unverified"
        assert data["broken_link"] is None

    def test_provenance_requires_an_action(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            _cli("--store", str(tmp_path / "x.sqlite3"), "provenance")

    def test_cli_verification_writes_nothing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_applied(tmp_path)
        store = env["store"]
        db_path = Path(str(store.db_path))
        watched = _db_family(db_path) + _db_family(Path(sidecar_path(db_path)))
        before = _file_fingerprints(*watched)
        _cli("--store", str(db_path), "provenance", "verify", env["node_id"])
        capsys.readouterr()
        assert _file_fingerprints(*watched) == before

    def test_cli_tamper_drill_each_link_exit_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _seed_applied(tmp_path)
        _tamper_link_0(env)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", env["node_id"])
        assert code == 1
        assert "source_sha256" in capsys.readouterr().out


# ============================================================ store helper


class TestEventChainStateHelper:
    def test_fetch_event_chain_state_matches_stored_row(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        with env["store"].read() as conn:
            row = conn.execute("SELECT event_id FROM event_log ORDER BY event_seq DESC LIMIT 1").fetchone()
        assert row is not None
        state = env["store"].fetch_event_chain_state(str(row["event_id"]))
        assert state is not None
        assert state["stored_hash"] == state["rederived_hash"]
        assert state["hash_ok"] is True
        assert state["prev_ok"] is True

    def test_fetch_event_chain_state_unknown_event_returns_none(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        assert env["store"].fetch_event_chain_state("no-such-event") is None

    def test_fetch_event_chain_state_detects_hash_tamper(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        with env["store"].read() as conn:
            row = conn.execute("SELECT event_id FROM event_log ORDER BY event_seq DESC LIMIT 1").fetchone()
        assert row is not None
        forged = hashlib.sha256(b"forged").hexdigest()
        _tamper_store(env["store"], "event_log", "event_hash = ?", "event_id = ?", (forged, str(row["event_id"])))
        state = env["store"].fetch_event_chain_state(str(row["event_id"]))
        assert state is not None
        assert state["hash_ok"] is False

    def test_fetch_event_chain_state_detects_prev_tamper(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        with env["store"].read() as conn:
            row = conn.execute(
                "SELECT event_id FROM event_log WHERE event_seq > 1 ORDER BY event_seq DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        forged = hashlib.sha256(b"forged-prev").hexdigest()
        _tamper_store(env["store"], "event_log", "prev_event_hash = ?", "event_id = ?", (forged, str(row["event_id"])))
        state = env["store"].fetch_event_chain_state(str(row["event_id"]))
        assert state is not None
        assert state["prev_ok"] is False

    def test_first_event_has_genesis_prev(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        with env["store"].read() as conn:
            row = conn.execute("SELECT event_id FROM event_log ORDER BY event_seq LIMIT 1").fetchone()
        assert row is not None
        state = env["store"].fetch_event_chain_state(str(row["event_id"]))
        assert state is not None
        assert state["prev_ok"] is True
        assert state["stored_prev"] is None

    def test_fetch_event_chain_state_is_read_only(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        store = env["store"]
        db_path = Path(str(store.db_path))
        before = _file_fingerprints(*_db_family(db_path))
        with store.read() as conn:
            row = conn.execute("SELECT event_id FROM event_log LIMIT 1").fetchone()
        assert row is not None
        store.fetch_event_chain_state(str(row["event_id"]))
        assert _file_fingerprints(*_db_family(db_path)) == before


# ============================================================ hardening


class TestHardening:
    def test_disagreeing_receipt_blocks_break_link_0(self, tmp_path: Path) -> None:
        """The certainty and extraction blocks must name the same receipt."""
        env = _seed_applied(tmp_path)

        def mutate(payload: dict[str, Any]) -> None:
            payload["payload"]["extraction"]["receipt_id"] = "ext-" + "0" * 32

        _rewrite_node_payload(env["store"], env["node_id"], mutate)
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "failed"
        assert report.broken_link == 0
        assert report.links[0].error_type == "SourceHashLinkError"

    def test_non_chain_columns_do_not_affect_verification(self, tmp_path: Path) -> None:
        """Review metadata (notes, verdicts) is not chain material — by design."""
        env = _seed_applied(tmp_path)
        _tamper_queue(
            env["store"],
            "decision_note = ?, corroborators = ?",
            "receipt_id = ?",
            ("edited note", 9, env["receipt_id"]),
        )
        report = verify_provenance(env["store"], env["node_id"])
        assert report.status == "verified"

    def test_rejected_submission_never_produces_a_verifiable_node(self, tmp_path: Path) -> None:
        engine, service, store, queue, actor = seed_engine(tmp_path)
        root = seed_root(tmp_path)
        receipt = engine.submit(make_args(source_path="scan-a.txt"), actor=actor, extraction_root=root)
        engine.reject(receipt["receipt_id"], operator=OPERATOR, note="not good enough")
        from know_your_context.extraction.tier import applied_node_id

        node_id = applied_node_id(SOURCE_SHA256, "child", "Graft procedure")
        with pytest.raises(ProvenanceNodeNotFoundError):
            verify_provenance(store, node_id)

    def test_verification_with_a_wrong_root_breaks_link_0(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path, with_source_path=True)
        wrong_root = tmp_path / "elsewhere"
        wrong_root.mkdir()
        report = verify_provenance(env["store"], env["node_id"], extraction_root=wrong_root)
        assert report.broken_link == 0

    def test_strict_error_message_carries_the_detail(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        _tamper_link_1(env)
        with pytest.raises(PayloadHashLinkError) as excinfo:
            verify_provenance_strict(env["store"], env["node_id"])
        assert "payload" in str(excinfo.value).lower()

    def test_link_report_is_frozen(self, tmp_path: Path) -> None:
        import dataclasses

        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.links[0].status = "broken"  # type: ignore[misc]

    def test_verified_implies_ok_and_no_broken_link(self, tmp_path: Path) -> None:
        env = _seed_applied(tmp_path)
        report = verify_provenance(env["store"], env["node_id"])
        assert (report.status == "verified") == report.ok
        assert report.ok == (report.broken_link is None)

    def test_cli_help_lists_provenance(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _cli("--help")
        assert excinfo.value.code == 0
        assert "provenance" in capsys.readouterr().out

    def test_cli_json_unverified_links_are_all_unverified(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        env = _seed_legacy_node(tmp_path)
        code = _cli("--store", str(env["store"].db_path), "provenance", "verify", "--json", env["node_id"])
        assert code == 3
        data = json.loads(capsys.readouterr().out)
        assert [link["status"] for link in data["links"]] == ["unverified"] * 5
