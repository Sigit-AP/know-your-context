"""Daemon write-path node-lease integration (W17 SUB-138).

With ``node_leases_enabled`` the write arbiter claims a lease on every node a
mutation touches; a second agent writing the same node while the lease is
unexpired gets a typed NODE_IN_USE error frame (holder + expiry in ``data``)
and the conflict lands in the hash-chained event log — never a silent
overwrite. The guard is a DaemonConfig knob: the shared test helper keeps the
legacy (guard-off) behavior so the pre-lease storm gates are untouched.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from _serve_helpers import SocketClient, make_config, wait_for
from know_your_context.leases import LeaseRegistry
from know_your_context.serve import rpc
from know_your_context.serve.superpower import NodeInUse, SuperpowerDaemon
from know_your_context.store import EventStore


def _node(node_id: str, summary: str = "leased node") -> dict:
    return {
        "node_id": node_id,
        "kind": "leaf",
        "domain": "P1",
        "summary": summary,
        "provenance": ["context://leases"],
        "source_version_id": "sv-test",
    }


@pytest.fixture()
def leased_daemon(tmp_path: Path):
    config = make_config(tmp_path, idle_timeout_seconds=120.0, node_leases_enabled=True, node_lease_ttl_seconds=60.0)
    instance = SuperpowerDaemon(config)
    instance.start()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, config
    instance.shutdown("test-teardown")
    thread.join(timeout=10)


class TestConfig:
    def test_default_off(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        assert config.node_leases_enabled is False

    def test_ttl_must_be_positive(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="node_lease_ttl"):
            make_config(tmp_path, node_lease_ttl_seconds=0.0)

    def test_stats_report_the_guard(self, leased_daemon) -> None:
        instance, _ = leased_daemon
        stats = instance.stats()
        assert stats["node_leases"]["enabled"] is True
        assert stats["node_leases"]["ttl_seconds"] == 60.0


class TestWritePathGuard:
    def test_first_writer_claims_the_node(self, leased_daemon) -> None:
        instance, config = leased_daemon
        with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
            response = client.call_tool("knowledge_log_event", {"node": _node("n-lease-1")}, actor="agent-a")
            assert "result" in response, response
            assert not response["result"].get("isError"), response
        assert wait_for(lambda: instance.stats()["node_leases"]["active"] >= 1)
        store = EventStore(config.db_path)
        try:
            lease = LeaseRegistry(store).holder_of("n-lease-1")
            assert lease is not None
            assert lease.actor == "agent-a"
        finally:
            store.close()

    def test_second_agent_gets_typed_node_in_use(self, leased_daemon) -> None:
        _, config = leased_daemon
        with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
            first = client.call_tool("knowledge_log_event", {"node": _node("n-lease-2")}, actor="agent-a")
            assert not first["result"].get("isError"), first
            second = client.call_tool(
                "knowledge_log_event", {"node": _node("n-lease-2", "hostile rewrite")}, actor="agent-b"
            )
        assert "error" in second, second
        assert second["error"]["code"] == rpc.NODE_IN_USE
        data = second["error"].get("data") or {}
        assert data.get("code") == "NODE_IN_USE"
        assert data.get("holder") == "agent-a"
        assert data.get("node_id") == "n-lease-2"
        assert data.get("expires_at")

    def test_holder_rewrite_renews_and_succeeds(self, leased_daemon) -> None:
        _, config = leased_daemon
        with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
            first = client.call_tool("knowledge_log_event", {"node": _node("n-lease-3")}, actor="agent-a")
            assert not first["result"].get("isError"), first
            second = client.call_tool(
                "knowledge_log_event", {"node": _node("n-lease-3", "owner update")}, actor="agent-a"
            )
            assert "result" in second, second
            assert not second["result"].get("isError"), second

    def test_conflict_is_recorded_in_the_chain(self, leased_daemon) -> None:
        instance, config = leased_daemon
        with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
            client.call_tool("knowledge_log_event", {"node": _node("n-lease-4")}, actor="agent-a")
            client.call_tool("knowledge_log_event", {"node": _node("n-lease-4", "hostile")}, actor="agent-b")
        instance.shutdown("test-checkpoint")
        store = EventStore(config.db_path)
        try:
            with store.read() as conn:
                rows = conn.execute(
                    "SELECT event_type, actor, node_id, payload_json FROM event_log "
                    "WHERE event_type = 'lease.conflict' ORDER BY event_seq"
                ).fetchall()
            assert len(rows) == 1
            assert str(rows[0]["actor"]) == "agent-b"
            assert str(rows[0]["node_id"]) == "n-lease-4"
            payload = json.loads(str(rows[0]["payload_json"]))
            assert payload["holder"] == "agent-a"
            assert store.verify_chain().ok
        finally:
            store.close()
        # The fixture teardown shuts an already-stopped daemon down again:
        # shutdown is idempotent by contract.
        instance.shutdown("test-teardown-again")

    def test_expired_lease_frees_the_node(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, idle_timeout_seconds=120.0, node_leases_enabled=True, node_lease_ttl_seconds=0.5)
        instance = SuperpowerDaemon(config)
        instance.start()
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
                first = client.call_tool("knowledge_log_event", {"node": _node("n-lease-5")}, actor="agent-a")
                assert not first["result"].get("isError"), first
                time.sleep(0.7)
                second = client.call_tool(
                    "knowledge_log_event", {"node": _node("n-lease-5", "after expiry")}, actor="agent-b"
                )
                assert "result" in second, second
                assert not second["result"].get("isError"), second
        finally:
            instance.shutdown("test-teardown")
            thread.join(timeout=10)

    def test_guard_off_allows_shared_writes(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, idle_timeout_seconds=120.0)  # node_leases_enabled defaults off
        instance = SuperpowerDaemon(config)
        instance.start()
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
                first = client.call_tool("knowledge_log_event", {"node": _node("n-free")}, actor="agent-a")
                second = client.call_tool("knowledge_log_event", {"node": _node("n-free", "b")}, actor="agent-b")
            assert not first["result"].get("isError"), first
            assert "result" in second, second
            assert not second["result"].get("isError"), second
        finally:
            instance.shutdown("test-teardown")
            thread.join(timeout=10)

    def test_read_verbs_never_lease(self, leased_daemon) -> None:
        _, config = leased_daemon
        with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
            client.call_tool("knowledge_log_event", {"node": _node("n-lease-6")}, actor="agent-a")
            read = client.call_tool("knowledge_get_node", {"node_id": "n-lease-6"}, actor="agent-b")
        assert "result" in read, read
        assert not read["result"].get("isError"), read

    def test_writes_without_node_ids_are_unguarded(self, leased_daemon) -> None:
        _, config = leased_daemon
        with SocketClient(config.paths.socket) as client:  # type: ignore[operator]
            # propose_improvement carries no node id: no lease is claimed.
            response = client.call_tool(
                "knowledge_propose_improvement",
                {
                    "proposal": {
                        "proposal_id": "p-lease-1",
                        "title": "tighten the gate",
                        "rationale": "leases proven",
                        "changes": [{"kind": "config", "key": "k", "value": "v"}],
                    }
                },
                actor="agent-a",
            )
        assert "result" in response, response


class TestNodeInUseType:
    def test_rpc_code_and_message(self) -> None:
        from know_your_context.leases import NodeLeaseConflict

        conflict = NodeLeaseConflict("n-x", "agent-a", "2026-01-01T00:01:00+00:00")
        error = NodeInUse(conflict)
        assert error.rpc_code == rpc.NODE_IN_USE
        assert error.data is not None
        assert error.data["code"] == "NODE_IN_USE"
        assert "agent-a" in str(error)
