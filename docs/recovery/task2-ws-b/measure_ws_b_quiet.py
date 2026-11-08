"""WS-B after re-measure on the ALREADY-MIGRATED stores (steady state, no
migration cost in the timings) + a typical-few-candidates tail probe."""

import json
import sys
import time
from pathlib import Path

SCRATCH = Path("/root/.hermes/cache/scratch/cluster-b")
OUT = SCRATCH / "ws-b"
sys.path.insert(0, "/root/know-your-context/src")

import measure_ws_b as base  # noqa: E402

from know_your_context.resolve.resolver import ReferenceResolver  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402


def typical_tail_probe(store) -> dict:
    with store.read() as conn:
        row = conn.execute(
            "SELECT name_tail, count(*) AS n FROM symbols GROUP BY name_tail ORDER BY n, name_tail LIMIT 1"
        ).fetchone()
    tail, n = str(row[0]), int(row[1])
    resolver = ReferenceResolver()
    ts = []
    for _ in range(7):
        t0 = time.perf_counter()
        got = resolver._tail_candidates(store, tail)
        ts.append((time.perf_counter() - t0) * 1000)
    assert len(got) == n
    return {"tail": tail, "candidates": n, "per_ref_ms_min": round(min(ts), 3), "per_ref_ms_max": round(max(ts), 3)}


out = {}
for key in ("small", "mid", "big"):
    res = base.measure(OUT / f"ws-b-after-{key}.sqlite3")
    store = EventStore(str(OUT / f"ws-b-after-{key}.sqlite3"))
    try:
        res["typical_tail"] = typical_tail_probe(store)
    finally:
        store.close()
    out[key] = res
    print(key, json.dumps(res), flush=True)
(OUT / "ws-b-after-quiet.json").write_text(json.dumps(out, indent=1))
