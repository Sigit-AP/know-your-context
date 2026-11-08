"""Trigger overhead measurement (SUB-160 kill criterion: >10% ingest slowdown
=> replace triggers). Ingests the same 238-file corpus twice: (a) v6 with
symbols_watch triggers, (b) v6 with triggers dropped. Same code, same corpus,
same machine; 3 reps each, best-of taken (noise floor)."""

import json
import sys
import time
from pathlib import Path

REPO = Path("/root/know-your-context")
CORPUS = Path("/root/.hermes/cache/scratch/cluster-b/corpus")
OUT = Path("/root/.hermes/cache/scratch/cluster-b/ws-b")
sys.path.insert(0, str(REPO / "src"))

from know_your_context.ingest.pipeline import IngestionPipeline  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402


def run_once(path: Path, drop_triggers: bool) -> float:
    if path.exists():
        path.unlink()
    store = EventStore(path)
    if drop_triggers:
        with store.transaction() as conn:
            for t in ("symbols_watch_ins", "symbols_watch_upd", "symbols_watch_del"):
                conn.execute(f"DROP TRIGGER {t}")
    try:
        pipe = IngestionPipeline(store, resolver=None)
        t0 = time.perf_counter()
        pipe.ingest_path(CORPUS)
        return time.perf_counter() - t0
    finally:
        store.close()


def main():
    out = {"with_triggers": [], "without_triggers": []}
    for rep in range(3):
        out["with_triggers"].append(round(run_once(OUT / f"ing-t-{rep}.sqlite3", False), 3))
        out["without_triggers"].append(round(run_once(OUT / f"ing-nt-{rep}.sqlite3", True), 3))
        print(rep, out["with_triggers"][-1], out["without_triggers"][-1], flush=True)
    best_t, best_nt = min(out["with_triggers"]), min(out["without_triggers"])
    out["best_with"] = best_t
    out["best_without"] = best_nt
    out["overhead_pct"] = round((best_t / best_nt - 1) * 100, 2)
    (OUT / "ws-b-trigger-overhead.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("best_with", "best_without", "overhead_pct")}))


if __name__ == "__main__":
    main()
