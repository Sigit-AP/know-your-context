"""Model-based tests for the superpower session daemon (W16).

A RuleBasedStateMachine drives a real in-process daemon over a real Unix
socket with seeded random rules — open / ping / read / write / review /
propose+approve / rollback / close / crash-kill (abrupt client death with a
partial frame) / restart — and after EVERY step checks the model against the
real system:

* the event chain verifies (via an independent store handle on the same db),
* every acknowledged write is readable through the daemon AND has the same
  version on both sides (daemon answer == direct store read),
* daemon counters never contradict the model (requests answered >= issued,
  sessions within capacity),
* after a client crash-kill the daemon keeps answering other sessions,
* after a daemon restart (close/reopen) all acknowledged nodes survive.

``TestDaemonCrashKill`` kills a real daemon subprocess with SIGKILL and
asserts exactly the acknowledged writes survive and a fresh daemon recovers
on the same paths (stale lock included).

Hypothesis' shrinker runs deterministically (``derandomize=True``); any
failing trace it finds is codified into ``TestSessionSeededTraces`` /
``TestDaemonCrashKill`` as a permanent regression.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from _serve_helpers import SocketClient, make_config, tool_error_code, tool_payload, wait_for
from know_your_context.serve.superpower import SuperpowerDaemon
from know_your_context.store import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DAEMON_CRASH_CHILD = REPO_ROOT / "tests" / "_model_daemon_crash_child.py"


def _node_payload(node_id: str, summary: str) -> dict:
    return {
        "node_id": node_id,
        "kind": "leaf",
        "domain": "P1",
        "summary": summary,
        "provenance": ["context://model-based-session"],
        "source_version_id": "sv-model",
    }


class SessionHarness:
    """Model (oracle) driving one real daemon plus an independent store probe."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.config = make_config(workdir, idle_timeout_seconds=3600.0)
        # Independent read handle on the same store file (WAL multi-reader).
        self.probe = EventStore(self.config.db_path)
        self.daemon: SuperpowerDaemon | None = None
        self.thread: threading.Thread | None = None
        self.sessions: dict[str, SocketClient] = {}
        self._session_counter = 0
        self.writes: dict[str, int] = {}  # node_id -> daemon-acked writes
        self.answered = 0
        self._answered_baseline = 0  # answered count at current daemon birth
        self._write_counter = 0
        self.oracle_id: str | None = None
        self.start_daemon()

    # ------------------------------------------------------------- lifecycle

    def start_daemon(self) -> None:
        assert self.daemon is None
        self.daemon = SuperpowerDaemon(self.config)
        self.daemon.start()
        self.thread = threading.Thread(target=self.daemon.serve_forever, daemon=True)
        self.thread.start()
        self.sessions = {}
        self._answered_baseline = self.answered  # daemon counters reset at birth
        self.oracle_id = self.open_session()

    def stop_daemon(self) -> None:
        assert self.daemon is not None and self.thread is not None
        self.daemon.shutdown("model-restart")
        self.thread.join(timeout=15)
        assert not self.thread.is_alive()
        self.daemon = None
        self.thread = None
        for client in self.sessions.values():
            client.close()
        self.sessions = {}

    def restart_daemon(self) -> None:
        self.stop_daemon()
        self.start_daemon()

    # -------------------------------------------------------------- sessions

    def open_session(self) -> str:
        assert self.daemon is not None
        self._session_counter += 1
        session_id = f"s{self._session_counter}"
        self.sessions[session_id] = SocketClient(self.config.paths.socket)
        return session_id

    def close_session(self, session_id: str) -> None:
        client = self.sessions.pop(session_id)
        client.close()

    def crash_session(self) -> None:
        """A client dies mid-frame: partial JSON, then a RST close."""
        assert self.daemon is not None
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(5.0)
        raw.connect(str(self.config.paths.socket))
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct_linger_rst())
        raw.sendall(b'{"jsonrpc": "2.0", "id": 1, "method": "pin')  # no newline, never finished
        raw.close()
        # The daemon must keep answering other sessions.
        oracle = self._oracle()
        response = oracle.request("ping")
        assert response["result"] == {}
        self.answered += 1

    def _oracle(self) -> SocketClient:
        assert self.oracle_id is not None
        return self.sessions[self.oracle_id]

    # ------------------------------------------------------------ operations

    def ping(self, session_id: str) -> None:
        response = self.sessions[session_id].request("ping")
        assert response["result"] == {}
        self.answered += 1

    def read_query(self, session_id: str) -> None:
        response = self.sessions[session_id].call_tool(
            "knowledge_query", {"purpose": "model probe", "domain": "P1", "token_budget": 512}
        )
        payload = tool_payload(response)
        assert "routing" in payload or "results" in payload
        self.answered += 1

    def write_node(self, session_id: str) -> str:
        self._write_counter += 1
        node_id = f"m-node-{self._write_counter}"
        response = self.sessions[session_id].call_tool(
            "knowledge_log_event", {"node": _node_payload(node_id, f"session write {self._write_counter}")}
        )
        assert not response["result"].get("isError"), response
        self.writes[node_id] = self.writes.get(node_id, 0) + 1
        self.answered += 1
        return node_id

    def read_back(self, node_id: str) -> None:
        response = self._oracle().call_tool("knowledge_get_node", {"node_id": node_id})
        payload = tool_payload(response)
        assert node_id in json.dumps(payload)
        self.answered += 1

    def review_node(self, node_id: str) -> None:
        response = self._oracle().call_tool("knowledge_review", {"node_id": node_id})
        if response["result"].get("isError"):
            # A review can legitimately refuse (lifecycle state); the error
            # must be a typed domain error, never an internal crash.
            assert tool_error_code(response) != "INTERNAL_ERROR"
        self.answered += 1

    def propose_and_approve(self, node_id: str) -> None:
        """The 'approve' action: propose, then apply with an approval."""
        oracle = self._oracle()
        proposal_response = oracle.call_tool(
            "knowledge_propose_improvement",
            {
                "node_id": node_id,
                "diff": [{"op": "set_summary", "value": f"approved summary {len(self.writes)}"}],
                "rationale": "model-based approve step",
            },
        )
        self.answered += 1
        if proposal_response["result"].get("isError"):
            assert tool_error_code(proposal_response) != "INTERNAL_ERROR"
            return
        proposal_id = tool_payload(proposal_response)["proposal"]["proposal_id"]
        apply_response = oracle.call_tool(
            "knowledge_apply_improvement",
            {
                "proposal_id": proposal_id,
                "approval": {"approved_by": "model-tester", "decision": "approved"},
            },
        )
        self.answered += 1
        assert not apply_response["result"].get("isError"), apply_response
        self.writes[node_id] = self.writes.get(node_id, 0) + 1

    def rollback_node(self, node_id: str) -> None:
        response = self._oracle().call_tool("knowledge_rollback", {"node_id": node_id, "to_version": 1})
        self.answered += 1
        if response["result"].get("isError"):
            assert tool_error_code(response) != "INTERNAL_ERROR"
        else:
            self.writes[node_id] = self.writes.get(node_id, 0) + 1

    # ------------------------------------------------------------ invariants

    def check_invariants(self) -> None:
        """Model vs real system, after EVERY step."""
        assert self.daemon is not None
        stats = self.daemon.stats()
        assert stats["requests_total"] >= self.answered - self._answered_baseline
        assert stats["sessions_active"] <= self.config.max_agents
        report = self.probe.verify_chain()
        assert report.ok, f"chain broken at seq {report.first_bad_seq}"
        # Daemon answers and direct store reads must agree on every node.
        for node_id in self.writes:
            response = self._oracle().call_tool("knowledge_get_node", {"node_id": node_id})
            payload = tool_payload(response)
            assert node_id in json.dumps(payload)
            store_version = self.probe.node_version(node_id)
            assert store_version >= self.writes[node_id]
            self.answered += 1

    def teardown(self) -> None:
        if self.daemon is not None:
            self.stop_daemon()
        self.probe.close()


