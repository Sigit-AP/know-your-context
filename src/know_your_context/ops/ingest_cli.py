"""The operational ``ingest`` CLI command (Wave 2, SUB-89).

Runs :class:`know_your_context.ingest.pipeline.IngestionPipeline` over a file
or directory and prints a one-line summary (or canonical JSON with
``--json``): files proposed/skipped/failed, node/edge counts, refs
resolved/pending/failed, and the duration. Typed failures use the standard
error envelope on stderr.

OPS surface only — like ``export``/``analytics`` this is not a knowledge
operation: it never joins ``KNOWLEDGE_OPERATIONS``/``OPS_OPERATIONS`` and is
never dispatched over MCP or HTTP. The frozen fourteen operations are
untouched. Ingestion output is proposal-only (contract v3); applying it to
the knowledge graph goes through the operator-gated propose/apply path.
"""

from __future__ import annotations

import argparse
import sys

from know_your_context.domain import canonical_json
from know_your_context.ingest.pipeline import (
    ExtractorUnavailableError,
    IngestionBudget,
    IngestionError,
    IngestionPipeline,
    IngestionReport,
)
from know_your_context.store import EventStore

__all__ = ["run_ingest_command"]


def _usage_error(message: str) -> int:
    envelope = {"error": {"code": "CLI_USAGE", "message": message, "retryable": False, "audit_event_id": None}}
    print(canonical_json(envelope), file=sys.stderr)
    return 2


def _operation_error(code: str, message: str) -> int:
    envelope = {"error": {"code": code, "message": message, "retryable": False, "audit_event_id": None}}
    print(canonical_json(envelope), file=sys.stderr)
    return 1


def render_summary(report: IngestionReport) -> str:
    """The deterministic one-line human summary.

    B3 (SUB-173): when the walk cap dropped files, the summary *leads* with it.
    The old form buried ``guards_hit max_files`` at the tail of a long line, so
    a truncated corpus looked like a successful complete ingest.
    """
    stats = report.stats
    totals = report.totals
    prefix = "ingest: "
    if report.truncated:
        prefix = (
            f"ingest: TRUNCATED (max_files={report.max_files}, "
            f"{report.files_truncated} file(s) not ingested; raise --max-files or ingest the rest) | "
        )
    parts = [
        f"files {stats['scanned']} (proposed {stats['proposed']}, skipped {stats['skipped']}, "
        f"failed {stats['failed']}, deleted {stats['deleted']})",
        f"nodes {totals['nodes']}",
        f"edges {totals['edges']}",
    ]
    if report.resolution is not None:
        parts.append(
            f"refs parked {totals['refs_parked']}, resolved {report.resolution['resolved']}, "
            f"failed {report.resolution['failed']}"
        )
    else:
        parts.append(f"refs parked {totals['refs_parked']}, resolution {report.resolution_status}")
    if report.guards_hit:
        parts.append(f"guards_hit {','.join(report.guards_hit)}")
    if report.primary_suggestions:
        parts.append(f"primary_suggestions {len(report.primary_suggestions)}")
    parts.append(f"duration {report.duration_ms} ms")
    return prefix + " | ".join(parts)


def _apply_after_ingest(store: EventStore, report: IngestionReport, args: argparse.Namespace):
    """Run the apply lane for ``ingest --apply`` and return its receipt.

    The projection is rebuilt only when the caller asked for it (default): the
    rebuild is what makes the very next ``query`` cheap and stale-free, but an
    operator batching ingests can defer it with ``--no-apply-rebuild``.
    """
    from know_your_context.ingest.apply import apply_ingested_corpus
    from know_your_context.projection import GraphProjector

    projector = GraphProjector(store, output_root=args.output_root) if args.apply_rebuild else None
    return apply_ingested_corpus(
        store,
        corpus_root=report.root,
        projector=projector,
        actor=args.actor,
    )


def run_ingest_command(args: argparse.Namespace) -> int:
    """The ``ingest`` ops command. Exit 0 success, 1 typed failure, 2 usage."""
    try:
        budget = IngestionBudget(
            max_files=args.max_files,
            max_nodes=args.max_nodes,
            max_edges=args.max_edges,
            max_refs=args.max_refs,
            max_file_bytes=args.max_file_bytes,
        )
    except ValueError as error:
        return _usage_error(str(error))
    store = EventStore(args.store)
    apply_receipt = None
    try:
        pipeline = IngestionPipeline(store, budget=budget, actor=args.actor)
        report = pipeline.ingest_path(
            args.path,
            incremental=args.incremental,
            recursive=args.recursive,
            resolve=not args.no_resolve,
        )
        if getattr(args, "apply", False):
            # B1 (SUB-173): the official ingest → apply → query path. Without
            # this the store holds a code index that `query` cannot see.
            apply_receipt = _apply_after_ingest(store, report, args)
    except (IngestionError, ExtractorUnavailableError) as error:
        return _operation_error(getattr(error, "code", "INGESTION_ERROR"), str(error))
    finally:
        store.close()
    if args.json:
        payload = report.to_dict()
        if apply_receipt is not None:
            payload = {**payload, "status": "applied", "apply": apply_receipt.to_dict()}
        print(canonical_json(payload))
    else:
        print(render_summary(report))
        if apply_receipt is not None:
            print(
                f"apply: files {apply_receipt.files_applied} | file nodes {apply_receipt.file_nodes} | "
                f"symbol nodes {apply_receipt.symbol_nodes} | replayed {apply_receipt.replayed} | "
                f"watermark {apply_receipt.knowledge_watermark} | projection "
                f"{apply_receipt.projection_version or '-'} | duration {apply_receipt.duration_ms} ms"
            )
    if report.truncated:
        # Stderr, not stdout: the machine-readable stream stays canonical while
        # an operator (or a wrapper script) cannot miss the cap. Exit stays 0 —
        # a capped walk is a legitimate partial ingest, not a failure.
        print(
            f"ingest: TRUNCATED max_files={report.max_files}: {report.files_truncated} file(s) were not "
            f"ingested; re-run with a higher --max-files (max 65536) to index the remainder",
            file=sys.stderr,
        )
    return 0
