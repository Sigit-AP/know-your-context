"""Tests for the async lifecycle lane of serve/webview.py (W13).

The sync WebviewServer is untouched; AsyncWebviewServer wraps it with
heartbeat-supervised serving, bounded async drain, and watchdog integration.
Real loopback HTTP probes with explicit socket timeouts.
"""

from __future__ import annotations

import asyncio
import http.client
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from know_your_context import runtime_guard
from know_your_context.serve.watchdog import Watchdog, WatchdogPolicy, WorkerState
from know_your_context.serve.webview import AsyncWebviewServer, WebviewServer
from know_your_context.store import EventStore


# The SocketGuard dance, mirrored from test_webview.py (SUB-101 documented the
# leak): a process-lifetime guard denies even loopback AF_INET once installed,
# so real-HTTP tests restore the true originals for the duration of the test.
def _suspend_socket_guard() -> dict[str, Any] | None:
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


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "webview.sqlite3"
    EventStore(path).close()
    return path


@pytest.fixture()
def server(store_path: Path) -> Any:
    instance = WebviewServer(store_path, port=0, token="async-test-token-0000")
    yield instance
    instance.shutdown()


def _get(port: int, path: str, *, timeout: float = 5.0) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


class TestLifecycle:
    def test_start_serve_stop(self, server: Any) -> None:
        async def main() -> tuple[int, int]:
            wrapper = AsyncWebviewServer(server)
            await wrapper.start()
            status, body = await asyncio.to_thread(_get, server.port, f"/?token={server.token}")
            assert status == 200 and b"<html" in body
            drained = await wrapper.stop(timeout_ms=5000)
            assert drained
            return status, 200

        asyncio.run(main())
        with pytest.raises(OSError):  # nothing listens anymore
            _get(server.port, "/", timeout=1.0)

    def test_double_start_rejected(self, server: Any) -> None:
        async def main() -> None:
            wrapper = AsyncWebviewServer(server)
            await wrapper.start()
            try:
                with pytest.raises(AssertionError):
                    await wrapper.start()
            finally:
                await wrapper.stop(timeout_ms=5000)

        asyncio.run(main())

    def test_stop_without_start_is_clean(self, server: Any) -> None:
        async def main() -> bool:
            return await AsyncWebviewServer(server).stop(timeout_ms=1000)

        assert asyncio.run(main()) is True

    def test_heartbeat_beats_while_idle(self, server: Any) -> None:
        async def main() -> int:
            wrapper = AsyncWebviewServer(server, heartbeat_poll_seconds=0.05)
            await wrapper.start()
            assert wrapper.heartbeat is not None
            before = wrapper.heartbeat.beat_count
            await asyncio.sleep(0.3)
            after = wrapper.heartbeat.beat_count
            await wrapper.stop(timeout_ms=5000)
            return after - before

        assert asyncio.run(main()) >= 2

    def test_stop_cancellation_propagates(self, server: Any) -> None:
        async def main() -> str:
            wrapper = AsyncWebviewServer(server)
            await wrapper.start()
            task = asyncio.create_task(wrapper.stop(timeout_ms=60_000))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return "cancelled"

        result = asyncio.run(asyncio.wait_for(main(), timeout=10.0))
        assert result == "cancelled"
        server.shutdown()  # the abandoned server still drains via the sync API


class TestWatchdogIntegration:
    def test_worker_registered_and_healthy(self, server: Any) -> None:
        async def main() -> tuple[tuple[str, ...], bool]:
            dog = Watchdog(policy=WatchdogPolicy(suspect_seconds=30.0, stall_seconds=90.0, check_interval_seconds=0.05))
            wrapper = AsyncWebviewServer(server, watchdog=dog)
            await wrapper.start()
            await asyncio.sleep(0.15)
            assert dog.check_once() == []  # beating → no escalation
            names = dog.worker_names()
            await wrapper.stop(timeout_ms=5000)
            report = dog.drain_all(timeout_ms=2000)
            return names, report.all_drained

        names, drained = asyncio.run(main())
        assert any("webview" in name for name in names)
        assert drained

    def test_dead_serve_thread_marked_for_supervisor(self, server: Any) -> None:
        """When the serve loop dies, the supervised worker reports DEAD."""

        async def main() -> WorkerState:
            wrapper = AsyncWebviewServer(server)
            await wrapper.start()
            assert wrapper.worker is not None
            server._httpd.server_close()  # yank the socket: handle_request raises
            deadline = time.monotonic() + 5.0
            while wrapper.worker.state not in (WorkerState.DEAD,) and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            state = wrapper.worker.state
            await wrapper.stop(timeout_ms=5000)
            return state

        assert asyncio.run(main()) is WorkerState.DEAD


class TestDrainSemantics:
    def test_stop_is_bounded(self, server: Any) -> None:
        async def main() -> float:
            wrapper = AsyncWebviewServer(server, heartbeat_poll_seconds=0.05)
            await wrapper.start()
            started = time.monotonic()
            drained = await wrapper.stop(timeout_ms=3000)
            elapsed = time.monotonic() - started
            assert drained
            return elapsed

        assert asyncio.run(main()) < 3.5

    def test_serves_until_stop_under_load(self, server: Any) -> None:
        async def main() -> int:
            wrapper = AsyncWebviewServer(server)
            await wrapper.start()
            hits = 0
            for _ in range(5):
                status, _ = await asyncio.to_thread(_get, server.port, f"/api/health?token={server.token}")
                assert status == 200
                hits += 1
            await wrapper.stop(timeout_ms=5000)
            return hits

        assert asyncio.run(main()) == 5
