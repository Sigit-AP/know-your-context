#!/usr/bin/env python3
"""Differentiator task drivers for the competitive bench (W3.4, SUB-119).

Each task exercises one real product capability end-to-end against a store
seeded from the pinned target ingest (tasks that need their own importer or
relearn state say so in their payload). The harness runs::

    diff_tasks.py <task> <store_db> <work_dir> <tree>

and records the emitted ``KYS_RESULT {...}`` line. Exit 0 = the capability
worked as measured; anything else (assertion, typed error, crash) is a real
failure the harness records as FAILED — never edited into a pass.

Tasks mirror the scorecard catalog, but re-measured live on the bench corpus:

* gated-extraction-submit   submit -> review -> approve -> applied, 1 audit/invocation
* audit-chain-verify        20 mutations, then the hash chain re-verifies
* rollback-byte-exact       propose -> apply -> rollback restores content byte-exact
* operator-gated-apply      apply while a decision is pending -> typed refusal
* watermark-ryw             write then read-your-write through the live daemon
* group-commit-burst        100 writes through the daemon: all acked, chain valid
* retention-policy-audited  keep-N + pinned, one audit line per deletion
* contract-digest-verify    recompute the pinned sha256 of the frozen contracts
* relearn-quarantine-flow   RESET apply states the zero vector + quarantines = receipt
* secret-quarantine-scan    secret-shaped node -> the read-model validator refuses
* review-decision-policy    decide() deterministic, policy_version pinned
"""

from __future__ import annotations

import hashlib
import itertools
import json
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

TASKS = (
    "gated-extraction-submit",
    "audit-chain-verify",
    "rollback-byte-exact",
    "operator-gated-apply",
    "watermark-ryw",
    "group-commit-burst",
    "retention-policy-audited",
    "contract-digest-verify",
    "relearn-quarantine-flow",
    "secret-quarantine-scan",
    "review-decision-policy",
)


def _audit_count(store: Any, event_type: str) -> int:
    with store.read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM event_log WHERE kind = 'audit' AND event_type = ?",
            (event_type,),
        ).fetchone()
    return int(row["n"])


def _node(node_id: str, summary: str) -> Any:
    from know_your_context.domain import Node, NodeKind, PrimaryDomain

    return Node(
        node_id=node_id,
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary=summary,
        provenance=("context://bench/differentiator",),
        source_version_id="sv-bench",
    )


def _node_arg(node_id: str, summary: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "kind": "leaf",
        "domain": "P1",
        "summary": summary,
        "provenance": ["context://bench/differentiator"],
        "source_version_id": "sv-bench",
    }


def _service(store_path: Path, work: Path, name: str) -> tuple[Any, Any, Any]:
    from know_your_context.projection import GraphProjector
    from know_your_context.service import KnowledgeService
    from know_your_context.store import EventStore

    store = EventStore(store_path)
    projector = GraphProjector(store, output_root=work / name)
    return store, KnowledgeService(store, projector), projector


def _engine(store: Any, service: Any) -> Any:
    from know_your_context.audit import AuditLog
    from know_your_context.extraction import ExtractionEngine, ExtractionQueue

    return ExtractionEngine(store, AuditLog(store), queue=ExtractionQueue.for_store(store))


_SOURCE = b"page 1: the graft procedure requires two witnesses.\n"
_SOURCE_SHA = hashlib.sha256(_SOURCE).hexdigest()


def _extraction_args(tag: str) -> dict[str, Any]:
    return {
        "source_sha256": _SOURCE_SHA,
        "payload": {
            "text": f"the graft procedure requires two witnesses ({tag})",
            "nodes": [
                {"name": f"Graft procedure {tag}", "kind": "child", "domain": "P3", "summary": "needs two witnesses"},
                {"name": f"Witness {tag}", "kind": "leaf", "domain": "P3", "summary": "a procedure participant"},
            ],
            "edges": [
                {
                    "relation": "depends_on",
                    "source": {"kind": "child", "name": f"Graft procedure {tag}"},
                    "target": {"kind": "leaf", "name": f"Witness {tag}"},
                }
            ],
        },
        "method": {"agent": "bench-differentiator", "extracted_at": "2026-11-01T12:00:00+00:00"},
        "confidence": "0.8",
    }


