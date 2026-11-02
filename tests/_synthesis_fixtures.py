"""Harness for the W15 synthesis-pass fixture scenarios (SUB-129).

Two fixture shapes live under ``tests/fixtures/synthesis/<pass>/``:

* **source scenarios** — real source files ingested through the real pipeline
  (``IngestionPipeline`` → resolver) before ``run_synthesis`` fires;
* **seed scenarios** — a ``seed.json`` describing files/symbols/edges/refs,
  used for passes whose trigger state the resolver tier would consume first
  (documented per scenario; the seeded state is exactly what a real pipeline
  leaves behind when its tiers fail closed).

Every scenario carries ``expected_edges``: ``(source, target, kind)`` triples
that MUST exist with ``provenance == "synthesis:<pass>"`` after synthesis.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from know_your_context.ingest.pipeline import IngestionPipeline
from know_your_context.resolve.resolver import resolve_pending
from know_your_context.resolve.synthesis import run_synthesis
from know_your_context.store import EventStore

SYNTHESIS_ROOT = Path(__file__).parent / "fixtures" / "synthesis"
SEEN = "2026-11-02T00:00:00+00:00"


def scenario_dirs() -> list[Path]:
    """Every scenario directory, sorted (deterministic order)."""
    if not SYNTHESIS_ROOT.is_dir():
        return []
    return sorted(path for path in SYNTHESIS_ROOT.iterdir() if path.is_dir())


def _seed(store: EventStore, payload: dict) -> None:
    for entry in payload.get("files", []):
        store.upsert_file(
            path=entry["path"],
            sha256=hashlib.sha256(entry["path"].encode()).hexdigest(),
            size=10,
            mtime=100,
            language=entry.get("language", "python"),
            generated_hint=False,
            extraction_version=1,
        )
    for entry in payload.get("symbols", []):
        store.upsert_symbol(
            node_id=entry["node_id"],
            name=entry["name"],
            kind=entry["kind"],
            path=entry["path"],
            line=entry.get("line", 1),
            col=entry.get("col", -1),
        )
    ref_ids: list[int] = []
    for entry in payload.get("refs", []):
        ref_id = store.insert_unresolved_ref(
            source_node_id=entry["source_node_id"],
            target_name=entry["target_name"],
            kind=entry["kind"],
            first_seen=SEEN,
        )
        ref_ids.append(ref_id)
        status = entry.get("status", "pending")
        if status == "failed":
            store.record_resolution_attempt(
                ref_id, outcome="failed", reason=entry.get("failure_reason", "no_resolution")
            )
        elif status == "resolved":
            store.record_resolution_attempt(ref_id, outcome="resolved")
    for entry in payload.get("edges", []):
        metadata = entry.get("metadata")
        if isinstance(metadata, str):
            # "$ref<N>" placeholders bind to the Nth inserted ref_id.
            for index, ref_id in enumerate(ref_ids):
                metadata = metadata.replace(f"$ref{index}", str(ref_id))
        store.add_graph_edge(
            entry["source"],
            entry["target"],
            entry["kind"],
            provenance=entry.get("provenance", "ingest:0123456789abcdef"),
            metadata=metadata,
        )


def run_scenario(store: EventStore, directory: Path, tmp_path: Path) -> list[tuple[str, str, str, str]]:
    """Execute one scenario; return (source, target, kind, provenance) rows."""
    seed_file = directory / "seed.json"
    if seed_file.is_file():
        _seed(store, json.loads(seed_file.read_text(encoding="utf-8")))
    else:
        workdir = tmp_path / directory.name
        shutil.copytree(directory, workdir)
        pipeline = IngestionPipeline(store)
        pipeline.ingest_path(workdir)
        resolve_pending(store)
    run_synthesis(store)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT source, target, kind, provenance FROM graph_edges ORDER BY source, target, kind"
        ).fetchall()
    return [(str(r["source"]), str(r["target"]), str(r["kind"]), str(r["provenance"])) for r in rows]


def _resolve_endpoint(store: EventStore, endpoint: object) -> str:
    """An endpoint is a literal node id or a unique-symbol query."""
    if isinstance(endpoint, str):
        return endpoint
    assert isinstance(endpoint, dict), f"endpoint must be a node id or symbol query, got {endpoint!r}"
    query = endpoint["symbol"]
    with store.read() as conn:
        rows = conn.execute(
            "SELECT node_id FROM symbols WHERE path = ? AND kind = ? AND name = ?",
            (query["path"], query["kind"], query["name"]),
        ).fetchall()
    assert len(rows) == 1, f"expected exactly one symbol for {query}, got {len(rows)}"
    return str(rows[0]["node_id"])


def expected_edges(store: EventStore, directory: Path) -> list[tuple[str, str, str, str]]:
    """Expected (source, target, kind, provenance) from expected.json."""
    payload = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    return [
        (
            _resolve_endpoint(store, e["source"]),
            _resolve_endpoint(store, e["target"]),
            str(e["kind"]),
            str(e["provenance"]),
        )
        for e in payload["edges"]
    ]
