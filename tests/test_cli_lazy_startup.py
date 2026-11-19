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


#: Subsystems a read-only local verb never needs. Measured (SUB-178, stress3
#: profile of ``query`` at L3 scale): importing ``http_api`` cost 2.83 s of the
#: 3.1 s CLI wall because its module level pulled uvicorn/starlette (serve
#: only), ``ingest.pipeline`` + ``extraction`` (write/HTTP verbs), ``backup``,
#: ``relearn`` and ``observability`` (ops verbs). In the 50-agent storm that
#: import tax is paid by every query process and decided the collapse.
#: SUB-219 (import floor): the write/ops engines moved off the query path too.
#: ``relearn``/``backup``/``review``/``scoring``/``scheduler`` are constructed
#: lazily on first write/ops use (before: ``service`` imported ``relearn`` at
#: module level and built all four engines in ``__init__`` — measured ≈40 MB
#: and ≈425 ms for the service/http_api import graph per process), and
#: ``asyncio``/``update_pipeline``/``source_watcher`` are serve-only (before:
#: ``http_api`` imported them at module scope). ``autoreview`` rides the write
#: path only (its manual-gates default resolves at first write, not at
#: ``KnowledgeService`` construction).
QUERY_FORBIDDEN_MODULES = (
    "uvicorn",
    "starlette",
    "asyncio",  # serve-only (BoundaryMiddleware); SUB-219 made it lazy
    "networkx",  # graphify_adapter's graph algorithms — rebuild path only
    "graphify",  # the vendored graph builder — rebuild path only
    "know_your_context.ingest.pipeline",
    "know_your_context.extraction",
    "know_your_context.observability",
    "know_your_context.relearn",
    "know_your_context.backup",
    "know_your_context.review",
    "know_your_context.scoring",
    "know_your_context.scheduler",
    "know_your_context.autoreview",
    "know_your_context.update_pipeline",  # serve watch-loop only
    "know_your_context.source_watcher",  # serve watch-loop only
)


def test_query_verb_loads_no_serve_or_write_subsystems(tmp_path: Path) -> None:
    """``query`` pays for the service chain only, never the HTTP/ingest stack.

    Two processes: the first publishes the projection (a fresh output root
    legitimately runs one rebuild — networkx/graphify are rebuild-path
    dependencies); the asserted second process then reads a current
    projection, which is the storm's steady state.
    """
    forbidden = sorted(set(QUERY_FORBIDDEN_MODULES))
    store_path = str(tmp_path / "q.sqlite3")
    proj_root = str(tmp_path / "proj")
    seed = (
        "import io, json, sys\n"
        "from know_your_context.store import EventStore\n"
        f"store = EventStore({store_path!r})\n"
        "store.upsert_symbol(node_id='n:1', name='Widget', kind='class', path='w.py', line=1, col=0)\n"
        "store.close()\n"
        "from know_your_context.cli import main\n"
        "sys.stdin = io.StringIO(json.dumps({'purpose': 'Widget', 'token_budget': 512}))\n"
        f"rc = main(['--store', {store_path!r}, '--output-root', {proj_root!r}, 'query', '-'])\n"
        "assert rc == 0, rc\n"
    )
    first = _run(["-c", seed])
    assert first.returncode == 0, first.stderr
    probe = (
        "import io, json, sys\n"
        "from know_your_context.cli import main\n"
        "sys.stdin = io.StringIO(json.dumps({'purpose': 'Widget', 'token_budget': 512}))\n"
        f"rc = main(['--store', {store_path!r}, '--output-root', {proj_root!r}, 'query', '-'])\n"
        "assert rc == 0, rc\n"
        f"loaded = sorted(m for p in {forbidden!r} for m in sys.modules if m == p or m.startswith(p + '.'))\n"
        "print('FORBIDDEN:' + ','.join(loaded))\n"
    )
    result = _run(["-c", probe])
    assert result.returncode == 0, result.stderr
    marker = [line for line in result.stdout.splitlines() if line.startswith("FORBIDDEN:")]
    assert marker and marker[0] == "FORBIDDEN:", f"query loaded serve/write subsystems: {marker}"
