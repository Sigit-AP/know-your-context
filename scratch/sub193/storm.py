"""SUB-193 before-storm: 50 concurrent `codeintel callers-of parse_args --depth 1` on L4.

Two modes:
  --pure      : 50 CLI subprocesses (exact harness shape), count 120s timeouts.
  --instrument: 44 CLI + 6 in-process workers that log per-phase timing under load.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time

K = "/root/know-your-context/.venv/bin/know-your-context"
STORE = "/root/.hermes/cache/scratch/stress3/run/L4_kyc/store"
AGENTS = 50
TIMEOUT = 120
SRC = "/root/know-your-context/src"


def pct(q, s):
    if not s:
        return None
    s = sorted(s)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def cli_query(agent: int) -> dict:
    cmd = [K, "--store", STORE, "codeintel", "callers-of", "parse_args", "--depth", "1"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd="/tmp")
        wall = time.time() - t0
        out = (r.stdout or "")
        try:
            last = json.loads(out.strip().splitlines()[-1])
            hits = bool(last.get("entries"))
        except Exception:
            last, hits = None, False
        return {"agent": agent, "mode": "cli", "exit": r.returncode, "wall": wall,
                "timed_out": False, "hits": hits, "stderr": (r.stderr or "")[-200:],
                "canonical": last}
    except subprocess.TimeoutExpired:
        return {"agent": agent, "mode": "cli", "exit": "TIMEOUT", "wall": TIMEOUT,
                "timed_out": True, "hits": False, "stderr": "", "canonical": None}


def instrumented_query(agent: int) -> dict:
    """In-process worker: identical query, per-phase timings (no CLI overhead)."""
    sys.path.insert(0, SRC)
    import know_your_context.analytics.code_intel_sql as cis
    from know_your_context.analytics.code_intel import DEFAULT_TRAVERSAL_KINDS, callers_of
    from know_your_context.store import EventStore

    phases: dict[str, float] = {}
    t0 = time.time()
    store = EventStore(STORE)
    phases["eventstore_open"] = time.time() - t0

    t0 = time.time()
    conn = cis.open_readonly_connection(STORE)
    phases["open_ro"] = time.time() - t0

    t0 = time.time()
    graph = cis.SqlCodeGraph(conn)
    graph.node_count  # forces memo + fingerprint + edge aggregation
    phases["node_count"] = time.time() - t0

    t0 = time.time()
    result = callers_of(graph, "parse_args", kinds=DEFAULT_TRAVERSAL_KINDS, max_depth=1, max_results=200)
    phases["traversal"] = time.time() - t0
    graph.close()
    store.close()
    return {"agent": agent, "mode": "inst", "exit": 0, "wall": sum(phases.values()),
            "timed_out": False, "hits": bool(result.get("entries")), "phases": phases,
            "canonical": result, "stderr": ""}


def run(mode: str) -> None:
    results: list[dict] = []
    lock = threading.Lock()
    n_inst = 6 if mode == "instrument" else 0
    n_cli = AGENTS - n_inst

    def worker(a: int, inst: bool) -> None:
        r = instrumented_query(a) if inst else cli_query(a)
        with lock:
            results.append(r)

    ths = []
    for a in range(n_cli):
        ths.append(threading.Thread(target=worker, args=(a, False)))
    for a in range(n_inst):
        ths.append(threading.Thread(target=worker, args=(a, True)))
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = time.time() - t0

    errs = [r for r in results if r["timed_out"] or r["exit"] != 0]
    ok = [r for r in results if not (r["timed_out"] or r["exit"] != 0)]
    lat = [r["wall"] for r in ok]
    print(f"mode={mode} wall={wall:.1f}s timeouts={sum(1 for r in results if r['timed_out'])}/"
          f"{len(results)} errors={len(errs)} p50={pct(0.5, lat)}s p95={pct(0.95, lat)}s "
          f"hits={sum(1 for r in ok if r['hits'])}/{len(ok)}")
    for r in errs[:8]:
        print(f"  agent {r['agent']} [{r['mode']}]: exit={r['exit']} wall={r['wall']:.1f}s "
              f"stderr={r['stderr'][-120:]!r}")
    inst = [r for r in results if r["mode"] == "inst" and r.get("phases")]
    if inst:
        keys = list(inst[0]["phases"].keys())
        print("  phase means (s) over instrumented workers:")
        for k in keys:
            vals = [r["phases"][k] for r in inst if k in r["phases"]]
            if vals:
                print(f"    {k:16s} mean={statistics.mean(vals):.2f} max={max(vals):.2f}")
    # canonical-equality check across workers
    canon = [json.dumps(r["canonical"], sort_keys=True, default=str) for r in ok if r["canonical"]]
    if len(canon) > 1:
        print("  canonical bytes identical across workers:", len(set(canon)) == 1)
    out = f"/tmp/sub193_{mode}.jsonl"
    with open(out, "w") as fh:
        for r in results:
            fh.write(json.dumps(r, default=str) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "pure")