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

## Hasil #1 (skala/hardware) ↔ Hasil #2 (verdict vs kompetitor)

Angka skala **L1–L4** (jumlah file/agen, korpus dan harness identik untuk
ketiga produk) adalah dasar metrik pada instrumen verdict di atas. Tier
hardware menjelaskan mengapa angkanya berubah: host uji 2 vCPU / 3,9 GB RAM
membuat `storm_rss_mb` menjadi metrik mahal, dan `sqlite RSS cap` menurunkan
`ingest_rss_mb` L4 dari 1.356,9 MB ke 721,4 MB sehingga verdict metrik itu
berubah KALAH → MENANG.

### Status akhir per level (baris kanonik)

| Level | MENANG | SETARA | KALAH | Sumber |
|-------|--------|--------|-------|--------|
| L1 (100 file, 1 agen) | 4 | 6 | 0 | `docs/research/final-head-to-head.md` §L1 |
| L2 (1.000 file, 10 agen) | 7 | 4 | 0 | §L2 |
| L3 (5.000 file, 50 agen) | 10 | 1 | 0 | §L3 |
| L4 (5.137 file / 2,12 jt LOC, 50 agen) | 10 | 0 | 1 | §L4 |

Reproduksi:

```
/root/know-your-context/.venv/bin/python /root/.hermes/cache/scratch/stress3/harness_final.py L3,L4 kyc
uv run python bench/verdict/run.py run      # instrumen 7-level
```

### Sudah tercapai vs belum

- **Tercapai:** ingest wall (L4 281–295 s vs 1.480/1.730 s), `ingest_rss_mb`
  (718–727 MB < 860/1.645), `artifact_mb`, `q_p50` (24,8–27,4 s vs
  119,5/127,7 s), hit-rate W1/W2 50/50, error 0 %, sigkill 0, `solo_w1`
  (532–742 ms vs 1.678/58.849 ms) — pada L4.
- **Belum:** `storm_rss_mb` L4 = 2.556–2.630 MB vs graphify 2.413 MB
  (**+7 %**, KALAH). L3 `storm_rss_mb` 2.490–2.542 vs 2.436 (**SETARA**).
  L1 `q_p50` 746 ms vs graphify 630 ms (**SETARA**).
- **Sebab terukur:** setiap modul inti menarik `pydantic` (+9,3 MB/proses,
  lantai 32–33 MB/proses); menutup 7 % itu menuntut refactor inti
  `domain.py`/`contracts.py`, bukan tweak konfigurasi.

## Audit Finish #1–#2 (canonical planning matrix)

The complete cross-repository mapping, L1–L4 coverage matrix, per-product metrics, acceptance criteria, and unproven gaps now live in [`research/finish-1-2-audit.md`](research/finish-1-2-audit.md). This section is the short verdict; that document is the audit record.

### Status without invented numbers

| Finish | Proven outcome | Not proven / not finished |
|---|---|---|
| #1 — push-200 | Algorithm and performance targets are recorded as met in `task1-push200-verdict.md`; composite is reported as 405.88% vs CodeGraph and 1362.92% vs Graphify for the cited ledger | Professionalism L1 vs CodeGraph is 78.35%, not 200%; no volume inflation is allowed. Official ledger was not rerun in this audit |
| #2 — audit + head-to-head | 31 wave claims audited: 27 proven, 2 partial, 2 minor not verified; L2/L3/L4 have cited measurements; final L3 is 10/1/0 and final L4 is 10/0/1 in the cited run | L1 table is not synchronized to the latest ledger tail; L4 storm RSS remains a stable-band loss vs Graphify; server-mode, retrieval relevance, dedicated non-ASCII stress, and some WS-C/name-match baselines are **BELUM TERUKUR** |

**Do not read hit-rate as retrieval quality:** the stress harness has no relevance ground truth. **Do not compare absolute values across ledgers or host windows:** only same-run product comparisons are verdict evidence.

**Next acceptance gates:** regenerate L1 from the current ledger; repeat L4 storm RSS serially and retain a band; then rerun the official Finish #1 instrument. Until those are done, the overall Finish #1–#2 verdict is **terukur sebagian, belum final penuh**.