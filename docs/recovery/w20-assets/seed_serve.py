"""Seed a rich store and serve the real webview for SUB-150 screenshot evidence.

Extends the W19 seed: 21 days of multi-hour activity (drives the dot-heatmap),
multi-day health history (drives the multi-series line), improvement proposals
proposed+applied (drives the proposals funnel), an extraction sidecar queue with
mixed statuses (drives the extractions funnel), and refs across kinds+statuses
(drives the kind x status heatmap matrix).
"""

import json
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/root/know-your-context/src")

from know_your_context.domain import (  # noqa: E402
    Event,
    EventKind,
    Evidence,
    Node,
    NodeKind,
    PrimaryDomain,
    Relation,
    RelationKind,
    ScoreVector,
)
from know_your_context.events import Invocation  # noqa: E402
from know_your_context.extraction import ExtractionQueue, sidecar_path  # noqa: E402
from know_your_context.leases import LeaseRegistry  # noqa: E402
from know_your_context.ops.lifecycle import record_health_snapshot  # noqa: E402
from know_your_context.serve.webview import WebviewServer  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
ROOT = Path("/root/.hermes/cache/scratch/w20-store")
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / "shot.sqlite3"
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()
SIDECAR = sidecar_path(DB)
for suffix in ("", "-wal", "-shm"):
    p = Path(SIDECAR + suffix)
    if p.exists():
        p.unlink()
HISTORY = ROOT / "lifecycle-health.jsonl"
if HISTORY.exists():
    HISTORY.unlink()

store = EventStore(DB)
scored = ScoreVector.model_validate({"p": "0.5", "m": "0.4", "e": "0.3", "uncertainty": "0.4", "confidence": "0.6"})


def upsert(node: Node) -> None:
    store.append_domain(
        Event(
            kind=EventKind.NODE_UPSERT,
            node_id=node.node_id,
            domain=node.domain,
            node=node,
            source_version_id=node.source_version_id,
            occurred_at=NOW,
        ),
        idempotency_key=f"w20:upsert:{node.node_id}:{store.node_version(node.node_id)}",
    )


def node(nid, domain, parent=None, summary=None, scores=None):
    return Node(
        node_id=nid,
        parent_id=parent,
        kind=NodeKind.ROOT if parent is None and nid == "n-root" else (NodeKind.PRIMARY if parent == "n-root" else NodeKind.CHILD),
        domain=domain,
        summary=summary or nid,
        provenance=("context://corpus/lesson.md",),
        source_version_id="sv-w20",
        scores=scores or ScoreVector.zero(),
        created_at=NOW,
        updated_at=NOW,
    )


upsert(node("n-root", PrimaryDomain.P1, None, "Knowledge Root"))
upsert(node("n-alpha", PrimaryDomain.P1, "n-root", "Run the gates before commit", scored))
upsert(node("n-beta", PrimaryDomain.P2, "n-root", "Lease conflicts are audited, never silent"))
upsert(node("n-gamma", PrimaryDomain.P3, "n-root", "Synthesis passes are kill-switched individually"))
upsert(node("n-delta", PrimaryDomain.P1, "n-alpha", "Rollback is byte-exact over the event log", scored))
upsert(node("n-eps", PrimaryDomain.P4, "n-beta", "Fair queue keeps heavy agents out of the read lane"))
upsert(node("n-iso", PrimaryDomain.P6, None, "Orphaned draft without any relations", scored))
store.append_domain(
    Event(
        kind=EventKind.RELATION_ADD,
        node_id="n-alpha",
        domain=PrimaryDomain.P1,
        relation=Relation(kind=RelationKind.SUPPORTS, source_node_id="n-alpha", target_node_id="n-delta"),
        occurred_at=NOW,
    ),
    idempotency_key="w20:rel:alpha-delta",
)
store.append_domain(
    Event(
        kind=EventKind.RELATION_ADD,
        node_id="n-beta",
        domain=PrimaryDomain.P2,
        relation=Relation(kind=RelationKind.SUPPORTS, source_node_id="n-beta", target_node_id="n-eps"),
        occurred_at=NOW,
    ),
    idempotency_key="w20:rel:beta-eps",
)
store.append_domain(
    Event(
        kind=EventKind.EVIDENCE_APPEND,
        node_id="n-alpha",
        domain=PrimaryDomain.P1,
        evidence=Evidence.model_validate(
            {
                "evidence_id": "ev-alpha-1", "node_id": "n-alpha", "source_version_id": "sv-w20",
                "source_uri": "context://corpus/lesson.md", "observed_at": NOW, "independence_key": "indep-1",
                "outcome": "0.5", "correctness": "1", "reliability": "1", "reproducibility": "1",
                "scope_fit": "1", "successful_use": "1", "utilization_u": "0.5", "task_coverage": "0.5",
                "quality_at_use": "0.5", "contradiction": "0", "latency_ms": 1, "compute_ms": 1, "output_chars": 4,
            }
        ),
        occurred_at=NOW,
    ),
    idempotency_key="w20:ev:alpha-1",
)

