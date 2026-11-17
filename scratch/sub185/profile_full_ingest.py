import os, time, hashlib, resource
from pathlib import Path
import sys

# Ensure our repo is importable
sys.path.insert(0, "/root/know-your-context/src")

from know_your_context.ingest.pipeline import IngestionPipeline, canonical_ingestion_dump
from know_your_context.store import EventStore


def profile_ingest(corpus_path: str, batch_size: int | None = None) -> dict:
    os.environ.setdefault("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
    os.environ.setdefault("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
    if batch_size is not None:
        from know_your_context.ingest import pipeline as _pipeline_module
        _pipeline_module.RESOLVE_BATCH = batch_size
    store = EventStore(":memory:")
    store.migrate()
    pipeline = IngestionPipeline(store)
    start = time.time()
    report = pipeline.ingest_path(Path(corpus_path), incremental=True, recursive=True, resolve=True)
    wall = time.time() - start
    max_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    dump = canonical_ingestion_dump(store)
    sha256 = hashlib.sha256(dump.encode("utf-8")).hexdigest()
    return {
        "wall_seconds": wall,
        "max_rss_mb": max_rss_kb / 1024,
        "canonical_sha256": sha256,
        "report_summary": {k: getattr(report, k) for k in ["stats", "duration_ms"]},
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: profile_full_ingest.py <corpus_path> [batch_size]")
        sys.exit(1)
    corpus = sys.argv[1]
    batch = int(sys.argv[2]) if len(sys.argv) > 2 else None
    result = profile_ingest(corpus, batch)
    for k, v in result.items():
        print(f"{k}: {v}")
