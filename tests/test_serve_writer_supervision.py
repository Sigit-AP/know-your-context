"""Writer-thread supervision drills (TASK2 WS-E: finding D1).

D1 (cluster-a verdict): the ``_WriteArbiter`` thread — the ONLY writer of
the store — was the one daemon thread not registered with the worker
watchdog, and its ``_run`` loop has no outermost guard. A crash (e.g. a
failed projection publish) left the daemon "half alive": reads served,
every write queued forever. The writer is now a first-class supervised
worker: heartbeat per loop iteration, budgeted restart through the same
``SupervisedThread`` adapter as the other four lanes, single-writer
semantics enforced by the respawn guard (never spawn over a live thread).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from _serve_helpers import make_config, wait_for
from know_your_context.serve.superpower import SuperpowerDaemon

_WATCHDOG_KNOBS: dict[str, Any] = {
    "idle_timeout_seconds": 120.0,
    "watchdog_check_interval_seconds": 0.05,
    "watchdog_suspect_seconds": 0.2,
    "watchdog_stall_seconds": 2.0,
    "watchdog_max_restarts": 2,
    "watchdog_restart_window_seconds": 30.0,
    # read_publish_wait must stay below stall (daemon_config fail-closed rule).
    "read_publish_wait_seconds": 1.0,
}


@pytest.fixture()
def daemon(tmp_path: Path) -> Any:
    config = make_config(tmp_path, **_WATCHDOG_KNOBS)
    instance = SuperpowerDaemon(config)
    instance.start()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, config
    instance.shutdown("test-teardown")
    thread.join(timeout=15)


def _write_node(daemon: SuperpowerDaemon, node_id: str, key: str) -> dict[str, Any]:
    assert daemon._arbiter is not None  # noqa: SLF001 - drill target
    return daemon._arbiter.submit_mutation(  # noqa: SLF001 - drill target
        "knowledge_log_event",
        {
            "node": {
                "node_id": node_id,
                "parent_id": "P1",
                "kind": "leaf",
                "domain": "P1",
                "summary": "writer supervision drill node",
                "provenance": ["context://drill"],
                "source_version_id": "sv-drill",
            },
            "idempotency_key": key,
        },
        actor="drill",
    )


def test_writer_is_registered_and_supervised(daemon: Any) -> None:
    instance, config = daemon
    watchdog = instance._worker_watchdog  # noqa: SLF001 - drill target
    assert watchdog is not None
    names = set(watchdog.worker_names())
    expected = {f"read-{i}" for i in range(config.read_pool_size)} | {"heavy", "acceptor", "reaper", "writer"}
    assert names == expected
    writer = watchdog._workers["writer"]  # noqa: SLF001 - drill target
    assert writer.is_alive()
    first = writer.heartbeat.beat_count
    assert wait_for(lambda: writer.heartbeat.beat_count > first, timeout=5.0), (
        "the writer must beat while idle (one beat per loop wakeup)"
    )


def test_crashed_writer_is_restarted_and_writes_do_not_hang(daemon: Any) -> None:
    """The drill: inject a crash into the writer loop; the watchdog must
    restart it and a write submitted across the crash must complete."""
    instance, _ = daemon
    arbiter = instance._arbiter  # noqa: SLF001 - drill target
    assert arbiter is not None
    crashed_thread = arbiter._thread  # noqa: SLF001

    original_drain = arbiter._drain_audit  # noqa: SLF001

    def crash_once() -> None:
        arbiter._drain_audit = original_drain  # noqa: SLF001 - one-shot: restore before dying
        raise RuntimeError("injected writer crash (D1 drill)")

    arbiter._drain_audit = crash_once  # noqa: SLF001
    assert wait_for(
        lambda: (
            arbiter._thread is not crashed_thread  # noqa: SLF001
            and not crashed_thread.is_alive()
            and arbiter._thread.is_alive()
        ),  # noqa: SLF001
        timeout=10.0,
    ), "the watchdog never replaced the crashed writer with a fresh thread"
    # The watchdog restarted the writer with a FRESH thread.
    assert arbiter._thread is not crashed_thread  # noqa: SLF001
    assert arbiter._thread.is_alive()  # noqa: SLF001
    # The write path across the crash completes (the queue did not hang).
    outcome: dict[str, Any] = {}
    failure: list[BaseException] = []

    def submit() -> None:
        try:
            outcome["result"] = _write_node(instance, "n-writer-drill", "drill-1")
        except BaseException as error:  # noqa: BLE001 - recorded for the assertion
            failure.append(error)

    client = threading.Thread(target=submit, daemon=True)
    client.start()
    client.join(timeout=15.0)
    assert not client.is_alive(), "write hung across the writer restart"
    assert not failure, f"write failed across the writer restart: {failure}"
    assert not outcome["result"].get("isError"), outcome
    # The restart is observable, not silent.
    alerts = [entry["kind"] for entry in instance._worker_alerts if entry["worker"] == "writer"]  # noqa: SLF001
    assert "restart" in alerts


def test_writer_respawn_refuses_a_live_thread(daemon: Any) -> None:
    """Single-writer invariant: the respawn factory must never duplicate the
    queue consumer — respawning over a live writer is a programmer error."""
    instance, _ = daemon
    arbiter = instance._arbiter  # noqa: SLF001
    assert arbiter is not None
    assert arbiter._thread.is_alive()  # noqa: SLF001
    with pytest.raises(AssertionError):
        arbiter._respawn()  # noqa: SLF001 - drill targets the guard
    assert arbiter._thread.is_alive()  # noqa: SLF001


def test_writer_restart_budget_exhaustion_is_terminal(daemon: Any) -> None:
    """A writer that keeps crashing burns the restart budget and ends DEAD +
    terminal (flapping is surfaced, never hidden) — then the watchdog stops
    restarting it."""
    instance, _ = daemon
    arbiter = instance._arbiter  # noqa: SLF001
    assert arbiter is not None
    watchdog = instance._worker_watchdog  # noqa: SLF001
    assert watchdog is not None

    def always_crash() -> None:
        raise RuntimeError("injected persistent writer crash (D1 drill)")

    arbiter._drain_audit = always_crash  # noqa: SLF001 - every incarnation crashes
    writer = watchdog._workers["writer"]  # noqa: SLF001
    assert wait_for(lambda: writer.terminal, timeout=30.0), "restart budget never exhausted"
    assert writer.state == "dead"
    kinds = [entry["kind"] for entry in instance._worker_alerts if entry["worker"] == "writer"]  # noqa: SLF001
    assert kinds.count("restart") == 2  # watchdog_max_restarts
    assert "dead" in kinds
    # Restore a healthy drain so the daemon's own teardown stays clean.
    arbiter._drain_audit = type(arbiter)._drain_audit.__get__(arbiter)  # noqa: SLF001