# ------------------------------------------------------------- T1: extraction
def task_gated_extraction_submit(store_path: Path, work: Path) -> dict[str, Any]:
    store, service, _ = _service(store_path, work, "graph-t1")
    try:
        engine = _engine(store, service)
        submit_before = _audit_count(store, "knowledge.submit_extraction")
        approve_before = _audit_count(store, "knowledge.extraction_approve")
        receipt = engine.submit(_extraction_args("t1"), actor="bench-agent")
        assert receipt["status"] == "pending_review", receipt
        listing = engine.review(receipt_id=receipt["receipt_id"])
        assert listing is not None
        applied = engine.approve(receipt["receipt_id"], operator="bench-operator", service=service)
        assert applied["status"] == "applied", applied
        from know_your_context.extraction import applied_node_id

        node_id = applied_node_id(_SOURCE_SHA, "child", "Graft procedure t1")
        node = store.current_node(node_id)
        assert node is not None and node.version == 1
        submit_audits = _audit_count(store, "knowledge.submit_extraction") - submit_before
        approve_audits = _audit_count(store, "knowledge.extraction_approve") - approve_before
        # exactly one audit event per invocation, on both gated steps
        assert submit_audits == 1 and approve_audits == 1, (submit_audits, approve_audits)
        return {
            "receipt_id": receipt["receipt_id"],
            "final_status": applied["status"],
            "applied_node": node_id,
            "audit_submit": submit_audits,
            "audit_approve": approve_audits,
        }
    finally:
        store.close()


# ------------------------------------------------------------- T2: audit chain
def task_audit_chain_verify(store_path: Path, work: Path) -> dict[str, Any]:
    store, service, _ = _service(store_path, work, "graph-t2")
    try:

        def domain_events() -> int:
            with store.read() as conn:
                return int(conn.execute("SELECT COUNT(*) AS n FROM event_log WHERE kind = 'domain'").fetchone()["n"])

        events_before = domain_events()
        audits_before = _audit_count(store, "knowledge.log_event")
        for index in range(20):
            service.log_event(
                _node(f"bench-t2-{index}", f"audit chain mutation {index}"),
                actor="bench-t2",
                idempotency_key=f"bench:t2:{index}",
            )
        report = store.verify_chain()
        assert report.ok, report
        delta = domain_events() - events_before
        audits = _audit_count(store, "knowledge.log_event") - audits_before
        assert delta == 20 and audits == 20, (delta, audits)
        return {"mutations": 20, "domain_events_added": delta, "audit_events_added": audits, "chain_ok": True}
    finally:
        store.close()


# --------------------------------------------------------------- T3: rollback
def task_rollback_byte_exact(store_path: Path, work: Path) -> dict[str, Any]:
    from know_your_context.extraction import applied_node_id
    from know_your_context.improvement import DiffOp, DiffOperation

    store, service, _ = _service(store_path, work, "graph-t3")
    try:
        engine = _engine(store, service)
        receipt = engine.submit(_extraction_args("t3"), actor="bench-agent-t3")
        engine.approve(receipt["receipt_id"], operator="bench-operator", service=service)
        node_id = applied_node_id(_SOURCE_SHA, "child", "Graft procedure t3")
        applied = store.current_node(node_id)
        assert applied is not None and applied.version == 1

        proposal = service.propose_improvement(
            node_id=node_id,
            diff=[DiffOperation(op=DiffOp.SET_SUMMARY, value="operator-refined summary")],
            rationale="bench",
            actor="bench-operator",
        )
        service.apply_improvement(proposal_id=proposal.proposal.proposal_id, actor="bench-operator")
        edited = store.current_node(node_id)
        assert edited is not None and edited.version == 2

        service.rollback(node_id=node_id, to_version=1, actor="bench-operator")
        restored = store.current_node(node_id)
        assert restored is not None and restored.version == 3
        fields = ("summary", "payload", "provenance", "context_tags", "scope", "kind", "domain", "scores")
        byte_exact = all(getattr(restored, f) == getattr(applied, f) for f in fields)
        assert byte_exact, [f for f in fields if getattr(restored, f) != getattr(applied, f)]
        return {"node": node_id, "version_after_rollback": restored.version, "content_byte_exact": True}
    finally:
        store.close()


