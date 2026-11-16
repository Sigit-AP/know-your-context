# Task 3 — Fix Gap Plan (SUB-173)

Date: 2026-11-16 (UTC). Scope: close the five measured gaps from
`docs/research/stress-3-products.md` §6 (B1, B2, B3, B4, B7). Frozen contracts
(`docs/contracts/v1–v5`, digest-pinned) are **not touched**; no commits.

Method: RISET (measured root cause per bug) → PLAN (this file) → BUILD (TDD:
RED observed first, then GREEN) → RETEST (stress harness re-run, numbers
compared before/after).

## 0. Baseline measurements (this host, this HEAD ca2fe3d)

| # | Symptom | Baseline (measured now) | Target |
|---|---|---|---|
| B1 | `ingest` writes code tables only; `query` sees nothing | `ingest` L1 exit 0 `status=proposal`; `node_revision=0`; `query {"purpose":"Cls17"}` → `routing=no_match, results=[]`, `required_knowledge_watermark=0` | after `ingest --apply`: `query` returns the ingested symbols (hit > 0) |
| B2 | `STALE_PROJECTION` false positive on first readers | `repro_stale2.py`: **18/40 ops failed** (`STALE_PROJECTION`, message "does not cover knowledge watermark 0") | 0 false positives under the same 20-agent race |
| B3 | `--max-files 1024` truncates silently | 1100-file corpus → `files 1024`, exit 0, only `guards_hit max_files` buried in the summary | explicit `TRUNCATED` line + `truncated: true` in JSON + stderr warning; default raised to the documented max |
| B4 | CLI startup 1.3–1.8 s | `--version` ×5 = **1.20, 1.20, 1.30, 1.21, 1.22 s**; `-X importtime`: cli 1004 ms (http_api 752, networkx 193, pypdf 102, uvicorn 54, security 333, update_pipeline 568) | ≤ 0.30 s |
| B7 | ingest superlinear in corpus size | L1 3.50 s / L2 65.2 s (profiled, wall); L2: `resolve_pending` 55.0 s of 64.5 s, `symbols_named` **30.6 s** with **3,058,564 `SymbolRow` constructions** (≈ 80 rows/call) | resolve cost per ref O(1) in corpus size; L2 ingest wall down ≥ 5× |

## 1. Root causes (evidence, not guesswork)

**B1 — no apply lane.** `ops/ingest_cli.py` and `run_ingest_verb` stop at the
proposal report (`status="proposal"`); the pipeline writes only schema-v3
tables (`files`, `symbols`, `graph_edges`, `unresolved_refs`). `query` reads the
*projection*, which is replayed from `event_log` domain events and materialised
in `node_revision`. `ingest` appends **no domain events**, so the knowledge
watermark stays 0 and the router has no roots → `no_match`. `write-contract-v5`
§"Supersession" says the write lane applies directly; the running system has no
such step at all. Fix = implement the missing apply step as an explicit,
operator-visible ops lane (`ingest --apply` / `apply-ingest`), which is exactly
the "ingest → apply → query" path the task names.

**B2 — lock contention reported as staleness.** `service._snapshot()` calls
`_repair_projection_once()`; the projector's `hold_lock()` uses
`flock(LOCK_EX|LOCK_NB)` and raises `ProjectorBusy` when another *process*
holds it. `_repair_projection_once` swallows that (`pass`), the re-check still
sees no manifest, and the reader raises `STALE_PROJECTION` — a lie: the
projection is being built, not stale. Measured: 18/20 concurrent readers fail,
then 30/30 succeed once the builder finishes. Fix = wait (bounded) for the
competing builder to publish, and keep the typed error only when the projection
genuinely does not cover the watermark afterwards.

**B3 — cap has no voice.** `IngestionPipeline` records `guards_hit=["max_files"]`
and returns; the CLI prints one long line, exit 0, and JSON carries no
`truncated` flag. Fix = explicit signal (human line + JSON field + stderr
warning) and a default that no longer silently halves a 5k-file repo.

**B4 — eager import graph.** `cli.py` imports `http_api` (→ extraction →
projection → networkx → pypdf → uvicorn), `security`, `update_pipeline` and
`domain` (pydantic) at module import, for every command including `--version`.
Fix = lazy imports inside the command handlers; only the pure operation-name
tuples stay eager (moved to a dependency-free module and re-exported by
`http_api`, so no public name moves).

**B7 — O(N²) resolver lookups.** For each parked ref `_resolve_ref` calls
`store.symbols_named(name)`, which materialises **every** row with that name.
In the corpus every module defines `__init__`, `add`, `summary`, `json`, `os`,
so a single lookup returns N rows: 38,371 refs × ~80 rows = 3.06M row objects
at L2, and the ratio grows with N → the 175×/50× superlinearity. The resolver
only needs *the count* (to decide exact/ambiguous) and *the single row* when the
count is 1. Fix = count-first (`SELECT COUNT(*)`, index-only) + single-row
fetch; same for `symbols_with_tail`. Secondary: one transaction per
`record_resolution_attempt` (38k fsyncs at L2 = 14.6 s) → batched writes in one
transaction; `pending_remaining` full row load → `COUNT(*)`.

## 2. Build plan (TDD, per bug)

