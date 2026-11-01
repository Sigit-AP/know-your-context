#!/usr/bin/env python3
"""Competitive benchmark harness: know-your-context vs codegraph vs graphify.

SUB-113 (wave 9). Measures three systems on IDENTICAL tasks against the same
pinned corpus (the ``bench/codebase`` dataset: fixture + requests + chalk +
tree-sitter-python, vendored archives pinned by commit + per-file sha256):

* ``ingest``        — index the same tree: wall time, exit code, result counts
* ``cold-start``    — per-query latency of a fresh process (no warm daemon)
* ``concurrent``    — N simultaneous clients × M queries: throughput + errors
* ``crash``         — SIGKILL mid-ingest, then recover: integrity + data loss
* ``determinism``   — two fresh ingests → byte-identical index artifact?
* ``tokens``        — estimated tokens (ceil(utf8 bytes / 4), the frozen
                      contract estimator) per answer to the same queries

A system that cannot perform a task is recorded ``NOT_SUPPORTED`` with the
evidence (e.g. a missing CLI) — that absence is itself the score. Every
number in the ledger carries the exact command that produced it; the ledger
also records the harness-level reproduction command per task.

No network is used: the dataset is the vendored ``bench/codebase`` one, and
both competitor CLIs run fully offline (``CODEGRAPH_TELEMETRY=0``).

Subcommands::

    check    # no-dataset self-checks + system discovery (exit 0/1)
    verify   # pinned-dataset integrity (delegates to bench/codebase)
    run      # full measurement -> results/ledger-<ts>.json + report-<ts>.md

Entry ``status`` semantics: ``OK`` = the task executed and was measured;
``NOT_SUPPORTED`` = the system lacks the capability (evidence attached — the
absence is itself the score); ``FAILED`` = the task could not be executed
(e.g. ingest command errored). Measured *verdicts* (byte-identical or not,
data loss or not, errors under load) live in entry fields — a competitor's
weak score is a measurement, not a harness failure.

Exit codes: 0 = every supported task executed; 1 = a task FAILED to execute,
or (with ``--strict``) any NOT_SUPPORTED; 2 = usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

RESULTS_DIR = BENCH_DIR / "results"
CODEBASE_BENCH = REPO_ROOT / "bench" / "codebase" / "run.py"

HARNESS_VERSION = 1

#: Competitor CLI discovery: env var first (pinned installs), then PATH.
CODEGRAPH_ENV = "CODEGRAPH_BIN"
CODEGRAPH_PKG = "@colbymchenry/codegraph"
CODEGRAPH_INSTALL = f"npm install -g {CODEGRAPH_PKG}"
GRAPHIFY_INSTALL = "install graphify (https://github.com/graphify); expected on PATH as `graphify`"

#: Concurrent-load shape: N clients, each issuing M sequential queries
#: through the system's query interface (one fresh process per query — the
#: only interface CLI systems expose, applied uniformly to all three).
DEFAULT_CLIENTS = 8
DEFAULT_QUERIES_PER_CLIENT = 5
DEFAULT_COLD_REPEATS = 3

#: Crash task kills the ingest at this fraction of the system's own clean
#: ingest wall time (measured by the `ingest` task in the same run).
KILL_FRACTION = 0.4
KILL_MIN_S = 0.3
KILL_MAX_S = 5.0

SUBPROCESS_TIMEOUT_S = 900

# Frozen query sets per target: the lookup queries of the pinned codebase
# bench ground truth (positive + negative), so every system answers the
# same questions over the same corpus. Loaded from ground_truth.json.


# --------------------------------------------------------------- utilities


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def estimated_tokens(text: str | bytes) -> int:
    """Frozen contract estimator: ceil(utf8_byte_length / 4)."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    return math.ceil(len(data) / 4)


