# Reading the 7-level verdict

`bench/verdict/` is a machine-measured verdict instrument: 7 levels × 10
metrics = 70 metrics, each computed by a script over all three repositories
(this repo, codegraph, graphify). Nothing is hand-written; every metric in
the ledger records its raw value and the exact measuring command, so any
number can be re-produced with
`uv run python bench/verdict/run.py metric <id> --repo <ours|codegraph|graphify>`.

## Scoring rules

- Per metric: relative percentage = `ours / opponent × 100`, computed
  **per opponent** — the two references are never merged into one figure.
- Opponent 0 and ours > 0 → reported as `unbounded` (null), never as a
  number; both 0 → 100.
- Level score = mean of the defined relative percentages of its 10 metrics.
- Composite = weighted mean across the 7 levels (equal weights, 1.0).
- The instrument never rounds a verdict up and never caps an opponent at
  zero-advantage. Determinism is pinned: two consecutive full runs produce
  byte-identical scores (verified in W12).

## The seven levels

| Level | Name | What it measures |
|---|---|---|
| l1 | profesional | engineering professionalism surface (LOC, tests, docs, changelog, license, pins) |
| l2 | codebase | structural code inventory (modules, functions, classes, typed errors, enums) |
| l3 | arsitektur | architecture surface (layers, CLI/HTTP/MCP, schema, migrations, config, kill switches) |
| l4 | flow engineering | operational flow machinery (async, locks, queues, timeouts, retries, shutdown) |
| l5 | kompleksitas codebase | code complexity + depth (functions, branches, nesting, imports, test rigor) |
| l6 | kompleksitas arsitektur | architecture complexity (components, edges, surfaces, middleware, events) |
| l7 | kompleksitas flow engineering | flow complexity (concurrency breadth, pipelines, recovery, throttling) |

## The metrics per level

- **l1**: `source_loc`, `test_loc`, `test_files`, `test_cases`, `doc_files`,
  `doc_lines`, `changelog_sections`, `comment_lines`, `license_files`,
  `pinned_deps`.
- **l2**: `packages`, `public_functions`, `classes`, `typed_errors`,
  `enums`, `interfaces`, `assertions`, `named_constants`,
  `typed_signatures`, `decorators`.
- **l3**: `layers`, `cli_commands`, `http_routes`, `mcp_tools`, `db_tables`,
  `migrations`, `env_vars`, `kill_switch_sites`, `config_fields`,
  `contract_docs`.
- **l4**: `async_functions`, `await_sites`, `lock_primitives`,
  `queue_primitives`, `timeout_sites`, `retry_sites`, `shutdown_handlers`,
  `worker_sites`, `batch_sites`, `journal_sites`.
- **l5**: `total_functions`, `branch_statements`, `max_nesting`, `imports`,
  `error_code_literals`, `property_tests`, `parametrized_tests`,
  `test_fixtures`, `loc_per_function`, `distinct_import_roots`.
- **l6**: `components`, `inter_layer_edges`, `surfaces`, `schema_versions`,
  `middleware`, `adapters`, `event_types`, `registrations`, `config_keys`,
  `kill_switch_breadth`.
- **l7**: `concurrency_breadth`, `pipeline_stages`, `recovery_paths`,
  `typed_raise_sites`, `throttle_sites`, `deadline_sites`,
  `watermark_lease_sites`, `flow_tests`, `watchdog_sites`, `drain_sites`.

## Latest measured numbers (ledger `20261102T011524Z`, W12)

Per-level relative percentages, `vs codegraph / vs graphify`:

| Level | vs codegraph | vs graphify |
|---|---|---|
| l1 | 53.90% | 88.41% |
| l2 | 730.05% | 2829.17% |
| l3 | 94.63% | 170.75% |
| l4 | 345.15% | 1616.74% |
| l5 | 96.01% | 902.88% |
| l6 | 130.20% | 307.58% |
| l7 | 412.00% | 707.41% |

Composite: **265.99% vs codegraph** (target 400% not met — reported as
measured), **946.13% vs graphify**, **606.06% combined**. Unbounded metrics
(opponent 0, ours > 0): 3 vs codegraph, 8 vs graphify — counted separately,
never converted into a number.

## How to run

```bash
uv run python bench/verdict/run.py check   # static validation of the instrument
uv run python bench/verdict/run.py run     # full 70-metric run, writes results/
uv run python bench/verdict/run.py print   # markdown summary of the last ledger
```

Exit codes: 0 = ok, 1 = check failed, 2 = usage error. Opponent repo roots
come from `KYS_VERDICT_ROOT_CODEGRAPH` / `KYS_VERDICT_ROOT_GRAPHIFY`.

## Where l1 was weak — and what W13 did about it

The W12 ledger exposed l1 (profesional) as the weakest level: this repo had
`changelog_sections` 0 (codegraph 34) and roughly half the doc lines. W13
closed the gap with real content, not padding: this changelog reconstruction
([CHANGELOG.md](../CHANGELOG.md)), the four top-level docs
([architecture.md](architecture.md), this file, [security.md](security.md),
[api.md](api.md)), and docstring completion on the watcher / pipeline /
resolver / serve surfaces — all pinned by `tests/test_docs_changelog.py`,
which re-derives the CLI/MCP/HTTP/verdict facts from source so the docs
cannot drift without a red build.
