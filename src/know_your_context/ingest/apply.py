"""Apply an ingested code index to the knowledge graph (B1, SUB-173).

Why this module exists
----------------------

``ingest`` (ops) and the contract-v3 ``ingest_file``/``ingest_directory`` verbs
write the *code* tables — ``files``, ``symbols``, ``graph_edges``,
``unresolved_refs`` — and stop. Knowledge ``query`` reads the *projection*,
which is replayed from ``event_log`` domain events and materialised in
``node_revision``. Nothing bridged the two, so a freshly ingested corpus was
invisible to ``query``: measured 0/4, 0/20, 0/50 and 0/50 hits across the four
stress levels, with ``node_revision = 0`` and ``store_watermark = 0`` while
``codeintel callers-of`` worked fine on the same store.

``docs/contracts/write-contract-v5.md`` (ACTIVE) states the write lane applies
directly; this module is the missing apply step, exposed as an explicit
operator lane (``ingest --apply`` / ``apply-ingest``) so no frozen operation
changes shape.

Shape of the applied graph
--------------------------

``KROOT`` → seven primary roots (``SeedImporter.ensure_roots``, unchanged) →
one ``branch`` node per ingested file (parent: the primary domain) → one
``leaf`` node per extracted symbol (parent: its file branch). Node ids are
deterministic: file branches use the codegraph id recipe
(``module:<sha256(path:module:name)>``), symbol leaves reuse the extractor's
own id (``function:<digest>``), so a re-apply is a pure replay and two stores
built from the same bytes hold the same id set.

Every node is written through ``EventStore.append_domain`` — the audited,
versioned write path — inside **one** outer transaction (the group-commit
seam), so a 5,000-file corpus costs one fsync instead of one per node.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from know_your_context.domain import (
    Event,
    EventKind,
    Node,
    NodeKind,
    PrimaryDomain,
)
from know_your_context.graph_model import make_node_id
from know_your_context.importer import SeedImporter
from know_your_context.ingest.pipeline import IngestionError
from know_your_context.projection import APPLIED_NODE_BYTES, ARTIFACT_MAX_BYTES, GraphProjector
from know_your_context.store import EventStore

__all__ = [
    "APPLY_VERSION",
    "ApplyError",
    "ApplyReceipt",
    "apply_ingested_corpus",
]

#: Bumped whenever the node shape changes: it is part of the idempotency key
#: namespace, so a shape change never silently replays an old projection.
APPLY_VERSION: int = 1

#: Default fail-closed ceiling on nodes written by one apply invocation.
DEFAULT_MAX_APPLY_NODES: int = 500_000


class ApplyError(IngestionError):
    """The apply lane refused to run (nothing to apply, or over its cap)."""

    code = "INGEST_APPLY_ERROR"


@dataclass(frozen=True)
class ApplyReceipt:
    """What one apply invocation wrote (or replayed)."""

    corpus_root: str
    domain: str
    files_applied: int
    file_nodes: int
    symbol_nodes: int
    nodes_applied: int
    replayed: int
    knowledge_watermark: int
    projection_version: str | None
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_root": self.corpus_root,
            "domain": self.domain,
            "files_applied": self.files_applied,
            "file_nodes": self.file_nodes,
            "symbol_nodes": self.symbol_nodes,
            "nodes_applied": self.nodes_applied,
            "replayed": self.replayed,
            "knowledge_watermark": self.knowledge_watermark,
            "projection_version": self.projection_version,
            "duration_ms": self.duration_ms,
            "apply_version": APPLY_VERSION,
        }


def _file_branch_id(path: str) -> str:
    """The deterministic knowledge id of one ingested file's branch node."""
    return make_node_id(path=path, kind="module", name=Path(path).name)


