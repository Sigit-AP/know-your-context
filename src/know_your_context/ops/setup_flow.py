"""The install flow (W17 SUB-138): webview alive from the first step to done.

``run_setup`` drives the first-run install — create/migrate the store, probe
hardware, verify the chain — while a real loopback webview serves
``/api/setup`` so the operator (or the browser that just opened) watches the
progress live. The webview never dies before the install completes; at
handover it stays up only when the persisted preference says enabled.

Kill switch: ``serve_webview=False`` runs the steps without any server.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from know_your_context.ops.webview_control import SetupProgress, read_prefs, write_prefs
from know_your_context.store import SCHEMA_VERSION, EventStore

__all__ = ["SETUP_STEPS", "run_setup"]

SETUP_STEPS: tuple[str, ...] = ("init store", "webview live", "hardware probe", "verify chain")


def _step_init_store(progress: SetupProgress, store_path: str) -> None:
    progress.begin("init store")
    store = EventStore(store_path)
    store.close()
    progress.done("init store", detail=f"schema v{SCHEMA_VERSION}")


def _step_webview(progress: SetupProgress, store_path: str, port: int, serve: bool) -> Any:
    """Start the live progress webview (or record the kill-switch skip)."""
    if not serve:
        progress.skip("webview live", "serve_webview=False")
        return None
    from know_your_context.serve.webview import WebviewServer

    progress.begin("webview live")
    server = WebviewServer(store_path, port=port)
    server.start()
    progress.done("webview live", detail=server.url)
    return server


def _step_hardware(progress: SetupProgress, report: dict[str, Any]) -> None:
    progress.begin("hardware probe")
    try:
        from know_your_context.hardware.probe import probe_hardware

        profile = probe_hardware()
    except Exception as error:  # noqa: BLE001 - probe failure is non-critical
        progress.skip("hardware probe", f"{type(error).__name__}: {error}")
        return
    progress.done(
        "hardware probe",
        detail=f"{profile.cpu_count} cores, {profile.disk.kind} disk, gpu={profile.gpu.kind}",
    )
    report["hardware"] = {"cpu_count": profile.cpu_count, "disk": str(profile.disk.kind)}


def _step_verify(progress: SetupProgress, store_path: str, report: dict[str, Any]) -> bool:
    progress.begin("verify chain")
    store = EventStore(store_path)
    try:
        chain = store.verify_chain()
    finally:
        store.close()
    if not chain.ok:
        progress.fail("verify chain", f"chain broken at seq {chain.first_bad_seq}")
        report["state"] = "failed"
        return False
    progress.done("verify chain", detail=f"{chain.checked} events verified")
    report["events_verified"] = chain.checked
    return True


def run_setup(
    store_path: str | Path,
    *,
    port: int = 0,
    serve_webview: bool = True,
    hold: bool = False,
    on_serving: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the install flow; returns the final report (never raises on step
    failure — a failed step fails the report, typed, with the progress file
    marked ``failed``).

    ``hold=True`` blocks on the webview after a successful install when the
    persisted preference enables it (the CLI's behavior); tests pass
    ``hold=False`` and read ``webview_kept`` for the same verdict.
    ``on_serving(url)`` fires once, right after the webview starts, while
    steps are still open — the proof the UI is alive mid-install.
    """
    store_path = str(store_path)
    progress = SetupProgress(store_path, list(SETUP_STEPS))
    report: dict[str, Any] = {"state": "running", "store": store_path, "webview_kept": False, "url": None}

    server: Any = None
    try:
        _step_init_store(progress, store_path)
        server = _step_webview(progress, store_path, port, serve_webview)
        if server is not None:
            report["url"] = server.url
            if on_serving is not None:
                on_serving(server.url)
        _step_hardware(progress, report)
        if not _step_verify(progress, store_path, report):
            return report

        progress.finish()
        report["state"] = "done"

        prefs = read_prefs(store_path)
        if prefs["enabled"] is None:
            # First install: record the default explicitly (off until the
            # operator opts in with `webview on`).
            write_prefs(store_path, enabled=False, port=None)
        report["webview_kept"] = bool(prefs["enabled"]) and server is not None
        if server is not None and hold and report["webview_kept"]:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return report
    finally:
        if server is not None and not (hold and report.get("webview_kept")):
            server.shutdown()