| Step | Test first (RED) | Then (GREEN) |
|---|---|---|
| B4 | `tests/test_cli_lazy_startup.py`: importing `know_your_context.cli` must not load `http_api`/`networkx`/`pypdf`/`uvicorn`/`security`/`update_pipeline`; `--version` subprocess < 0.6 s | lazy imports in `cli.py`; `operations.py` with the operation tuples, re-exported from `http_api` |
| B3 | `tests/test_ingest_max_files_guard.py`: 12-file corpus, `--max-files 5` → JSON `truncated: true`, `files_truncated: 7`, stderr `TRUNCATED`; no silent exit 0 | `IngestionReport.truncated/files_truncated`, CLI warning, default `--max-files 65536` |
| B7 | `tests/test_resolver_lookup_cost.py`: store with 300 same-named symbols + 50 refs → row materialisations ≤ 50 (was 15,000); `symbols_named` keeps its exact semantics | `symbols_named_count` + `symbols_with_tail_count`; resolver count-first; `record_resolution_attempts` batch; `pending_unresolved_count` |
| B2 | `tests/test_projection_race.py`: first reader sees `ProjectorBusy` while another builder publishes → query must succeed (wait), and only a genuinely uncovered projection raises the typed error | bounded wait loop in `service._snapshot`; honest message |
| B1 | `tests/test_ingest_apply_query.py`: ingest a 6-file corpus → `apply` → `query {"purpose": <class name>}` returns the symbol node; second apply is idempotent (same counts, `replayed>0`, watermark unchanged); determinism: two fresh stores → byte-identical apply report | `ingest/apply.py` (roots → file branches → symbol leaves, one outer transaction), `apply-ingest` command + `ingest --apply`, projection rebuild at the end |

Order: B4 → B3 → B7 → B2 → B1 (isolated/low-risk first, the apply lane last
because it depends on the others' green suite).

## 4. Results (SUB-175 retest, this host, 2026-11-16 ~21:00 UTC)

Two further root causes surfaced during the retest and were fixed in the same
TDD loop (RED observed first in each case):

- **B1a — relevance windowing.** After the apply lane worked, `query` still
  missed applied symbols on corpora whose graph exceeds `max_nodes=64`: the
  BFS window from the routed root excluded the exact lexical hit, and results
  fell back to id order (measured on a 328-node applied graph: `Cls17` ranked
  #1 in the index yet absent from the response). Spec 8 says traversal is
  *relevance*-bounded, so `service.query` now seeds the candidate set with the
  routed root's positive lexical hits (bounded by `max_nodes`) before
  ordering. Small graphs are byte-identical in behaviour; only the windowed
  case changes.
- **B1b — the projection build gate reused fragment bounds.** The pinned
  Graphify `validate_semantic_fragment` caps (10k nodes / 100k edges / 25 MB)
  gate one LLM fragment; a generation carries a whole corpus, so L2's 12,927
  applied nodes were rejected (`nodes has 12927 entries; max is 10000`).
  `resolve_fragment_bounds(store)` scales only the three aggregate counts
  from the store's code index (additive: the 10k fragment default stays as
  the hand-authored-knowledge allowance; ceilings keep it fail-closed;
  payload bound = `resolve_artifact_cap`, so gate and publish never
  disagree). Per-item checks (id charset, schema) stay pinned.

| # | Baseline (measured) | After fix (measured) | Evidence |
|---|---|---|---|
| B1 | `query` 0% hit on ingested corpus at every level; no apply lane | L1: `ingest --apply` → `query Cls17` → `ranked`, hit `Cls17`; L2 (12,919 symbols): `Cls17`, `fn_12_1`, `fn_0_0` all hit; `apply-ingest` standalone works; second apply idempotent (`replayed=1279`, same watermark, same `gen-ce0435fa…` hash — also deterministic across fresh stores) | `/tmp/b1l2.sqlite`, CLI transcripts |
| B2 | 18/40 ops `STALE_PROJECTION` under 20-agent first-reader race | 40/40 ok in the same `repro_stale2.py` | scratch/stress3 |
| B3 | 76/1100 files dropped silently, exit 0 | `TRUNCATED` in the summary line + stderr warning naming the cap and the fix; default `--max-files 65536` ingests all 12/12 in the repro | CLI transcript |
| B4 | `--version` 1.20–1.30 s | 0.17–0.21 s ×8 runs (entrypoint and `python -m`) | `/usr/bin/time` |
| B7 | L1→L3 3.5→802 s (175×/50×); L2 57–65 s | L2 14.1 s wall (4.0×); L3 69.9 s vs 801.8 s (11.5×); scaling L1→L2→L3 = 1.1→12.9→68.5 s ≈ linear | `/tmp/b7l3.sqlite` |

Gates: ruff check + format clean on `src/`+`tests/`; pyright 0 errors on all
touched files; full pytest suite result recorded on the board.

## 3. Gate (must all be green before reporting)

1. `pytest` full suite green (16k+ tests) — no regressions, no new failures.
2. Determinism: apply/ingest repeated runs byte-identical (`PYTHONHASHSEED`
   0/1/42 where applicable).
3. `ruff check` + `ruff format --check` + `pyright` clean on touched files.
4. Stress retest: `repro_stale2.py` (B2) → 0 false positives; `--version`
   ×5 (B4) ≤ 0.3 s; L2 ingest before/after (B7); `ingest --apply` → `query`
   hit > 0 at L1 and L2 (B1); max-files repro (B3).
