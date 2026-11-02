"""Verifiable provenance chain for agent-extracted nodes (W14, I2, SUB-127).

The problem: nodes born from ``knowledge.submit_extraction`` (contract v4)
carry a deliberately lower truth tier, yet nothing answered "this node exists
BECAUSE of what?" end-to-end. The evidence existed but scattered across four
artifacts — the node payload, the extraction queue sidecar, the submission
audit event and the apply event — with no verifier re-deriving the whole
chain. One forged link went unnoticed.

This module closes that gap with a five-link hash chain, every link
re-derived from artifacts that already exist (the frozen store schema is
untouched; verification is pure re-derivation):

0. ``source_sha256`` — node ``certainty.source_sha256`` ↔ the ``sha256:…``
   provenance entry ↔ the queue row ↔ the stored receipt; optionally the
   source file is re-hashed when an extraction root is supplied.
1. ``payload_hash`` — ``sha256(canonical(payload))`` re-derived from the
   queue payload ↔ the stored ``payload_sha256`` column ↔ the node's
   ``extraction.payload_sha256`` ↔ the receipt.
2. ``capability_actor`` — the method fingerprint, the locked idempotency key
   (``source|actor|fingerprint``) and the receipt id
   (``ext-`` + sha256(idem_key|payload_sha256)) are re-derived from the queue
   row, and the node's ``agent:…`` provenance entry must name the method's
   agent.
3. ``submission_event`` — the ``knowledge.submit_extraction`` audit event
   carrying this receipt id: its stored hash is re-derived
   (:meth:`EventStore.fetch_event_chain_state`), its predecessor link checked,
   and its actor/result references cross-checked against the queue row.
4. ``node_event`` — the domain apply event (``node_id`` +
   ``source_version_id = extraction:<receipt>``): hash and predecessor
   re-derived, embedded certainty block cross-checked, actor = the deciding
   operator.

Verdicts are honest: ``verified`` (all five links re-derive and match),
``failed`` (a typed broken link with its index), or ``unverified`` (a legacy
node with no contract-v4 provenance block — never silently "verified").
Verification is read-only: it issues SELECTs only, a property the test suite
proves at the byte level.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from know_your_context.domain import canonical_json
from know_your_context.extraction.queue import ExtractionQueue, QueueRow
from know_your_context.extraction.schema import sha256_file
from know_your_context.security import PathEscapeError, SecurityError, resolve_within_root
from know_your_context.store import EventStore

__all__ = [
    "LINK_NAMES",
    "STATUS_FAILED",
    "STATUS_UNVERIFIED",
    "STATUS_VERIFIED",
    "CapabilityActorLinkError",
    "LinkReport",
    "NodeEventLinkError",
    "PayloadHashLinkError",
    "ProvenanceChainError",
    "ProvenanceError",
    "ProvenanceNodeNotFoundError",
    "ProvenanceReport",
    "SourceHashLinkError",
    "SubmissionEventLinkError",
    "verify_provenance",
    "verify_provenance_strict",
]

#: The five chain links, in order. Frozen: the CLI and the scorecard proofs
#: depend on these names and their order.
LINK_NAMES: tuple[str, ...] = ("source_sha256", "payload_hash", "capability_actor", "submission_event", "node_event")

STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_UNVERIFIED = "unverified"

_LINK_OK = "ok"
_LINK_BROKEN = "broken"
_LINK_UNVERIFIED = "unverified"

_SUBMIT_OPERATION = "knowledge.submit_extraction"  # contract v4 verb (frozen)
_HEX64 = frozenset("0123456789abcdef")


# --------------------------------------------------------------------- errors


class ProvenanceError(SecurityError):
    """Base class for provenance verification failures."""


class ProvenanceNodeNotFoundError(ProvenanceError):
    """The node id names no node in the store."""


class ProvenanceChainError(ProvenanceError):
    """One chain link failed verification; carries the link's index and name."""

    def __init__(self, message: str, *, link_index: int) -> None:
        super().__init__(message)
        self.link_index = link_index
        self.link_name = LINK_NAMES[link_index]


class SourceHashLinkError(ProvenanceChainError):
    """Link 0: the source sha256 binding does not re-derive."""


class PayloadHashLinkError(ProvenanceChainError):
    """Link 1: the extraction payload hash does not re-derive."""


class CapabilityActorLinkError(ProvenanceChainError):
    """Link 2: the actor/method identity binding does not re-derive."""


class SubmissionEventLinkError(ProvenanceChainError):
    """Link 3: the submission audit event does not re-derive."""


class NodeEventLinkError(ProvenanceChainError):
    """Link 4: the apply (node) event does not re-derive."""


