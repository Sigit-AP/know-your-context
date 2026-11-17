# SUB-186 — Ingest memory profile + bounded-walk fix

Scope: `src/know_your_context/ingest/pipeline.py` (+ `store.py` if a peak lived there) and
`tests/test_ingest_memory_bounds.py`. No commit. HEAD at start: `803866e`.

## Problem (inherited from SUB-178 head-to-head)

Ingest peak RSS was the largest remaining gap against the two reference products
(same harness, same corpora, `--max-files 65536 --max-*-20000000`):

| level | corpus | kyc RSS | codegraph RSS | graphify RSS |
|---|---|---|---|---|
| L3 | 5 000 synthetic files | **793.7 MB** | 549.7 MB | 441.7 MB |
| L4 | 5 137 real files / 2.12M LOC | **1 936.8 MB** | 1 631.8 MB | 860.4 MB |

## Method

`scratch/sub186/profile_phases.py` monkeypatches the pipeline phase boundaries
and snapshots, at each boundary, `tracemalloc` current/peak and RSS
(`/proc/self/status` + `ru_maxrss`). Phase names:

* `walk` — `_walk`
* `A-preflight` — per-file gates + tracked fast path (inside `_run`)
* `B-analyze` — read/hash/decode/extract (`_analyze_all`/`_analyze_stream`)
* `C-commit` — digest + store writes (`_commit_analyzed`, up to `_delete_missing`)
* `resolve` — `resolve_pending` (SUB-88 resolver)
* `sugg` — `_primary_suggestions` (primary-split proposals)

`scratch/sub186/ab_ingest.py` is the RSS/wall A/B harness: identical argv to the
SUB-172/178 stress harness, peak RSS sampled from the process tree every 100 ms
(psutil), BEFORE runs shadow the editable install with a pristine HEAD worktree
via `PYTHONPATH` (verified: `know_your_context.__file__` inside the worktree).

## Findings — where the bytes actually are

### 1. Phase B/C retention (fixed here)

`_analyze_all` accumulated an `_Analyzed` **for every file of the walk** before
Phase C ran, and each `_Analyzed` carried the file's raw `content` bytes. The
whole corpus (bytes + extraction results) was therefore resident at the end of
Phase B, and Phase C added normalized row dicts + pydantic `GraphNode`
proposals on top.

Measured with tracemalloc (`L3s` = first 2 000 files of L3, HEAD):

| phase boundary | tm_cur | tm_peak |
|---|---|---|
| B-analyze:end | 52.6 MB | 52.7 MB |
| C-commit:end | 117.0 MB | 117.2 MB |
| resolve:end | 117.4 MB | **243.4 MB** |

Unit-scale proof (300 files × 64 KiB = 19.7 MB corpus, workers forced to 1):
HEAD peaked at **24.8 MiB traced > the whole corpus**; streamed walk peaks at
~3 MiB (the residual is the primary-split proposal list, O(nodes) by design).

### 2. Resolve pass (NOT in this scope — reported for follow-up)

`resolve/resolver.py:292` pulls the **entire pending queue**
(`pending_unresolved_refs(limit=None)`) and then builds `decisions`,
`edge_payloads` and the read-back `recorded_rows` for all of them. At L3s that
is 77 706 refs and +126 MB traced (243 MB peak) on top of whatever Phase C left
alive. This is the single largest peak contributor at L3/L4 and belongs to
`resolve/resolver.py` (chunked pass with identical decision order), not to the
ingest pipeline.

### 3. Deliberate RSS (not waste)

The single `HardwarePlan` grants SQLite `cache_size = 288 MiB` and
`mmap_size = 1 GiB` on this host (`hardware_plan(...)`: 2 workers, batch 32).
Those pages are counted in RSS and are the user's standing "no RAM
minimisation" stance — they are not touched here.

## Fix (TDD)

RED first: `tests/test_ingest_memory_bounds.py` on HEAD —
`assert 24822143 < (19661100 // 2)` (peak = whole corpus).

GREEN after the change below.

1. `_Analyzed` no longer carries `content`; it carries `generated_hint`, the one
   derived fact Phase C needed from the bytes (`is_generated_hint` head-marker
   check), computed while the bytes are in hand in `_analyze_one`.
2. `_analyze_all` (dict-returning, all files) became `_analyze_stream`
   (generator, canonical order, window = `workers * 4`): Phase C pulls one
   outcome per file and commits it before the next one is produced, so content
   and extraction results are released per file.
3. Phase C asserts the stream index equals the walk index (order invariant).

Byte-identical output is pinned by
`test_streamed_ingest_output_is_identical_to_the_parallel_path`
(report + `canonical_ingestion_dump`, serial vs parallel workers).

## Measurements

- **L3 full ingest** (5 000 files)
  - before‑fix: wall = 313.69 s, peak RSS = 818.3 MiB (CLI A/B)
  - after‑fix:  wall = 176.35 s, peak RSS = 737.7 MiB (CLI A/B)
- **L3‑subset (L3s, 2 000 files) phase profiling**
  - before‑fix (tracemalloc):
    - B‑analyze end tm ≈ 52.6 MiB, C‑commit end tm ≈ 117 MiB, resolve peak tm ≈ 243 MiB
    - RSS peaks: walk ≈ 70 MiB, B‑end ≈ 200 MiB, C‑end ≈ 345 MiB, resolve ≈ 692 MiB
  - after‑fix (tracemalloc):
    - B‑analyze end tm ≈ 0 MiB (generator created), C‑commit end tm ≈ 117 MiB, resolve peak tm ≈ 243 MiB
    - RSS peaks: walk ≈ 70 MiB, C‑end ≈ 345 MiB, resolve ≈ 578 MiB
  - after‑fix (no‑tm, full‑corpus): see `profile_L3-before-notm.json` and `profile_L3-after-notm.json`
- **Hash‑identical output** – verified by the test suite (`test_streamed_ingest_output_is_identical_to_the_parallel_path`).
- **L4 ingest** – only the baseline (before‑fix) measurement performed (wall ≈ 775 s, RSS ≈ 1.94 GiB); after‑fix run not scheduled (out of L3 scope).