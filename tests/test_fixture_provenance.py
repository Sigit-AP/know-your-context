"""Provenance-chain fixtures (SUB-129).

``tests/fixtures/provenance/`` holds real source files whose bytes bind the
extraction-submission provenance chain (source sha256 → payload hash →
capability actor → submission event → node event). Every fixture drives the
real submit → approve → verify flow and the chain must verify end to end.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from _extraction_helpers import make_args, make_method, make_payload, seed_engine
from know_your_context.extraction.engine import SourceHashMismatchError
from know_your_context.security.provenance import verify_provenance

PROV_ROOT = Path(__file__).parent / "fixtures" / "provenance"
FILES = tuple(json.loads((PROV_ROOT / "files.json").read_text(encoding="utf-8"))["files"])

OPERATOR = "operator-fixture"


def _run_chain(tmp_path: Path, fixture_name: str, *, tamper: bool = False):
    engine, service, store, queue, actor = seed_engine(tmp_path)
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    data = (PROV_ROOT / fixture_name).read_bytes()
    if tamper:
        data = data + b" tampered"
    (root / fixture_name).write_bytes(data)
    source_sha256 = hashlib.sha256(data).hexdigest()
    args = make_args(
        source_sha256=source_sha256,
        source_path=fixture_name,
        payload=make_payload(text=f"fixture {fixture_name}"),
        method=make_method(agent="w15-fixture"),
    )
    receipt = engine.submit(args, actor=actor, extraction_root=root)
    decision = engine.approve(receipt["receipt_id"], operator=OPERATOR, service=service)
    return store, list(decision["applied_nodes"])


@pytest.mark.parametrize("fixture_name", FILES, ids=lambda n: n.removesuffix(".txt"))
def test_fixture_chain_verifies(tmp_path: Path, fixture_name: str) -> None:
    store, node_ids = _run_chain(tmp_path, fixture_name)
    assert node_ids, f"{fixture_name}: no nodes applied"
    for node_id in node_ids:
        report = verify_provenance(store, node_id)
        assert report.ok, f"{fixture_name}: chain broken at {report.broken_link}"


def test_resubmit_with_stale_hash_after_tamper_is_rejected(tmp_path: Path) -> None:
    """Tampering the fixture file on disk invalidates the previously bound
    source hash: a resubmission with the original hash fails closed."""
    engine, _service, _store, _queue, actor = seed_engine(tmp_path)
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    fixture = PROV_ROOT / "field-note-gamma.txt"
    original = fixture.read_bytes()
    target = root / "field-note-gamma.txt"
    target.write_bytes(original)
    original_hash = hashlib.sha256(original).hexdigest()
    args = make_args(source_sha256=original_hash, source_path="field-note-gamma.txt")
    engine.submit(args, actor=actor, extraction_root=root)
    # tamper the on-disk copy, then resubmit with the stale hash
    target.write_bytes(original + b" tampered")
    with pytest.raises(SourceHashMismatchError):
        engine.submit(args, actor=actor, extraction_root=root)


def test_wrong_source_hash_is_rejected_at_submit(tmp_path: Path) -> None:
    engine, _service, store, _queue, actor = seed_engine(tmp_path)
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    fixture = PROV_ROOT / "field-note-beta.txt"
    (root / "field-note-beta.txt").write_bytes(fixture.read_bytes())
    wrong = hashlib.sha256(b"not the fixture").hexdigest()
    args = make_args(source_sha256=wrong, source_path="field-note-beta.txt")
    with pytest.raises(SourceHashMismatchError):
        engine.submit(args, actor=actor, extraction_root=root)
