"""The operation-name vocabulary, dependency-free.

``http_api`` owns the dispatch surface and has always exported the three
tuples below; it re-exports them from here so ``import
know_your_context.http_api`` keeps every historical name. They live in their
own module because the CLI needs them to *build its parser* — and importing
``http_api`` for three string tuples pulls the whole extraction/projection/
networkx/pypdf/uvicorn graph into every invocation, including ``--version``
(measured 1.2 s vs 0.15 s for the reference tools; SUB-173 B4).

Nothing here may import anything: this module is the first thing the CLI
touches and must stay free of side effects.
"""

from __future__ import annotations

__all__ = ["DEFAULT_HOST_ALLOWLIST", "KNOWLEDGE_OPERATIONS", "OPS_OPERATIONS"]

DEFAULT_HOST_ALLOWLIST: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
KNOWLEDGE_OPERATIONS: tuple[str, ...] = (
    # The frozen ten v1 operations (spec section 8).
    "query",
    "get_node",
    "get_subtree",
    "log_event",
    "record_outcome",
    "evaluate",
    "review",
    "propose_improvement",
    "apply_improvement",
    "rollback",
    # Contract v2 (operator ruling 2026-10-26, progress.md; addendum
    # docs/contracts/retrieval-verbs-v2.md): the four G1 retrieval verbs.
    "path",
    "explain",
    "affected",
    "god_nodes",
)
OPS_OPERATIONS: tuple[str, ...] = (
    "rebuild",
    "backup",
    "restore",
    "health",
    # Final-audit F-4 (adapter half): the operator-gated relearn lifecycle
    # rides the ops route — the knowledge operations stay untouched by it.
    "relearn_scan",
    "relearn_status",
    "relearn_preview",
    "relearn_apply",
)
