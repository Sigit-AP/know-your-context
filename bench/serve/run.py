"""bench/serve — serve-lane latency benchmark under the 50-agent storm.

Measures what the storm gate measures, as a reproducible baseline artifact:
the frozen 16-verb storm (28 lifecycle agents + 22 pool agents) against an
in-process ``SuperpowerDaemon``, reporting client-side p50/p95/p99 per lane
(read/write/heavy) plus the machine-speed factor the gate uses.

Every ledger records the exact command that reproduces it. Latency is a
physical measurement (not deterministic); the WORKLOAD is pinned exactly so
before/after comparisons on the same host are apples-to-apples.

Usage (from the repository root, locked environment):

    uv run python bench/serve/run.py run          # one storm, write a ledger
    uv run python bench/serve/run.py check        # ledger self-checks (exit 0/1)
"""

from __future__ import annotations

import itertools
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain, Relation, RelationKind  # noqa: E402
from know_your_context.projection import GraphProjector  # noqa: E402
from know_your_context.serve import rpc  # noqa: E402
from know_your_context.serve.daemon_config import DaemonConfig  # noqa: E402
from know_your_context.serve.superpower import SuperpowerDaemon, classify_tool  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
AGENTS = 50
LIFECYCLE_AGENTS = 28
REFERENCE_READ_P50_MS = 226.0  # the gate's calibration reference (SUB-104)


def _percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


