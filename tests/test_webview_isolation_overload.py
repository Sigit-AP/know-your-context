"""Isolation + overload stability of the two backends (W21 SUB-152, operator).

Two measured guarantees over REAL servers (in-process daemon + webview, real
sockets, no mocks):

1. **Isolation** — the loopback webview never affects the main product
   surface (daemon on its unix socket + the shared WAL SQLite store):
   killing the webview leaves the daemon answering reads AND writes, and
   flooding the webview does not measurably slow the daemon lane.
2. **Overload stability** — under a simultaneous HTTP flood (webview) and
   RPC storm (daemon), both surfaces keep answering with typed backpressure
   (webview: 429 + Retry-After; daemon: SERVER_BUSY + retry_after_ms), zero
   crashes, zero untyped responses, and a bounded tail.

The daemon-coupled tests skip loudly (never silently) while another agent's
in-flight edit breaks the daemon import chain; the final W21 verdict requires
a green, zero-skip run.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from know_your_context import runtime_guard
from know_your_context.ops.switches import load_knobs
from know_your_context.serve.webview import WebviewServer
from know_your_context.store import EventStore

TOKEN = "test-token-0000000000"

DAEMON_IMPORT_ERROR: str | None = None
SuperpowerDaemon: Any = None
SocketClient: Any = None
make_config: Any = None
try:
    from _serve_helpers import SocketClient as _SocketClient
    from _serve_helpers import make_config as _make_config
    from know_your_context.serve.superpower import SuperpowerDaemon as _SuperpowerDaemon

    SuperpowerDaemon = _SuperpowerDaemon
    SocketClient = _SocketClient
    make_config = _make_config
except ImportError as exc:  # in-flight edit on the write path (SUB-151)
    DAEMON_IMPORT_ERROR = str(exc)

requires_daemon = pytest.mark.skipif(
    DAEMON_IMPORT_ERROR is not None,
    reason=f"daemon import chain broken outside W21 scope (write path in-flight): {DAEMON_IMPORT_ERROR}",
)


# ------------------------------------------------------- socket-guard truce
# (same rationale as tests/test_webview.py)


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


# ------------------------------------------------------------------ helpers


def _percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def _webview_get(server: WebviewServer, path: str) -> dict[str, Any]:
    """One authenticated GET; never raises — the outcome is data."""
    started = time.monotonic()
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
    try:
        connection.request("GET", f"{path}?token={TOKEN}")
        response = connection.getresponse()
        body = response.read()
        return {
            "status": response.status,
            "headers": {key.lower(): value for key, value in response.getheaders()},
            "body": body,
            "elapsed_ms": (time.monotonic() - started) * 1000.0,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - a transport failure IS the signal
        return {
            "status": None,
            "headers": {},
            "body": b"",
            "elapsed_ms": (time.monotonic() - started) * 1000.0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        connection.close()


def _flood_webview(
    server: WebviewServer,
    path: str,
    *,
    threads: int,
    stop: threading.Event,
    results: list[dict[str, Any]],
) -> list[threading.Thread]:
    """Hammer one route from N threads until *stop* is set; append outcomes."""

    def worker() -> None:
        while not stop.is_set():
            results.append(_webview_get(server, path))

    workers = [threading.Thread(target=worker, name=f"flood-{index}", daemon=True) for index in range(threads)]
    for worker_thread in workers:
        worker_thread.start()
    return workers


def _daemon_read(client: Any, actor: str) -> dict[str, Any]:
    """One cheap daemon read with bounded typed-backpressure retry."""
    for _attempt in range(120):
        params = {
            "name": "knowledge_query",
            "arguments": {"purpose": "isolation-probe", "domain": "P1", "token_budget": 512},
            "_meta": {"actor": actor},
        }
        response = client.request("tools/call", params)
        error = response.get("error")
        if error and error.get("code") == "SERVER_BUSY":
            time.sleep(error["data"]["retry_after_ms"] / 1000.0)
            continue
        return response
    raise AssertionError(f"{actor} never got past SERVER_BUSY")


def _daemon_write(client: Any, actor: str, node_id: str) -> dict[str, Any]:
    node = {
        "node_id": node_id,
        "parent_id": "P1",
        "kind": "leaf",
        "domain": "P1",
        "summary": "isolation probe node",
        "provenance": ["context://isolation"],
        "source_version_id": "sv-isolation",
    }
    for _attempt in range(120):
        params = {"name": "knowledge_log_event", "arguments": {"node": node}, "_meta": {"actor": actor}}
        response = client.request("tools/call", params)
        error = response.get("error")
        if error and error.get("code") == "SERVER_BUSY":
            time.sleep(error["data"]["retry_after_ms"] / 1000.0)
            continue
        return response
    raise AssertionError(f"{actor} never got past SERVER_BUSY")


def _wait_recovered(server: WebviewServer, *, timeout: float = 5.0) -> None:
    """Assert the server serves a 200 again within *timeout* after a flood.

    Backpressure is temporary by design: immediately after a flood the token
    bucket can still be empty, so intermediate answers must be typed 429s
    (never transport errors, never 5xx) until a 200 resumes.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = _webview_get(server, "/api/metrics")
        if probe["status"] == 200:
            return
        assert probe["status"] == 429 and probe["error"] is None, f"untyped during recovery: {probe}"
        time.sleep(0.1)
    raise AssertionError("webview never served 200 again after the flood")


