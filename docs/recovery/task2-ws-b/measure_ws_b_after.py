"""WS-B AFTER measurement (SUB-160): same probes as measure_ws_b.py on fresh
copies of the pinned stores (v5 -> v6 migration happens at open; its duration
is recorded). Output: ws-b-after.json."""

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path("/root/know-your-context")
SCRATCH = Path("/root/.hermes/cache/scratch/cluster-b")
OUT = SCRATCH / "ws-b"
sys.path.insert(0, str(REPO / "src"))

import measure_ws_b as base  # noqa: E402

from know_your_context.store import EventStore  # noqa: E402


def measure_with_migration(db_path: Path) -> dict:
    t0 = time.perf_counter()
    store = EventStore(str(db_path))  # v5 -> v6 migration happens here
    migration_s = time.perf_counter() - t0
    store.close()
    res = base.measure(db_path)
    res["migration_open_s"] = round(migration_s, 2)
    # post-migration EXPLAIN of the NEW queries
    store = EventStore(str(db_path))
    try:
        with store.read() as conn:
            res["explain_tail_index"] = base.explain(
                conn, "SELECT * FROM symbols WHERE name_tail = ? ORDER BY path, line, col, node_id", ("parse",)
            )
            res["explain_module_file_new"] = base.explain(
                conn, "SELECT * FROM files WHERE lower(path_rev) GLOB ? ORDER BY path", ("yp.ipa/*",)
            )
            res["explain_journal_conflict_new"] = base.explain(
                conn, "SELECT request_hash FROM invocation_journal WHERE effective_idempotency_key = ?", ("k",)
            )
            res["explain_watch_seq"] = base.explain(conn, "SELECT seq FROM symbols_watch WHERE id = 1")
    finally:
        store.close()
    return res


def main():
    out = {}
    for key, src in (("small", SCRATCH / "bench-c.sqlite3"), ("big", SCRATCH / "bench-c2.sqlite3")):
        dst = OUT / f"ws-b-after-{key}.sqlite3"
        if dst.exists():
            dst.unlink()
        shutil.copy(src, dst)
        out[key] = measure_with_migration(dst)
        print(key, json.dumps(out[key]), flush=True)
        (OUT / "ws-b-after.json").write_text(json.dumps(out, indent=1))
    # intermediate point: same deterministic derivation as before, from the AFTER big store
    mid = OUT / "ws-b-after-mid.sqlite3"
    if mid.exists():
        mid.unlink()
    shutil.copy(OUT / "ws-b-after-big.sqlite3", mid)
    conn = sqlite3.connect(str(mid))
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
    out["mid"] = base.measure(mid)
    print("mid", json.dumps(out["mid"]), flush=True)
    (OUT / "ws-b-after.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
