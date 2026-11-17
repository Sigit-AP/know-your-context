# SUB-190 — codebase bench baseline refresh (2026-11-17)

Gate `tests/test_codebase_bench.py::TestRegressionGate::test_baseline_gate` failed with 9
drift items. All 9 were classified **SAH (resolution improved)** and the baseline was
refreshed with the two-witness procedure. No regression was found; nothing was reverted.

## Verdict per item (9/9 SAH — resolution improved)

All drifts move in the resolution-improvement direction: `refs_resolved` ↑,
`refs_failed` ↓, `edges` ↑ (resolving a previously-failed ref mints a graph edge).
There is no item where resolution got worse (which would show as `refs_resolved` ↓ /
`refs_failed` ↑ / `edges` ↓).

| # | target | key | old (baseline) | new | Δ | verdict |
|---|--------|-----|----------------|-----|---|---------|
| 1 | requests | edges | 2832 | 2922 | +90 | SAH |
| 2 | requests | refs_resolved | 1163 | 1264 | +101 | SAH |
| 3 | requests | refs_failed | 1724 | 1623 | −101 | SAH |
| 4 | chalk | edges | 321 | 322 | +1 | SAH |
| 5 | chalk | refs_resolved | 90 | 92 | +2 | SAH |
| 6 | chalk | refs_failed | 532 | 530 | −2 | SAH |
| 7 | tree-sitter-python | edges | 1561 | 1724 | +163 | SAH |
| 8 | tree-sitter-python | refs_resolved | 296 | 858 | +562 | SAH |
| 9 | tree-sitter-python | refs_failed | 2382 | 1820 | −562 | SAH |

Unchanged (control): fixture 10 nodes / 18 edges / 8-of-10 resolved — no drift, and
`nodes`, `refs_total`, `stats.*`, `failed_files` are identical for every target.

## Evidence (why this is the resolver fix, not a regression or data change)

- Baseline was pinned at commit `ea4f07d` (2026-11-02).
- The resolver fix landed **after** that pin: `803866e` "fix: union-on-ambiguous +
  sql-backend ascii-safe + lazy-startup seams + persisted verdict cache (SUB-178
  partial)" (2026-11-17), +173 lines in `src/know_your_context/resolve/resolver.py`
  (+337 lines of new tests in `tests/test_resolve_scope.py`).
- The resolver's own docstring states the new ambiguity tier "can only convert
  failures into edges, never retarget a ref that already resolved" — previously the
  ref kept failing with `ambiguous_exact:N`; now a union-on-ambiguous pass resolves
  it. That is exactly the observed signature: `refs_failed` −`refs_resolved` and
  `edges` +`refs_resolved` per target.
- Dataset provenance unchanged: `TestDatasetIntegrity::test_archives_match_manifest`
  passes (vendored tarballs still match `manifest.json` sha256 pins), so the drift is
  not from corpus change.

## Two-witness procedure

`bench/codebase/run.py` measured twice (repeats=3, the gate's workload), plus the
`--update-baseline` run as a third witness:

1. Witness A — `results/ledger-20261117T124709Z.json` (measure via driver)
2. Witness B — `results/ledger-20261117T124734Z.json`
3. Refresh run — `results/ledger-20261117T124933Z.json` + new `baseline.json`

Comparison: every **strict-gate field** (stats, failed_files, nodes, edges,
refs_total/resolved/pending/failed) is byte-identical across A and B, and the
refreshed baseline matches witness A field-for-field. Each run's internal
determinism check reports `byte_identical: True` (ingestion dump sha256 run_a ==
run_b). Note: the canonical-dump sha256 itself differs *across processes* because
the dump includes `files.mtime` and each process extracts a fresh archive with
fresh mtimes — that hash is not part of the strict gate and the harness's own
determinism definition (two ingestions within a run) holds.

## Proof after refresh

```
$ .venv/bin/pytest tests/test_codebase_bench.py -q
17 passed in 17.71s
```

`bench/codebase/baseline.json` regenerated (`created: 20261117T124933Z`, version 1,
same schema). Per scope: only `bench/codebase/baseline.json` + this doc changed;
`src/` untouched; no commit made.