# Code side: files, symbols, call edges, refs across kinds + statuses.
store.upsert_file(path="a.py", sha256="a" * 64, size=10, mtime=1, language="python", generated_hint=False, extraction_version=1)
store.upsert_file(path="b.js", sha256="b" * 64, size=10, mtime=1, language="javascript", generated_hint=False, extraction_version=1)
store.upsert_symbol(node_id="function:aaa", name="alpha_fn", kind="function", path="a.py", line=1)
store.upsert_symbol(node_id="function:bbb", name="beta_fn", kind="function", path="b.js", line=2)
store.upsert_symbol(node_id="class:ccc", name="AlphaClass", kind="class", path="a.py", line=9)
store.add_graph_edge("function:aaa", "function:bbb", "calls", line=3)

# Refs matrix: kinds (calls/imports/inherits) x statuses (pending/failed/resolved).
ref_specs = [
    ("function:aaa", "gamma_fn", "calls", "failed"),
    ("function:aaa", "delta_fn", "calls", "failed"),
    ("function:bbb", "epsilon_fn", "calls", "pending"),
    ("function:bbb", "zeta_mod", "imports", "pending"),
    ("function:aaa", "eta_mod", "imports", "resolved"),
    ("class:ccc", "BaseAlpha", "inherits", "resolved"),
    ("class:ccc", "MixinBeta", "inherits", "failed"),
    ("function:bbb", "theta_fn", "calls", "pending"),
    ("function:aaa", "iota_mod", "imports", "failed"),
    ("function:bbb", "kappa_fn", "calls", "resolved"),
]
for index, (src, target, kind, want) in enumerate(ref_specs):
    ref_id = store.insert_unresolved_ref(source_node_id=src, target_name=target, kind=kind)
    if want == "failed":
        store.record_resolution_attempt(ref_id, outcome="failed", reason="no_match")
    elif want == "resolved":
        store.record_resolution_attempt(ref_id, outcome="resolved", reason=None)

# Activity: 21 days of events across 4 agents at varied hours (drives the
# dot-heatmap day x daypart matrix + the metrics event_series trend).
import random  # noqa: E402

rng = random.Random(20261106)
agents = ["agent-1", "agent-2", "agent-3", "agent-4"]
actions = [
    ("op.complete", {"operation": "remember", "node_id": "n-alpha"}),
    ("op.complete", {"operation": "recall", "ok": True}),
    ("op.complete", {"operation": "query", "q": "rollback"}),
    ("op.complete", {"operation": "synthesize", "passes": 12}),
    ("op.complete", {"operation": "ingest", "files": 3}),
    ("op.failed", {"operation": "publish", "reason": "budget exceeded"}),
    ("lease.conflict", {"node_id": "n-gamma", "holder": "agent-1", "expires_at": "2026-11-07T19:39:55+00:00"}),
]
seq = 0
for days_ago in range(20, -1, -1):
    # weekday-ish volume curve: mid-week peak, weekend light
    weekday = (NOW - timedelta(days=days_ago)).weekday()
    base = 2 if weekday >= 5 else 5 + (3 - abs(3 - weekday))
    for _ in range(base + rng.randint(0, 3)):
        actor = rng.choice(agents)
        event_type, payload = actions[rng.randint(0, len(actions) - 1)]
        hour = rng.choice([8, 9, 10, 11, 13, 14, 15, 16, 17, 22])
        stamp = (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=rng.randint(0, 59))
        with store.transaction() as conn:
            store.insert_event(
                conn,
                event_id=f"w20-audit-{seq}",
                kind="audit",
                event_type=event_type,
                knowledge_affecting=False,
                actor=actor,
                occurred_at=stamp.isoformat(),
                payload=payload,
                idempotency_key=f"w20:audit:{seq}",
                node_id=payload.get("node_id"),
            )
        seq += 1

# Improvement proposals (domain events with markers): 5 proposed, 2 applied —
# drives the proposals funnel (signals -> proposed -> applied).
def improvement(seq_id, event_type, key, marker, days_ago):
    with store.transaction() as conn:
        store.insert_event(
            conn,
            event_id=f"w20-imp-{seq_id}",
            kind="domain",
            event_type=event_type,
            knowledge_affecting=True,
            actor="agent-2",
            occurred_at=(NOW - timedelta(days=days_ago, hours=3)).isoformat(),
            payload={"payload": {key: marker}},
            idempotency_key=f"w20:imp:{seq_id}",
            node_id=marker.get("node_id"),
        )


