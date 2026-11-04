"""Webview lifecycle control: persisted on/off preference + setup progress (W17).

The webview is opt-out-able and honest: the preference is a sidecar file
(atomic writes, corrupt = typed error), the status command reconciles the
file with a live probe, and install progress is a sidecar the Setup tab
renders live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.ops.webview_control import (
    SetupProgress,
    WebviewPrefsError,
    prefs_path_for,
    progress_path_for,
    read_prefs,
    read_progress,
    write_prefs,
)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "s.sqlite3"
    path.touch()
    return path


class TestPrefs:
    def test_unset_reads_none(self, store: Path) -> None:
        prefs = read_prefs(store)
        assert prefs["enabled"] is None
        assert prefs["port"] is None

    def test_roundtrip(self, store: Path) -> None:
        write_prefs(store, enabled=True, port=8484)
        prefs = read_prefs(store)
        assert prefs["enabled"] is True
        assert prefs["port"] == 8484
        assert prefs["updated_at"]

    def test_off_persists(self, store: Path) -> None:
        write_prefs(store, enabled=False)
        assert read_prefs(store)["enabled"] is False

    def test_atomic_no_partial_write(self, store: Path) -> None:
        write_prefs(store, enabled=True)
        text = prefs_path_for(store).read_text(encoding="utf-8")
        assert json.loads(text)["enabled"] is True
        assert not list(prefs_path_for(store).parent.glob("*.webview.json.*"))

    def test_corrupt_file_raises_typed(self, store: Path) -> None:
        prefs_path_for(store).write_text("{ not json", encoding="utf-8")
        with pytest.raises(WebviewPrefsError, match="corrupt"):
            read_prefs(store)

    def test_non_dict_file_raises_typed(self, store: Path) -> None:
        prefs_path_for(store).write_text("[1,2]", encoding="utf-8")
        with pytest.raises(WebviewPrefsError, match="corrupt"):
            read_prefs(store)

    def test_bad_enabled_raises_typed(self, store: Path) -> None:
        prefs_path_for(store).write_text('{"enabled": "yes"}', encoding="utf-8")
        with pytest.raises(WebviewPrefsError, match="corrupt"):
            read_prefs(store)

    @pytest.mark.parametrize("port", [0, 70000, -1, "8484"])
    def test_bad_port_rejected(self, store: Path, port: object) -> None:
        with pytest.raises(WebviewPrefsError):
            write_prefs(store, enabled=True, port=port)  # type: ignore[arg-type]


class TestSetupProgress:
    def test_idle_when_no_file(self, store: Path) -> None:
        progress = read_progress(store)
        assert progress["state"] == "idle"
        assert progress["steps"] == []

    def test_full_lifecycle(self, store: Path) -> None:
        progress = SetupProgress(store, ["choose-mode", "init store", "verify"])
        snapshot = read_progress(store)
        assert snapshot["state"] == "running"
        assert [step["status"] for step in snapshot["steps"]] == ["pending", "pending", "pending"]

        progress.done("choose-mode", detail="webview")
        progress.begin("init store")
        assert read_progress(store)["steps"][1]["status"] == "running"
        progress.done("init store", detail="schema v5")
        snapshot = read_progress(store)
        assert snapshot["steps"][1]["status"] == "done"
        assert snapshot["steps"][1]["detail"] == "schema v5"
        progress.begin("verify")
        progress.done("verify")
        progress.finish()
        assert read_progress(store)["state"] == "done"

    def test_finish_with_open_steps_refused(self, store: Path) -> None:
        progress = SetupProgress(store, ["a", "b"])
        progress.done("a")
        with pytest.raises(WebviewPrefsError, match="still open"):
            progress.finish()

    def test_fail_marks_whole_setup_failed(self, store: Path) -> None:
        progress = SetupProgress(store, ["a", "b"])
        progress.begin("a")
        progress.fail("a", "disk full")
        snapshot = read_progress(store)
        assert snapshot["state"] == "failed"
        assert snapshot["steps"][0]["status"] == "failed"
        assert snapshot["steps"][0]["detail"] == "disk full"

    def test_unknown_step_raises(self, store: Path) -> None:
        progress = SetupProgress(store, ["a"])
        with pytest.raises(WebviewPrefsError, match="unknown setup step"):
            progress.begin("nope")

    def test_empty_plan_rejected(self, store: Path) -> None:
        with pytest.raises(WebviewPrefsError):
            SetupProgress(store, [])

    def test_every_transition_is_durable(self, store: Path) -> None:
        progress = SetupProgress(store, ["a"])
        path = progress_path_for(store)
        assert path.is_file()
        progress.begin("a")
        assert json.loads(path.read_text(encoding="utf-8"))["steps"][0]["status"] == "running"

    def test_corrupt_progress_raises_typed(self, store: Path) -> None:
        progress_path_for(store).write_text("nope", encoding="utf-8")
        with pytest.raises(WebviewPrefsError, match="corrupt"):
            read_progress(store)
