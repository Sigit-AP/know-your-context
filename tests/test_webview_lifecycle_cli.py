"""CLI lifecycle of the webview (W17 SUB-138): on / off / status / setup.

The on/off preference persists next to the store; status reconciles the file
with a live TCP probe of the saved port (a stale pidfile or config never
claims "running"); setup keeps a real webview alive across the install steps
and its progress is served at /api/setup while the steps run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from know_your_context import runtime_guard
from know_your_context.cli import main
from know_your_context.ops.webview_control import prefs_path_for, progress_path_for, read_prefs
from know_your_context.store import EventStore


def _suspend_socket_guard() -> dict[str, Any] | None:
    import socket

    if runtime_guard._SOCKET_GUARD_DEPTH == 0:  # noqa: SLF001
        return None
    state: dict[str, Any] = {
        "depth": runtime_guard._SOCKET_GUARD_DEPTH,  # noqa: SLF001
        "originals": dict(runtime_guard._SOCKET_ORIGINALS),  # noqa: SLF001
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "sendmsg": socket.socket.sendmsg,
        "create_connection": socket.create_connection,
    }
    originals = state["originals"]
    if originals:
        socket.socket.connect = originals["connect"]  # type: ignore[method-assign]
        socket.socket.connect_ex = originals["connect_ex"]  # type: ignore[method-assign]
        socket.socket.sendto = originals["sendto"]  # type: ignore[method-assign]
        socket.socket.sendmsg = originals["sendmsg"]  # type: ignore[method-assign]
        socket.create_connection = originals["create_connection"]  # type: ignore[assignment]
    runtime_guard._SOCKET_GUARD_DEPTH = 0  # noqa: SLF001
    runtime_guard._SOCKET_ORIGINALS.clear()  # noqa: SLF001
    return state


def _resume_socket_guard(state: dict[str, Any]) -> None:
    import socket

    socket.socket.connect = state["connect"]  # type: ignore[method-assign]
    socket.socket.connect_ex = state["connect_ex"]  # type: ignore[method-assign]
    socket.socket.sendto = state["sendto"]  # type: ignore[method-assign]
    socket.socket.sendmsg = state["sendmsg"]  # type: ignore[method-assign]
    socket.create_connection = state["create_connection"]  # type: ignore[assignment]
    runtime_guard._SOCKET_ORIGINALS.update(state["originals"])  # noqa: SLF001
    runtime_guard._SOCKET_GUARD_DEPTH = state["depth"]  # noqa: SLF001


@pytest.fixture(autouse=True)
def _loopback_sockets() -> Any:
    state = _suspend_socket_guard()
    try:
        yield
    finally:
        if state is not None:
            _resume_socket_guard(state)


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    path = tmp_path / "cli-webview.sqlite3"
    EventStore(path).close()
    return str(path)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = main(list(argv))
    out = capsys.readouterr().out
    for line in out.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return code, payload
    return code, {}


class TestOnOff:
    def test_off_persists_and_reports(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = run(capsys, "--store", store_path, "webview", "off")
        assert code == 0
        assert payload == {"status": "disabled"}
        assert read_prefs(store_path)["enabled"] is False

    def test_bare_webview_respects_persisted_off(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        run(capsys, "--store", store_path, "webview", "off")
        code, payload = run(capsys, "--store", store_path, "webview", "--no-open")
        assert code == 0
        assert payload["status"] == "disabled"

    def test_on_persists_port(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = run(capsys, "--store", store_path, "webview", "on", "--port", "0", "--no-open", "--serve-once")
        assert code == 0
        assert read_prefs(store_path)["enabled"] is True

    def test_on_without_store_fails_typed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--store", str(tmp_path / "absent.sqlite3"), "webview", "status"])
        assert code != 0


class TestStatus:
    def test_status_not_running_when_nothing_serves(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = run(capsys, "--store", store_path, "webview", "status")
        assert code == 0
        assert payload["enabled"] is None
        assert payload["running"] is False

    def test_status_running_with_live_server(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        from know_your_context.serve.webview import WebviewServer

        server = WebviewServer(store_path, port=0, token="status-token-00000000")
        server.start()
        try:
            from know_your_context.ops.webview_control import write_prefs

            write_prefs(store_path, enabled=True, port=server.port)
            code, payload = run(capsys, "--store", store_path, "webview", "status")
            assert code == 0
            assert payload["running"] is True
            assert payload["enabled"] is True
            assert payload["port"] == server.port
        finally:
            server.shutdown()

    def test_status_stale_port_is_not_running(self, store_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        from know_your_context.ops.webview_control import write_prefs

        # Port 1 is never a real webview; status must not trust the file.
        write_prefs(store_path, enabled=True, port=1)
        _, payload = run(capsys, "--store", store_path, "webview", "status")
        assert payload["running"] is False


class TestSetup:
    def test_setup_writes_progress_and_finishes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        store = str(tmp_path / "setup.sqlite3")
        code, payload = run(capsys, "--store", store, "setup", "--no-webview")
        assert code == 0
        assert payload["state"] == "done"
        progress = json.loads(progress_path_for(store).read_text(encoding="utf-8"))
        assert progress["state"] == "done"
        assert all(step["status"] in ("done", "skipped") for step in progress["steps"])
        assert prefs_path_for(store).is_file()  # the default preference is recorded

    def test_setup_serves_progress_live(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """The webview is alive mid-install: /api/setup answers during the run."""
        import http.client
        import urllib.parse

        from know_your_context.ops.setup_flow import run_setup

        store = str(tmp_path / "setup-live.sqlite3")
        observed: list[tuple[int, dict[str, Any]]] = []

        def _hook(url: str) -> None:
            parsed = urllib.parse.urlsplit(url)
            token = urllib.parse.parse_qs(parsed.query)["token"][0]
            port = int(str(parsed.netloc).rsplit(":", 1)[1])
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("GET", f"/api/setup?token={token}")
                response = connection.getresponse()
                observed.append((response.status, json.loads(response.read())))
            finally:
                connection.close()

        report = run_setup(store, port=0, serve_webview=True, on_serving=_hook)
        assert report["state"] == "done"
        # The hook fired mid-run: the webview answered while steps were open.
        assert observed, "the webview never served during install"
        status, payload = observed[0]
        assert status == 200
        assert payload["progress"]["state"] == "running"
        statuses = [step["status"] for step in payload["progress"]["steps"]]
        assert "done" in statuses and ("pending" in statuses or "running" in statuses)
        capsys.readouterr()

    def test_setup_respects_disabled_webview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Persisted off = the post-install webview shuts down with the flow."""
        from know_your_context.ops.setup_flow import run_setup

        store = str(tmp_path / "setup-off.sqlite3")
        report = run_setup(store, port=0, serve_webview=True)
        assert report["state"] == "done"
        assert report["webview_kept"] is False
        capsys.readouterr()