for i, nid in enumerate(["n-alpha", "n-beta", "n-gamma", "n-delta", "n-eps"]):
    improvement(i, "knowledge.propose_improvement", "improvement_proposal",
                {"proposal_id": f"imp-{i:03d}", "node_id": nid, "impact": "medium"}, days_ago=6 - i)
for i in (0, 2):
    improvement(10 + i, "knowledge.apply_improvement", "improvement_apply",
                {"proposal_id": f"imp-{i:03d}", "node_id": ["n-alpha", "n-gamma"][i // 2]}, days_ago=1)

# Bench: completed invocations with real integer durations.
bench_ops = {
    "knowledge.query": [3, 4, 5, 4, 12, 6, 5],
    "knowledge.remember": [8, 9, 11, 14],
    "knowledge.evaluate": [21, 24, 19, 33],
    "knowledge.review": [15, 14],
    "knowledge.propose": [32, 41],
    "knowledge.rollback": [6, 7],
}
for operation, durations in bench_ops.items():
    for i, duration in enumerate(durations):
        inv = Invocation.create(operation, request_hash=f"w20-{operation}-{i}", actor="agent-1")
        with store.transaction() as conn:
            store.insert_journal(conn, inv)
            row = store.insert_event(
                conn,
                event_id=inv.audit_event_id,
                kind="audit",
                event_type="op.complete",
                knowledge_affecting=False,
                actor=inv.actor,
                occurred_at=(NOW - timedelta(days=1, minutes=i)).isoformat(),
                payload={"operation": operation},
                idempotency_key=f"w20:inv:{operation}:{i}",
            )
            store.complete_journal(conn, inv.invocation_id, outcome={"duration_ms": duration, "ok": True}, audit_event_seq=row.event_seq)

# One active lease for the leases panel.
leases = LeaseRegistry(store)
leases.acquire("n-alpha", "agent-1", ttl_seconds=86400)
leases.acquire("n-gamma", "agent-3", ttl_seconds=86400)

# Health history: 9 snapshots over 8 days (drives the multi-series line + the
# health KPI sparkline).
for back in range(8, -1, -1):
    record_health_snapshot(store, history_path=HISTORY, now=NOW - timedelta(days=back))

store.close()

# Extraction sidecar queue: mixed statuses (drives the extractions funnel).
queue = ExtractionQueue(SIDECAR)
queue.close()
conn = sqlite3.connect(SIDECAR)
conn.row_factory = sqlite3.Row
statuses = [
    ("ex-0001", "applied", 2, 0, "0.860000000000", "0.900000000000"),
    ("ex-0002", "applied", 1, 0, "0.780000000000", "0.810000000000"),
    ("ex-0003", "rejected", 0, 2, "0.640000000000", "0.180000000000"),
    ("ex-0004", "pending_review", 0, 0, "0.820000000000", "0.740000000000"),
    ("ex-0005", "corroborated", 3, 0, "0.710000000000", "0.880000000000"),
    ("ex-0006", "contradiction_flagged", 1, 1, "0.690000000000", "0.420000000000"),
    ("ex-0007", "pending_review", 0, 0, "0.550000000000", "0.510000000000"),
    ("ex-0008", "rejected", 0, 1, "0.470000000000", "0.090000000000"),
]
with conn:
    for receipt_id, status, corroborators, contradictions, conf_rep, conf_eff in statuses:
        conn.execute(
            "INSERT INTO extraction_submissions (receipt_id, idem_key, source_sha256, source_path, actor,"
            " method_json, method_fingerprint, payload_json, payload_sha256, confidence_reported,"
            " confidence_effective, status, corroborators, contradictions, overlap_ratio, receipt_json,"
            " decision_note, decided_by, submitted_at, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                f"{receipt_id}-idem",
                "c" * 64,
                f"corpus/{receipt_id}.md",
                "agent-2" if receipt_id.endswith(("1", "3", "5", "7")) else "agent-4",
                json.dumps({"extractor": "markdown-structure", "version": 3}),
                "f" * 16,
                json.dumps({"nodes": [{"id": f"n-{receipt_id}"}, {"id": f"n-{receipt_id}-b"}], "edges": [{"source": "a", "target": "b"}]}),
                "d" * 64,
                conf_rep,
                conf_eff,
                status,
                corroborators,
                contradictions,
                "0.125000000000",
                json.dumps({"receipt_id": receipt_id}),
                "auto-decided" if status in ("applied", "rejected") else None,
                "reviewer" if status in ("applied", "rejected") else None,
                (NOW - timedelta(days=3, hours=int(receipt_id[-1]))).isoformat(),
                (NOW - timedelta(days=2)).isoformat() if status in ("applied", "rejected") else None,
            ),
        )
conn.close()

server = WebviewServer(DB, port=18750, token="w20-shot-token-0000000000")
server.start()
print("URL: http://127.0.0.1:18750/?token=w20-shot-token-0000000000", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    server.shutdown()