class _Client:
    """Minimal newline-delimited JSON-RPC client for the daemon socket."""

    def __init__(self, socket_path: Path, *, timeout: float = 45.0) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(str(socket_path))
        self._reader = self._sock.makefile("rb")
        self._writer = self._sock.makefile("wb")
        self._ids = itertools.count(1)

    def request(self, method: str, params: dict) -> dict:
        frame = (
            json.dumps({"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}).encode() + b"\n"
        )
        self._writer.write(frame)
        self._writer.flush()
        line = self._reader.readline()
        if not line:
            raise ConnectionError("daemon closed the connection")
        return json.loads(line)

    def close(self) -> None:
        try:
            self._writer.close()
            self._reader.close()
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    """The gate's seed graph: KROOT, the seven primary domains, one child."""
    graph_root = tmp_path / "graph"
    db = tmp_path / "soul.sqlite3"
    store = EventStore(db)
    projector = GraphProjector(store, output_root=graph_root)

    def upsert(node: Node) -> None:
        store.append_domain(
            Event(
                kind=EventKind.NODE_UPSERT,
                node_id=node.node_id,
                domain=node.domain,
                node=node,
                source_version_id=node.source_version_id,
            ),
            idempotency_key=f"bench:serve:upsert:{node.node_id}",
        )

    upsert(
        Node(
            node_id="KROOT",
            kind=NodeKind.ROOT,
            domain=PrimaryDomain.P1,
            summary="Knowledge Root",
            provenance=("context://roots",),
            source_version_id="sv-bench",
        )
    )
    for domain in PrimaryDomain:
        upsert(
            Node(
                node_id=domain.value,
                parent_id="KROOT",
                kind=NodeKind.PRIMARY,
                domain=domain,
                summary=domain.title,
                provenance=("context://roots",),
                source_version_id="sv-bench",
            )
        )
    upsert(
        Node(
            node_id="n-gates",
            parent_id="P1",
            kind=NodeKind.CHILD,
            domain=PrimaryDomain.P1,
            summary="Run the gates before commit",
            context_tags=("gates",),
            provenance=("context://corpus/lesson.md",),
            source_version_id="sv-bench",
        )
    )
    store.append_domain(
        Event(
            kind=EventKind.RELATION_ADD,
            node_id="P1",
            domain=PrimaryDomain.P1,
            relation=Relation(kind=RelationKind.PARENT_OF, source_node_id="P1", target_node_id="n-gates"),
        ),
        idempotency_key="bench:serve:rel:p1:n-gates",
    )
    projector.rebuild()
    store.close()
    return graph_root, db


def _node(node_id: str) -> dict:
    return {
        "node_id": node_id,
        "parent_id": "P1",
        "kind": "leaf",
        "domain": "P1",
        "summary": "gate node",
        "provenance": ["context://gate"],
        "source_version_id": "sv-bench",
    }


def _evidence(evidence_id: str, node_id: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "node_id": node_id,
        "source_version_id": "sv-bench",
        "source_uri": "context://gate",
        "observed_at": "2026-10-28T00:00:00+00:00",
        "context_tags": ["gate"],
        "independence_key": evidence_id,
        "outcome": "1",
        "correctness": "0.9",
        "reliability": "0.9",
        "reproducibility": "0.9",
        "scope_fit": "0.9",
        "successful_use": "1",
        "task_coverage": "1",
        "quality_at_use": "0.9",
        "contradiction": "0",
        "utilization_u": "0.5",
        "latency_ms": 10,
        "compute_ms": 5,
        "output_chars": 10,
    }


def _head_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a ledger must still write without git
        return "unknown"


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bench-serve-"))
    graph_root, db = _seed(tmp)
    config = DaemonConfig(
        db_path=db,
        graph_root=graph_root,
        idle_timeout_seconds=300.0,
        max_agents=AGENTS,
        heavy_queue_size=128,
        per_actor_heavy_cap=4,
    )
    (graph_root / "storm_doc.py").write_text("def storm_gate():\n    return 42\n", encoding="utf-8")
    daemon = SuperpowerDaemon(config)
    daemon.start()
    thread = threading.Thread(target=daemon.serve_forever, daemon=True)
    thread.start()

    latencies: dict[str, list[float]] = {"read": [], "write": [], "heavy": []}
    lock = threading.Lock()
    failures: list[str] = []

    def call(client: _Client, actor: str, name: str, arguments: dict) -> dict:
        started = time.monotonic()
        response: dict = {}
        for _ in range(60):
            params = {"name": name, "arguments": arguments, "_meta": {"actor": actor}}
            response = client.request("tools/call", params)
            if "error" in response and response["error"]["code"] == rpc.SERVER_BUSY:
                time.sleep(response["error"]["data"]["retry_after_ms"] / 1000.0)
                continue
            break
        else:
            raise AssertionError(f"{actor} never got past SERVER_BUSY for {name}")
        with lock:
            latencies[classify_tool(name)].append((time.monotonic() - started) * 1000.0)
        if response.get("result", {}).get("isError"):
            raise AssertionError(f"{name}: isError {str(response)[:200]}")
        return response

    def agent(index: int) -> None:
        actor = f"storm-{index:02d}"
        lifecycle = index < LIFECYCLE_AGENTS
        node_id = f"n-storm-{index:02d}" if lifecycle else f"n-storm-pool-{index % 8}"
        try:
            with _Client(config.paths.socket) as client:
                call(client, actor, "knowledge_log_event", {"node": _node(node_id)})
                call(
                    client, actor, "knowledge_record_outcome", {"evidence": _evidence(f"ev-storm-{index:02d}", node_id)}
                )
                call(client, actor, "knowledge_evaluate", {"node_id": node_id})
                call(client, actor, "knowledge_review", {"node_id": node_id})
                if lifecycle:
                    response = call(
                        client,
                        actor,
                        "knowledge_propose_improvement",
                        {
                            "node_id": node_id,
                            "diff": [{"op": "set_summary", "value": f"storm summary {index}"}],
                            "rationale": "storm",
                        },
                    )
                    proposal_id = json.loads(response["result"]["content"][0]["text"])["proposal"]["proposal_id"]
                    call(client, actor, "knowledge_apply_improvement", {"proposal_id": proposal_id})
                    call(client, actor, "knowledge_rollback", {"node_id": node_id})
                for name, arguments in (
                    ("knowledge_query", {"purpose": "storm", "domain": "P1", "token_budget": 512}),
                    ("knowledge_get_node", {"node_id": node_id}),
                    ("knowledge_get_subtree", {"node_id": node_id, "depth": 1}),
                    ("knowledge_path", {"from_id": "KROOT", "to_id": node_id}),
                    ("knowledge_explain", {"node_id": node_id}),
                    ("knowledge_affected", {"node_id": node_id, "depth": 1}),
                    ("knowledge_god_nodes", {"limit": 3}),
                ):
                    call(client, actor, name, arguments)
                call(client, actor, "knowledge_ingest_file", {"path": "storm_doc.py"})
                call(client, actor, "knowledge_ingest_directory", {"path": ".", "max_files": 1})
        except BaseException as error:  # noqa: BLE001 - collected into the ledger
            with lock:
                failures.append(f"{actor}: {type(error).__name__}: {error}")

    barrier = threading.Barrier(AGENTS)

    def agent_at_barrier(index: int) -> None:
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            with lock:
                failures.append(f"storm-{index:02d}: barrier broken")
            return
        agent(index)

    # The gate's machine-speed calibration: 20 solo sys-lane reads.
    solo: list[float] = []
    with _Client(config.paths.socket, timeout=30.0) as client:
        for _ in range(20):
            t0 = time.monotonic()
            client.request("daemon.status", {})
            solo.append((time.monotonic() - t0) * 1000.0)
    factor = min(4.0, max(1.0, _percentile(solo, 0.50) / REFERENCE_READ_P50_MS))

    started = time.monotonic()
    threads = [threading.Thread(target=agent_at_barrier, args=(index,)) for index in range(AGENTS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    elapsed = time.monotonic() - started
    daemon.shutdown("bench-done")
    thread.join(timeout=30)

    lanes = {
        lane: {
            "count": len(samples),
            "p50": round(_percentile(samples, 0.50), 1),
            "p95": round(_percentile(samples, 0.95), 1),
            "p99": round(_percentile(samples, 0.99), 1),
        }
        for lane, samples in latencies.items()
    }
    ledger = {
        "suite": "serve",
        "recorded_at": datetime.now(UTC).isoformat(),
        "git_head": _head_sha(),
        "reproduce": "uv run python bench/serve/run.py run",
        "workload": "50 agents (28 lifecycle + 22 pool) x 16 frozen verbs, same mix as tests/test_serve_gates.py",
        "agents": AGENTS,
        "elapsed_s": round(elapsed, 1),
        "speed_factor": round(factor, 3),
        "cpu_count": os.cpu_count(),
        "failures": failures[:10],
        "failure_count": len(failures),
        "lanes": lanes,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"ledger-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(ledger, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(ledger, indent=1))
    print(f"ledger: {out}")
    return 0 if not failures else 1


def check() -> int:
    """Self-check: the newest ledger parses and carries the required shape."""
    ledgers = sorted(RESULTS.glob("ledger-*.json"))
    if not ledgers:
        print("bench/serve: no ledgers yet (run the suite first)")
        return 0
    latest = json.loads(ledgers[-1].read_text(encoding="utf-8"))
    required = {"suite", "git_head", "reproduce", "lanes", "failure_count"}
    missing = required - set(latest)
    if missing:
        print(f"bench/serve: ledger {ledgers[-1].name} missing keys: {sorted(missing)}")
        return 1
    for lane in ("read", "write", "heavy"):
        stats = latest["lanes"].get(lane)
        if not stats or not {"count", "p50", "p95", "p99"} <= set(stats):
            print(f"bench/serve: ledger lane {lane!r} malformed")
            return 1
    print(f"bench/serve: ledger {ledgers[-1].name} OK")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"run", "check"}:
        print(__doc__)
        return 2
    return run() if sys.argv[1] == "run" else check()


if __name__ == "__main__":
    sys.exit(main())