_LINK_ERRORS: tuple[type[ProvenanceChainError], ...] = (
    SourceHashLinkError,
    PayloadHashLinkError,
    CapabilityActorLinkError,
    SubmissionEventLinkError,
    NodeEventLinkError,
)


# --------------------------------------------------------------------- report


@dataclass(frozen=True)
class LinkReport:
    """The verdict for one chain link."""

    index: int
    name: str
    status: str  # ok | broken | unverified
    detail: str
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class ProvenanceReport:
    """The full five-link verdict for one node."""

    node_id: str
    status: str  # verified | failed | unverified
    links: tuple[LinkReport, ...]
    broken_link: int | None
    reason: str

    @property
    def ok(self) -> bool:
        return self.status == STATUS_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "broken_link": self.broken_link,
            "reason": self.reason,
            "links": [link.to_dict() for link in self.links],
        }


def _ok(index: int, detail: str) -> LinkReport:
    return LinkReport(index=index, name=LINK_NAMES[index], status=_LINK_OK, detail=detail)


def _broken(index: int, detail: str) -> LinkReport:
    return LinkReport(
        index=index,
        name=LINK_NAMES[index],
        status=_LINK_BROKEN,
        detail=detail,
        error_type=_LINK_ERRORS[index].__name__,
    )


# ------------------------------------------------------------------- helpers


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _method_fingerprint(method: Mapping[str, Any]) -> str:
    """Re-derive ``MethodDescriptor.fingerprint`` from the stored JSON."""
    canonical = canonical_json(
        {
            "agent": str(method.get("agent", "")),
            "prompt_id": str(method.get("prompt_id", "")),
            "tool": str(method.get("tool", "")),
        }
    )
    return _sha256_text(canonical)


def _provenance_entries(node_provenance: tuple[str, ...], prefix: str) -> list[str]:
    return [entry for entry in node_provenance if entry.startswith(prefix)]


# ---------------------------------------------------------------------- links


def _check_source_link(
    node_certainty: Mapping[str, Any] | None,
    node_provenance: tuple[str, ...],
    row: QueueRow | None,
    *,
    extraction_root: str | Path | None,
    declared_receipts: tuple[str | None, str | None] = (None, None),
) -> LinkReport:
    index = 0
    if node_certainty is None:
        return _broken(index, "the node carries a partial provenance block: no certainty block")
    certainty_receipt, extraction_receipt = declared_receipts
    if certainty_receipt is not None and extraction_receipt is not None and certainty_receipt != extraction_receipt:
        return _broken(
            index,
            f"the node's blocks disagree on the receipt: certainty={certainty_receipt} extraction={extraction_receipt}",
        )
    source = node_certainty.get("source_sha256")
    if not isinstance(source, str) or len(source) != 64 or any(char not in _HEX64 for char in source):
        return _broken(index, f"certainty.source_sha256 is missing or malformed: {source!r}")
    if f"sha256:{source}" not in node_provenance:
        return _broken(index, f"node provenance lacks the sha256:{source} entry")
    if row is None:
        return _broken(index, "no extraction queue row carries the node's receipt id")
    if row.source_sha256 != source:
        return _broken(index, f"queue source_sha256 {row.source_sha256} != node certainty {source}")
    receipt_source = row.receipt.get("source_sha256")
    if receipt_source is not None and receipt_source != source:
        return _broken(index, f"receipt source_sha256 {receipt_source} != node certainty {source}")
    if extraction_root is not None and row.source_path:
        try:
            resolved = resolve_within_root(extraction_root, row.source_path)
        except PathEscapeError:
            return _broken(index, f"source_path {row.source_path!r} escapes the extraction root")
        if not resolved.is_file() or not os.access(resolved, os.R_OK):
            return _broken(index, f"source file {row.source_path!r} no longer exists or is unreadable")
        actual = sha256_file(resolved)
        if actual != source:
            return _broken(index, f"source file re-hashes to {actual}, not the bound {source}")
    return _ok(index, f"source sha256 {source[:12]}… bound identically in node, provenance, queue and receipt")


def _check_payload_link(node_extraction: Mapping[str, Any] | None, row: QueueRow | None) -> LinkReport:
    index = 1
    if node_extraction is None:
        return _broken(index, "the node carries a partial provenance block: no extraction block")
    if row is None:
        return _broken(index, "no extraction queue row carries the node's receipt id")
    rederived = _sha256_text(canonical_json(row.payload))
    if rederived != row.payload_sha256:
        return _broken(index, f"queue payload re-hashes to {rederived}, not the stored {row.payload_sha256}")
    node_hash = node_extraction.get("payload_sha256")
    if node_hash != row.payload_sha256:
        return _broken(index, f"node extraction.payload_sha256 {node_hash} != queue {row.payload_sha256}")
    receipt_hash = row.receipt.get("payload_sha256")
    if receipt_hash is not None and receipt_hash != row.payload_sha256:
        return _broken(index, f"receipt payload_sha256 {receipt_hash} != queue {row.payload_sha256}")
    return _ok(index, f"payload hash {row.payload_sha256[:12]}… re-derives from the stored canonical payload")


