# API reference

Three surfaces expose the same deterministic engine: the **CLI** (operator),
**MCP** (agents, 17 frozen tools) and **HTTP** (`/v1/`, six routes). The
knowledge operations are a frozen contract — behavior changes only with a new
contract version. Commands marked *ops-only* are never dispatched over MCP or
HTTP.

Global CLI flags: `--store` (default `$KNOW_YOUR_CONTEXT_STORE` or
`know-your-context.sqlite3`), `--output-root`, `--actor`, `--idempotency-key`,
`--ops-root`, `--version`. Payloads are JSON objects; `-` reads from stdin.
Exit codes: `0` success, `1` typed operation failure, `2` validation/usage
error (`health` returns 1 when the store is unhealthy).

## Knowledge operations (frozen fourteen)

Each is a CLI subcommand taking one JSON payload, and an MCP tool
(`knowledge_<op>`) and an HTTP verb (`POST /v1/knowledge/<op>`).

| Command | MCP tool | What it does |
|---|---|---|
| `query` | `knowledge_query` | Token-budgeted traversal for a purpose; honors `access_only`, depth, mode, relation allowlist |
| `get-node` | `knowledge_get_node` | Fetch one node with provenance and scores |
| `get-subtree` | `knowledge_get_subtree` | Fetch a bounded subtree |
| `log-event` | `knowledge_log_event` | Append an audited learning event |
| `record-outcome` | `knowledge_record_outcome` | Attach an outcome to an event (feeds scoring) |
| `evaluate` | `knowledge_evaluate` | Score a node against the deterministic rubric |
| `review` | `knowledge_review` | Operator review of a proposal |
| `propose-improvement` | `knowledge_propose_improvement` | Propose a graph change (never applied silently) |
| `apply-improvement` | `knowledge_apply_improvement` | Apply an approved proposal |
| `rollback` | `knowledge_rollback` | Byte-exact restore of pre-apply hashes and scores |
| `path` | `knowledge_path` | Shortest path between nodes (retrieval verbs v2) |
| `explain` | `knowledge_explain` | Explain a node's neighborhood and provenance |
| `affected` | `knowledge_affected` | Impact set of a change |
| `god-nodes` | `knowledge_god_nodes` | Over-connected nodes report |

Contract v3/v4 additions (same three surfaces):

| Command | MCP tool | What it does |
|---|---|---|
| `ingest-file` | `knowledge_ingest_file` | Ingest one file into the v3 ingestion tables (proposal-only) |
| `ingest-directory` | `knowledge_ingest_directory` | Ingest a directory tree (bytewise order, incremental) |
| `submit-extraction` | `knowledge_submit_extraction` | Queue an external (vision/LLM) extraction, hash-bound, review-gated (contract v4) |

## Ops operations (CLI + HTTP `POST /v1/ops/<op>`)

| Command | What it does |
|---|---|
| `rebuild` | Rebuild the projection from the event log |
| `backup` | Contained, verified backup of the store |
| `restore` | Restore from a verified backup |
| `health` | 11 fail-closed self-checks; exit 1 when unhealthy |
| `relearn-scan` | Detect source drift and emit a deterministic `RelearnOffer` |
| `relearn-status` | Show the relearn state machine |
| `relearn-preview` | Preview the diff a relearn would apply |
| `relearn-apply` | Apply an approved relearn mode |

## Management commands (ops-only)

| Command | Subcommands / key flags | What it does |
|---|---|---|
| `serve` | `--daemon`, `--proxy`, `--socket`, `--http`, `--capability-mode` | Persistent server: HTTP adapter, superpower daemon, or MCP stdio proxy |
| `daemon` | `status`, `stop` (`--force`) | Inspect or stop the superpower daemon; stop escalates graceful → forced → SIGKILL |
| `capability` | `issue`, `revoke`, `list` | Manage `kyc1.` capability tokens (see [security.md](security.md)) |
| `export` | `--formats dot,mermaid,…` / `all`, `--out` | Twelve deterministic export formats from the active generation |
| `analytics` | `--modules centrality,communities,…` / `all` | Analytics artifacts (PageRank, HITS, Katz, Louvain, bridges, health, …) |
| `report` | `--format json\|markdown`, `--top-god`, `--top-surprising`, `--top-questions` | Composite report: god nodes, surprising connections, proven-answerable questions |
| `config` | `describe`, `effective`, `dump` | Layered configuration with provenance; secrets redacted |
| `metrics` | | Operational metrics for the store |
| `diagnose` | | 11 self-checks; exit code reflects health |
| `lifecycle` | `record`, `trend`, `prune`, `vacuum`, `fragmentation` | Hash-chained health ledger, retention, verified vacuum |
| `doctor` | | Five probe categories (env, deps, permissions, integrity, performance) with structured suggestions |
| `migrate` | `plan`, `apply`, `export`, `import`, `verify` | Schema migration ladder and hashed portable bundles |
| `watchtower` | | Fleet aggregation across stores; probes never write; exit 1 if any store is down/corrupt |
| `webview` | `--port`, `--no-open` | Loopback-only token-authenticated web viewer |
| `ingest` | `path`, incremental flags | File or directory ingestion (proposal-only) |
| `extraction` | `review` (`--status`, `--receipt`), `approve`, `reject` | Operator-only contract-v4 review queue |
| `watch` | `path`, `--interval`, `--once`, `--max-cycles`, `--debounce` | Polling watcher driving incremental re-ingestion |

## MCP server

`know_your_context.mcp_server` exposes exactly 17 tools — the fourteen
frozen operations plus `knowledge_ingest_file`, `knowledge_ingest_directory`
and `knowledge_submit_extraction`. Every call is argument-validated against
the tool schema, audited (rejections included), and answered with canonical
JSON or an error envelope `{code, message, audit_event_id}`. Unknown tools
return `UNKNOWN_TOOL`; internal failures return `INTERNAL_ERROR` with
sanitized text — raw exception messages never cross the boundary. The server
refuses to start when provider credentials exist in its environment
([security.md](security.md)). Hermes setup: [integration/hermes.md](integration/hermes.md).

## HTTP API

Version `v1`, loopback by default with a host allowlist
(`localhost`, `127.0.0.1`, `::1`), byte-capped bodies with a total deadline.

| Route | Method | What it does |
|---|---|---|
| `/v1/health` | GET | Liveness/health report |
| `/v1/knowledge/ingest/file` | POST | Contract-v3 single-file ingestion |
| `/v1/knowledge/ingest/directory` | POST | Contract-v3 directory ingestion |
| `/v1/knowledge/extraction` | POST | Contract-v4 external extraction submission |
| `/v1/knowledge/{operation}` | POST | One of the fourteen frozen knowledge operations |
| `/v1/ops/{operation}` | POST | One of the eight ops operations |

Errors are typed and stable: `VALIDATION_ERROR` (adapter boundary, already
journaled), `NOT_FOUND` (unknown operation), `PAYLOAD_TOO_LARGE` (byte cap),
`INTERNAL_ERROR` (sanitized). Responses serialize through `to_jsonable` into
canonical JSON.

## Reading errors and retries

Typed failures are deterministic: retrying an unchanged failing call fails
the same way (`PATH_NOT_FOUND`, `UNSUPPORTED_MEDIA_TYPE`,
`FILE_TOO_LARGE`, `SCOPE_DENIED`, …). The single retryable signal is
`SERVER_BUSY`, which carries `retry_after_ms`. Every failure on a mutation
path is journaled exactly once — the audit id in the error envelope is the
receipt.
