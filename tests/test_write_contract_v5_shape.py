"""Shape tests for the v5 write contract addendum (SUB-153).

The addendum (``docs/contracts/write-contract-v5.md``) is frozen upon
publication: these tests pin its structure — the disk-mode write posture,
the explicit supersession of the v3/v4 proposal-only write path, the
mandatory post-write auto-review scope and pattern families, the typed
reject errors, the relearn carve-out and the legacy-mode escape hatch —
and prove the v1/v2/v3/v4 contracts in the same directory stay
byte-identical via pinned sha256 digests.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "docs" / "contracts"
V5 = CONTRACTS / "write-contract-v5.md"

# Pinned digests of the frozen contracts (v1 + v2 + v3 + v4). A single byte
# of drift in any of these files fails the pin — the addendum mechanism
# exists precisely so these documents never change.
FROZEN_CONTRACTS: dict[str, str] = {
    "access-policy.md": "68cdbaf88ef5e85dfc868bb35452c8f4b431b577fd69ff3bd3eac422e654d24e",
    "recommendation-contract.md": "811800cad1233ce14031f6a1e54220477bbda097aeea5e3e8d5b8827b2b1fe82",
    "relearn-contract.md": "24b6223eb4be813a9a4809147dce8dc30af17717ca3137727cae7194e58e6579",
    "retrieval-verbs-v2.md": "51541fa68e029a4b9c2130188e862cd11a5e27b31db30e596b378718af76d6da",
    "ingestion-contract-v3.md": "952a0f6dd0344374d299237ce1236e0c228b574a291bafe83ee02c0c27ea1467",
    "extraction-contract-v4.md": "6276ec6f11bbdeea087195339330bc21779f160f4fca424b688adbdb912bf627",
}

# The v5 document itself is frozen upon publication — pinned byte-exact.
V5_DIGEST = "1e63f5fd18b6d958a9b114025107909606a69c338c7f8bf612a397fdbba28907"


@pytest.fixture(scope="module")
def text() -> str:
    assert V5.is_file(), "the v5 write contract must exist"
    # Whitespace-normalized: prose wraps at the margin, so phrase pins match
    # against a single-space-folded rendering.
    return re.sub(r"\s+", " ", V5.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------- envelope


def test_addendum_file_exists() -> None:
    assert V5.is_file(), "docs/contracts/write-contract-v5.md is missing"


def test_contract_version_header(text: str) -> None:
    assert "Contract version: 5.0.0 (addendum)" in text


def test_status_is_active_and_dated(text: str) -> None:
    assert "Status: ACTIVE (2026-11-08)" in text


def test_authority_references_the_operator_decision(text: str) -> None:
    assert "SUB-151" in text
    assert "docs/recovery/disk-mode-autoreview.md" in text


def test_scope_declares_the_prior_contracts_unchanged(text: str) -> None:
    assert "byte-identical" in text
    assert "unchanged" in text


def test_frozen_upon_publication(text: str) -> None:
    assert "frozen upon publication" in text


def test_changes_require_a_new_contract_version(text: str) -> None:
    assert "requires a new contract version and operator approval" in text


@pytest.mark.parametrize("name", sorted(FROZEN_CONTRACTS))
def test_prior_contracts_stay_byte_identical(name: str) -> None:
    path = CONTRACTS / name
    assert path.is_file(), f"{name} is missing"
    assert _sha256(path) == FROZEN_CONTRACTS[name], f"{name} drifted from its frozen digest"


def test_v5_document_is_byte_frozen() -> None:
    assert _sha256(V5) == V5_DIGEST, (
        "write-contract-v5.md drifted from its frozen digest — changes belong in a new contract version"
    )


# ------------------------------------------------------------- disk-mode


def test_disk_mode_is_the_default_posture(text: str) -> None:
    assert "the store is a disk" in text
    assert "Every write lands immediately" in text
    assert "The default is disk-mode" in text


def test_no_human_or_agent_gate_in_the_write_path(text: str) -> None:
    assert "no human or agent approval anywhere" in text


def test_writes_land_in_one_invocation(text: str) -> None:
    for verb in ("`log_event`", "`record_outcome`", "`apply_improvement`", "`submit_extraction`"):
        assert verb in text, verb
    assert "`ingest_file`/`ingest_directory`" in text


def test_append_only_compensation_never_deletes_history(text: str) -> None:
    assert "never rewritten" in text
    assert "compensation" in text


def test_deterministic_and_model_free(text: str) -> None:
    assert "no LLM" in text
    assert "pure function of the stored bytes" in text


# ------------------------------------------------------------- supersession


def test_supersession_is_explicit(text: str) -> None:
    assert "supersedes" in text
    assert "for the write lane only" in text


def test_v4_proposal_only_pin_is_quoted_and_superseded(text: str) -> None:
    assert "Nothing here writes the canonical graph" in text
    assert "Superseded for the write lane" in text
    assert 'decided_by="auto-review"' in text


def test_manual_gates_flag_restores_v4_behavior(text: str) -> None:
    assert "`KNOW_YOUR_CONTEXT_MANUAL_GATES=1`" in text
    assert "`pending_review`" in text
    assert "`ApprovalRecord`" in text
    assert "fails closed at configuration time" in text


# ------------------------------------------------------------------- scope


def test_review_is_scoped_to_change_actions_only(text: str) -> None:
    assert "only for change actions" in text
    assert "create, edit or rewrite" in text


def test_reads_are_never_reviewed(text: str) -> None:
    section = text.split("change actions only")[1]
    assert "never reviewed" in section
    for verb in ("`query`", "`get_node`", "`get_subtree`"):
        assert verb in section, verb


def test_evaluate_is_out_of_scope(text: str) -> None:
    assert "`evaluate`" in text


# ------------------------------------------------------------------ review


def test_review_runs_inside_the_write_audit_boundary(text: str) -> None:
    assert "store.commit(post_write=…)" in text
    assert "exactly one final audit event per invocation" in text
    assert "`result_references.auto_review`" in text


def test_roundtrip_reason_codes_are_pinned(text: str) -> None:
    for code in ("roundtrip:node-missing", "roundtrip:version-mismatch", "roundtrip:content-mismatch"):
        assert code in text, code


def test_prompt_injection_five_families_are_pinned(text: str) -> None:
    assert "five families" in text
    for family in (
        "override-instructions",
        "persona-hijack",
        "prompt-exfiltration",
        "chat-template-marker",
        "jailbreak-keyword",
    ):
        assert family in text, family


def test_secret_and_active_markup_families_are_pinned(text: str) -> None:
    assert "`SECRET_PATTERNS`" in text
    assert "secret:<category>" in text
    for name in ("script-tag", "iframe-tag", "js-scheme", "event-handler"):
        assert name in text, name


def test_scan_covers_raw_text_and_match_view(text: str) -> None:
    assert "security match view" in text
    assert "zero-width" in text


def test_pass_is_done_fail_compensates(text: str) -> None:
    assert "Pass = done" in text
    assert "Fail = automatic compensation plus a typed error" in text
    assert "retired and tombstoned" in text
    assert "cascade-undo" in text


def test_reviewer_never_judges_quality(text: str) -> None:
    assert "Writing quality is the writer's business" in text


def test_kill_criterion_returns_to_the_queue(text: str) -> None:
    assert "Kill criterion" in text
    assert "no-AI boundary is not negotiable" in text


# ------------------------------------------------------------- typed errors


@pytest.mark.parametrize(
    "code",
    ["AUTO_REVIEW_REJECTED", "EXTRACTION_AUTO_REJECTED", "INGEST_AUTO_REVIEW_REJECTED"],
)
def test_typed_reject_errors_are_pinned(text: str, code: str) -> None:
    assert f"`{code}`" in text, code


def test_http_adapter_surfaces_typed_422(text: str) -> None:
    assert "422" in text


# ----------------------------------------------------------------- relearn


def test_relearn_stays_operator_gated(text: str) -> None:
    section = text.split("Relearn stays operator-gated")[1]
    assert "operator-gated in both modes" in section
    assert "relearn-contract.md" in section
    assert "store-wide trust decision" in section


# ------------------------------------------------------------------ journal


def test_verdict_is_queryable_from_the_audit_trail(text: str) -> None:
    assert "error.audit_references" in text
    assert "a journal, never a queue" in text


def test_webview_proposals_tab_is_the_review_history(text: str) -> None:
    assert "Proposals" in text


def test_rollback_guarantee_is_unchanged(text: str) -> None:
    assert "Byte-exact" in text
    assert "knowledge.rollback" in text


def test_no_new_verbs(text: str) -> None:
    assert "adds no verbs" in text
