# Write Contract (v5) — Disk-Mode Writes and Auto-Review

Contract version: 5.0.0 (addendum)
Status: ACTIVE (2026-11-08)
Authority: operator decision recorded in
`docs/recovery/disk-mode-autoreview.md` (SUB-151): the product is a store
that behaves like a disk — every manual gate/approval is removed from the
write path and replaced by a deterministic post-write auto-review; the
removal is the directive itself, not a conflict with the frozen contracts.
Documented as a contract under SUB-153.
Scope: this addendum **supersedes** the proposal-only / approval-gated
write-path posture pinned by the v3 ingestion addendum and the v4
extraction addendum **for the write lane only**, and pins the disk-mode
write semantics plus the mandatory post-write auto-review. The v1, v2, v3
and v4 documents in this directory are **unchanged** and remain
byte-identical — they are frozen historical records; where their prose
conflicts with the semantics below, this contract governs the running
system. The seventeen existing operations keep their names and shapes.

Any change to the semantics below requires a new contract version and
operator approval. This document is frozen upon publication.

## Design posture: the store is a disk

- **Every write lands immediately.** `log_event`, `record_outcome`,
  `propose_improvement`/`apply_improvement`, `submit_extraction`,
  `ingest_file`/`ingest_directory` commit in one invocation — the receipt
  is the answer. There is no queue and no human or agent approval anywhere
  in the write path. An agent using the store writes, reads back, and
  moves on.
- **Append-only stands.** The event log is never rewritten. A rejected
  write is undone by compensation — a new event — never by deleting
  history.
- **Deterministic and model-free.** The review is hashes and pinned
  patterns; no LLM, no network, no provider call. The review runs on the
  same lane as the write, synchronously, inside the write's own audit
  boundary.
- **The reviewer's job is narrow.** Writing quality is the writer's
  business; the review verifies the write landed as intended and carries
  nothing dangerous.

## Supersession of the v3/v4 write-path pinning (explicit)

- The v4 extraction addendum pins "Nothing here writes the canonical
  graph" — application exclusively through the operator-run
  `extraction approve` surface. **Superseded for the write lane**: in
  disk-mode, `submit_extraction` validates, auto-reviews and applies in a
  single invocation (`status: applied`, `decided_by="auto-review"`).
- The v3 ingestion addendum pins proposals applied "exclusively through
  the existing operator-gated propose/apply path". **Superseded for the
  write lane**: `ingest_file`/`ingest_directory` apply directly under
  auto-review.
- The v1–v4 documents remain byte-identical in this directory, digest-
  pinned by test; they record the posture they froze and are not edited.
- **Escape hatch**: `KNOW_YOUR_CONTEXT_MANUAL_GATES=1` (strict boolean
  parse; an invalid value fails closed at configuration time) restores the
  v3/v4-pinned behavior exactly — extraction submissions queue as
  `pending_review` for the operator `extraction approve|reject` surface,
  and high-impact improvement applies require an operator
  `ApprovalRecord`. The default is disk-mode.

## What is reviewed — change actions only

Auto-review is mandatory **only for change actions** — operations that
create, edit or rewrite stored content:

- `log_event`, `record_outcome`, `apply_improvement` (knowledge service);
- `submit_extraction` (disk-mode apply);
- `ingest_file`, `ingest_directory` (per file).

Read and information-extraction actions are **never reviewed**: `query`,
`get_node`, `get_subtree`, the v2 retrieval verbs, analytics, exports and
reports change nothing, so there is nothing to verify or compensate.
`evaluate` writes only computed scores over content that was already
reviewed when it entered the store, and is likewise out of scope.

## The review (mandatory, synchronous, deterministic)

The review runs inside the write's own audit boundary
(`store.commit(post_write=…)`), so exactly one final audit event per
invocation is preserved — the verdict rides the write's audit as
`result_references.auto_review` (`{verdict, reasons, roundtrip_ok,
operation, target}`):

1. **Round-trip verify** — read back what was written and check it
   against the action's intent: the node exists, the version moved as the
   receipt claims, the content fingerprint equals the written content's
   fingerprint. Failure reason codes: `roundtrip:node-missing`,
   `roundtrip:version-mismatch`, `roundtrip:content-mismatch`.
2. **Badness scan** — deterministic pattern families over every free-text
   field of what is actually stored, over the raw text **and** the
   security match view (NFKC + separator/format strip + homoglyph fold),
   so injections and secrets split by zero-width or exotic separators
   still match:
   - **Prompt injection, five families** — `override-instructions`,
     `persona-hijack`, `prompt-exfiltration`, `chat-template-marker`,
     `jailbreak-keyword` (reason codes `prompt-injection:<family>`);
   - **Secrets** — the pinned `SECRET_PATTERNS` categories (reason code
     `secret:<category>`);
   - **Active markup** — `script-tag`, `iframe-tag`, `js-scheme`,
     `event-handler` (reason codes `active-markup:<name>`);
   - any further bad-method detection joins this list as typed reason
     codes — never as a model call.

**Pass = done.** The write stays, the receipt stands, the verdict journals.

**Fail = automatic compensation plus a typed error.** Before the failure
audit closes, append-only: a pre-existing node is restored to its prior
revision (a new `rollback` revision); a node the rejected write created is
retired and tombstoned (the current view never carries the rejected
content); a rejected extraction submission is never applied (queue row
`rejected`, `decided_by="auto-review"`); a rejected ingested file's rows
are removed by the bounded cascade-undo. The caller receives a typed
error, never an exception on the wire:

- `AUTO_REVIEW_REJECTED` — knowledge service verbs;
- `EXTRACTION_AUTO_REJECTED` — `submit_extraction`;
- `INGEST_AUTO_REVIEW_REJECTED` — `ingest_file`/`ingest_directory`;
- the HTTP adapter surfaces all three as a typed 422 envelope.

**Kill criterion**: if accurate badness detection ever proves to require a
model, the lane returns to the queued posture via the flag — the no-AI
boundary is not negotiable.

## Relearn stays operator-gated

Relearn (`relearn-scan` / `relearn-apply`, the RESET/ACCUMULATE decision)
remains operator-gated in both modes (operator decision, SUB-151).
Rationale: relearn rewrites the evidence base of the entire store — it is
a store-wide trust decision, not a content write, so a content-level
reviewer cannot stand in for the operator's intent to reset or accumulate
accumulated trust; the mode choice is pinned by the frozen
`relearn-contract.md` (byte-frozen, digest-pinned by test), and loosening
it requires a new version of that contract, not a clause in this one.

## Shared guarantees

- Exactly one final audit event per invocation, pass and reject alike; the
  verdict is queryable from the audit trail itself
  (`result_references.auto_review` on success, `error.audit_references` on
  failure) and the webview **Proposals** tab renders the auto-review
  history — a journal, never a queue.
- Deterministic across processes and hash seeds: the review outcome is a
  pure function of the stored bytes and the pinned patterns.
- Byte-exact `knowledge.rollback` of any applied write is unchanged; the
  compensation path reuses the same append-only machinery.

## Adapter mapping

This addendum adds no verbs; the CLI, HTTP and MCP surfaces keep their
existing names and shapes. Rejections surface as the typed errors above:
CLI exit 1 with the error code, HTTP 422 typed envelope, MCP typed error.