def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (same convention as bench/run.py)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def run_cmd(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = SUBPROCESS_TIMEOUT_S,
) -> dict[str, Any]:
    """Run a command; return exit code, wall time and captured output."""
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        wall_ms = (time.perf_counter() - started) * 1000.0
        return {
            "argv": argv,
            "exit_code": proc.returncode,
            "wall_ms": round(wall_ms, 3),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        wall_ms = (time.perf_counter() - started) * 1000.0
        return {
            "argv": argv,
            "exit_code": None,
            "wall_ms": round(wall_ms, 3),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timed_out": True,
        }


def _tail(text: str, n: int = 400) -> str:
    return text[-n:] if len(text) > n else text


# ------------------------------------------------------- dataset (pinned)


def _load_codebase_bench() -> Any:
    """Load bench/codebase/run.py as a module (dataset single source of truth)."""
    spec = importlib.util.spec_from_file_location("kys_codebase_bench_for_competitive", CODEBASE_BENCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dataset_present() -> bool:
    return _load_codebase_bench().dataset_present()


def load_query_sets() -> dict[str, list[str]]:
    """Frozen per-target query lists from the codebase bench ground truth."""
    truth = _load_codebase_bench().load_ground_truth()
    return {name: [q["query"] for q in spec.get("lookups", [])] for name, spec in truth["targets"].items()}


def target_names() -> list[str]:
    cb = _load_codebase_bench()
    return ["fixture", *(spec["name"] for spec in cb.REPOS)]


def prepare_tree(target: str, dest: Path) -> None:
    """Materialize a pinned target tree into dest (offline)."""
    cb = _load_codebase_bench()
    if target == "fixture":
        shutil.copytree(cb.FIXTURE_DIR, dest)
        return
    manifest = cb.load_manifest()
    entry = next(e for e in manifest["repos"] if e["name"] == target)
    problems = cb.verify_repo(entry)
    if problems:
        raise SystemExit(f"dataset integrity failure for {target}: {problems[:3]}")
    cb.extract_archive(cb.BENCH_DIR / entry["archive"]["file"], dest)


def product_module() -> str:
    """The product's python package name (a rename to ``know_your_context``
    is in flight; resolve whichever is importable, prefer the old name)."""
    for name in ("know_your_context", "know_your_context"):
        try:
            __import__(name)
            return name
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError("neither know_your_context nor know_your_context is importable")


# --------------------------------------------------------- system discovery


def find_codegraph() -> str | None:
    override = os.environ.get(CODEGRAPH_ENV)
    if override and Path(override).exists():
        return override
    return shutil.which("codegraph")


def find_graphify() -> str | None:
    return shutil.which("graphify")


def system_report() -> dict[str, Any]:
    """Discover the three systems; returns availability + versions + evidence."""
    report: dict[str, Any] = {}

    try:
        mod = product_module()
        __import__(f"{mod}.ingest.extractor")
        __import__(f"{mod}.store")

        report["know_your_context"] = {
            "available": True,
            "interface": "in-repo python (EventStore + IngestionPipeline + SymbolLookup)",
            "module": mod,
            "driver_python": sys.executable,
        }
    except Exception as exc:  # pragma: no cover - defensive
        report["know_your_context"] = {
            "available": False,
            "evidence": f"import failed: {type(exc).__name__}: {exc}",
        }

    cg = find_codegraph()
    if cg:
        env = dict(os.environ, CODEGRAPH_TELEMETRY="0", NO_COLOR="1")
        res = run_cmd([cg, "version"], env=env, timeout=60)
        version = res["stdout"].strip() or res["stderr"].strip()
        report["codegraph"] = {
            "available": res["exit_code"] == 0,
            "cli": cg,
            "version": version,
            "evidence": f"`codegraph version` exit={res['exit_code']} -> {version!r}",
        }
    else:
        report["codegraph"] = {
            "available": False,
            "evidence": f"cli not found on PATH and ${CODEGRAPH_ENV} unset (install: {CODEGRAPH_INSTALL})",
        }

    gf = find_graphify()
    if gf:
        res = run_cmd([gf, "--version"], timeout=60)
        version = res["stdout"].strip() or res["stderr"].strip()
        report["graphify"] = {
            "available": res["exit_code"] == 0,
            "cli": gf,
            "version": version,
            "evidence": f"`graphify --version` exit={res['exit_code']} -> {version!r}",
        }
    else:
        report["graphify"] = {
            "available": False,
            "evidence": f"cli not found on PATH ({GRAPHIFY_INSTALL})",
        }
    return report


# ------------------------------------------------------- kys driver scripts
#
# The product has no per-query CLI for the code store, so the harness drives
# it through small python -c drivers — one fresh process per invocation,
# exactly like the competitor CLIs.

_KYS_INGEST_DRIVER = r"""
import json, sys
sys.path.insert(0, {src!r})
from {mod}.ingest import extractor
from {mod}.ingest.pipeline import IngestionPipeline, canonical_ingestion_dump
from {mod}.store import EventStore
store = EventStore(sys.argv[1])
pipeline = IngestionPipeline(store, extractor=extractor)
report = pipeline.ingest_path(sys.argv[2], resolve=False)
pipeline.resolve_pending()
with store.read() as conn:
    out = {{
        "symbols": conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"],
        "edges": conn.execute("SELECT COUNT(*) c FROM graph_edges").fetchone()["c"],
        "refs_total": conn.execute("SELECT COUNT(*) c FROM unresolved_refs").fetchone()["c"],
        "refs_resolved": conn.execute(
            "SELECT COUNT(*) c FROM unresolved_refs WHERE status = 'resolved'").fetchone()["c"],
        "files_proposed": report.stats["proposed"],
        "files_failed": report.stats["failed"],
        "dump_sha256": __import__("hashlib").sha256(
            canonical_ingestion_dump(store).encode("utf-8")).hexdigest(),
    }}
store.close()
print("KYS_RESULT " + json.dumps(out, sort_keys=True))
"""

_KYS_QUERY_DRIVER = r"""
import json, sys
sys.path.insert(0, {src!r})
from {mod}.retrieval_v2 import SymbolLookup
from {mod}.store import EventStore
store = EventStore(sys.argv[1])
hits = SymbolLookup().search(store, sys.argv[2], limit=10)
payload = [{{"name": h.name, "kind": h.kind, "path": h.path, "line": h.line, "source": h.source}} for h in hits]
store.close()
print(json.dumps(payload, sort_keys=True))
"""

_KYS_CHECK_DRIVER = r"""
import json, sys
sys.path.insert(0, {src!r})
from {mod}.store import EventStore
store = EventStore(sys.argv[1])
with store.read() as conn:
    out = {{
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
        "symbols": conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"],
        "edges": conn.execute("SELECT COUNT(*) c FROM graph_edges").fetchone()["c"],
        "refs_total": conn.execute("SELECT COUNT(*) c FROM unresolved_refs").fetchone()["c"],
    }}
store.close()
print("KYS_RESULT " + json.dumps(out, sort_keys=True))
"""


def _kys_driver(script: str) -> str:
    return script.format(src=str(REPO_ROOT / "src"), mod=product_module())


def _kys_result(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith("KYS_RESULT "):
            return json.loads(line[len("KYS_RESULT ") :])
    raise ValueError(f"driver printed no KYS_RESULT: {_tail(stdout, 200)!r}")


# ----------------------------------------------------------- entry builder


def entry(
    system: str,
    task: str,
    target: str | None,
    status: str,
    **fields: Any,
) -> dict[str, Any]:
    return {"system": system, "task": task, "target": target, "status": status, **fields}


def not_supported(system: str, task: str, target: str | None, evidence: str) -> dict[str, Any]:
    return entry(system, task, target, "NOT_SUPPORTED", evidence=evidence)


# ------------------------------------------------------------ task: ingest


def _ingest_kys(tree: Path, store_path: Path) -> dict[str, Any]:
    argv = [sys.executable, "-c", _kys_driver(_KYS_INGEST_DRIVER), str(store_path), str(tree)]
    res = run_cmd(argv)
    out: dict[str, Any] = {"command": argv, "exit_code": res["exit_code"], "wall_ms": res["wall_ms"]}
    if res["exit_code"] == 0:
        out["counts"] = _kys_result(res["stdout"])
    else:
        out["evidence"] = _tail(res["stderr"])
    return out


def _codegraph_counts(cli: str, tree: Path) -> dict[str, int] | None:
    env = dict(os.environ, CODEGRAPH_TELEMETRY="0", NO_COLOR="1")
    res = run_cmd([cli, "status", str(tree)], env=env, timeout=120)
    if res["exit_code"] != 0:
        return None
    text = res["stdout"]
    parsed: dict[str, int] = {}
    for label, key in (("Files", "files"), ("Nodes", "nodes"), ("Edges", "edges")):
        m = re.search(rf"^\s*{label}:\s*([\d,]+)", text, re.MULTILINE)
        if m:
            parsed[key] = int(m.group(1).replace(",", ""))
    return parsed or None


def _ingest_codegraph(cli: str, tree: Path) -> dict[str, Any]:
    shutil.rmtree(tree / ".codegraph", ignore_errors=True)
    env = dict(os.environ, CODEGRAPH_TELEMETRY="0", NO_COLOR="1")
    argv = [cli, "init", "-y", str(tree)]
    res = run_cmd(argv, env=env)
    out: dict[str, Any] = {"command": argv, "exit_code": res["exit_code"], "wall_ms": res["wall_ms"]}
    counts = _codegraph_counts(cli, tree)
    if counts:
        out["counts"] = counts
    if res["exit_code"] != 0:
        out["evidence"] = _tail(res["stdout"] + res["stderr"])
    db = tree / ".codegraph" / "codegraph.db"
    if db.exists():
        out["artifact"] = str(db.relative_to(tree))
        out["artifact_bytes"] = db.stat().st_size
    return out


def _graphify_counts(tree: Path) -> dict[str, int] | None:
    graph = tree / "graphify-out" / "graph.json"
    if not graph.exists():
        return None
    try:
        data = json.loads(graph.read_text())
    except Exception:
        return None
    return {
        "nodes": len(data.get("nodes", [])),
        "links": len(data.get("links", [])),
        "hyperedges": len(data.get("hyperedges", [])),
    }


def _ingest_graphify(cli: str, tree: Path) -> dict[str, Any]:
    shutil.rmtree(tree / "graphify-out", ignore_errors=True)
    argv = [cli, "update", str(tree)]
    res = run_cmd(argv)
    out: dict[str, Any] = {"command": argv, "exit_code": res["exit_code"], "wall_ms": res["wall_ms"]}
    counts = _graphify_counts(tree)
    if counts:
        out["counts"] = counts
    if res["exit_code"] != 0:
        out["evidence"] = _tail(res["stdout"] + res["stderr"])
    graph = tree / "graphify-out" / "graph.json"
    if graph.exists():
        out["artifact"] = str(graph.relative_to(tree))
        out["artifact_bytes"] = graph.stat().st_size
    return out


# ------------------------------------------------------------- crash task


def _kill_mid_ingest(argv: list[str], *, env: dict[str, str] | None, delay_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.Popen(
        argv,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # own process group: the kill must reach worker children too
    )
    time.sleep(delay_s)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # already finished before the kill landed
    try:
        stdout, stderr = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        # A grandchild kept the pipes open; close them and collect what arrived.
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        proc.wait(timeout=30)
        stdout, stderr = "", ""
    return {
        "command": argv,
        "kill_after_s": delay_s,
        "kill_elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "exit_code": proc.returncode,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }


def _crash_kys(tree: Path, work: Path, clean: dict[str, Any]) -> dict[str, Any]:
    store = work / "crash.db"
    store.unlink(missing_ok=True)
    clean_wall_s = (clean.get("wall_ms") or 10_000.0) / 1000.0
    delay = min(KILL_MAX_S, max(KILL_MIN_S, KILL_FRACTION * clean_wall_s))
    argv = [sys.executable, "-c", _kys_driver(_KYS_INGEST_DRIVER), str(store), str(tree)]
    kill = _kill_mid_ingest(argv, env=None, delay_s=delay)

    probe_argv = [sys.executable, "-c", _kys_driver(_KYS_CHECK_DRIVER), str(store)]
    probe = run_cmd(probe_argv)
    post_kill: dict[str, Any] = {"command": probe_argv, "exit_code": probe["exit_code"]}
    if probe["exit_code"] == 0:
        post_kill["state"] = _kys_result(probe["stdout"])
    else:
        post_kill["evidence"] = _tail(probe["stderr"])

    recovery = run_cmd(argv)
    recovered: dict[str, Any] = {"command": argv, "exit_code": recovery["exit_code"], "wall_ms": recovery["wall_ms"]}
    if recovery["exit_code"] == 0:
        recovered["counts"] = _kys_result(recovery["stdout"])
    else:
        recovered["evidence"] = _tail(recovery["stderr"])

    clean_counts = clean.get("counts") or {}
    recovered_counts = recovered.get("counts") or {}
    data_loss = {
        key: {"clean": clean_counts.get(key), "recovered": recovered_counts.get(key)}
        for key in ("symbols", "edges", "refs_total")
        if clean_counts.get(key) != recovered_counts.get(key)
    }
    return {
        "kill": kill,
        "post_kill": post_kill,
        "recovery": recovered,
        "data_loss": data_loss,
        "recovery_ran": recovery["exit_code"] == 0,
        "recovered_ok": recovery["exit_code"] == 0 and not data_loss,
    }


def _crash_codegraph(cli: str, tree: Path, clean: dict[str, Any]) -> dict[str, Any]:
    shutil.rmtree(tree / ".codegraph", ignore_errors=True)
    env = dict(os.environ, CODEGRAPH_TELEMETRY="0", NO_COLOR="1")
    clean_wall_s = (clean.get("wall_ms") or 2_000.0) / 1000.0
    delay = min(KILL_MAX_S, max(KILL_MIN_S, KILL_FRACTION * clean_wall_s))
    kill = _kill_mid_ingest([cli, "init", "-y", str(tree)], env=env, delay_s=delay)

    artifacts = sorted(p.name for p in (tree / ".codegraph").iterdir()) if (tree / ".codegraph").exists() else []
    status_argv = [cli, "status", str(tree)]
    status = run_cmd(status_argv, env=env, timeout=120)
    post_kill = {
        "artifacts": artifacts,
        "command": status_argv,
        "exit_code": status["exit_code"],
        "counts": _codegraph_counts(cli, tree),
        "detects_interruption": "never finished" in status["stdout"] or "interrupted" in status["stdout"],
        "evidence": _tail(status["stdout"] + status["stderr"], 600),
    }

    # Documented recovery: init refuses on an existing project; index rebuilds.
    reinit = run_cmd([cli, "init", "-y", str(tree)], env=env)
    reindex_argv = [cli, "index", str(tree)]
    reindex = run_cmd(reindex_argv, env=env)
    recovered = {
        "reinit_refused": {
            "command": [cli, "init", "-y", str(tree)],
            "exit_code": reinit["exit_code"],
            "evidence": _tail(reinit["stdout"] + reinit["stderr"], 300),
        },
        "reindex": {
            "command": reindex_argv,
            "exit_code": reindex["exit_code"],
            "wall_ms": reindex["wall_ms"],
            "counts": _codegraph_counts(cli, tree),
        },
    }
    clean_counts = clean.get("counts") or {}
    recovered_counts = recovered["reindex"]["counts"] or {}
    data_loss = {
        key: {"clean": clean_counts.get(key), "recovered": recovered_counts.get(key)}
        for key in ("files", "nodes", "edges")
        if clean_counts.get(key) != recovered_counts.get(key)
    }
    return {
        "kill": kill,
        "post_kill": post_kill,
        "recovery": recovered,
        "data_loss": data_loss,
        "recovery_ran": reindex["exit_code"] == 0,
        "recovered_ok": reindex["exit_code"] == 0 and not data_loss,
    }


def _crash_graphify(cli: str, tree: Path, clean: dict[str, Any]) -> dict[str, Any]:
    shutil.rmtree(tree / "graphify-out", ignore_errors=True)
    clean_wall_s = (clean.get("wall_ms") or 3_000.0) / 1000.0
    delay = min(KILL_MAX_S, max(KILL_MIN_S, KILL_FRACTION * clean_wall_s))
    update_argv = [cli, "update", str(tree)]
    kill = _kill_mid_ingest(update_argv, env=None, delay_s=delay)

    out_dir = tree / "graphify-out"
    artifacts = sorted(p.name for p in out_dir.iterdir()) if out_dir.exists() else []
    graph = out_dir / "graph.json"
    graph_state: dict[str, Any] = {"exists": graph.exists(), "parses": None, "counts": None}
    if graph.exists():
        try:
            data = json.loads(graph.read_text())
            graph_state["parses"] = True
            graph_state["counts"] = {"nodes": len(data.get("nodes", [])), "links": len(data.get("links", []))}
        except Exception as exc:
            graph_state["parses"] = False
            graph_state["error"] = f"{type(exc).__name__}: {exc}"
    post_kill = {"artifacts": artifacts, "graph_json": graph_state}

    recovery = run_cmd(update_argv)
    recovered: dict[str, Any] = {
        "command": update_argv,
        "exit_code": recovery["exit_code"],
        "wall_ms": recovery["wall_ms"],
        "counts": _graphify_counts(tree),
    }
    clean_counts = clean.get("counts") or {}
    recovered_counts = recovered.get("counts") or {}
    data_loss = {
        key: {"clean": clean_counts.get(key), "recovered": recovered_counts.get(key)}
        for key in ("nodes", "links")
        if clean_counts.get(key) != recovered_counts.get(key)
    }
    return {
        "kill": kill,
        "post_kill": post_kill,
        "recovery": recovered,
        "data_loss": data_loss,
        "recovery_ran": recovery["exit_code"] == 0,
        "recovered_ok": recovery["exit_code"] == 0 and not data_loss,
    }


# ------------------------------------------------------- query task runner


#: Exit codes a query interface may return for a NON-error answer.
#: graphify documents: 0 = answer, 3 = ambiguous (candidates listed),
#: 4 = no node matches — the honest answer to the negative query.
QUERY_OK_EXIT: dict[str, frozenset[int]] = {
    "know_your_context": frozenset({0}),
    "codegraph": frozenset({0}),
    "graphify": frozenset({0, 3, 4}),
}


def _query_once(system: str, cli: str | None, target_work: Path, query: str) -> dict[str, Any]:
    """One fresh-process query; returns wall time, exit code, payload."""
    if system == "know_your_context":
        argv = [sys.executable, "-c", _kys_driver(_KYS_QUERY_DRIVER), str(target_work / "store.db"), query]
        env = None
    elif system == "codegraph":
        assert cli is not None
        argv = [cli, "query", query, "-p", str(target_work / "tree"), "-j"]
        env = dict(os.environ, CODEGRAPH_TELEMETRY="0", NO_COLOR="1")
    elif system == "graphify":
        assert cli is not None
        argv = [
            cli,
            "query",
            query,
            "--graph",
            str(target_work / "tree" / "graphify-out" / "graph.json"),
        ]
        env = None
    else:  # pragma: no cover - defensive
        raise ValueError(system)
    res = run_cmd(argv, env=env, timeout=120)
    exit_ok = res["exit_code"] in QUERY_OK_EXIT[system]
    return {
        "command": argv,
        "exit_code": res["exit_code"],
        "exit_ok": exit_ok,
        "wall_ms": res["wall_ms"],
        "payload_bytes": len(res["stdout"].encode("utf-8")),
        "answer_tokens": estimated_tokens(res["stdout"]),
        "stderr_tail": _tail(res["stderr"], 200) if not exit_ok else "",
    }


def _concurrent_client(
    system: str, cli: str | None, target_work: Path, queries: list[str], results: list[dict[str, Any]], index: int
) -> None:
    for query in queries:
        results.append({"client": index, "query": query, **_query_once(system, cli, target_work, query)})


# ---------------------------------------------------------------- measure


SYSTEM_ORDER = ("know_your_context", "codegraph", "graphify")
TASK_ORDER = ("ingest", "cold-start", "concurrent", "crash", "determinism", "tokens")

#: Target used for the crash task (big enough that a mid-ingest kill lands
#: inside the walk for every system).
CRASH_TARGET = "requests"


def measure(
    *,
    systems: list[str] | None = None,
    targets: list[str] | None = None,
    clients: int = DEFAULT_CLIENTS,
    queries_per_client: int = DEFAULT_QUERIES_PER_CLIENT,
    cold_repeats: int = DEFAULT_COLD_REPEATS,
    keep_work: bool = False,
) -> dict[str, Any]:
    """Full competitive measurement. Offline; returns the ledger dict."""
    if not dataset_present():
        raise SystemExit("dataset not vendored (run: uv run python bench/codebase/run.py fetch)")

    discovered = system_report()
    wanted_systems = [s for s in (systems or list(SYSTEM_ORDER))]
    query_sets = load_query_sets()
    wanted_targets = targets or [t for t in target_names() if query_sets.get(t)]

    work_root = Path(tempfile.mkdtemp(prefix="kys-competitive-", dir=os.environ.get("TMPDIR")))
    ledger: dict[str, Any] = {
        "harness": "bench/competitive",
        "harness_version": HARNESS_VERSION,
        "started_utc": utc_stamp(),
        "work_dir": str(work_root),
        "config": {
            "clients": clients,
            "queries_per_client": queries_per_client,
            "cold_repeats": cold_repeats,
            "kill_fraction": KILL_FRACTION,
        },
        "systems": discovered,
        "reproduce": "uv run python bench/competitive/run.py run",
        "entries": [],
    }
    entries: list[dict[str, Any]] = ledger["entries"]

    try:
        for system in wanted_systems:
            info = discovered.get(system, {"available": False, "evidence": "unknown system"})
            cli: str | None = info.get("cli") if info.get("available") else None

            for target in wanted_targets:
                target_work = work_root / system / target
                target_work.mkdir(parents=True, exist_ok=True)
                tree = target_work / "tree"
                prepare_tree(target, tree)

                if not info.get("available"):
                    for task in TASK_ORDER:
                        entries.append(not_supported(system, task, target, info.get("evidence", "unavailable")))
                    continue
                assert cli is not None or system == "know_your_context"  # kys drives python directly

                # ---- task: ingest
                if system == "know_your_context":
                    ingest = _ingest_kys(tree, target_work / "store.db")
                elif system == "codegraph":
                    assert cli is not None
                    ingest = _ingest_codegraph(cli, tree)
                else:
                    assert cli is not None
                    ingest = _ingest_graphify(cli, tree)
                status = "OK" if ingest["exit_code"] == 0 else "FAILED"
                entries.append(entry(system, "ingest", target, status, **ingest))

                if status != "OK":
                    for task in ("cold-start", "concurrent", "crash", "determinism", "tokens"):
                        entries.append(
                            not_supported(system, task, target, f"ingest failed (exit {ingest['exit_code']})")
                        )
                    continue

                # ---- task: cold-start query latency (+ token payloads)
                cold_queries: list[dict[str, Any]] = []
                for query in query_sets[target]:
                    runs = [_query_once(system, cli, target_work, query) for _ in range(cold_repeats)]
                    walls = [r["wall_ms"] for r in runs]
                    cold_queries.append(
                        {
                            "query": query,
                            "repeats": cold_repeats,
                            "exit_codes": [r["exit_code"] for r in runs],
                            "p50_ms": _percentile(walls, 0.50),
                            "min_ms": min(walls),
                            "command": runs[0]["command"],
                            "answer_tokens": runs[0]["answer_tokens"],
                            "payload_bytes": runs[0]["payload_bytes"],
                        }
                    )
                cold_ok = all(all(rc in QUERY_OK_EXIT[system] for rc in q["exit_codes"]) for q in cold_queries)
                entries.append(
                    entry(
                        system,
                        "cold-start",
                        target,
                        "OK" if cold_ok else "FAILED",
                        interface="fresh process per query",
                        queries=cold_queries,
                        p50_ms=_percentile([q["p50_ms"] for q in cold_queries], 0.50),
                    )
                )

                # ---- task: tokens per answer (same queries, same corpus)
                total_tokens = sum(q["answer_tokens"] for q in cold_queries)
                entries.append(
                    entry(
                        system,
                        "tokens",
                        target,
                        "OK",
                        estimator="ceil(utf8_bytes/4)",
                        total_answer_tokens=total_tokens,
                        per_query=[{"query": q["query"], "answer_tokens": q["answer_tokens"]} for q in cold_queries],
                    )
                )

                # ---- task: concurrent load
                plan = [query_sets[target][i % len(query_sets[target])] for i in range(queries_per_client)]
                results: list[dict[str, Any]] = []
                threads = [
                    threading.Thread(target=_concurrent_client, args=(system, cli, target_work, plan, results, index))
                    for index in range(clients)
                ]
                started = time.perf_counter()
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                wall_s = time.perf_counter() - started
                total = len(results)
                errors = sum(1 for r in results if not r["exit_ok"])
                lat = [r["wall_ms"] for r in results]
                entries.append(
                    entry(
                        system,
                        "concurrent",
                        target,
                        "OK" if errors < total else "FAILED",
                        verdict=f"{errors}/{total} errors",
                        interface="parallel client processes (one fresh process per query)",
                        clients=clients,
                        queries_per_client=queries_per_client,
                        total_queries=total,
                        errors=errors,
                        wall_s=round(wall_s, 3),
                        throughput_qps=round(total / wall_s, 3) if wall_s > 0 else None,
                        latency_p50_ms=_percentile(lat, 0.50),
                        latency_p95_ms=_percentile(lat, 0.95),
                        command=results[0]["command"] if results else None,
                    )
                )

                # ---- task: determinism (two fresh ingests, byte-identical artifact?)
                if system == "know_your_context":
                    store_b = target_work / "store-b.db"
                    second = _ingest_kys(tree, store_b)
                    a_sha = (ingest.get("counts") or {}).get("dump_sha256")
                    b_sha = (second.get("counts") or {}).get("dump_sha256")
                    det = {
                        "artifact": "canonical_ingestion_dump",
                        "run_a_sha256": a_sha,
                        "run_b_sha256": b_sha,
                        "byte_identical": a_sha == b_sha and a_sha is not None,
                        "command": second["command"],
                    }
                elif system == "codegraph":
                    assert cli is not None
                    first_db = tree / ".codegraph" / "codegraph.db"
                    run_a = sha256_file(first_db) if first_db.exists() else None
                    second = _ingest_codegraph(cli, tree)
                    run_b = sha256_file(first_db) if first_db.exists() else None
                    counts_a = ingest.get("counts") or {}
                    counts_b = second.get("counts") or {}
                    det = {
                        "artifact": ".codegraph/codegraph.db",
                        "run_a_sha256": run_a,
                        "run_b_sha256": run_b,
                        "byte_identical": run_a == run_b and run_a is not None,
                        "counts_stable": all(counts_a.get(k) == counts_b.get(k) for k in ("files", "nodes", "edges")),
                        "command": second["command"],
                    }
                else:
                    assert cli is not None
                    graph = tree / "graphify-out" / "graph.json"
                    run_a = sha256_file(graph) if graph.exists() else None
                    second = _ingest_graphify(cli, tree)
                    run_b = sha256_file(graph) if graph.exists() else None
                    counts_a = ingest.get("counts") or {}
                    counts_b = second.get("counts") or {}
                    det = {
                        "artifact": "graphify-out/graph.json",
                        "run_a_sha256": run_a,
                        "run_b_sha256": run_b,
                        "byte_identical": run_a == run_b and run_a is not None,
                        "counts_stable": all(counts_a.get(k) == counts_b.get(k) for k in ("nodes", "links")),
                        "command": second["command"],
                    }
                entries.append(
                    entry(
                        system,
                        "determinism",
                        target,
                        "OK",
                        verdict="byte_identical" if det["byte_identical"] else "not_byte_identical",
                        **det,
                    )
                )

            # ---- task: crash recovery (once per system, on the crash target)
            if not info.get("available"):
                continue
            assert cli is not None or system == "know_your_context"
            crash_target = CRASH_TARGET if CRASH_TARGET in wanted_targets else wanted_targets[0]
            target_work = work_root / system / crash_target
            tree = target_work / "tree"
            clean_entry = next(
                e for e in entries if e["system"] == system and e["task"] == "ingest" and e["target"] == crash_target
            )
            if clean_entry["status"] != "OK":
                entries.append(
                    not_supported(
                        system, "crash", crash_target, f"clean ingest failed (exit {clean_entry['exit_code']})"
                    )
                )
                continue
            if system == "know_your_context":
                crash = _crash_kys(tree, target_work, clean_entry)
            elif system == "codegraph":
                assert cli is not None
                crash = _crash_codegraph(cli, tree, clean_entry)
            else:
                assert cli is not None
                crash = _crash_graphify(cli, tree, clean_entry)
            entries.append(
                entry(
                    system,
                    "crash",
                    crash_target,
                    "OK" if crash["recovery_ran"] else "FAILED",
                    verdict="recovered" if crash["recovered_ok"] else "data_loss_or_failure",
                    **crash,
                )
            )

        ledger["finished_utc"] = utc_stamp()
        ledger["summary"] = summarize(entries)
        return ledger
    finally:
        if not keep_work:
            shutil.rmtree(work_root, ignore_errors=True)


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    per_system: dict[str, dict[str, int]] = {}
    for e in entries:
        bucket = per_system.setdefault(e["system"], {"OK": 0, "FAILED": 0, "NOT_SUPPORTED": 0})
        bucket[e["status"]] = bucket.get(e["status"], 0) + 1
    return {"entries": len(entries), "per_system": per_system}


# ---------------------------------------------------------------- report


def render_report(ledger: dict[str, Any]) -> str:
    lines = [
        "# Competitive benchmark — know-your-context vs codegraph vs graphify",
        "",
        f"Run: {ledger['started_utc']} · harness v{ledger['harness_version']} · "
        f"dataset: bench/codebase pinned corpus (fixture + requests + chalk + tree-sitter-python)",
        "",
        "## Systems",
        "",
        "| System | Available | Version / interface |",
        "|--------|-----------|---------------------|",
    ]
    for name in SYSTEM_ORDER:
        info = ledger["systems"].get(name, {})
        if info.get("available"):
            detail = info.get("version") or info.get("interface", "")
            lines.append(f"| {name} | yes | {detail} |")
        else:
            lines.append(f"| {name} | NO | {info.get('evidence', 'unavailable')} |")

    entries = ledger["entries"]

    def find(system: str, task: str, target: str | None = None) -> dict[str, Any] | None:
        for e in entries:
            if e["system"] == system and e["task"] == task and (target is None or e["target"] == target):
                return e
        return None

    targets = sorted({e["target"] for e in entries if e["target"]})
    lines += [
        "",
        "## Ingest (same pinned tree)",
        "",
        "| Target | System | Wall (ms) | Counts | Exit |",
        "|--------|--------|-----------|--------|------|",
    ]
    for target in targets:
        for system in SYSTEM_ORDER:
            e = find(system, "ingest", target)
            if e is None:
                continue
            if e["status"] == "NOT_SUPPORTED":
                lines.append(f"| {target} | {system} | NOT_SUPPORTED | — | — |")
            else:
                counts = ", ".join(f"{k}={v}" for k, v in (e.get("counts") or {}).items() if k != "dump_sha256")
                lines.append(f"| {target} | {system} | {e.get('wall_ms')} | {counts} | {e.get('exit_code')} |")

    lines += [
        "",
        "## Cold-start query latency (fresh process per query; p50 over queries)",
        "",
        "| Target | System | p50 (ms) | Exit codes OK |",
        "|--------|--------|----------|----------------|",
    ]
    for target in targets:
        for system in SYSTEM_ORDER:
            e = find(system, "cold-start", target)
            if e is None:
                continue
            if e["status"] == "NOT_SUPPORTED":
                lines.append(f"| {target} | {system} | NOT_SUPPORTED | — |")
            else:
                lines.append(f"| {target} | {system} | {e.get('p50_ms')} | {'yes' if e['status'] == 'OK' else 'NO'} |")

    lines += [
        "",
        "## Concurrent load (N parallel clients × M queries; one fresh process per query)",
        "",
        "| Target | System | Clients | Queries | Errors | Wall (s) | Throughput (q/s) | p50 (ms) | p95 (ms) |",
        "|--------|--------|---------|---------|--------|----------|------------------|----------|----------|",
    ]
    for target in targets:
        for system in SYSTEM_ORDER:
            e = find(system, "concurrent", target)
            if e is None:
                continue
            if e["status"] == "NOT_SUPPORTED":
                lines.append(f"| {target} | {system} | — | — | — | — | NOT_SUPPORTED | — | — |")
            else:
                lines.append(
                    f"| {target} | {system} | {e.get('clients')} | {e.get('total_queries')} | {e.get('errors')} "
                    f"| {e.get('wall_s')} | {e.get('throughput_qps')} | {e.get('latency_p50_ms')} "
                    f"| {e.get('latency_p95_ms')} |"
                )

    lines += [
        "",
        "## Crash recovery (SIGKILL mid-ingest on the requests tree, then recover)",
        "",
        "| System | Kill exit | Post-kill state | Recovery | Data loss | Verdict |",
        "|--------|-----------|-----------------|----------|-----------|---------|",
    ]
    for system in SYSTEM_ORDER:
        e = find(system, "crash")
        if e is None:
            continue
        if e["status"] == "NOT_SUPPORTED":
            lines.append(f"| {system} | — | NOT_SUPPORTED | — | — | — |")
            continue
        post = e.get("post_kill", {})
        if system == "know_your_context":
            state = post.get("state", {})
            post_desc = (
                f"integrity={state.get('integrity')}, symbols={state.get('symbols')}, edges={state.get('edges')}"
            )
            recovery = e.get("recovery", {})
            rec_desc = f"re-ingest exit={recovery.get('exit_code')}"
        elif system == "codegraph":
            post_desc = f"detects_interruption={post.get('detects_interruption')}, counts={post.get('counts')}"
            reinit_exit = e["recovery"]["reinit_refused"]["exit_code"]
            reindex_exit = e["recovery"]["reindex"]["exit_code"]
            rec_desc = f"init refused (exit {reinit_exit}), index exit={reindex_exit}"
        else:
            gj = post.get("graph_json", {})
            post_desc = f"graph.json exists={gj.get('exists')} (artifacts: {', '.join(post.get('artifacts', []))})"
            rec_desc = f"update exit={e.get('recovery', {}).get('exit_code')}"
        loss = e.get("data_loss") or {}
        loss_desc = "none" if not loss else json.dumps(loss)
        verdict = e.get("verdict", e["status"])
        lines.append(f"| {system} | {e['kill']['exit_code']} | {post_desc} | {rec_desc} | {loss_desc} | {verdict} |")

    lines += [
        "",
        "## Determinism (two fresh ingests → byte-identical index artifact?)",
        "",
        "| Target | System | Artifact | Byte-identical | Counts stable |",
        "|--------|--------|----------|----------------|----------------|",
    ]
    for target in targets:
        for system in SYSTEM_ORDER:
            e = find(system, "determinism", target)
            if e is None:
                continue
            if e["status"] == "NOT_SUPPORTED":
                lines.append(f"| {target} | {system} | — | NOT_SUPPORTED | — |")
            else:
                stable = e.get("counts_stable")
                stable_txt = "—" if stable is None else ("yes" if stable else "NO")
                identical_txt = "YES" if e.get("byte_identical") else "NO"
                lines.append(f"| {target} | {system} | {e.get('artifact')} | {identical_txt} | {stable_txt} |")

    lines += [
        "",
        "## Tokens per answer (same queries, same corpus; estimator ceil(utf8 bytes / 4))",
        "",
        "| Target | System | Total answer tokens | Queries |",
        "|--------|--------|---------------------|---------|",
    ]
    for target in targets:
        for system in SYSTEM_ORDER:
            e = find(system, "tokens", target)
            if e is None:
                continue
            if e["status"] == "NOT_SUPPORTED":
                lines.append(f"| {target} | {system} | NOT_SUPPORTED | — |")
            else:
                n_queries = len(e.get("per_query", []))
                lines.append(f"| {target} | {system} | {e.get('total_answer_tokens')} | {n_queries} |")

    lines += [
        "",
        "## Scoreboard",
        "",
        "| System | OK | FAILED | NOT_SUPPORTED |",
        "|--------|----|--------|---------------|",
    ]
    for system, counts in ledger["summary"]["per_system"].items():
        lines.append(
            f"| {system} | {counts.get('OK', 0)} | {counts.get('FAILED', 0)} | {counts.get('NOT_SUPPORTED', 0)} |"
        )

    lines += [
        "",
        "Every number above comes from a real subprocess/driver invocation recorded in the ledger "
        "(`entries[].command` is the exact argv; `entries[].exit_code` its exit code). "
        f"Reproduce the whole run: `{ledger['reproduce']}`.",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ commands


def cmd_check() -> int:
    """No-dataset self-checks: estimator parity, discovery, query-set shape."""
    failures: list[str] = []

    contracts = __import__(f"{product_module()}.contracts", fromlist=["canonical_json", "estimated_tokens_of_payload"])
    canonical_json = contracts.canonical_json
    estimated_tokens_of_payload = contracts.estimated_tokens_of_payload

    # Parity: the harness estimator applied to the canonical JSON bytes of a
    # payload must equal the frozen contract estimator for that payload.
    for payload in ("", "a", "abcd", "hello world", "x" * 4097, {"k": [1, 2, 3]}, [None, True, 3.5]):
        if estimated_tokens(canonical_json(payload)) != estimated_tokens_of_payload(payload):
            failures.append(f"estimator parity failed for {str(payload)[:16]!r}")

    discovered = system_report()
    if not discovered["know_your_context"]["available"]:
        failures.append(f"know_your_context unavailable: {discovered['know_your_context'].get('evidence')}")
    for name in ("codegraph", "graphify"):
        info = discovered[name]
        note = f"available ({info.get('version')})" if info.get("available") else f"SKIP: {info.get('evidence')}"
        print(f"[check] {name}: {note}")

    if dataset_present():
        cb = _load_codebase_bench()
        manifest = cb.load_manifest()
        for repo_entry in manifest["repos"]:
            problems = cb.verify_repo(repo_entry)
            if problems:
                failures.extend(f"{repo_entry['name']}: {p}" for p in problems[:3])
        query_sets = load_query_sets()
        for target in target_names():
            if not query_sets.get(target):
                failures.append(f"no frozen queries for target {target}")
        print(f"[check] dataset: present, {sum(len(q) for q in query_sets.values())} frozen queries")
    else:
        print("[check] dataset: NOT vendored — run/verify will refuse; tests skip honestly")

    if failures:
        for failure in failures:
            print(f"[check] FAIL: {failure}")
        return 1
    print("[check] OK")
    return 0


def cmd_verify() -> int:
    if not dataset_present():
        print("[verify] dataset not vendored")
        return 1
    cb = _load_codebase_bench()
    return cb.cmd_verify()


def cmd_run(args: argparse.Namespace) -> int:
    if not dataset_present():
        print("[run] dataset not vendored (run: uv run python bench/codebase/run.py fetch)")
        return 1
    ledger = measure(
        systems=args.systems.split(",") if args.systems else None,
        targets=args.targets.split(",") if args.targets else None,
        clients=args.clients,
        queries_per_client=args.queries_per_client,
        cold_repeats=args.cold_repeats,
        keep_work=args.keep_work,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = ledger["started_utc"]
    ledger_path = RESULTS_DIR / f"ledger-{stamp}.json"
    report_path = RESULTS_DIR / f"report-{stamp}.md"
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    report_path.write_text(render_report(ledger))
    print(f"[run] ledger: {ledger_path.relative_to(REPO_ROOT)}")
    print(f"[run] report: {report_path.relative_to(REPO_ROOT)}")
    summary = ledger["summary"]
    for system, counts in summary["per_system"].items():
        print(
            f"[run] {system}: OK={counts.get('OK', 0)} FAILED={counts.get('FAILED', 0)} "
            f"NOT_SUPPORTED={counts.get('NOT_SUPPORTED', 0)}"
        )
    failed = sum(c.get("FAILED", 0) for c in summary["per_system"].values())
    unsupported = sum(c.get("NOT_SUPPORTED", 0) for c in summary["per_system"].values())
    if failed:
        return 1
    if args.strict and unsupported:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="no-dataset self-checks + system discovery")
    sub.add_parser("verify", help="pinned-dataset integrity (delegates to bench/codebase)")
    run = sub.add_parser("run", help="full competitive measurement")
    run.add_argument("--systems", help="comma-separated subset of know_your_context,codegraph,graphify")
    run.add_argument("--targets", help="comma-separated subset of fixture,requests,chalk,tree-sitter-python")
    run.add_argument("--clients", type=int, default=DEFAULT_CLIENTS)
    run.add_argument("--queries-per-client", type=int, default=DEFAULT_QUERIES_PER_CLIENT)
    run.add_argument("--cold-repeats", type=int, default=DEFAULT_COLD_REPEATS)
    run.add_argument("--keep-work", action="store_true", help="keep the scratch work dir for inspection")
    run.add_argument("--strict", action="store_true", help="exit 1 when any task is NOT_SUPPORTED")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return cmd_check()
    if args.command == "verify":
        return cmd_verify()
    if args.command == "run":
        return cmd_run(args)
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
