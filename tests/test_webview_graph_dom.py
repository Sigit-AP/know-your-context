"""DOM-level graph interaction tests (W18 SUB-146).

Runs the real ``webview_assets/app.js`` inside a minimal DOM shim under node
(``tests/webview_dom_harness.mjs``) and exercises the graph mechanics end to
end: node click expands the card and slides the properties panel in, the
panel × and empty-canvas clicks collapse it, edges are cubic beziers that
re-bend on expand, HUD zoom updates the transform, and tab clicks drive the
shell (view activation + breadcrumb). Skipped only when no node binary is
available on the host.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "webview_dom_harness.mjs"


def _node() -> str | None:
    return shutil.which("node") or shutil.which("node", path="/root/.hermes/tools/node-26.12.1-linux-x64/bin")


def test_graph_interactions_dom_level() -> None:
    node = _node()
    if node is None:
        pytest.skip("no node binary on this host")
    result = subprocess.run(  # noqa: S603 - fixed binary path, fixed args
        [node, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
    assert result.returncode == 0 and not failures, f"graph DOM harness failed:\n{result.stdout}\n{result.stderr}"
    assert result.stdout.strip().endswith("passed")


def test_graph_interactions_deterministic() -> None:
    node = _node()
    if node is None:
        pytest.skip("no node binary on this host")
    runs = []
    for _ in range(2):
        result = subprocess.run(  # noqa: S603 - fixed binary path, fixed args
            [node, str(HARNESS)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        runs.append(result.stdout)
    assert runs[0] == runs[1]