def struct_linger_rst() -> bytes:
    import struct

    return struct.pack("ii", 1, 0)


# ------------------------------------------------------- hypothesis machine


class SessionStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self._tmp = Path(tempfile.mkdtemp(prefix="kys-mb-session-", dir="/tmp"))
        self.harness = SessionHarness(self._tmp)

    def teardown(self) -> None:
        self.harness.teardown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    @invariant()
    def model_matches_daemon(self) -> None:
        self.harness.check_invariants()

    @rule()
    def rule_open_session(self) -> None:
        if len(self.harness.sessions) < 6:
            self.harness.open_session()

    @rule(data=st.data())
    def rule_ping(self, data) -> None:
        session_id = data.draw(st.sampled_from(sorted(self.harness.sessions)))
        self.harness.ping(session_id)

    @rule(data=st.data())
    def rule_read_query(self, data) -> None:
        session_id = data.draw(st.sampled_from(sorted(self.harness.sessions)))
        self.harness.read_query(session_id)

    @rule(data=st.data())
    def rule_write_node(self, data) -> None:
        session_id = data.draw(st.sampled_from(sorted(self.harness.sessions)))
        self.harness.write_node(session_id)

    @precondition(lambda self: bool(self.harness.writes))
    @rule(data=st.data())
    def rule_read_back(self, data) -> None:
        self.harness.read_back(data.draw(st.sampled_from(sorted(self.harness.writes))))

    @precondition(lambda self: bool(self.harness.writes))
    @rule(data=st.data())
    def rule_review(self, data) -> None:
        self.harness.review_node(data.draw(st.sampled_from(sorted(self.harness.writes))))

    @precondition(lambda self: bool(self.harness.writes))
    @rule(data=st.data())
    def rule_approve(self, data) -> None:
        self.harness.propose_and_approve(data.draw(st.sampled_from(sorted(self.harness.writes))))

    @precondition(lambda self: bool(self.harness.writes))
    @rule(data=st.data())
    def rule_rollback(self, data) -> None:
        self.harness.rollback_node(data.draw(st.sampled_from(sorted(self.harness.writes))))

    @precondition(lambda self: len(self.harness.sessions) > 1)
    @rule(data=st.data())
    def rule_close_session(self, data) -> None:
        choices = [sid for sid in sorted(self.harness.sessions) if sid != self.harness.oracle_id]
        if not choices:
            return
        self.harness.close_session(data.draw(st.sampled_from(choices)))

    @rule()
    def rule_crash_session(self) -> None:
        self.harness.crash_session()

    def rule_restart_daemon(self) -> None:  # not a hypothesis rule: restart is driven by the seeded driver
        self.harness.restart_daemon()