def _check_actor_link(
    node_provenance: tuple[str, ...],
    receipt_id: str,
    row: QueueRow | None,
) -> LinkReport:
    index = 2
    if row is None:
        return _broken(index, "no extraction queue row carries the node's receipt id")
    fingerprint = _method_fingerprint(row.method)
    if fingerprint != row.method_fingerprint:
        return _broken(
            index, f"method fingerprint re-derives to {fingerprint}, not the stored {row.method_fingerprint}"
        )
    idem_key = f"{row.source_sha256}|{row.actor}|{fingerprint}"
    if idem_key != row.idem_key:
        return _broken(index, "the locked idempotency key does not re-derive from (source, actor, method)")
    receipt = "ext-" + _sha256_text(f"{idem_key}|{row.payload_sha256}")[:32]
    if receipt != row.receipt_id:
        return _broken(index, f"receipt id re-derives to {receipt}, not the stored {row.receipt_id}")
    if receipt != receipt_id:
        return _broken(index, f"the node names receipt {receipt_id}, but the queue row re-derives {receipt}")
    agent = str(row.method.get("agent", ""))
    if f"agent:{agent}" not in node_provenance:
        return _broken(index, f"node provenance lacks the agent:{agent} entry")
    return _ok(index, f"actor {row.actor!r} re-derives through method fingerprint, idem key and receipt id")


def _check_submission_event_link(store: EventStore, receipt_id: str, row: QueueRow | None) -> LinkReport:
    index = 3
    with store.read() as conn:
        rows = conn.execute(
            "SELECT event_id, actor, payload_json FROM event_log WHERE kind = 'audit' AND event_type = ?"
            " ORDER BY event_seq",
            (_SUBMIT_OPERATION,),
        ).fetchall()
    matches = []
    for record in rows:
        try:
            payload = json.loads(str(record["payload_json"]))
        except json.JSONDecodeError:
            return _broken(index, f"submission audit event {record['event_id']} payload is not JSON")
        references = payload.get("result_references")
        if isinstance(references, Mapping) and references.get("receipt_id") == receipt_id:
            matches.append((record, references))
    if not matches:
        return _broken(index, f"no {_SUBMIT_OPERATION} audit event carries receipt id {receipt_id}")
    record, references = matches[0]
    state = store.fetch_event_chain_state(str(record["event_id"]))
    if state is None:  # pragma: no cover - the row was just read
        return _broken(index, "submission audit event vanished mid-verification")
    if not state["hash_ok"]:
        return _broken(index, "submission audit event hash does not re-derive from its stored fields")
    if not state["prev_ok"]:
        return _broken(index, "submission audit event prev link does not match the chain")
    if row is not None:
        if state["actor"] != row.actor:
            return _broken(index, f"submission event actor {state['actor']!r} != queue actor {row.actor!r}")
        if references.get("source_sha256") != row.source_sha256:
            return _broken(index, "submission event result references disagree with the queue source hash")
        if references.get("payload_sha256") != row.payload_sha256:
            return _broken(index, "submission event result references disagree with the queue payload hash")
    return _ok(index, f"submission audit event {record['event_id']} re-derives and binds the receipt")


def _check_node_event_link(
    store: EventStore,
    node_id: str,
    receipt_id: str,
    row: QueueRow | None,
) -> LinkReport:
    index = 4
    source_version_id = f"extraction:{receipt_id}"
    with store.read() as conn:
        record = conn.execute(
            "SELECT event_id, actor, payload_json FROM event_log WHERE kind = 'domain' AND node_id = ?"
            " AND source_version_id = ? ORDER BY event_seq LIMIT 1",
            (node_id, source_version_id),
        ).fetchone()
    if record is None:
        return _broken(index, f"no apply event for node {node_id} under source version {source_version_id}")
    state = store.fetch_event_chain_state(str(record["event_id"]))
    if state is None:  # pragma: no cover - the row was just read
        return _broken(index, "apply event vanished mid-verification")
    if not state["hash_ok"]:
        return _broken(index, "apply event hash does not re-derive from its stored fields")
    if not state["prev_ok"]:
        return _broken(index, "apply event prev link does not match the chain")
    try:
        payload = json.loads(str(record["payload_json"]))
    except json.JSONDecodeError:
        return _broken(index, f"apply event {record['event_id']} payload is not JSON")
    event_node = payload.get("node")
    if not isinstance(event_node, Mapping):
        return _broken(index, "apply event payload carries no node record")
    event_payload = event_node.get("payload")
    event_certainty = event_payload.get("certainty") if isinstance(event_payload, Mapping) else None
    if not isinstance(event_certainty, Mapping) or event_certainty.get("receipt_id") != receipt_id:
        return _broken(index, "apply event's embedded certainty block does not name the node's receipt id")
    if row is not None:
        event_extraction = event_payload.get("extraction") if isinstance(event_payload, Mapping) else None
        if isinstance(event_extraction, Mapping) and event_extraction.get("payload_sha256") != row.payload_sha256:
            return _broken(index, "apply event's embedded payload hash disagrees with the queue")
        if row.decided_by is not None and state["actor"] != row.decided_by:
            return _broken(index, f"apply event actor {state['actor']!r} != deciding operator {row.decided_by!r}")
    return _ok(index, f"apply event {record['event_id']} re-derives and embeds the node's certainty block")


