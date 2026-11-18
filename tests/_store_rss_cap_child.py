"""SUB-209 child: open a pre-built store and report pragma + RSS behavior.

Called by ``tests/test_store_ingest_rss_cap.py`` in a fresh process so the
env kill switch ``INGEST_DISABLE_HW_TUNING`` is set before the store opens
(mode ``capped``) or left unset (mode ``default``). The store lives in WAL
mode with a ~70 MB ``wide`` table; the child reports:

- the applied PRAGMA ``mmap_size`` / ``cache_size``,
- the db file size the probe plan saw,
- VmRSS before, after 1 and after 100 ``COUNT(*)`` full scans (the SUB-206
  metric: one COUNT(*) costs ~db-sized RSS under the aggressive recipe).

Output is a single JSON line on stdout. /proc RSS is Linux-only; the caller
gates the RSS assertions on ``sys.platform == "linux"``.
"""

from __future__ import annotations

import json
import os
import sys

if sys.platform == "linux":

    def _rss_mb() -> float:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        return 0.0

else:

    def _rss_mb() -> float:  # pragma: no cover - RSS delta asserts are Linux-gated
        return 0.0


def main() -> int:
    mode, db_path = sys.argv[1], sys.argv[2]
    if mode == "capped":
        os.environ["INGEST_DISABLE_HW_TUNING"] = "1"
    elif mode != "default":
        print(json.dumps({"error": f"unknown mode {mode!r}"}))
        return 2

    from know_your_context.store import EventStore

    db_bytes = os.path.getsize(db_path)
    store = EventStore(db_path)
    tuning = store.applied_tuning
    try:
        with store.read() as conn:
            mmap_size = int(conn.execute("PRAGMA mmap_size").fetchone()[0])
            cache_size = int(conn.execute("PRAGMA cache_size").fetchone()[0])
            if sys.platform == "linux":
                before = _rss_mb()
                conn.execute("SELECT COUNT(*) FROM wide").fetchone()
                after_1q = _rss_mb()
                for _ in range(99):
                    conn.execute("SELECT COUNT(*) FROM wide").fetchone()
                after_100q = _rss_mb()
            else:  # pragma: no cover - RSS asserts are Linux-gated
                before = after_1q = after_100q = 0.0
    finally:
        store.close()

    print(
        json.dumps(
            {
                "mode": mode,
                "db_bytes": db_bytes,
                "mmap_size": mmap_size,
                "cache_size": cache_size,
                "applied_mmap_bytes": tuning.mmap_bytes,
                "applied_cache_kib": tuning.cache_kib,
                "rss_before_mb": round(before, 1),
                "rss_after_1q_mb": round(after_1q, 1),
                "rss_after_100q_mb": round(after_100q, 1),
                "delta_1q_mb": round(after_1q - before, 1),
                "delta_100q_mb": round(after_100q - before, 1),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