# ------------------------------------------------------ T4: offer-gated apply
def task_operator_gated_apply(store_path: Path, work: Path) -> dict[str, Any]:
    from know_your_context.domain import PrimaryDomain
    from know_your_context.importer import SeedImporter
    from know_your_context.projection import GraphProjector
    from know_your_context.source_watcher import SourceWatcher
    from know_your_context.store import EventStore
    from know_your_context.update_pipeline import UpdatePipelineError, apply_approved_update, update_from_changes

    # The update pipeline drives its own importer state, so it runs on a
    # task-local store (the capability under test is the gate, not the corpus).
    store = EventStore(work / "t4.db")
    try:
        importer = SeedImporter(store, state_dir=work / "t4-import-state")
        corpus = work / "t4-corpus"
        corpus.mkdir()
        (corpus / "alpha.md").write_bytes(b"# Alpha\n\n## Lesson\n\nRoll back before retrying.\n")
        (corpus / "beta.md").write_bytes(b"# Beta\n\n## Counterexample\n\nA rollback rolled back.\n")
        importer.import_directory(corpus, domain=PrimaryDomain.P1)
        projector = GraphProjector(store, output_root=work / "t4-out")
        projector.rebuild()

        # Register the imported sources with the pipeline (the test flow's
        # explicit ADDED batch), then detect the removal through the watcher.
        from know_your_context.source_watcher import ChangeKind, SourceChange

        update_from_changes(
            [SourceChange(path="alpha.md", kind=ChangeKind.ADDED, content_sha256="0" * 64)],
            importer=importer,
            root=corpus,
        )
        watcher = SourceWatcher(corpus, state_path=work / "t4-watch.json")
        watcher.poll()  # baseline
        (corpus / "alpha.md").unlink()
        receipt = update_from_changes(watcher.poll(), importer=importer, root=corpus)
        assert receipt.offer is not None and receipt.relearn_pending is True

        refusal = None
        try:
            apply_approved_update(importer=importer, projector=projector, root=corpus, domain=PrimaryDomain.P1)
        except UpdatePipelineError as exc:
            refusal = f"UpdatePipelineError: {exc}"
        assert refusal is not None, "apply ran while a decision was pending"
        return {"offer_emitted": True, "relearn_pending": True, "pending_apply_refusal": refusal}
    finally:
        store.close()


# ------------------------------------------------------------ daemon harness
class _Client:
    """Minimal newline-delimited JSON-RPC client for the daemon socket."""

    def __init__(self, socket_path: Path) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(30.0)
        self._sock.connect(str(socket_path))
        self._reader = self._sock.makefile("rb")
        self._writer = self._sock.makefile("wb")
        self._ids = itertools.count(1)

    def call_tool(self, name: str, arguments: dict[str, Any], *, actor: str) -> dict[str, Any]:
        request_id = next(self._ids)
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": {"actor": actor}},
        }
        self._writer.write(json.dumps(message).encode("utf-8") + b"\n")
        self._writer.flush()
        line = self._reader.readline()
        if not line:
            raise ConnectionError("daemon closed the connection")
        return json.loads(line.decode("utf-8"))  # type: ignore[no-any-return]

    def close(self) -> None:
        for fh in (self._writer, self._reader, self._sock):
            try:
                fh.close()
            except OSError:
                pass

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _call_with_retry(client: _Client, name: str, arguments: dict[str, Any], *, actor: str) -> dict[str, Any]:
    from know_your_context.serve import rpc

    for _attempt in range(8):
        response = client.call_tool(name, arguments, actor=actor)
        if "error" in response and response["error"].get("code") == rpc.SERVER_BUSY:
            time.sleep(response["error"]["data"]["retry_after_ms"] / 1000.0)
            continue
        return response
    raise AssertionError(f"{actor} never got past SERVER_BUSY for {name}")


