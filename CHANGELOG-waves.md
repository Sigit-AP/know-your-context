# Changelog — development waves W14–W23 and the task2 remediation

This is the detailed per-wave changelog for the audited engineering waves that
followed v0.3.0 (see [CHANGELOG.md](CHANGELOG.md) for the release-level history
and the format contract; every wave has a recovery document under
`docs/recovery/`). Every cited commit is verifiable with
`git cat-file -e <hash>^{commit}`.

## W14 — innovations (experience re-rank, provenance, debt queue) — 2026-11-02

### Added

- Experience-driven re-rank (`retrieval_v2.experience`, I1): retrieval learns
  from recorded outcomes — clamped multiplier [0.5, 1.5], 12-digit quantized
  Decimal, hash-chained sidecar ledger, fail-open byte-identical when
  disabled `1d94b0a`.
- Verifiable 5-link provenance chain (`provenance.py`, I2): source sha256 →
  payload hash → capability actor → submission event → node event, machine
  re-derivable read-only without a schema change `1d94b0a`.
- Knowledge-debt queue (`debt.py`, I4): severity×impact scored, proposal
  drafts per item, hash-chained burn-down ledger `1d94b0a`.

## W15 — fixtures, fuzzing, API surface, hardware-aware layer — 2026-11-02

### Added

- Differential fuzzing + query depth (I3), pinned fixture corpus, public API
  surface with env configuration `1696093`.
- Hardware-aware architecture: one `HardwarePlan` (probe → tuning →
  autoscaler → GPU gate) consumed by the store, the daemon and ops; GPU
  offload decides from a measured formula, never a guess `1696093`.

## W16 — model-based testing + Power-of-Ten gates — 2026-11-03

### Added

- Model-based testing harness (`serve/simulation.py`): deterministic
  virtual-time replay of concurrent agent traffic `6a72f83`.
- Power-of-Ten static gate (`tools/power_of_ten.py`) with justified ratchet:
  violations may shrink, never grow `6a72f83`.

### Changed

- Storm-gate performance hardening: 87 s → 35 s under identical assertions
  `6a72f83`.

## W17 — webview overhaul (activity log, leases, 19 tabs) — 2026-11-04

### Added

- Agent activity log, node leases (schema v5, additive), 19-tab webview
  `b67b52f`.
- Contention-aware storm calibration: host calibration factor derived from a
  mini-storm, never by loosening the gate `b67b52f`.

## W18–W20 — visual waves — 2026-11-05 → 2026-11-07

### Changed

- W18: light professional theme, graph fidelity (per-node gradient chips, HUD
  stats), sidebar icon system audited 7/7 principles `e48e719`.
- W19: visual-first every tab, 19 → 14 tab merge, patterns chapter `dbdc77a`.
- W20: complex visuals — dot-heatmap with per-cell tooltips, interactive
  multi-line metrics, proportion bars, funnels, heatmap tables; 9 real bugs
  found by visual audit and pinned `180dd0c`.

## Disk-mode v5 — writes commit directly with synchronous auto-review — 2026-11-07

### Changed

- Disk-mode is the official write behavior: every write commits directly with
  a deterministic synchronous auto-review (round-trip fingerprint/version +
  prompt-injection/secret/active-markup scan over the raw and match views);
  rejection is append-only compensation with a typed error. Contract
  `docs/contracts/write-contract-v5.md` supersedes the proposal-only write
  paths of v3/v4; `KNOW_YOUR_CONTEXT_MANUAL_GATES=1` restores the historical
  behavior `b786c42`, `5952097`.
- Webview backend parity with graphify's HTTP layer (header auth, bind
  warnings, graceful SIGTERM) plus measured isolation/overload proofs; a
  listen-backlog fix took flood p99 from 1023.7 ms to 43.4 ms `b786c42`.

## task2 wave 1 — measured hot paths — 2026-11-08

### Changed

- FTS freshness event-driven (`symbols_watch` trigger seq): cold hit p50 at
  1.85M symbols 3723 → 71.4 ms (52×); `symbols.name_tail` + index: tail miss
  1092 → 0.7 ms (1560×); module_file 47.6 → 1.6–2.8 ms; ingest 1× corpus
  111.97 → 12.18 s; name-match N+1 batched 199.94 → 3.663 s (54.6×) `634d568`.
- Rust kernel: real-world divergence 5/36 → 0/36 byte-identical, `spec.engine`
  routing honored (0 kernel attempts for python-engine languages), GIL
  released for multi-core extraction `634d568`.

## task2 wave 2 — writer supervision + incremental publish — 2026-11-08

### Fixed

- The writer thread is a supervised worker like every other lane (heartbeat,
  budgeted restart, terminal DEAD surfaced): a writer exception can no longer
  leave a half-alive daemon `edbcdb0`.
- Projection publish is incremental (≈4× fewer rebuild rows per publish);
  capability audit append is O(1) (was a full-log read per append) `edbcdb0`.

## task2 final — framer cap — 2026-11-08

### Fixed

- `FrameReader` off-by-one at the frame cap: content of exactly
  `max_frame_bytes` is accepted (the newline delimiter is not charged to the
  payload); the 1 MiB boundary is pinned by test `b58bfd2`.