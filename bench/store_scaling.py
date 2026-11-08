#!/usr/bin/env python3
"""Store event-path scaling measurement (TASK2 WS-A: S1 + S2).

Reconstruction of the cluster-a verdict "B1 lane A" harness (the original
lived in pruned scratch). Deterministic, offline, no mocks.

Subcommands::

    lane-a PATH [--events N]   build a store with N serial append_domain
                               writes (1 txn/op), reporting windowed ops/s —
                               the S1 COUNT(*) regression shows up as the
                               first-window -> last-window throughput drop;
                               then runs verify_chain (wall + peak RSS) — S2.
    verify PATH                verify_chain wall + peak RSS on an EXISTING store.
    explain PATH               EXPLAIN QUERY PLAN for the insert-path count
                               (S1) and the journal conflict lookup (S3).

Peak RSS uses resource.getrusage(RUSAGE_SELF).ru_maxrss (KiB on Linux), the
same high-water metric as the verdict ("RSS puncak 951 MiB, delta ~920 MiB").
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# STORE_SCALING_SRC overrides the source tree (used for before/after A/B
# against a clean-HEAD worktree without copying this script into it).
sys.path.insert(0, os.environ.get("STORE_SCALING_SRC", str(Path(__file__).resolve().parents[1] / "src")))

from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402

# A/B helper: opening a newer-schema store with older code fails closed by
# design; the cap shim exists so measurement-only before/after runs can open
# the SAME store file with both revisions (tests monkeypatch the same global).
import know_your_context.store as _store_module  # noqa: E402

if _cap := os.environ.get("STORE_SCALING_SCHEMA_CAP"):
    _store_module.SCHEMA_VERSION = int(_cap)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
WINDOW = 1000


def _node(node_id: str) -> Node:
    return Node.model_validate(
        {
            "node_id": node_id,
            "kind": NodeKind.LEAF,
            "domain": PrimaryDomain.P1,
            "source_version_id": "sv-1",
            "payload": {"fact": node_id},
            "summary": f"node {node_id}",
            "provenance": ("soul/01-identity-compliance.md",),
            "context_tags": ("identity",),
            "scope": "global",
            "created_at": NOW,
            "updated_at": NOW,
            "professional_level": 1,
            "version": 1,
        }
    )


def _node_event(node: Node) -> Event:
    return Event.model_validate(
        {
            "kind": EventKind.NODE_UPSERT,
            "node_id": node.node_id,
            "domain": node.domain,
            "node": node,
            "source_version_id": node.source_version_id,
            "occurred_at": NOW,
        }
    )


def _peak_rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _verify(store: EventStore) -> dict[str, object]:
    rss_before = _peak_rss_mib()
    started = time.monotonic()
    report = store.verify_chain()
    wall_s = time.monotonic() - started
    return {
        "ok": report.ok,
        "checked": report.checked,
        "wall_s": round(wall_s, 3),
        "rss_peak_mib_before": round(rss_before, 1),
        "rss_peak_mib_after": round(_peak_rss_mib(), 1),
    }


def cmd_lane_a(path: Path, events: int) -> int:
    if path.exists():
        print(f"error: {path} already exists; lane-a builds a fresh store", file=sys.stderr)
        return 2
    store = EventStore(path)
    windows: list[dict[str, float | int]] = []
    started = time.monotonic()
    window_started = started
    try:
        for i in range(events):
            store.append_domain(_node_event(_node(f"n-{i}")), idempotency_key=f"idem-{i}")
            done = i + 1
            if done % WINDOW == 0:
                now = time.monotonic()
                windows.append({"at_event": done, "ops_per_s": round(WINDOW / (now - window_started), 1)})
                window_started = now
        total_s = time.monotonic() - started
        result = {
            "events": events,
            "total_s": round(total_s, 1),
            "avg_ops_per_s": round(events / total_s, 1),
            "first_window": windows[0],
            "last_window": windows[-1],
            "windows": windows,
            "verify": _verify(store),
        }
    finally:
        store.close()
    print(json.dumps(result, indent=1))
    return 0


def cmd_verify(path: Path) -> int:
    store = EventStore(path)
    try:
        print(json.dumps(_verify(store), indent=1))
    finally:
        store.close()
    return 0


def cmd_explain(path: Path) -> int:
    store = EventStore(path)
    try:
        with store.read() as conn:
            plans = {
                "insert_path_count_s1_legacy": conn.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(*) AS total FROM event_log"
                ).fetchall(),
                "insert_path_anchor_s1_fixed": conn.execute(
                    "EXPLAIN QUERY PLAN SELECT value FROM system_state WHERE key = 'chain_event_count'"
                ).fetchall(),
                "journal_conflict_lookup_s3": conn.execute(
                    "EXPLAIN QUERY PLAN SELECT request_hash FROM invocation_journal "
                    "WHERE effective_idempotency_key = ?",
                    ("probe",),
                ).fetchall(),
            }
        rendered = {name: [str(row["detail"]) for row in rows] for name, rows in plans.items()}
    finally:
        store.close()
    print(json.dumps(rendered, indent=1))
    return 0


def cmd_append(path: Path, events: int) -> int:
    """Append ``events`` serial writes onto an EXISTING store (S1 A/B)."""
    store = EventStore(path)
    windows: list[dict[str, float | int]] = []
    try:
        with store.read() as conn:
            base = int(conn.execute("SELECT COUNT(*) AS c FROM event_log").fetchone()["c"])
        window_started = time.monotonic()
        started = window_started
        for i in range(events):
            store.append_domain(_node_event(_node(f"n-{base + i}")), idempotency_key=f"idem-{base + i}")
            done = i + 1
            if done % WINDOW == 0:
                now = time.monotonic()
                windows.append({"at_event": base + done, "ops_per_s": round(WINDOW / (now - window_started), 1)})
                window_started = now
        total_s = time.monotonic() - started
        result = {
            "base_events": base,
            "appended": events,
            "total_s": round(total_s, 1),
            "avg_ops_per_s": round(events / total_s, 1) if total_s else None,
            "windows": windows,
            "verify": _verify(store),
        }
    finally:
        store.close()
    print(json.dumps(result, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    lane = sub.add_parser("lane-a", help="serial append_domain scaling (S1) + verify_chain (S2)")
    lane.add_argument("path", type=Path)
    lane.add_argument("--events", type=int, default=160000)
    append = sub.add_parser("append", help="append K serial writes onto an existing store (S1 A/B)")
    append.add_argument("path", type=Path)
    append.add_argument("--events", type=int, default=5000)
    verify = sub.add_parser("verify", help="verify_chain wall + peak RSS on an existing store")
    verify.add_argument("path", type=Path)
    explain = sub.add_parser("explain", help="EXPLAIN plans for S1/S3 queries")
    explain.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "lane-a":
        return cmd_lane_a(args.path, args.events)
    if args.command == "append":
        return cmd_append(args.path, args.events)
    if args.command == "verify":
        return cmd_verify(args.path)
    return cmd_explain(args.path)


if __name__ == "__main__":
    sys.exit(main())
