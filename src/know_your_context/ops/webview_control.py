"""Webview lifecycle control + setup progress (W17 SUB-138).

Two persisted sidecars next to the store file (never inside it — the store's
schema is frozen at v5):

* ``<store>.webview.json`` — the operator's persisted preference:
  ``{"enabled": bool, "port": int|null, "updated_at": iso}``. Written
  atomically (tmp + rename). ``webview on/off`` flips it; ``webview status``
  reconciles it with a live probe (a pidfile alone never proves liveness).
* ``<store>.setup-progress.json`` — the install-time progress feed the
  webview's Setup tab renders live: an ordered step list with per-step
  status, written atomically after every transition.

Both files are advisory: a corrupt file is reported, never trusted silently.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "SetupProgress",
    "WebviewPrefsError",
    "prefs_path_for",
    "progress_path_for",
    "read_prefs",
    "read_progress",
    "write_prefs",
]


class WebviewPrefsError(RuntimeError):
    """A lifecycle control failure (typed)."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def prefs_path_for(store_path: str | Path) -> Path:
    """The persisted preference sidecar for one store file."""
    return Path(str(store_path) + ".webview.json")


def progress_path_for(store_path: str | Path) -> Path:
    """The install-progress sidecar for one store file."""
    return Path(str(store_path) + ".setup-progress.json")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_prefs(store_path: str | Path) -> dict[str, Any]:
    """The persisted preference; the default ``enabled: None`` means unset.

    ``enabled=None`` lets the caller apply its own default while still
    distinguishing "user never said" from "user said off".
    """
    path = prefs_path_for(store_path)
    if not path.is_file():
        return {"enabled": None, "port": None, "updated_at": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WebviewPrefsError(f"prefs file {path} is corrupt: {error}") from error
    if not isinstance(payload, dict):
        raise WebviewPrefsError(f"prefs file {path} is corrupt: top level must be an object")
    enabled = payload.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise WebviewPrefsError(f"prefs file {path} is corrupt: enabled must be boolean")
    port = payload.get("port")
    if port is not None and (not isinstance(port, int) or not 0 < port < 65536):
        raise WebviewPrefsError(f"prefs file {path} is corrupt: port must be in 1..65535")
    return {"enabled": enabled, "port": port, "updated_at": payload.get("updated_at")}


def write_prefs(store_path: str | Path, *, enabled: bool, port: int | None = None) -> dict[str, Any]:
    """Persist the preference atomically and return the stored payload."""
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536):
        raise WebviewPrefsError(f"port must be an int in 1..65535, got {port!r}")
    payload = {"enabled": bool(enabled), "port": port, "updated_at": _now_iso()}
    _write_json_atomic(prefs_path_for(store_path), payload)
    return payload


_STEP_STATUSES = ("pending", "running", "done", "failed", "skipped")


@dataclass
class SetupProgress:
    """The install-time progress writer: one sidecar, rewritten per transition.

    Steps are declared up front (the install plan), then transitioned one at
    a time. Every transition rewrites the file atomically, so a polling reader
    (the webview's Setup tab) always sees a complete, consistent document.
    """

    store_path: str | Path
    steps: list[str]
    state: str = "running"
    _statuses: dict[str, str] = field(default_factory=dict)
    _details: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.steps:
            raise WebviewPrefsError("setup progress needs at least one step")
        self._statuses = {name: "pending" for name in self.steps}
        self._flush()

    def _payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "steps": [
                {"name": name, "status": self._statuses[name], "detail": self._details.get(name, "")}
                for name in self.steps
            ],
            "updated_at": _now_iso(),
        }

    def _flush(self) -> None:
        _write_json_atomic(progress_path_for(self.store_path), self._payload())

    def _set(self, step: str, status: str, detail: str = "") -> None:
        if step not in self._statuses:
            raise WebviewPrefsError(f"unknown setup step {step!r}")
        if status not in _STEP_STATUSES:
            raise WebviewPrefsError(f"unknown step status {status!r}")
        self._statuses[step] = status
        if detail:
            self._details[step] = detail
        self._flush()

    def begin(self, step: str, detail: str = "") -> None:
        self._set(step, "running", detail)

    def done(self, step: str, detail: str = "") -> None:
        self._set(step, "done", detail)

    def skip(self, step: str, detail: str = "") -> None:
        self._set(step, "skipped", detail)

    def fail(self, step: str, detail: str) -> None:
        """Mark the step failed and the whole setup failed (fail loud)."""
        self._set(step, "failed", detail)
        self.state = "failed"
        self._flush()

    def finish(self) -> None:
        """All steps resolved: the setup is complete."""
        open_steps = [name for name, status in self._statuses.items() if status in ("pending", "running")]
        if open_steps:
            raise WebviewPrefsError(f"setup cannot finish: steps still open: {open_steps}")
        self.state = "done"
        self._flush()


def read_progress(store_path: str | Path) -> dict[str, Any]:
    """The install progress; ``state: idle`` when no install ever ran."""
    path = progress_path_for(store_path)
    if not path.is_file():
        return {"state": "idle", "steps": [], "updated_at": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WebviewPrefsError(f"progress file {path} is corrupt: {error}") from error
    if not isinstance(payload, dict) or payload.get("state") not in ("running", "done", "failed"):
        raise WebviewPrefsError(f"progress file {path} is corrupt: bad state")
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise WebviewPrefsError(f"progress file {path} is corrupt: steps must be a list")
    return payload