# ------------------------------------------------------------------- verdict


def verify_provenance(
    store: EventStore,
    node_id: str,
    *,
    queue: ExtractionQueue | None = None,
    extraction_root: str | Path | None = None,
) -> ProvenanceReport:
    """Re-derive the five-link provenance chain of one node (read-only).

    Never writes. Legacy nodes without a contract-v4 provenance block return
    an honest ``unverified`` report; a broken link returns ``failed`` with
    ``broken_link`` naming the exact link index.
    """
    node = store.current_node(node_id)
    if node is None:
        raise ProvenanceNodeNotFoundError(f"no node named {node_id!r} exists in the store")
    payload = node.payload if isinstance(node.payload, Mapping) else {}
    certainty = payload.get("certainty")
    extraction = payload.get("extraction")
    if not isinstance(certainty, Mapping) and not isinstance(extraction, Mapping):
        links = tuple(
            LinkReport(
                index=index,
                name=name,
                status=_LINK_UNVERIFIED,
                detail="no contract-v4 provenance block; the link cannot be derived",
            )
            for index, name in enumerate(LINK_NAMES)
        )
        return ProvenanceReport(
            node_id=node_id,
            status=STATUS_UNVERIFIED,
            links=links,
            broken_link=None,
            reason="the node carries no contract-v4 provenance block; provenance is unverified, not assumed valid",
        )
    certainty_map: Mapping[str, Any] | None = certainty if isinstance(certainty, Mapping) else None
    extraction_map: Mapping[str, Any] | None = extraction if isinstance(extraction, Mapping) else None
    receipt_id = ""
    for block in (certainty_map, extraction_map):
        if block is not None and isinstance(block.get("receipt_id"), str):
            receipt_id = str(block["receipt_id"])
            break
    declared_receipts = (
        str(certainty_map["receipt_id"])
        if certainty_map is not None and isinstance(certainty_map.get("receipt_id"), str)
        else None,
        str(extraction_map["receipt_id"])
        if extraction_map is not None and isinstance(extraction_map.get("receipt_id"), str)
        else None,
    )
    sidecar = queue if queue is not None else ExtractionQueue.for_store(store)
    row = sidecar.get(receipt_id) if receipt_id else None
    provenance = tuple(node.provenance)
    links = (
        _check_source_link(
            certainty_map, provenance, row, extraction_root=extraction_root, declared_receipts=declared_receipts
        ),
        _check_payload_link(extraction_map, row),
        _check_actor_link(provenance, receipt_id, row),
        _check_submission_event_link(store, receipt_id, row),
        _check_node_event_link(store, node_id, receipt_id, row),
    )
    broken = next((link.index for link in links if link.status == _LINK_BROKEN), None)
    if broken is None:
        return ProvenanceReport(
            node_id=node_id,
            status=STATUS_VERIFIED,
            links=links,
            broken_link=None,
            reason="all 5 provenance links re-derive and match across node, queue, audit and event artifacts",
        )
    return ProvenanceReport(
        node_id=node_id,
        status=STATUS_FAILED,
        links=links,
        broken_link=broken,
        reason=f"provenance chain broken at link {broken} ({LINK_NAMES[broken]}): {links[broken].detail}",
    )


def verify_provenance_strict(
    store: EventStore,
    node_id: str,
    *,
    queue: ExtractionQueue | None = None,
    extraction_root: str | Path | None = None,
) -> ProvenanceReport:
    """Like :func:`verify_provenance` but raises the typed link error."""
    report = verify_provenance(store, node_id, queue=queue, extraction_root=extraction_root)
    if report.status == STATUS_FAILED and report.broken_link is not None:
        link = report.links[report.broken_link]
        raise _LINK_ERRORS[report.broken_link](link.detail, link_index=report.broken_link)
    return report
