"""SUB-192 A/B proof: byte-identical ingestion dump before/after the _run refactor.

Usage: .venv/bin/python scratch/sub192-ab/ab_ingest_dump.py [tag]
Prints the canonical_ingestion_dump sha256 for a fresh ingest of the
tests/fixtures/ingest corpus with the REAL extractor + resolver.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from know_your_context.ingest.pipeline import IngestionPipeline, canonical_ingestion_dump  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "tests" / "fixtures" / "ingest"


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    assert CORPUS.is_dir(), CORPUS
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "store.sqlite3")
        with EventStore(db) as store:
            pipeline = IngestionPipeline(store)  # real extractor + real resolver
            report = pipeline.ingest_path(CORPUS, incremental=True, recursive=True, resolve=True)
            dump = canonical_ingestion_dump(store)
            digest = hashlib.sha256(dump.encode("utf-8")).hexdigest()
    print(f"tag={tag} dump_sha256={digest} dump_bytes={len(dump)}")
    print(
        f"stats={dict(report.stats)} totals={dict(report.totals)} "
        f"resolution={report.resolution_status} files={len(report.files)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
