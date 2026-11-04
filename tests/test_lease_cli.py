"""CLI surface of the node lease engine (W17 SUB-138).

``know-your-context lease`` is an ops command (never a knowledge op, never
dispatched over MCP/HTTP): list / acquire / release / force-release against
``--store``, canonical JSON on stdout, typed errors on stderr.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.cli import main
from know_your_context.leases import LeaseRegistry
from know_your_context.store import EventStore


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    path = tmp_path / "cli-lease.sqlite3"
    store = EventStore(path)
    store.close()
    return str(path)


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict | list | None, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else None
    return code, payload, captured.err


class TestList:
    def test_empty_store_lists_nothing(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload, _ = run_cli(capsys, "--store", store_path, "lease", "list")
        assert code == 0
        assert payload == {"count": 0, "leases": []}

    def test_lists_active_leases(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        store = EventStore(store_path)
        try:
            registry = LeaseRegistry(store)
            registry.acquire("n-1", "agent-a")
            registry.acquire("n-2", "agent-b")
        finally:
            store.close()
        code, payload, _ = run_cli(capsys, "--store", store_path, "lease", "list")
        assert code == 0
        assert isinstance(payload, dict)
        assert payload["count"] == 2
        assert [row["node_id"] for row in payload["leases"]] == ["n-1", "n-2"]
        assert payload["leases"][0]["actor"] == "agent-a"
        assert payload["leases"][0]["expires_at"]


class TestAcquire:
    def test_acquire_prints_lease(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload, _ = run_cli(
            capsys, "--store", store_path, "lease", "acquire", "n-9", "--actor", "agent-a", "--ttl", "30"
        )
        assert code == 0
        assert payload["node_id"] == "n-9"  # type: ignore[index]
        assert payload["actor"] == "agent-a"  # type: ignore[index]

    def test_acquire_conflict_exits_1_with_typed_error(
        self, store_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_cli(capsys, "--store", store_path, "lease", "acquire", "n-9", "--actor", "agent-a")[0] == 0
        code, payload, err = run_cli(capsys, "--store", store_path, "lease", "acquire", "n-9", "--actor", "agent-b")
        assert code == 1
        assert "NODE_IN_USE" in err or "NODE_IN_USE" in json.dumps(payload)

    def test_acquire_validates_ttl(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, _, _ = run_cli(capsys, "--store", store_path, "lease", "acquire", "n-9", "--actor", "a", "--ttl", "0")
        assert code != 0


class TestRelease:
    def test_holder_release(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        run_cli(capsys, "--store", store_path, "lease", "acquire", "n-9", "--actor", "agent-a")
        code, payload, _ = run_cli(capsys, "--store", store_path, "lease", "release", "n-9", "--actor", "agent-a")
        assert code == 0
        assert payload == {"node_id": "n-9", "released": True}

    def test_release_by_other_actor(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        run_cli(capsys, "--store", store_path, "lease", "acquire", "n-9", "--actor", "agent-a")
        code, payload, _ = run_cli(capsys, "--store", store_path, "lease", "release", "n-9", "--actor", "agent-b")
        assert code == 0
        assert payload == {"node_id": "n-9", "released": False}

    def test_force_release(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        run_cli(capsys, "--store", store_path, "lease", "acquire", "n-9", "--actor", "agent-a")
        code, payload, _ = run_cli(capsys, "--store", store_path, "lease", "force-release", "n-9")
        assert code == 0
        assert payload == {"node_id": "n-9", "released": True}


class TestStoreGuard:
    def test_missing_store_fails_typed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        missing = str(tmp_path / "absent.sqlite3")
        code = main(["--store", missing, "lease", "list"])
        assert code != 0
