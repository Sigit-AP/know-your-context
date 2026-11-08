"""WS-B before/after measurement (SUB-160): Q1 freshness, R1 tail, Q2 LIKE miss, R2 module_file, S3 journal.

Method: operate on COPIES of the pinned cluster-b scale stores (bench-c ~16MB,
bench-c2 ~1.72GB) so the verdict's artifacts stay pristine. Cold path is
isolated with LookupConfig(query_cache_entries=0) so every query pays the
freshness check. Intermediate point: delete round-tagged rows from a c2 copy
(deterministic: highest tag first) until symbols <= 600k, then VACUUM.
"""

import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path("/root/know-your-context")
SCRATCH = Path("/root/.hermes/cache/scratch/cluster-b")
OUT = SCRATCH / "ws-b"
sys.path.insert(0, str(REPO / "src"))

from know_your_context.resolve.resolver import ReferenceResolver  # noqa: E402
from know_your_context.retrieval_v2.lookup import LookupConfig, SymbolLookup  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402

HIT = ["parse", "request", "session", "Response", "Parser"]
MISS = ["xyzzy_missing_term", "qwoqiwe_nonexistent"]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def explain(conn, sql, params=()):
    return ["|".join(str(c) for c in row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


def measure(db_path: Path) -> dict:
    store = EventStore(str(db_path))
    try:
        lookup = SymbolLookup(LookupConfig(query_cache_entries=0))
        res = {"db_mb": round(os.path.getsize(db_path) / 1e6, 1)}
        with store.read() as conn:
            res["symbols"] = conn.execute("SELECT count(*) FROM symbols").fetchone()[0]
            res["files"] = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            res["explain_fingerprint"] = explain(
                conn,
                "SELECT count(*), IFNULL(sum(length(node_id) + length(name) + length(kind) + length(path)"
                " + abs(line) + abs(col)), 0) FROM symbols",
            )
            res["explain_tail_like"] = explain(
                conn,
                "SELECT * FROM symbols WHERE name LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' "
                "ORDER BY path, line, col, node_id",
                ("%.parse", "%::parse"),
            )
            res["explain_module_file"] = explain(
                conn, "SELECT * FROM files WHERE path LIKE ? ESCAPE '\\' ORDER BY path", ("%/api.py",)
            )
            res["explain_journal_conflict"] = explain(
                conn, "SELECT request_hash FROM invocation_journal WHERE effective_idempotency_key = ?", ("k",)
            )
            cols = [r[1] for r in conn.execute("PRAGMA table_info(symbols)")]
            res["symbols_columns"] = cols
            res["schema_version"] = conn.execute("SELECT max(version) FROM schema_versions").fetchone()[0]
            # fingerprint cost (the Q1 guard)
            t0 = time.perf_counter()
            conn.execute(
                "SELECT count(*), IFNULL(sum(length(node_id) + length(name) + length(kind) + length(path)"
                " + abs(line) + abs(col)), 0) FROM symbols"
            ).fetchone()
            res["fingerprint_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        # cold queries (freshness check paid per query)
        hit_t, miss_t = [], []
        for q in HIT * 3:
            t0 = time.perf_counter()
            lookup.search(store, q, limit=10)
            hit_t.append((time.perf_counter() - t0) * 1000)
        for q in MISS * 2:
            t0 = time.perf_counter()
            lookup.search(store, q, limit=10)
            miss_t.append((time.perf_counter() - t0) * 1000)
        res["cold_hit_p50_ms"] = round(pct(hit_t, 0.5), 2)
        res["cold_hit_p95_ms"] = round(pct(hit_t, 0.95), 2)
        res["cold_miss_p50_ms"] = round(pct(miss_t, 0.5), 2)
        res["cold_miss_max_ms"] = round(max(miss_t), 2)
        # R1: tail candidates per ref (hit + miss)
        resolver = ReferenceResolver()
        tail_hit = None
        with store.read() as conn:
            tail_hit = conn.execute(
                "SELECT name FROM symbols WHERE name NOT LIKE '%.%' AND name NOT LIKE '%::%' "
                "GROUP BY name ORDER BY count(*) DESC, name LIMIT 1"
            ).fetchone()[0]
        for label, tail in (("hit", tail_hit), ("miss", "zzz_no_such_tail")):
            t0 = time.perf_counter()
            n = len(resolver._tail_candidates(store, tail))
            ms = (time.perf_counter() - t0) * 1000
            res[f"tail_{label}_ms"] = round(ms, 2)
            res[f"tail_{label}_candidates"] = n
        res["tail_hit_name"] = tail_hit
        # R2: module_file suffix timing
        t0 = time.perf_counter()
        status, _ = resolver._module_file(store, "api.py")
        ms = (time.perf_counter() - t0) * 1000
        res["module_file_ms"] = round(ms, 3)
        res["module_file_status"] = status
        return res
    finally:
        store.close()


def intermediate_copy() -> Path:
    dst = OUT / "ws-b-mid.sqlite3"
    if dst.exists():
        return dst
    src = OUT / "ws-b-big.sqlite3"
    shutil.copy(src, dst)
    conn = sqlite3.connect(str(dst))
    tags = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT substr(path, 1, 5) FROM symbols WHERE path GLOB '_r[0-9][0-9][0-9]*' ORDER BY 1 DESC"
        )
    ]
    for tag in tags:
        n = conn.execute("SELECT count(*) FROM symbols").fetchone()[0]
        if n <= 600_000:
            break
        conn.execute("DELETE FROM graph_edges WHERE source LIKE ?", (f"{tag}/%",))
        conn.execute("DELETE FROM symbols WHERE path LIKE ?", (f"{tag}/%",))
        conn.execute("DELETE FROM files WHERE path LIKE ?", (f"{tag}/%",))
        conn.commit()
    conn.execute("VACUUM")
    conn.close()
    return dst


def main():
    OUT.mkdir(exist_ok=True)
    out = {}
    pairs = [
        ("small", SCRATCH / "bench-c.sqlite3", OUT / "ws-b-small.sqlite3"),
        ("big", SCRATCH / "bench-c2.sqlite3", OUT / "ws-b-big.sqlite3"),
    ]
    for key, src, dst in pairs:
        if not dst.exists():
            shutil.copy(src, dst)
        out[key] = measure(dst)
        print(key, json.dumps(out[key]), flush=True)
    out["mid"] = measure(intermediate_copy())
    print("mid", json.dumps(out["mid"]), flush=True)
    (OUT / "ws-b-before.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
