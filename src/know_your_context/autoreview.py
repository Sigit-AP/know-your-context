"""Disk-mode post-write auto-review (deterministic, no model).

Operator decision (see ``docs/recovery/disk-mode-autoreview.md``): the store
behaves like a disk — writes land immediately, no human approval queue. The
manual gates are replaced by a **mandatory synchronous auto-review** that runs
inside the write's own audit boundary (``store.commit(post_write=…)``), so
every write still produces exactly one final audit event — with the verdict
recorded in its ``result_references.auto_review``:

1. **Round-trip** — read back what was written and verify it matches the
   action's intent (the node exists, the version moved as the receipt claims,
   the content fingerprint is the written content's fingerprint).
2. **Badness scan** — deterministic pattern families over the written
   content: prompt-injection phrasing, secret shapes (the same pinned
   patterns the publish gate uses, over the raw text *and* the match view),
   and active markup that must never reach a rendering surface. No LLM, no
   network — pure rules.

A ``reject`` verdict compensates append-only *before* the failure audit
closes: a pre-existing node is restored to its prior revision (a new
``rollback`` revision), a node the rejected write created is retired and
tombstoned. The typed :class:`AutoReviewRejected` carries the reason codes in
``reasons`` and the verdict mapping in ``audit_references`` (recorded by
``failure_outcome``). The audit trail is the internal journal of record —
never a human gate.

Writing quality is the writer's business; the reviewer only verifies the
write landed as intended and carries nothing dangerous.

The legacy behavior (proposal queues, operator approvals) stays available
under ``KNOW_YOUR_CONTEXT_MANUAL_GATES=1``; the default is disk-mode.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from know_your_context.domain import Event, EventKind, LifecycleStatus, Node
from know_your_context.events import DomainReceipt
from know_your_context.improvement import content_fingerprint
from know_your_context.ops.switches import parse_switch_bool
from know_your_context.projection import _secret_category_scan  # noqa: SLF001 - same-package reuse of the pinned scan
from know_your_context.security import _redaction_match_view  # noqa: SLF001 - same-package reuse of the match view
from know_your_context.store import EventStore, StoreError

__all__ = [
    "ENV_MANUAL_GATES",
    "AutoReviewRejected",
    "AutoReviewVerdict",
    "compensate_node_write",
    "manual_gates_enabled",
    "review_node_write",
    "scan_node",
    "scan_text",
    "scan_value",
]

#: The environment flag that restores the legacy manual-gate behavior.
ENV_MANUAL_GATES = "KNOW_YOUR_CONTEXT_MANUAL_GATES"

#: Recursion/depth bounds for scanning JSON-shaped values.
_SCAN_MAX_DEPTH = 8
_SCAN_MAX_CHARS = 1_048_576


def manual_gates_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Resolve the gate mode; default is disk-mode (``False``).

    Strict parse (the owned-switch rule): only the documented boolean
    spellings are accepted; anything else raises
    :class:`~know_your_context.ops.switches.SwitchError` — fail closed at
    configuration time rather than silently picking a mode.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    raw = environ.get(ENV_MANUAL_GATES)
    if raw is None:
        return False
    return parse_switch_bool(ENV_MANUAL_GATES, raw)


class AutoReviewRejected(StoreError):
    """The post-write auto-review rejected the write (typed, compensated).

    ``audit_references`` carries the verdict mapping — ``failure_outcome``
    records it on the write invocation's single failure audit, so the
    rejection is queryable from the audit trail itself.
    """

    code = "AUTO_REVIEW_REJECTED"

    def __init__(
        self,
        message: str,
        *,
        reasons: tuple[str, ...] = (),
        audit_references: Mapping[str, Any] | None = None,
        audit_event_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reasons = tuple(reasons)
        self.audit_references = dict(audit_references or {})
        self.audit_event_id = audit_event_id


@dataclass(frozen=True)
class AutoReviewVerdict:
    """The deterministic outcome of one post-write review."""

    operation: str
    target: str
    verdict: str  # "pass" | "reject"
    reasons: tuple[str, ...]
    roundtrip_ok: bool

    def as_references(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "target": self.target,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "roundtrip_ok": self.roundtrip_ok,
        }


# ------------------------------------------------------------------- patterns
#
# Prompt-injection phrasing: agent-directed instructions embedded in content.
# Deliberately narrow and multi-word so prose like "ignore the previous
# section of this document" stays clean — every rule needs the instruction/
# override vocabulary, a chat-template marker, or an exfiltration verb.

_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override-instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,80}"
            r"\b(?:all|any|the|your|previous|prior|above|earlier|preceding)\b[^.\n]{0,80}"
            r"\b(?:instructions?|prompts?|rules?|directives?|guidelines?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "persona-hijack",
        re.compile(
            r"\b(?:you are now|act as|pretend (?:to be|you are)|from now on[,. ]+you (?:are|will|shall))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt-exfiltration",
        re.compile(
            r"\b(?:reveal|print|show|repeat|output|leak|display)\b[^.\n]{0,60}"
            r"\b(?:your|the)\b[^.\n]{0,40}"
            r"\b(?:system prompt|initial prompt|system message|hidden instructions?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "chat-template-marker",
        re.compile(r"<\|im_(?:start|end)\|>|\[INST\]|<<\s*SYS\s*>>|</?\s*system\s*>", re.IGNORECASE),
    ),
    (
        "jailbreak-keyword",
        re.compile(r"\b(?:jailbreak|do anything now|DAN mode)\b", re.IGNORECASE),
    ),
)

# Active markup: content that executes when a surface renders it. The webview
# is textContent-only, but the store outlives any one surface.
_ACTIVE_MARKUP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("script-tag", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("iframe-tag", re.compile(r"<\s*iframe\b", re.IGNORECASE)),
    ("js-scheme", re.compile(r"javascript\s*:", re.IGNORECASE)),
    ("event-handler", re.compile(r"\bon(?:error|click|load|focus|mouseover)\s*=", re.IGNORECASE)),
)


def _scan_view(text: str, view: str) -> list[str]:
    reasons: list[str] = []
    for name, pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(view):
            reasons.append(f"prompt-injection:{name}")
    for name, pattern in _ACTIVE_MARKUP_PATTERNS:
        if pattern.search(view):
            reasons.append(f"active-markup:{name}")
    category = _secret_category_scan(text)
    if category is not None:
        reasons.append(f"secret:{category}")
    return reasons


def scan_text(text: str) -> tuple[str, ...]:
    """Deterministic badness scan of one string; sorted typed reason codes.

    Patterns run over the raw text and — when it differs — the security
    match view (NFKC + separator/format strip + homoglyph fold), so secrets
    and injections split by zero-width or exotic separators still match.
    """
    if not text:
        return ()
    reasons = _scan_view(text, text)
    view = _redaction_match_view(text)
    if view != text:
        reasons.extend(_scan_view(view, view))
    return tuple(sorted(set(reasons)))


def scan_value(value: Any, *, _depth: int = 0, _budget: list[int] | None = None) -> tuple[str, ...]:
    """Recursive scan of every string inside a JSON-shaped value (bounded)."""
    budget = _budget if _budget is not None else [_SCAN_MAX_CHARS]
    reasons: list[str] = []
    if _depth > _SCAN_MAX_DEPTH or budget[0] <= 0:
        return ()
    if isinstance(value, str):
        budget[0] -= len(value)
        return scan_text(value[: max(0, min(len(value), budget[0] + len(value)))])
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            reasons.extend(scan_value(key, _depth=_depth + 1, _budget=budget))
            reasons.extend(scan_value(value[key], _depth=_depth + 1, _budget=budget))
        return tuple(sorted(set(reasons)))
    if isinstance(value, (list, tuple)):
        for item in value:
            reasons.extend(scan_value(item, _depth=_depth + 1, _budget=budget))
        return tuple(sorted(set(reasons)))
    return ()


def scan_node(node: Node) -> tuple[str, ...]:
    """Scan every free-text field of a node (content, not identity)."""
    reasons: list[str] = []
    for text in (node.summary, node.scope, *node.plus, *node.minus, *node.context_tags, *node.provenance):
        reasons.extend(scan_text(text))
    reasons.extend(scan_value(node.payload))
    return tuple(sorted(set(reasons)))


# ---------------------------------------------------------------- round-trip


def review_node_write(
    store: EventStore,
    *,
    operation: str,
    node_id: str,
    expected_fingerprint: str | None = None,
    expected_version: int | None = None,
) -> tuple[AutoReviewVerdict, Node | None]:
    """Verify one written node: round-trip first, then the badness scan.

    ``expected_fingerprint``/``expected_version`` pin the intent: the node
    read back must carry exactly the written content at the receipt's
    version. The content scan runs on what is actually stored.
    """
    reasons: list[str] = []
    roundtrip_ok = True
    node = store.current_node(node_id)
    if node is None:
        roundtrip_ok = False
        reasons.append("roundtrip:node-missing")
    else:
        if expected_version is not None and node.version != expected_version:
            roundtrip_ok = False
            reasons.append("roundtrip:version-mismatch")
        if expected_fingerprint is not None and content_fingerprint(node) != expected_fingerprint:
            roundtrip_ok = False
            reasons.append("roundtrip:content-mismatch")
        reasons.extend(scan_node(node))
    reasons = sorted(set(reasons))
    verdict = AutoReviewVerdict(
        operation=operation,
        target=node_id,
        verdict="pass" if not reasons else "reject",
        reasons=tuple(reasons),
        roundtrip_ok=roundtrip_ok,
    )
    return verdict, node


def _revision_node(store: EventStore, node_id: str, version: int) -> Node | None:
    with store.read() as conn:
        row = conn.execute(
            "SELECT payload_json FROM node_revision WHERE node_id = ? AND version = ?", (node_id, version)
        ).fetchone()
    return None if row is None else Node.model_validate(json.loads(str(row["payload_json"])))


def _tombstone_of(node: Node, at: datetime) -> Node:
    """The retired-and-tombstoned copy: history keeps the rejected revision
    (immutable audit), but the current view must not carry its content."""
    return node.model_copy(
        update={
            "payload": {},
            "summary": "",
            "scope": "",
            "plus": (),
            "minus": (),
            "context_tags": (),
            "provenance": ("auto-review:rejected",),
            "updated_at": at,
        }
    )


def compensate_node_write(
    store: EventStore,
    *,
    node_id: str,
    prior_version: int | None,
    actor: str,
    idempotency_key: str,
    at: datetime | None = None,
) -> DomainReceipt:
    """Undo a rejected node write, append-only: restore or retire.

    A node with a prior revision is restored to that revision's content as a
    new ``rollback`` revision (the improvement-rollback precedent); a node the
    rejected write created is retired. Idempotent on *idempotency_key*.
    """
    at = at or datetime.now(UTC)
    current = store.current_node(node_id)
    if current is None:
        raise StoreError(f"cannot compensate: node {node_id!r} is already absent")
    if prior_version is None or prior_version < 1:
        # A node the rejected write created is retired *and tombstoned*.
        event = Event(
            kind=EventKind.NODE_RETIRE,
            node_id=node_id,
            domain=current.domain,
            node=_tombstone_of(current, at),
            source_version_id=current.source_version_id,
            actor=actor,
            occurred_at=at,
            payload={"auto_review_compensation": {"node_id": node_id, "mode": "retire"}},
        )
        return store.append_domain(event, idempotency_key=idempotency_key, expected_version=current.version)
    revision = _revision_node(store, node_id, prior_version)
    if revision is None:
        raise StoreError(f"cannot compensate: node {node_id!r} has no revision {prior_version}")
    restored = revision.model_copy(update={"updated_at": at, "lifecycle_status": LifecycleStatus.ROLLBACK})
    event = Event(
        kind=EventKind.NODE_UPSERT,
        node_id=node_id,
        domain=current.domain,
        node=restored,
        source_version_id=current.source_version_id,
        actor=actor,
        occurred_at=at,
        payload={
            "auto_review_compensation": {
                "node_id": node_id,
                "mode": "restore",
                "to_version": prior_version,
            }
        },
    )
    return store.append_domain(event, idempotency_key=idempotency_key, expected_version=current.version)