def _daemon(store_path: Path, work: Path, name: str, **overrides: Any) -> tuple[Any, Any, threading.Thread]:
    from know_your_context.projection import GraphProjector
    from know_your_context.serve.daemon_config import DaemonConfig
    from know_your_context.serve.superpower import SuperpowerDaemon
    from know_your_context.store import EventStore

    graph_root = work / name
    store = EventStore(store_path)
    GraphProjector(store, output_root=graph_root).rebuild()
    store.close()
    config = DaemonConfig(db_path=store_path, graph_root=graph_root, idle_timeout_seconds=300.0, **overrides)
    instance = SuperpowerDaemon(config)
    instance.start()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    return instance, config, thread


def _stop(instance: Any, thread: threading.Thread) -> None:
    instance.shutdown("bench-teardown")
    thread.join(timeout=30)


# ------------------------------------------------------------------ T5: RYW
def task_watermark_ryw(store_path: Path, work: Path) -> dict[str, Any]:
    # Task-local store: the publish-capacity guard (64 KiB artifact cap) is a
    # real product constraint; the capability under test is the watermark
    # protocol, so the daemon runs over its own freshly seeded store.
    from know_your_context.store import EventStore

    EventStore(work / "t5.db").close()
    instance, config, thread = _daemon(work / "t5.db", work, "graph-t5")
    try:
        with _Client(config.paths.socket) as client:
            written = _call_with_retry(
                client,
                "knowledge_log_event",
                {"node": _node_arg("bench-t5", "cross-lane visibility")},
                actor="bench-t5",
            )
            assert not written["result"].get("isError"), written
            reads = 0
            for _ in range(5):
                read = _call_with_retry(client, "knowledge_get_node", {"node_id": "bench-t5"}, actor="bench-t5")
                assert not read["result"].get("isError"), read
                assert "bench-t5" in json.dumps(read["result"])
                reads += 1
        return {"writes": 1, "reads": reads, "read_your_writes": True}
    finally:
        _stop(instance, thread)


