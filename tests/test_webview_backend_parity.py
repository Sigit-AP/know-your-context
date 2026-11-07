"""Backend parity tests (W21 SUB-152): webview vs graphify's real HTTP layer.

Graphify's HTTP transport (``graphify/serve.py`` Streamable HTTP) accepts the
API key via headers — ``X-API-Key`` or ``Authorization: Bearer`` with a
case-insensitive scheme (RFC 6750) — warns on stderr when binding a wildcard
address without auth, states the auth posture in its startup banner, and
shuts down gracefully on SIGTERM (via uvicorn). These tests pin the webview's
parity behavior:

* header auth channels work without a query token and never mint a cookie;
* the operator is warned on stderr for a risky bind/auth posture;
* SIGTERM drains through the same ``finally: shutdown()`` path as SIGINT.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from know_your_context import runtime_guard
from know_your_context.serve.webview import WebviewServer, main, security_notes
from know_your_context.store import EventStore

TOKEN = "test-token-0000000000"


# ------------------------------------------------------- socket-guard truce
# (same rationale as tests/test_webview.py: a leaked process-wide SocketGuard
# from the MCP adapter tests must not deny this real loopback server)


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


@pytest.fixture(scope="module")
def store_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("webview-parity") / "webview.sqlite3"
    store = EventStore(path)
    store.close()
    return path


@pytest.fixture(scope="module")
def server(store_path: Path) -> Any:
    instance = WebviewServer(store_path, port=0, token=TOKEN)
    instance.start()
    yield instance
    instance.shutdown()


def _http(
    server: WebviewServer,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """One request with NO query token: only the given headers can authorize."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, body
    finally:
        connection.close()


# ====================================================================== G1


class TestHeaderAuth:
    """Graphify serve.py:2756-2794 parity: X-API-Key or Authorization Bearer."""

    def test_bearer_token_authorizes_api(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/health", headers={"Authorization": f"Bearer {TOKEN}"})
        assert status == 200
        assert "error" not in json.loads(body.decode("utf-8"))

    def test_bearer_token_authorizes_index(self, server: WebviewServer) -> None:
        status, headers, _ = _http(server, "/", headers={"Authorization": f"Bearer {TOKEN}"})
        assert status == 200
        assert headers["content-type"].startswith("text/html")

    def test_bearer_scheme_is_case_insensitive(self, server: WebviewServer) -> None:
        # RFC 6750 §2.1: the scheme token is case-insensitive (graphify
        # serve.py:2777-2780 lowercases before comparing).
        for scheme in ("bearer", "BEARER", "BeArEr"):
            status, _, _ = _http(server, "/api/health", headers={"Authorization": f"{scheme} {TOKEN}"})
            assert status == 200, scheme

    def test_bearer_token_is_stripped(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/health", headers={"Authorization": f"Bearer  {TOKEN} "})
        assert status == 200

    def test_wrong_bearer_token_is_401(self, server: WebviewServer) -> None:
        status, _, body = _http(server, "/api/health", headers={"Authorization": "Bearer wrong-token-000000"})
        assert status == 401
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "UNAUTHORIZED"

    def test_bearer_without_credentials_is_401(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/health", headers={"Authorization": "Bearer"})
        assert status == 401

    def test_non_bearer_scheme_is_401(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/health", headers={"Authorization": f"Basic {TOKEN}"})
        assert status == 401

    def test_x_api_key_authorizes(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/health", headers={"X-API-Key": TOKEN})
        assert status == 200

    def test_wrong_x_api_key_is_401(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/health", headers={"X-API-Key": "wrong-token-000000"})
        assert status == 401

    def test_header_auth_never_mints_a_cookie(self, server: WebviewServer) -> None:
        # Only the ?token= page flow may establish the browser session cookie;
        # a header-authenticated API client must not become a browser session.
        status, headers, _ = _http(server, "/", headers={"Authorization": f"Bearer {TOKEN}"})
        assert status == 200
        assert "set-cookie" not in headers

    def test_no_credentials_at_all_is_401(self, server: WebviewServer) -> None:
        status, _, _ = _http(server, "/api/health")
        assert status == 401


# ====================================================================== G3/G4


class TestSecurityNotes:
    """Graphify serve.py:2923-2933 parity: the banner must state a risky posture."""

    def test_loopback_with_auth_is_quiet(self) -> None:
        assert security_notes("127.0.0.1", auth_disabled=False) == ()

    def test_wildcard_bind_warns(self) -> None:
        notes = security_notes("0.0.0.0", auth_disabled=False)
        assert len(notes) == 1
        assert "WARNING" in notes[0] and "0.0.0.0" in notes[0]

    def test_auth_disabled_warns(self) -> None:
        notes = security_notes("127.0.0.1", auth_disabled=True)
        assert len(notes) == 1
        assert "WARNING" in notes[0] and "KNOW_YOUR_CONTEXT_WEBVIEW_NO_AUTH" in notes[0]

    def test_wildcard_bind_without_auth_warns_twice(self) -> None:
        assert len(security_notes("0.0.0.0", auth_disabled=True)) == 2

    def test_main_warns_on_wildcard_bind(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_WEBVIEW_HOST", "0.0.0.0")
        assert main(["--store", str(store_path), "--serve-once"]) == 0
        captured = capsys.readouterr()
        assert "WARNING" in captured.err and "0.0.0.0" in captured.err

    def test_main_warns_when_auth_disabled(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_WEBVIEW_NO_AUTH", "1")
        assert main(["--store", str(store_path), "--serve-once"]) == 0
        captured = capsys.readouterr()
        assert "WARNING" in captured.err and "KNOW_YOUR_CONTEXT_WEBVIEW_NO_AUTH" in captured.err

    def test_main_is_quiet_on_loopback_with_auth(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("KNOW_YOUR_CONTEXT_WEBVIEW_HOST", raising=False)
        monkeypatch.delenv("KNOW_YOUR_CONTEXT_WEBVIEW_NO_AUTH", raising=False)
        assert main(["--store", str(store_path), "--serve-once"]) == 0
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err


# ====================================================================== G11


class TestSigtermGracefulShutdown:
    """Graphify gets SIGTERM handling from uvicorn; the webview main() folds
    SIGTERM into the KeyboardInterrupt path so the same finally-drain runs."""

    def test_sigterm_handler_raises_keyboard_interrupt(self) -> None:
        from know_your_context.serve.webview import _sigterm_as_keyboard_interrupt

        with pytest.raises(KeyboardInterrupt):
            _sigterm_as_keyboard_interrupt(signal.SIGTERM, None)

    def test_sigterm_exits_zero_via_subprocess(self, store_path: Path) -> None:
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "know_your_context.serve.webview",
                "--store",
                str(store_path),
                "--port",
                "0",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            banner = process.stdout.readline().decode("utf-8")
            assert banner.startswith("webview: http://"), banner
            process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, f"stderr: {stderr.decode('utf-8')!r}"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
