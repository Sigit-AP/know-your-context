"""Crash-injection child for the model-based store tests (W16).

Not a test module. Spawned as a real subprocess by
``tests/test_model_based_store.py``; it performs committed writes and then
dies with ``os._exit(137)`` — the SIGKILL signature: no atexit, no finally,
no connection close. The parent reopens the store and asserts the model.

Usage: ``python tests/_model_store_crash_child.py <db_path> <mode> <count> <id_prefix>``

Modes:
  commit       append <count> committed node upserts, then die without close.
  uncommitted  append <count> committed upserts, open a transaction, insert
               one more event row, and die before the commit — the partial
               transaction must leave no trace after recovery.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _die() -> None:
    os._exit(137)  # SIGKILL signature: no cleanup of any kind


def main() -> int:
    db_path = Path(sys.argv[1])
    mode = sys.argv[2]
    count = int(sys.argv[3])
    prefix = sys.argv[4]

    from know_your_context.domain import Event, EventKind, Node, NodeKind, PrimaryDomain
    from know_your_context.store import EventStore

    store = EventStore(db_path)
    for index in range(count):
        node_id = f"{prefix}-{index}"
        store.append_domain(
            Event(
                kind=EventKind.NODE_UPSERT,
                node_id=node_id,
                domain=PrimaryDomain.P1,
                node=Node(
                    node_id=node_id,
                    kind=NodeKind.LEAF,
                    domain=PrimaryDomain.P1,
                    summary=f"crash child {node_id}",
                    provenance=("context://crash-child",),
                    source_version_id="sv-crash",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                source_version_id="sv-crash",
                occurred_at=NOW,
            ),
            idempotency_key=f"crash:{prefix}:{index}",
        )
        print(f"committed {node_id}", flush=True)

    if mode == "commit":
        _die()
    elif mode == "uncommitted":
        with store.transaction() as conn:
            store.insert_event(
                conn,
                event_id=f"{prefix}-uncommitted",
                kind="domain",
                event_type=EventKind.NODE_UPSERT.value,
                knowledge_affecting=True,
                actor="crash-child",
                occurred_at=NOW.isoformat(),
                payload={"node": None},
                node_id=f"{prefix}-uncommitted",
                idempotency_key=f"crash:{prefix}:uncommitted",
            )
            _die()  # die inside the transaction: the row must vanish
    else:
        raise SystemExit(f"unknown mode {mode!r}")
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