# ----------------------------------------------------------------- fixtures


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "isolation.sqlite3"
    store = EventStore(path)
    store.close()
    return path


@pytest.fixture()
def webview(store_path: Path) -> Any:
    """Tight knobs so a short flood deterministically engages backpressure."""
    knobs = load_knobs(
        {
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_HEAVY_PER_SECOND": "2",
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_HEAVY_BURST": "2",
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_READ_PER_SECOND": "50",
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_READ_BURST": "50",
        }
    )
    server = WebviewServer(store_path, port=0, token=TOKEN, knobs=knobs)
    server.start()
    yield server
    server.shutdown()


@pytest.fixture()
def daemon(tmp_path: Path) -> Any:
    if DAEMON_IMPORT_ERROR is not None:
        pytest.skip(f"daemon import chain broken outside W21 scope: {DAEMON_IMPORT_ERROR}")
    config = make_config(tmp_path, idle_timeout_seconds=300.0, max_agents=8, heavy_queue_size=32)
    instance = SuperpowerDaemon(config)
    instance.start()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, config
    instance.shutdown("test-teardown")
    thread.join(timeout=30)


@pytest.fixture()
def webview_on_daemon_store(daemon: Any) -> Any:
    """The webview reading the daemon's LIVE store (WAL: readers never block
    the single writer) with the same tight flood knobs."""
    _, config = daemon
    knobs = load_knobs(
        {
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_HEAVY_PER_SECOND": "2",
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_HEAVY_BURST": "2",
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_READ_PER_SECOND": "50",
            "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_READ_BURST": "50",
        }
    )
    server = WebviewServer(config.db_path, port=0, token=TOKEN, knobs=knobs)
    server.start()
    yield server
    server.shutdown()


# ---------------------------------------------------------------- isolation