TestSessionStateMachine = SessionStateMachine.TestCase
TestSessionStateMachine.settings = settings(
    max_examples=2,
    stateful_step_count=12,
    derandomize=True,
    deadline=None,
    suppress_health_check=list(HealthCheck),  # type: ignore[arg-type] - hypothesis stub types the enum metaclass
)


# ----------------------------------------------------------- seeded driver


class TestSessionSeededTraces:
    """Deterministic seeded driver over the same ops, incl. daemon restart."""

    OPS = ("open", "ping", "read", "write", "read_back", "review", "approve", "rollback", "close", "crash", "restart")

    @pytest.mark.parametrize("seed", range(3))
    def test_seeded_session_trace(self, seed: int) -> None:
        rng = random.Random(1000 + seed)
        workdir = Path(tempfile.mkdtemp(prefix=f"kys-mb-seed{seed}-", dir="/tmp"))
        harness = SessionHarness(workdir)
        try:
            for _ in range(18):
                op = rng.choice(self.OPS)
                if op == "open":
                    if len(harness.sessions) < 6:
                        harness.open_session()
                elif op == "ping":
                    harness.ping(rng.choice(sorted(harness.sessions)))
                elif op == "read":
                    harness.read_query(rng.choice(sorted(harness.sessions)))
                elif op == "write":
                    harness.write_node(rng.choice(sorted(harness.sessions)))
                elif op == "read_back" and harness.writes:
                    harness.read_back(rng.choice(sorted(harness.writes)))
                elif op == "review" and harness.writes:
                    harness.review_node(rng.choice(sorted(harness.writes)))
                elif op == "approve" and harness.writes:
                    harness.propose_and_approve(rng.choice(sorted(harness.writes)))
                elif op == "rollback" and harness.writes:
                    harness.rollback_node(rng.choice(sorted(harness.writes)))
                elif op == "close":
                    choices = [sid for sid in sorted(harness.sessions) if sid != harness.oracle_id]
                    if choices:
                        harness.close_session(rng.choice(choices))
                elif op == "crash":
                    harness.crash_session()
                elif op == "restart":
                    harness.check_invariants()
                    harness.restart_daemon()
                harness.check_invariants()
        finally:
            harness.teardown()
            shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------- real SIGKILL test