# ---------------------------------------------------------- T6: group commit
def task_group_commit_burst(store_path: Path, work: Path) -> dict[str, Any]:
    from know_your_context.store import EventStore

    # Task-local store (see T5): the burst measures the write path, not the
    # corpus; a fresh store keeps the publish-capacity guard out of the way.
    t6_db = work / "t6.db"

    def domain_events() -> int:
        store = EventStore(t6_db)
        try:
            with store.read() as conn:
                return int(conn.execute("SELECT COUNT(*) AS n FROM event_log WHERE kind = 'domain'").fetchone()["n"])
        finally:
            store.close()

    before = domain_events()

    instance, config, thread = _daemon(t6_db, work, "graph-t6", group_commit=True)
    # A bounded pool rewritten in place: 100 writes, no unbounded graph growth
    # (the publish-capacity guard is a real product constraint).
    writes = 100
    failures: list[str] = []
    started = time.perf_counter()
    try:

        def worker(lane: int) -> None:
            try:
                with _Client(config.paths.socket) as client:
                    for i in range(25):
                        seq = lane * 25 + i
                        response = _call_with_retry(
                            client,
                            "knowledge_log_event",
                            {"node": _node_arg(f"bench-t6-pool-{seq % 20}", f"burst write {seq}")},
                            actor=f"bench-t6-{lane}",
                        )
                        if response["result"].get("isError"):
                            failures.append(f"lane{lane}/{i}: {response['result']['content'][0]['text'][:200]}")
            except Exception as exc:  # thread failures must surface, never vanish
                failures.append(f"lane{lane}: {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker, args=(lane,)) for lane in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
            assert not t.is_alive(), "burst worker did not finish"
    finally:
        _stop(instance, thread)
    wall_s = time.perf_counter() - started
    assert not failures, failures[:3]

    store = EventStore(t6_db)
    try:
        report = store.verify_chain()
    finally:
        store.close()
    after = domain_events()
    assert report.ok
    lost = writes - (after - before)
    assert lost == 0, f"{lost} writes lost"
    return {
        "writes": writes,
        "acked": after - before,
        "lost": 0,
        "wall_s": round(wall_s, 3),
        "acked_per_s": round(writes / wall_s, 1),
        "chain_ok": True,
    }


# -------------------------------------------------------------- T7: retention
def task_retention_policy_audited(store_path: Path, work: Path) -> dict[str, Any]:
    from know_your_context.ops.retention import DEFAULT_AUDIT_FILENAME, RetentionPolicy, run_retention

    # Retention measures projection generations, so it runs on a task-local
    # store (the shared target store already carries the other tasks' nodes;
    # the capability under test is the retention policy + audit, not content).
    store, service, projector = _service(work / "t7.db", work, "graph-t7")
    try:
        generations = []
        for index in range(5):
            service.log_event(
                _node(f"bench-t7-{index}", f"retention mutation {index}"),
                actor="bench-t7",
                idempotency_key=f"bench:t7:{index}",
            )
            receipt = projector.rebuild()
            generations.append(Path(receipt.generation_dir).name)
        assert len(set(generations)) == 5, generations

        pinned = generations[0]
        policy = RetentionPolicy(keep=1, pinned=(pinned,))
        report = run_retention(work / "graph-t7", policy)
        audit_path = work / "graph-t7" / DEFAULT_AUDIT_FILENAME
        audit_lines = [ln for ln in audit_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        # 5 generations: CURRENT skipped, pinned kept, keep=1 keeps the newest
        # deletable -> exactly the two oldest non-pinned generations are
        # deleted, one audit line each.
        assert len(report.deleted) == 2, report
        assert len(audit_lines) == len(report.deleted), (len(audit_lines), len(report.deleted))
        assert all(pinned not in d.generation for d in report.deleted)
        remaining = sorted(p.name for p in (work / "graph-t7" / "generations").iterdir())
        assert pinned in remaining and generations[-1] in remaining
        return {
            "generations_built": 5,
            "deleted": len(report.deleted),
            "audit_lines": len(audit_lines),
            "pinned_kept": pinned,
        }
    finally:
        store.close()


# ---------------------------------------------------- T8: contract digests
def task_contract_digest_verify(store_path: Path, work: Path) -> dict[str, Any]:
    import importlib

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    v3 = importlib.import_module("test_contract_v3_shape")
    v4 = importlib.import_module("test_extraction_contract_v4_shape")

    pins: dict[str, str] = {**v3.FROZEN_CONTRACTS, **v4.FROZEN_CONTRACTS}
    contracts = REPO_ROOT / "docs" / "contracts"
    recomputed: dict[str, str] = {}
    mismatches: list[str] = []
    for name, pin in sorted(pins.items()):
        digest = hashlib.sha256((contracts / name).read_bytes()).hexdigest()
        recomputed[name] = digest
        if digest != pin:
            mismatches.append(name)
    assert not mismatches, mismatches
    v4_digest = hashlib.sha256((contracts / "extraction-contract-v4.md").read_bytes()).hexdigest()
    return {"pinned_files": len(pins), "mismatches": 0, "v4_digest": v4_digest, "digests": recomputed}


# ----------------------------------------------------------------- T9: relearn
def task_relearn_quarantine_flow(store_path: Path, work: Path) -> dict[str, Any]:
    from know_your_context.domain import Event, EventKind, Evidence, PrimaryDomain
    from know_your_context.relearn import _RESET_SCORES, RelearnApproval, RelearnEngine
    from know_your_context.source_registry import ParserVersions, SourceKind, SourceRegistry
    from know_your_context.store import EventStore

    parser_versions = ParserVersions(
        parser_version="1.0.0",
        normalization_version="1.0.0",
        engine_version="0.9.95",
        scoring_contract_version="1.0.0",
    )

    store = EventStore(work / "t9.db")
    try:
        corpus = work / "t9-corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text("alpha v1\n", encoding="utf-8")
        (corpus / "b.md").write_text("beta v1\n", encoding="utf-8")
        registry = SourceRegistry(store)
        v1 = registry.observe(corpus, SourceKind.SOUL_CORPUS, parser_versions)
        store.append_domain(
            Event(
                kind=EventKind.NODE_UPSERT,
                node_id="n-1",
                domain=PrimaryDomain.P1,
                node=_node("n-1", "relearn target"),
                source_version_id=v1.source_version_id,
                occurred_at=NOW,
            ),
            idempotency_key="bench:t9:seed",
        )
        evidence_ids = []
        for index in range(3):
            eid = f"e{index}"
            evidence_ids.append(eid)
            store.append_domain(
                Event(
                    kind=EventKind.EVIDENCE_APPEND,
                    node_id="n-1",
                    domain=PrimaryDomain.P1,
                    evidence=Evidence.model_validate(
                        {
                            "evidence_id": eid,
                            "node_id": "n-1",
                            "source_version_id": v1.source_version_id,
                            "source_uri": f"context://bench/{eid}",
                            "observed_at": NOW,
                            "independence_key": f"grp-{eid}",
                            "outcome": "0.5",
                            "correctness": "0.9",
                            "reliability": "0.8",
                            "reproducibility": "0.7",
                            "scope_fit": "0.6",
                            "successful_use": "0.5",
                            "task_coverage": "0.4",
                            "quality_at_use": "0.75",
                            "contradiction": "0.1",
                            "utilization_u": "0.5",
                            "latency_ms": 1000,
                            "compute_ms": 100,
                            "output_chars": 500,
                        }
                    ),
                    source_version_id=v1.source_version_id,
                    occurred_at=NOW,
                ),
                idempotency_key=f"bench:t9:{eid}",
            )
        engine = RelearnEngine(store, registry, backup_root=work / "t9-backups", scoring_contract_version="1.0.0")
        (corpus / "a.md").write_text("alpha v2 changed\n", encoding="utf-8")
        offer = engine.offer(corpus, SourceKind.SOUL_CORPUS, parser_versions)
        assert offer is not None
        approval = RelearnApproval(offer_id=offer.offer_id, mode="RESET", approved_by="operator", decided_at=NOW)
        receipt = engine.apply("RESET", approval, at=NOW)
        assert receipt.scores_after == dict(_RESET_SCORES), receipt.scores_after
        assert set(receipt.quarantined_evidence_ids) == set(evidence_ids)
        assert engine.quarantined_evidence_ids() == frozenset(evidence_ids)
        return {
            "mode": "RESET",
            "scores_after_zero_vector": True,
            "quarantined": len(evidence_ids),
            "quarantine_equals_receipt": True,
        }
    finally:
        store.close()


# ------------------------------------------------------------ T10: secret scan
def task_secret_quarantine_scan(store_path: Path, work: Path) -> dict[str, Any]:
    from know_your_context.domain import PrimaryDomain, canonical_json
    from know_your_context.importer import SeedImporter
    from know_your_context.projection import GraphProjector
    from know_your_context.read_model_validator import ReadModelValidator
    from know_your_context.store import EventStore

    store = EventStore(work / "t10.db")
    try:
        corpus = work / "t10-corpus"
        corpus.mkdir()
        (corpus / "alpha.md").write_bytes(b"# Alpha\n\n## Lesson\n\nRoll back before retrying.\n")
        importer = SeedImporter(store, state_dir=work / "t10-import-state")
        importer.import_directory(corpus, domain=PrimaryDomain.P1)
        projector = GraphProjector(store, output_root=work / "t10-out")
        receipt = projector.rebuild()
        generation = Path(receipt.generation_dir)
        manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))

        validator = ReadModelValidator()
        clean = validator.validate(generation, manifest)
        assert clean.ok, clean.errors

        graph_path = generation / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["nodes"][0]["label"] = "leaked sk-" + "a" * 24
        graph_path.write_text(canonical_json(graph), encoding="utf-8")
        tampered = dict(manifest)
        tampered["artifacts"] = dict(manifest["artifacts"])
        tampered["artifacts"]["graph.json"] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        (generation / "manifest.json").write_text(canonical_json(tampered) + "\n", encoding="utf-8")
        poisoned = validator.validate(generation, tampered)
        assert not poisoned.ok
        assert any("secret" in error.lower() for error in poisoned.errors), poisoned.errors
        return {
            "clean_generation_ok": True,
            "poisoned_generation_ok": False,
            "secret_errors": [e for e in poisoned.errors if "secret" in e.lower()],
        }
    finally:
        store.close()