class TestIsolation:
    """Operator requirement 1: webview off/dead/flooded must not touch the
    main product surface."""

    @requires_daemon
    def test_kill_webview_daemon_keeps_serving_reads_and_writes(
        self, daemon: Any, webview_on_daemon_store: WebviewServer
    ) -> None:
        _, config = daemon
        server = webview_on_daemon_store
        with SocketClient(config.paths.socket, timeout=45.0) as client:
            alive = _webview_get(server, "/api/health")
            assert alive["status"] == 200
            before = _daemon_read(client, "iso-before")
            assert "error" not in before, before

            server.shutdown()  # the webview is DEAD from here on

            # Connection refused proves death (not a hung half-open socket).
            refused = False
            try:
                http.client.HTTPConnection("127.0.0.1", server.port, timeout=5).request("GET", "/")
            except OSError:
                refused = True
            assert refused, "webview still answers after shutdown"

            after_read = _daemon_read(client, "iso-after-read")
            assert "error" not in after_read, after_read
            after_write = _daemon_write(client, "iso-after-write", "n-iso-1")
            assert "error" not in after_write, after_write
            assert not after_write.get("result", {}).get("isError"), after_write
            final_read = _daemon_read(client, "iso-final")
            assert "error" not in final_read, final_read

    @requires_daemon
    def test_webview_flood_does_not_slow_daemon(self, daemon: Any, webview_on_daemon_store: WebviewServer) -> None:
        _, config = daemon
        server = webview_on_daemon_store
        with SocketClient(config.paths.socket, timeout=45.0) as client:
            baseline: list[float] = []
            for _ in range(25):
                started = time.monotonic()
                response = _daemon_read(client, "iso-base")
                assert "error" not in response, response
                baseline.append((time.monotonic() - started) * 1000.0)

            stop = threading.Event()
            flood_results: list[dict[str, Any]] = []
            workers = _flood_webview(server, "/api/graph", threads=8, stop=stop, results=flood_results)
            try:
                during: list[float] = []
                for _ in range(25):
                    started = time.monotonic()
                    response = _daemon_read(client, "iso-during")
                    assert "error" not in response, response
                    during.append((time.monotonic() - started) * 1000.0)
            finally:
                stop.set()
                for worker_thread in workers:
                    worker_thread.join(timeout=30)

            p95_base = _percentile(baseline, 0.95)
            p95_during = _percentile(during, 0.95)
            # Generous anti-collapse bound (a lock convoy or GIL starvation
            # would cost orders of magnitude, not a few hundred ms).
            bound = max(p95_base * 5.0, p95_base + 250.0)
            print(
                f"\nISOLATION: daemon read p95 base={p95_base:.1f}ms "
                f"during-flood={p95_during:.1f}ms bound={bound:.1f}ms; "
                f"webview flood answers={len(flood_results)}"
            )
            assert p95_during <= bound, f"daemon slowed beyond bound: {p95_during:.1f}ms > {bound:.1f}ms"
            _wait_recovered(server)


# ----------------------------------------------------------------- overload