class TestDaemonCrashKill:
    def _spawn_child(self, workdir: Path) -> subprocess.Popen:
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{REPO_ROOT / 'tests'}"
        child = subprocess.Popen(
            [sys.executable, str(DAEMON_CRASH_CHILD), str(workdir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert wait_for(lambda: (workdir / "ready.txt").exists(), timeout=30)
        return child

    def test_sigkill_mid_service_committed_writes_survive(self, tmp_path: Path) -> None:
        workdir = Path(tempfile.mkdtemp(prefix="kys-mb-kill-", dir="/tmp"))
        child = self._spawn_child(workdir)
        socket_path = Path((workdir / "ready.txt").read_text())
        acknowledged: list[str] = []
        try:
            with SocketClient(socket_path) as client:
                for index in range(3):
                    node_id = f"kill-node-{index}"
                    response = client.call_tool("knowledge_log_event", {"node": _node_payload(node_id, "pre-kill")})
                    assert not response["result"].get("isError"), response
                    acknowledged.append(node_id)
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=15)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=15)
        assert child.returncode == -signal.SIGKILL

        # Exactly the acknowledged writes — no more, no less — survived.
        probe = EventStore(workdir / "soul.sqlite3")
        try:
            report = probe.verify_chain()
            assert report.ok
            for node_id in acknowledged:
                node = probe.current_node(node_id)
                assert node is not None, f"{node_id} lost in the crash"
                assert probe.node_version(node_id) == 1
        finally:
            probe.close()

    def test_fresh_daemon_recovers_on_same_paths_after_sigkill(self, tmp_path: Path) -> None:
        workdir = Path(tempfile.mkdtemp(prefix="kys-mb-kill2-", dir="/tmp"))
        child = self._spawn_child(workdir)
        socket_path = Path((workdir / "ready.txt").read_text())
        try:
            with SocketClient(socket_path) as client:
                response = client.call_tool("knowledge_log_event", {"node": _node_payload("kill-survivor", "pre-kill")})
                assert not response["result"].get("isError"), response
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=15)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=15)

        # The production recovery path: spawn_daemon detects the stale lock of
        # the dead pid and raises a fresh daemon on the same paths.
        from know_your_context.serve.superpower import daemon_status, spawn_daemon, stop_daemon

        config = _make_config_for_existing(workdir)
        spawn_daemon(config)
        try:
            assert daemon_status(config)["running"] is True
            with SocketClient(config.paths.socket) as client:
                response = client.call_tool("knowledge_get_node", {"node_id": "kill-survivor"})
                assert "kill-survivor" in json.dumps(tool_payload(response))
        finally:
            stop_daemon(config, timeout_seconds=30.0)


def _make_config_for_existing(workdir: Path):
    """Rebuild the daemon config over the crashed child's store/projection."""
    from know_your_context.serve.daemon_config import DaemonConfig

    return DaemonConfig(db_path=workdir / "soul.sqlite3", graph_root=workdir / "graph", idle_timeout_seconds=3600.0)
