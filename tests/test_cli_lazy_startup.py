"""B4 (SUB-173): the CLI entrypoint must not pay the whole import graph.

Measured baseline (stress-3-products §6 B4): ``--version`` took 1.30-1.80 s
against 0.14-0.17 s for the reference products, because ``cli.py`` imported
``http_api`` (→ extraction → projection → networkx → pypdf → uvicorn),
``security``, ``update_pipeline`` and ``domain`` (pydantic) eagerly even for a
command that touches no store. These tests pin the fix: the module-level import
of ``know_your_context.cli`` loads no heavy subsystem, and a ``--version``
invocation finishes well inside the 0.3 s target.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every module whose import is the reason the CLI floor was 1.2 s. None of them
# may be loaded by importing the CLI module itself: each command imports what it
# needs, when it needs it.
HEAVY_MODULES = (
    "know_your_context.http_api",
    "know_your_context.store",
    "know_your_context.domain",
    "know_your_context.security",
    "know_your_context.update_pipeline",
    "know_your_context.extraction",
    "know_your_context.projection",
    "networkx",
    "pypdf",
    "uvicorn",
    "pydantic",
)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_importing_the_cli_module_loads_no_heavy_subsystem() -> None:
    heavy = sorted(set(HEAVY_MODULES))
    script = (
        "import sys\n"
        "import know_your_context.cli  # noqa: F401\n"
        f"print('\\n'.join(sorted(m for m in sys.modules if m in {heavy!r})))\n"
    )
    result = _run(["-c", script])
    assert result.returncode == 0, result.stderr
    loaded = [line for line in result.stdout.splitlines() if line.strip()]
    assert loaded == [], f"eager imports kept the CLI floor high: {loaded}"


def test_version_invocation_finishes_under_the_startup_target() -> None:
    samples: list[float] = []
    for _ in range(3):
        started = time.perf_counter()
        result = _run(["-m", "know_your_context", "--version"])
        samples.append(time.perf_counter() - started)
        assert result.returncode == 0, result.stderr
        assert "0.3.0" in result.stdout
    # 0.3 s is the target measured on an idle host; the subprocess interpreter
    # start (~40 ms) plus a generous CI margin keeps this assertion about the
    # 4x import win, not about host noise.
    assert min(samples) < 0.6, f"--version floor is still too high: {samples}"


def test_version_and_help_do_not_open_a_store(tmp_path: Path) -> None:
    store = tmp_path / "must-not-exist.sqlite3"
    result = _run(["-m", "know_your_context", "--store", str(store), "--version"])
    assert result.returncode == 0
    assert not store.exists(), "a --version invocation must not create the store"