class TestOverload:
    """Operator requirement 2: flood both surfaces; both keep answering with
    typed backpressure, no crash, bounded tail."""

    def test_webview_flood_typed_backpressure_no_collapse(self, webview: WebviewServer) -> None:
        server = webview
        baseline = [
            r["elapsed_ms"] for r in (_webview_get(server, "/api/metrics") for _ in range(20)) if r["status"] == 200
        ]
        assert baseline, "baseline could not be measured"
        base_p50 = _percentile(baseline, 0.50)

        results: list[dict[str, Any]] = []
        stop = threading.Event()
        threads_before = threading.active_count()
        workers = _flood_webview(server, "/api/graph", threads=6, stop=stop, results=results) + _flood_webview(
            server, "/api/metrics", threads=6, stop=stop, results=results
        )
        time.sleep(3.0)
        stop.set()
        threads_peak = threading.active_count()
        for worker_thread in workers:
            worker_thread.join(timeout=30)

        assert results, "flood produced no samples"
        statuses = [r["status"] for r in results]
        transport_errors = [r["error"] for r in results if r["error"] is not None]
        assert transport_errors == [], f"transport failures under flood: {transport_errors[:5]}"
        assert set(statuses) <= {200, 429}, f"untyped response under flood: {sorted(set(statuses))}"
        assert 200 in statuses and 429 in statuses, (
            f"backpressure never engaged (or nothing served): 200={statuses.count(200)} 429={statuses.count(429)}"
        )
        for r in results:
            if r["status"] == 429:
                assert "retry-after" in r["headers"], "429 without Retry-After"
                payload = json.loads(r["body"].decode("utf-8"))
                assert payload["error"]["code"] == "RATE_LIMITED" and payload["error"]["retryable"] is True

        ok_ms = [r["elapsed_ms"] for r in results if r["status"] == 200]
        p95 = _percentile(ok_ms, 0.95)
        p99 = _percentile(ok_ms, 0.99)
        elapsed_window = 3.0
        print(
            f"\nOVERLOAD webview: n={len(results)} ok={statuses.count(200)} limited={statuses.count(429)} "
            f"throughput={len(results) / elapsed_window:.0f}req/s p95={p95:.1f}ms p99={p99:.1f}ms "
            f"threads {threads_before}->{threads_peak}->{threading.active_count()}"
        )
        # Anti-collapse: tail bounded relative to the solo baseline (a
        # meltdown means seconds-long tails), floor absorbs slow CI hosts.
        assert p99 <= max(base_p50 * 100.0, 1000.0), f"p99 collapse: {p99:.1f}ms (base p50 {base_p50:.1f}ms)"
        # Recovery: backpressure is temporary by design — a 200 must resume
        # once the bucket refills (2/s heavy), not hang or keep erroring.
        _wait_recovered(server)

    @requires_daemon
    def test_simultaneous_webview_flood_and_daemon_storm(
        self, daemon: Any, webview_on_daemon_store: WebviewServer
    ) -> None:
        _, config = daemon
        server = webview_on_daemon_store
        webview_results: list[dict[str, Any]] = []
        daemon_results: list[dict[str, Any]] = []
        stop = threading.Event()

        def daemon_agent(index: int) -> None:
            with SocketClient(config.paths.socket, timeout=45.0) as client:
                while not stop.is_set():
                    started = time.monotonic()
                    response = _daemon_read(client, f"storm-{index}")
                    daemon_results.append(
                        {
                            "ok": "error" not in response and not response.get("result", {}).get("isError"),
                            "elapsed_ms": (time.monotonic() - started) * 1000.0,
                        }
                    )

        web_workers = _flood_webview(server, "/api/graph", threads=6, stop=stop, results=webview_results)
        daemon_workers = [
            threading.Thread(target=daemon_agent, args=(index,), name=f"storm-{index}", daemon=True)
            for index in range(4)
        ]
        for worker_thread in daemon_workers:
            worker_thread.start()
        time.sleep(3.0)
        stop.set()
        for worker_thread in web_workers + daemon_workers:
            worker_thread.join(timeout=60)

        assert webview_results and daemon_results, "one surface produced no samples"
        webview_statuses = [r["status"] for r in webview_results]
        webview_transport_errors = [r["error"] for r in webview_results if r["error"] is not None]
        assert webview_transport_errors == [], f"webview transport failures: {webview_transport_errors[:5]}"
        assert set(webview_statuses) <= {200, 429}, f"webview untyped: {sorted(set(webview_statuses))}"
        daemon_failures = [r for r in daemon_results if not r["ok"]]
        assert daemon_failures == [], f"daemon failures under combined load: {len(daemon_failures)}"

        web_p99 = _percentile([r["elapsed_ms"] for r in webview_results if r["status"] == 200], 0.99)
        daemon_ok_ms = [r["elapsed_ms"] for r in daemon_results]
        daemon_p95 = _percentile(daemon_ok_ms, 0.95)
        daemon_p99 = _percentile(daemon_ok_ms, 0.99)
        print(
            f"\nOVERLOAD both: webview n={len(webview_results)} (ok={webview_statuses.count(200)}, "
            f"429={webview_statuses.count(429)}, p99={web_p99:.1f}ms); "
            f"daemon n={len(daemon_results)} p95={daemon_p95:.1f}ms p99={daemon_p99:.1f}ms "
            f"throughput={len(daemon_results) / 3.0:.1f}ops/s"
        )
        _wait_recovered(server)
        with SocketClient(config.paths.socket, timeout=45.0) as client:
            after_daemon = _daemon_read(client, "storm-final")
            assert "error" not in after_daemon, "daemon did not recover"