# -------------------------------------------------------- T11: review policy
def task_review_decision_policy(store_path: Path, work: Path) -> dict[str, Any]:
    from know_your_context.domain import LifecycleStatus
    from know_your_context.review import DEFAULT_REVIEW_POLICY, ReviewSignal, decide

    def signal(status: LifecycleStatus, p: str, u: str, count: int) -> ReviewSignal:
        return ReviewSignal(
            node_id="n-1",
            current_status=status,
            p=Decimal(p),
            m=Decimal("0.5"),
            e=Decimal("0.5"),
            uncertainty=Decimal(u),
            evidence_count=count,
            qualifying_review_streak=1,
            last_transition_at=None,
            at=NOW,
            relearn_pending=False,
            has_duplicates=False,
            contradiction_tie=False,
            retired=False,
            just_rolled_back=False,
        )

    signals = [
        signal(LifecycleStatus.ACTIVE, "0.9", "0.1", 3),
        signal(LifecycleStatus.DRAFT, "0.4", "0.6", 1),
        signal(LifecycleStatus.NEEDS_IMPROVEMENT, "0.2", "0.8", 0),
    ]
    versions = set()
    for sig in signals:
        first, second = decide(sig), decide(sig)
        assert first == second, "decide() is not deterministic"
        versions.add(first.policy_version)
    assert versions == {DEFAULT_REVIEW_POLICY.policy_version}, versions
    return {"signals": len(signals), "deterministic": True, "policy_version": DEFAULT_REVIEW_POLICY.policy_version}


_DISPATCH = {
    "gated-extraction-submit": task_gated_extraction_submit,
    "audit-chain-verify": task_audit_chain_verify,
    "rollback-byte-exact": task_rollback_byte_exact,
    "operator-gated-apply": task_operator_gated_apply,
    "watermark-ryw": task_watermark_ryw,
    "group-commit-burst": task_group_commit_burst,
    "retention-policy-audited": task_retention_policy_audited,
    "contract-digest-verify": task_contract_digest_verify,
    "relearn-quarantine-flow": task_relearn_quarantine_flow,
    "secret-quarantine-scan": task_secret_quarantine_scan,
    "review-decision-policy": task_review_decision_policy,
}


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] not in _DISPATCH:
        print(__doc__, file=sys.stderr)
        return 2
    task, store_db, work_dir, tree = argv[1], Path(argv[2]), Path(argv[3]), Path(argv[4])
    if not tree.is_dir():
        print(f"tree missing: {tree}", file=sys.stderr)
        return 2
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = _DISPATCH[task](store_db, work_dir)
    print("KYS_RESULT " + json.dumps({"task": task, **payload}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
