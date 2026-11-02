# Architecture

Know Your Context is a deterministic local context engine for AI agents,
organized as three pillars — multi-modal **knowledge**, deterministic **code
intelligence**, and **auditable memory** — over one append-only SQLite event
store. There is no model, no provider call and no vector index inside the
process; the boundary is enforced at runtime (see [security.md](security.md)).

## Component diagram (text)

```
                        ┌──────────────────────────────────────────────┐
  agents / operators    │                 SURFACES                     │
  ──────────────────▶   │  cli.py   mcp_server.py   http_api.py        │
                        │  serve/webview.py (loopback, token)          │
                        └───────┬──────────────────┬───────────────────┘
                                │ frozen ops        │ ops-only commands
                                ▼                   ▼
                        ┌───────────────┐   ┌───────────────────────────┐
                        │  service.py   │   │ ops/ lifecycle doctor     │
                        │  KnowledgeService│ │     migrate watchtower    │
                        │  14 frozen ops│   │ export/report/ingest CLIs │
                        └──────┬────────┘   └───────────────────────────┘
                               │ audited events
        ┌──────────────────────┼───────────────────────────────┐
        ▼                      ▼                               ▼
┌───────────────┐     ┌────────────────┐             ┌──────────────────┐
│ store.py      │     │ relearn.py     │             │ serve/           │
│ append-only,  │     │ operator-gated │             │ superpower.py    │
│ hash-chained, │     │ re-baseline    │             │ 50-agent daemon  │
│ schema v4, WAL│     └───────────────┘             │ rpc.py proxy.py  │
└──────┬────────┘                                 │ budgets.py       │
       │ rebuild                                   │ simulation.py    │
       ▼                                           └────────┬─────────┘
┌────────────────┐                                          │ unix socket 0600
│ projection.py │                                            ▼
│ read model    │                              single writer + read pool
└──────┬────────┘                              (WAL, group commit)
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ READ SIDE: retrieval.py retrieval_v2/ analytics/ exporters/ report/  │
└──────────────────────────────────────────────────────────────────────┘

INGESTION (proposal-only, contract v3/v4):

sources ─▶ ingest/languages.py (33 languages, fail-closed registry)
        ─▶ ingest/extractor.py  (tree-sitter; Rust kernel byte-identical)
        ─▶ ingest/pipeline.py  (files/symbols/graph_edges/unresolved_refs)
        ─▶ resolve/resolver.py (exact → name_tail → import_path tiers)
        ─▶ resolve/synthesis.py (12 deterministic synthesis passes)
        ─▶ resolve/lang_helpers.py (21 helpers, kill-switched tier)
        ─▶ ingest/watcher.py / source_watcher.py ─▶ update_pipeline.py
```

## Write path (auditable memory)

Writes never go straight to the graph. Every mutation lands as an event in
`store.py` (append-only, hash-chained, schema v4, WAL), becomes a proposal,
and is applied through the operator-gated flow in `contracts.py` /
`service.py`. `relearn.py` re-baselines only after an explicit operator
decision; `update_pipeline.py` makes the watcher path *detect-only* — a
changed corpus produces a `RelearnOffer` and sets `RELEARN_PENDING`, never a
silent baseline flip. Rollback restores exact pre-apply hashes and scores.

## Read side

`projection.py` rebuilds the read model from the event log; reads are served
from the projection and never mutate. `retrieval_v2/` adds FTS5 symbol
lookup, call hierarchy and token-budgeted retrieval; `analytics/` provides
PageRank, HITS, Katz, Louvain, cycles, bridges and health scores;
`exporters/` renders twelve deterministic formats (DOT, Mermaid, CSV,
GraphML, GEXF, Cytoscape, D3, SVG, TGF, adjacency, HTML, markdown book);
`report/` composes god-node and surprising-connection reports with
proven-answerable suggested questions.

## Surfaces and the frozen contract

- `cli.py` — the operator surface: the 14 knowledge operations, 8 ops
  operations, and management commands (see [api.md](api.md)).
- `mcp_server.py` — exactly 17 tools (`knowledge_*`); every call is audited,
  argument-validated and error-enveloped. OPS-only commands are never
  dispatched over MCP or HTTP.
- `http_api.py` — six routes under `/v1/`: `health`, two ingestion uploads,
  `knowledge/extraction`, and the `{operation}` dispatchers for knowledge
  and ops verbs.
- `serve/superpower.py` — the W7 multi-agent daemon: explicit capacity
  (default 50 agents), typed `SERVER_BUSY` backpressure with
  `retry_after_ms`, read pool vs single writer, fair-queue lanes, per-agent
  token buckets, session leases, escalating stop (graceful → forced →
  SIGKILL). W10 added static budgets (`budgets.py`, asserted at start),
  invariant assertions, and `simulation.py` (seeded DST-lite storms).
- `security/` — capability tokens and the no-AI runtime guard; see
  [security.md](security.md).

## Invariants that hold the design together

1. **Determinism**: same input bytes → same output bytes, across engines
   (Rust kernel vs Python extractor), processes and hash seeds; traversal
   order is bytewise everywhere.
2. **Fail closed on safety, fail open on convenience**: malformed input,
   containment escapes, capability failures and projection violations raise
   typed errors; optional accelerators (Rust kernel, helper tier) degrade to
   the baseline engine.
3. **Kill switches**: every optimization can be disabled independently
   (`KNOW_YOUR_CONTEXT_NO_GROUP_COMMIT`, `KYS_DISABLE_RUST_KERNEL`,
   `helper_tier_enabled`, `KNOW_YOUR_CONTEXT_NO_WATCH`, …).
4. **Proposal-only ingestion**: contract v3/v4 writes only the ingestion
   tables and review queues; the knowledge domain changes exclusively
   through the operator-gated propose/apply path.

## Where to read next

- [api.md](api.md) — every CLI command, MCP tool and HTTP route.
- [security.md](security.md) — the security model and its failure modes.
- [verdict.md](verdict.md) — how the 7-level verdict instrument measures
  this architecture against the reference systems.
- `docs/contracts/` — the frozen contracts (ingestion v3, extraction v4,
  retrieval verbs v2, relearn, access policy).
- `docs/adr/` — architecture decision records.
- `docs/operations.md` / `docs/verification.md` — operational runbooks.