def apply_ingested_corpus(
    store: EventStore,
    *,
    corpus_root: str | Path | None = None,
    domain: PrimaryDomain = PrimaryDomain.P1,
    actor: str = "ingest-apply",
    projector: GraphProjector | None = None,
    source_version_id: str | None = None,
    max_nodes: int = DEFAULT_MAX_APPLY_NODES,
    artifact_cap_bytes: int | None = None,
) -> ApplyReceipt:
    """Materialise every ingested file/symbol as knowledge nodes.

    Reads the store's own code index (``files`` + ``symbols``) — no re-walk, no
    re-extraction: the ingest already decided what the corpus is. The write is
    one transaction; the whole call is idempotent (fixed idempotency keys), so
    running it twice reports ``replayed == nodes_applied`` and moves nothing.

    ``projector`` rebuilds the projection once at the end so the very next
    ``query`` is served from a generation that covers the new watermark; pass
    ``None`` to leave the build to the reader's repair path.
    """
    started = time.monotonic()
    if not isinstance(domain, PrimaryDomain):
        raise ApplyError(f"domain must be a PrimaryDomain member, got {domain!r}")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
        raise ApplyError(f"max_nodes must be a positive integer, got {max_nodes!r}")
    root_label = str(Path(corpus_root).resolve()) if corpus_root is not None else str(store.db_path)
    version_id = source_version_id or f"code-index:apply-v{APPLY_VERSION}"

    files = sorted(store.list_files(), key=lambda row: row.path)
    if not files:
        raise ApplyError(
            "no ingested files to apply: run `know-your-context ingest <path>` first "
            "(the apply lane materialises the code index, it does not walk the corpus)"
        )
    if len(files) > max_nodes:
        raise ApplyError(f"{len(files)} files exceed the apply cap of {max_nodes} nodes")

    # B1 (SUB-174): the published graph must fit one artifact (retrieval-verbs-v2
    # pins ``graph.json``). The projection sizes its cap from this store's code
    # index, so a legitimate corpus publishes; an operator-supplied ceiling
    # below what the corpus needs is refused here, typed and before any write,
    # instead of failing mid-publish or wedging a later repair.
    if artifact_cap_bytes is not None:
        if isinstance(artifact_cap_bytes, bool) or int(artifact_cap_bytes) < 1:
            raise ApplyError(f"artifact_cap_bytes must be a positive integer, got {artifact_cap_bytes!r}")
        _files_indexed, symbols_indexed = store.code_index_size()
        needed = (len(files) + int(symbols_indexed)) * APPLIED_NODE_BYTES + ARTIFACT_MAX_BYTES
        if needed > int(artifact_cap_bytes):
            raise ApplyError(
                f"corpus needs about {needed} bytes of graph.json ({(len(files) + int(symbols_indexed))} "
                f"nodes x {APPLIED_NODE_BYTES} B), over the {int(artifact_cap_bytes)}-byte artifact cap; "
                "raise the cap or apply a smaller corpus"
            )

    # Roots are shared with every other import lane: fixed ids, fixed
    # idempotency keys, so the primaries exist exactly once per store.
    SeedImporter(store, actor=actor).ensure_roots(version_id)

    file_nodes = 0
    symbol_nodes = 0
    replayed = 0
    with store.transaction():  # one fsync for the whole corpus
        for row in files:
            branch = Node(
                node_id=_file_branch_id(row.path),
                parent_id=domain.value,
                kind=NodeKind.BRANCH,
                domain=domain,
                summary=Path(row.path).name,
                payload={"source_file": row.path, "language": row.language, "node_kind": "module"},
                provenance=(f"code://{row.path}",),
                context_tags=(row.language, "ingest-apply"),
                source_version_id=version_id,
            )
            receipt = store.append_domain(
                Event(
                    kind=EventKind.NODE_UPSERT,
                    node_id=branch.node_id,
                    domain=branch.domain,
                    node=branch,
                    source_version_id=version_id,
                    actor=actor,
                ),
                idempotency_key=f"ingest-apply:v{APPLY_VERSION}:{branch.node_id}",
            )
            file_nodes += 1
            replayed += 1 if receipt.idempotent_replay else 0

            for symbol in store.symbols_in_path(row.path):
                leaf = Node(
                    node_id=symbol.node_id,
                    parent_id=branch.node_id,
                    kind=NodeKind.LEAF,
                    domain=domain,
                    summary=symbol.name,
                    payload={
                        "source_file": row.path,
                        "node_kind": symbol.kind,
                        "line": symbol.line,
                        "col": symbol.col,
                    },
                    provenance=(f"code://{row.path}#L{symbol.line}",),
                    context_tags=(symbol.kind, row.language, "ingest-apply"),
                    source_version_id=version_id,
                )
                receipt = store.append_domain(
                    Event(
                        kind=EventKind.NODE_UPSERT,
                        node_id=leaf.node_id,
                        domain=leaf.domain,
                        node=leaf,
                        source_version_id=version_id,
                        actor=actor,
                    ),
                    idempotency_key=f"ingest-apply:v{APPLY_VERSION}:{leaf.node_id}",
                )
                symbol_nodes += 1
                replayed += 1 if receipt.idempotent_replay else 0
                if file_nodes + symbol_nodes > max_nodes:
                    raise ApplyError(f"apply exceeded its {max_nodes}-node cap")

    if projector is not None:
        projector.rebuild()

    return ApplyReceipt(
        corpus_root=root_label,
        domain=domain.value,
        files_applied=len(files),
        file_nodes=file_nodes,
        symbol_nodes=symbol_nodes,
        nodes_applied=file_nodes + symbol_nodes,
        replayed=replayed,
        knowledge_watermark=store.knowledge_high_watermark(),
        projection_version=store.projection_version(),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
