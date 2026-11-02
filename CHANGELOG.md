# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and versions
follow [Semantic Versioning](https://semver.org/).

This project develops in audited engineering waves (W1–W13); each wave has a
recovery document under `docs/recovery/`. Wave tags appear inline on the
entries they produced. Every cited commit hash is verifiable with
`git cat-file -e <hash>^{commit}`.

## [Unreleased]

### Added

- W8: contract v4 `submit_extraction` — external agents submit vision/LLM
  extractions bound to the source file's sha256, tagged `agent-extracted`,
  review-gated, never auto-promoted; plus the composite graph report
  (god nodes, surprising connections with structured explanations) `f35e7f0`.
- W9: capability scorecard harness (`bench/scorecard/`, 124 capabilities,
  proof-pinned) and the competitive benchmark harness (`bench/competitive/`,
  identical tasks across three systems) `d4067b3`.
- W9: brand rename Know Your Soul → Know Your Context (package, CLI, MCP,
  kernel rebuilt) `d4067b3`.
- W10: capability-based security — operator-issued `kyc1.` HMAC-SHA256
  tokens binding actor/verbs/scope/expiry, atomic revocation effective on
  the next verification, hash-chained audit log; `capability issue|revoke|list`
  CLI and `--capability-mode` `b1a16e8`.
- W10: tiger-style hardening of the superpower server — 22 static budgets
  asserted at start, fair-queue invariants, arbiter watermark/dedup bounds —
  plus deterministic simulation (DST-lite, seeded storm scenarios) `b1a16e8`.
- W11: scorecard v1.1.0 — 124 capabilities / 220 proofs, composite 428.6%
  against the two reference systems `d3afc18`.
- W12: verdict 7-level instrument (`bench/verdict/`, 70 machine-measured
  metrics per level across three repos, ledger with raw values and the exact
  measuring command per metric) `ea4f07d`.
- W12: depth push — 19 → 33 extraction languages (all fail-closed Python
  engine), synthesis passes 4 → 12, 21 resolver language helpers behind the
  `helper_tier_enabled` kill switch `ea4f07d`.
- W12: ops surface — `lifecycle` (hash-chained health ledger, retention,
  verified vacuum), `doctor` (5 probe categories), `migrate` (plan/apply,
  hashed portable bundles), `watchtower` (fleet aggregation, never writes
  during probes) `ea4f07d`.
- W13: this changelog plus professional documentation set
  (`docs/architecture.md`, `docs/verdict.md`, `docs/security.md`,
  `docs/api.md`) and docstring completion of the watcher / pipeline /
  resolver / serve surfaces.

### Fixed

- `stop_daemon` escalation ladder: graceful → forced → SIGKILL with stale
  artifact cleanup `4847be2`.
- macOS CI platform robustness: Darwin `statfs` probe-aware watcher, typed
  bind policy, CI-aware timing gates `bb34c97` `fa62dc7`.
- Web view: session cookie after first load (token no longer carried in
  every URL), tab screenshots from real runtime `370d4d5`.
- CI green: tests package init for serve imports, bandit `nosec`
  justifications, bare-style test imports unified `c7f1361` `bb22426`
  `fa12ef8`.

### Security

- pypdf pinned to 6.21.4, addressing CVE-2026-119203, CVE-2026-119204 and
  CVE-2026-119205 `143570a`.

## [0.3.0] - 2026-10-29

The superpower release: one detached multi-agent daemon per store.

### Added

- W7: superpower server — up to 50 concurrent agent sessions with typed
  backpressure (`SERVER_BUSY` + `retry_after_ms`), a read pool (default 8)
  on WAL snapshots vs a single writer with group commit, weighted-fair
  queueing lanes, per-agent token buckets, session leases, graceful drain,
  write arbitration with idempotency dedup `046f983`.
- W7: interactive web view (`webview`) — loopback-only, token in URL,
  anti-DNS-rebinding, seven tabs (Graph, Search, Metrics, Health, Analytics,
  Audit, Proposals) `046f983`.
- W7: extraction coverage raised to 19 tree-sitter languages plus synthesis
  passes over the resolved graph `046f983`.

## [0.2.0] - 2026-10-28

The universal knowledge graph release: code + document ingestion, the Rust
kernel, and retrieval v2.

### Added

- W1: universal knowledge graph — graph model v2, store schema v3, frozen
  contracts `50f8732`.
- W2: code ingestion — tree-sitter extractor over 12 languages, two-phase
  reference resolver (exact → name_tail → import_path, ambiguity never
  guesses), incremental `(size, mtime) → sha256` pre-filter with bounded
  cascade re-extraction `2712253`.
- W3: document ingestion — structure-aware markdown, PDF text layers and
  plain text; scanned PDFs flagged honestly as `no_text_layer` `d838f0f`.
- W4: retrieval v2 — FTS5 symbol lookup, call hierarchy, token-budgeted
  retrieval `8764e7b`.
- W5: optional Rust extraction kernel, byte-identical with the Python
  engine (verified by cross-engine tests); codebase benchmark harness
  (pinned psf/requests + chalk + tree-sitter-python) `d0f1755`.
- Parser dependencies pinned exactly (`tree-sitter-language-pack`, `pypdf`)
  with attribution notice `d006f70`.

### Fixed

- W5: relative from-import extraction crash `12f16db`.
- W6: audit closure wave — parity contract, v3 adapters, primary invariant,
  typed failure reasons; version bumped to 0.2.0 `3294883`.

## [0.1.0] - 2026-10-26

The initial public release (retrospectively reconstructed — the 0.1.0
version string shipped in `pyproject.toml` from the first commit; the first
git tag is v0.2.0): the auditable memory pillar with the ATM analytics
suite.

### Added

- Knowledge memory core: append-only hash-chained SQLite event store, the
  fourteen frozen knowledge operations, operator-gated
  propose/apply/rollback flow with byte-exact restore `9d8be5d`.
- ATM wave A: graph analytics (centrality, communities, components, cycles,
  paths), report v2, export orchestrator `6272fe6`.
- ATM waves B/C: deterministic DOT, Mermaid, CSV, GraphML and SVG exporters
  `65bb301`.
- ATM wave D: full integration — 30 artifacts wired, retention engine
  `a4c19d2`.
- ATM mega wave 2: algorithms suite, six additional export formats, query
  tooling `452b0bb`.
- Institutional graph benchmark harness (GAPBS / LDBC / Graphalytics style
  kernels with committed results) `47ab1d2`.
- Deterministic visual assets generated from real data `a7f331a`.
- CI and release workflows with security gates; Ubuntu + macOS test matrix
  `3c4bdfc` `275bb82`.
- Apache-2.0 license with graphify attribution notice `4a22905` `300d7a5`.

### Changed

- Brand rename to Know Your Soul (package, CLI, MCP, docker) `03e3909`.

### Fixed

- CDLP community detection quality via hop attenuation plus self-vote
  (institutional benchmark) `afbf8ad`.
- Reader-storm progress assertions made progress-based, not time-based
  `92248ec` `86b8d15`.
- Live-corpus pin test skips when the corpus is absent (CI runners)
  `e5bc724`.
- Bandit findings closed with inline, justified `nosec` annotations
  `21d7c9a` `52678b2`.

### Security

- Hardening waves (documented under `docs/recovery/wave2-*.md`): loopback
  CSRF ops containment, unix socket permissions 0600, MCP/HTTP auth, total
  body deadline + streaming body cap, internal envelope sanitization, API
  key via environment only, path-escape message sanitization.

## Development wave log

| Wave | Date (UTC) | Headline | Recovery docs |
|---|---|---|---|
| W1 | 2026-10-27 | Graph model v2 + schema v3 + contracts | `docs/recovery/w1-graph-model.md`, `w1-schema-v3.md`, `w1-contract-v3.md` |
| W2 | 2026-10-28 | Code ingestion: extractor, pipeline, resolver | `docs/recovery/w2-extractor.md`, `w2-pipeline.md`, `w2-resolver.md` |
| W3 | 2026-10-28 | Document ingestion (PDF/markdown/text) | `docs/recovery/w3-documents.md` |
| W4 | 2026-10-28 | Retrieval v2 + polling watcher | `docs/recovery/w4-retrieval.md`, `w4-watcher.md` |
| W5 | 2026-10-28 | Rust kernel + codebase bench + import fix | `docs/recovery/w5-rust-kernel.md`, `w5-codebase-bench.md`, `w5-fix-relative-import.md` |
| W6 | 2026-10-28 | Audit closure (parity, adapters, invariants) | `docs/recovery/w6-f1-parity.md`, `w6-f2f4.md` |
| W7 | 2026-10-29 | Superpower server, 19 languages, synthesis, web view | `docs/recovery/w7-superpower.md`, `w7-languages.md`, `w7-synthesis.md`, `w7-webview.md` |
| W8 | 2026-10-30 | Contract v4 extraction, graph report, token bench | `docs/recovery/w8-extraction-v4.md`, `w8-graph-report.md`, `w8-token-bench.md` |
| W9 | 2026-11-01 | Capability scorecard + competitive harness + rename | `docs/recovery/w9-scorecard.md`, `w9-competitive.md`, `rename-know-your-context.md` |
| W10 | 2026-11-01 | Capability security + tiger style + DST | `docs/recovery/w10-capability-security.md`, `w10-tiger-dst.md` |
| W11 | 2026-11-01 | Catalog v1.1.0 (124 capabilities) | `docs/recovery/w11-catalog.md` |
| W12 | 2026-11-02 | Verdict 7-level, depth push, ops tools | `docs/recovery/w12-verdict.md`, `w12-depth.md`, `w12-tools.md` |
| W13 | 2026-11-02 | Changelog + professional docs + docstrings | `docs/recovery/w13-docs.md` |

[Unreleased]: https://github.com/Sigit-AP/know-your-context/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Sigit-AP/know-your-context/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Sigit-AP/know-your-context/releases/tag/v0.2.0
[0.1.0]: https://github.com/Sigit-AP/know-your-context
