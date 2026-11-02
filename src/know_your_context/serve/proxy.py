"""The thin MCP stdio proxy between a host agent and the superpower daemon.

Mirrors the codegraph proxy mode (RESEARCH-ARCH section 1b): the host sees a
plain MCP stdio server; the proxy answers the handshake locally
(``initialize`` / ``tools/list`` / ``ping`` — byte-identical to the daemon's
answers, from the shared :mod:`know_your_context.serve.rpc` constants) and
forwards everything else over the daemon's Unix socket. The daemon is
auto-spawned when absent. A PPID watchdog (codegraph #277) kills the proxy
when its host dies — including ``kill -9``, where no stdin EOF is owed.

Per-agent audit: the proxy stamps ``params._meta.actor`` onto every
forwarded ``tools/call`` that does not carry one (its own configured actor,
default ``mcp-proxy``); a host-supplied actor is preserved. The daemon
independently rejects actor-less requests, so the audit contract holds even
for clients that bypass the proxy.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any, BinaryIO

from know_your_context.serve import rpc
from know_your_context.serve.daemon_config import DaemonConfig
from know_your_context.serve.superpower import ensure_daemon

__all__ = ["StdioProxy", "run_proxy"]

# Methods answered locally without a daemon round-trip (fast handshake, #172).
_LOCAL_METHODS = frozenset({"initialize", "ping", "tools/list"})

# Early-ppid capture (codegraph #1185): the parent pid is recorded at import
# time — the earliest possible moment in the exec'd proxy process — so a host
# that dies during interpreter startup still leaves a stale reference for the
# watchdog to compare against.
_EARLY_PPID = os.getppid()


def _remaining(deadline: float) -> float:
    """Seconds until the monotonic deadline, clamped at zero."""
    return max(0.0, deadline - time.monotonic())


class StdioProxy:
    """One stdio session (stdin/stdout) bridged to the daemon socket."""

    def __init__(
        self,
        config: DaemonConfig,
        *,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        actor: str | None = None,
        ppid: int | None = None,
        sleep: Any = None,
    ) -> None:
        self._config = config
        self._stdin = stdin if stdin is not None else sys.stdin.buffer
        self._stdout = stdout if stdout is not None else sys.stdout.buffer
        self._actor = actor or config.proxy_actor
        self._ppid = ppid if ppid is not None else _EARLY_PPID
        self._sleep = sleep if sleep is not None else time.sleep  # W13: injectable for DST
        self._socket: socket.socket | None = None
        self._socket_reader: rpc.FrameReader | None = None
        self._socket_lock = threading.Lock()  # send+read is one atomic round-trip
        self._dead = threading.Event()

    # --------------------------------------------------------------- lifecycle

    def run(self) -> int:
        """Serve until the host closes stdin or the PPID watchdog fires."""
        try:
            self._connect()
        except Exception:  # noqa: BLE001 - startup failure must not hang the host
            self._socket = None
        watchdog = threading.Thread(target=self._ppid_watch, name="superpower-ppid", daemon=True)
        watchdog.start()
        threading.Thread(target=self._lease_keepalive, name="superpower-lease", daemon=True).start()
        reader = rpc.FrameReader(self._stdin, max_bytes=self._config.max_frame_bytes)
        while True:
            try:
                frame = reader.read()
            except rpc.ProtocolError as error:
                self._send(rpc.error_response(None, error.code, str(error), error.data))
                return 2
            except ConnectionError, OSError:
                return 0
            if frame is None:
                return 0
            try:
                envelope = rpc.parse_request(frame)
            except rpc.ProtocolError as error:
                self._send(rpc.error_response(None, error.code, str(error), error.data))
                continue
            if envelope.is_notification:
                continue  # MCP lifecycle/cancel notifications: never answered
            response = self._handle(envelope)
            self._send(response)
            if self._dead.is_set():
                return 0

    # --------------------------------------------------------------- dispatch

    def _handle(self, envelope: rpc.RequestEnvelope) -> dict[str, Any]:
        method = envelope.method
        if method == "initialize":
            return rpc.result_response(envelope.id, rpc.initialize_result(envelope.params))
        if method == "ping":
            return rpc.result_response(envelope.id, {})
        if method == "tools/list":
            return rpc.result_response(envelope.id, rpc.tool_listing_result(self._config.max_token_budget))
        if method.startswith("daemon."):
            return rpc.error_response(envelope.id, rpc.SCOPE_DENIED, "admin methods are not exposed to MCP hosts")
        params = envelope.params
        if method == "tools/call":
            params = self._stamp_actor(params)
        forwarded = self._forward(envelope, params)
        if forwarded is not None:
            return forwarded
        return rpc.error_response(
            envelope.id,
            rpc.DAEMON_UNAVAILABLE,
            "the knowledge daemon is unavailable; the proxy could not reach or respawn it",
        )

    def _stamp_actor(self, params: Mapping[str, Any]) -> dict[str, Any]:
        stamped = dict(params)
        meta = stamped.get("_meta")
        meta_dict = dict(meta) if isinstance(meta, Mapping) else {}
        actor = meta_dict.get("actor")
        if not isinstance(actor, str) or not actor.strip():
            meta_dict["actor"] = self._actor
        stamped["_meta"] = meta_dict
        return stamped

    # ------------------------------------------------------------- forwarding

    def _connect(self) -> None:
        from know_your_context.serve.flow_retry import run_with_timeout

        result = ensure_daemon(self._config)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # W13: an explicit connect timeout — a wedged listener that accepts
            # nothing must not hang the host's tool call forever.
            connection.settimeout(self._config.proxy_connect_timeout_seconds)
            run_with_timeout(
                lambda: connection.connect(str(result.socket_path)),
                timeout_ms=int(self._config.proxy_connect_timeout_seconds * 1000),
            )
            # The IO bound is generous (heavy-lane ingests run for minutes)
            # but explicit: a wedged daemon holding the socket open is caught.
            connection.settimeout(self._config.proxy_io_timeout_seconds)
        except BaseException:
            connection.close()
            raise
        self._socket = connection
        self._socket_reader = rpc.FrameReader(connection.makefile("rb"), max_bytes=self._config.max_frame_bytes)

    def _forward(self, envelope: rpc.RequestEnvelope, params: Mapping[str, Any]) -> dict[str, Any] | None:
        """Forward with recovery: a dead daemon is respawned within a budget.

        A spawn under host load legitimately takes seconds, so the retry
        budget is time-based (spawn timeout + margin), not attempt-based.
        W13: the fixed sleeps are now a deterministic exponential backoff
        (``compute_delay_ms``; seeded via ``proxy_retry_seed`` for DST), and
        every socket round-trip carries an explicit timeout bounded by the
        remaining deadline.
        """
        from know_your_context.serve.flow_retry import RetryPolicy, compute_delay_ms

        policy = RetryPolicy(
            max_attempts=1,  # unused: the outer deadline bounds retries
            base_delay_ms=100,
            max_delay_ms=1_000,
            backoff_multiplier=2.0,
            jitter_ratio=0.2,
            seed=self._config.proxy_retry_seed,
        )
        rng = policy.rng()
        attempt = 0
        deadline = time.monotonic() + self._config.spawn_timeout_seconds + 10.0
        while time.monotonic() < deadline:
            attempt += 1
            if self._socket is None or self._socket_reader is None:
                try:
                    self._connect()
                except Exception:  # noqa: BLE001 - retried until the deadline
                    self._sleep(min(compute_delay_ms(attempt, policy, rng=rng) / 1000.0, _remaining(deadline)))
                    continue
            assert self._socket is not None and self._socket_reader is not None
            with self._socket_lock:  # heartbeat and forwards never interleave frames
                try:
                    # Bound the round-trip by both the configured IO timeout
                    # and the remaining retry deadline — whichever bites first.
                    self._socket.settimeout(min(self._config.proxy_io_timeout_seconds, max(0.1, _remaining(deadline))))
                    message: dict[str, Any] = {"jsonrpc": "2.0", "id": envelope.id, "method": envelope.method}
                    if params:
                        message["params"] = dict(params)
                    self._socket.sendall(rpc.encode_message(message))
                    response = self._socket_reader.read()
                except ConnectionError, OSError, rpc.ProtocolError:
                    response = None
            if response is None:
                # Clean EOF, a broken pipe or an IO timeout: the daemon is
                # gone (or wedged) — drop the connection and let the next
                # pass respawn it.
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
                self._socket_reader = None
                self._sleep(min(compute_delay_ms(attempt, policy, rng=rng) / 1000.0, _remaining(deadline)))
                continue
            return response
        return None

    def _lease_keepalive(self) -> None:
        """Renew the daemon session lease on the host's behalf (W7 #6).

        Real MCP hosts do not heartbeat; the proxy does it for them so a
        thinking agent never loses its slot between tool calls.
        """
        interval = max(0.2, self._config.session_lease_seconds / 3)
        while not self._dead.wait(interval):
            if self._socket is None or self._socket_reader is None:
                continue
            try:
                with self._socket_lock:
                    # W13: the keepalive round-trip is tightly bounded — an
                    # unbounded read here would hold the socket lock and
                    # starve the forward path behind a wedged daemon.
                    self._socket.settimeout(max(1.0, interval * 2))
                    self._socket.sendall(
                        rpc.encode_message({"jsonrpc": "2.0", "id": "lease-hb", "method": "session.heartbeat"})
                    )
                    self._socket_reader.read()
            except ConnectionError, OSError, rpc.ProtocolError:
                continue  # the forward path owns recovery

    # ----------------------------------------------------------------- helpers

    def _send(self, message: Mapping[str, Any]) -> None:
        try:
            rpc.write_message(self._stdout, message)
        except OSError, BrokenPipeError:
            self._dead.set()

    def _ppid_watch(self) -> None:
        """Exit when the host process is gone (kill -9 reparents us).

        Reparenting to init (ppid 1) is itself proof of orphaning even when
        the early capture already observed the reparented parent.
        """
        while not self._dead.wait(self._config.ppid_poll_seconds):
            current = os.getppid()
            if current != self._ppid or current == 1:
                os._exit(0)


def run_proxy(
    config: DaemonConfig,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    actor: str | None = None,
) -> int:
    """Run the MCP stdio proxy loop until the host closes stdin.

    Bridges newline-delimited JSON-RPC on stdio to the daemon's unix
    socket: an unrecoverable frame is answered with a typed error frame and
    exits 2; EOF, a dead parent (PPID watchdog) or a daemon-initiated close
    return 0. ``actor`` stamps host requests that carry no
    ``params._meta.actor``; agent requests keep their own actor.
    """
    return StdioProxy(config, stdin=stdin, stdout=stdout, actor=actor).run()
