# Operations — know-your-context

This document covers day-2 operation of the store, projection and
API surfaces: observability, health/readiness, backup/restore, projection
recovery, and the security posture. The HTTP/Unix-socket server
(`http_api.py`) builds on the pure policies documented here.

## Disk-mode writes and auto-review (SUB-151)

Default mode: **the store is a disk**. Every write (`log_event`,
`record_outcome`, `propose_improvement`/`apply_improvement`,
`submit_extraction`, `ingest`) lands immediately — no human approval queue
anywhere. The mandatory replacement is a deterministic post-write
auto-review (`src/know_your_context/autoreview.py`), running **inside the
write's own audit boundary** (`store.commit(post_write=…)`), so the
one-final-audit-per-invocation invariant is unchanged and the verdict rides
the write's audit as `result_references.auto_review`:

1. **Round-trip** — the written node/evidence/file rows are read back and
   verified against the intent (existence, version, content fingerprint).
2. **Badness scan** — deterministic patterns (no model): prompt-injection
   phrasing (`ignore/disregard/… previous instructions`, persona hijack,
   prompt exfiltration, chat-template markers, jailbreak keywords), secret
   shapes (the pinned `SECRET_PATTERNS` over the raw text and the security
   match view), and active markup (`<script>`, `javascript:`, `on*=`).

A reject compensates append-only before the failure audit closes: a
pre-existing node is restored to its prior revision (a new `rollback`
revision); a node the rejected write created is retired and tombstoned; a
bad extraction submission is never applied (queue row `rejected` with the
typed reason codes); a bad ingested file's rows are removed by the bounded
cascade. Typed errors: `AUTO_REVIEW_REJECTED` (service, HTTP 422),
`EXTRACTION_AUTO_REJECTED` (422), `INGEST_AUTO_REVIEW_REJECTED`. Writing
quality is the writer's business — the reviewer only verifies the write
landed as intended and carries nothing dangerous.

**Legacy mode**: `KNOW_YOUR_CONTEXT_MANUAL_GATES=1` (strict boolean parse;
an invalid value fails closed at configuration time) restores the frozen
contract-v4 behavior byte-for-byte: extraction submissions queue as
`pending_review` for the operator `extraction approve|reject` surface, and
high-impact improvement applies require an operator `ApprovalRecord`. The
relearn gate (frozen `docs/contracts/relearn-contract.md`) is
operator-gated in both modes.

The webview **Proposals** tab leads with the auto-review history
(pass/reject badges, typed reason codes, compensation links), read from the
audit trail — a journal, never an approval queue.

## Security posture

Least privilege by default (`src/know_your_context/security.py`):

- **Bind policy** — `assert_bind_allowed(bind, api_key=...)`: Unix sockets
  (`unix:/path.sock`) and loopback binds (`127.0.0.1`, `::1`, `localhost`)
  need no key. Any wider bind (`0.0.0.0`, LAN addresses) without a configured
  API key raises `BindPolicyError` at startup. Non-loopback deployments belong
  behind TLS / a reverse proxy.
- **Request authorization** — `authorize_request(RequestView, api_key=..., host_allowlist=...)`:
  the Host allowlist is enforced on *every* request, loopback included, and an
  empty allowlist fails closed. Beyond loopback a configured API key is
  mandatory (compared via `hmac.compare_digest` over SHA-256 digests). The
  `make_host_allowlist` / `make_request_authorizer` factories are the server
  middleware seam.
- **Payload sanitization** — `sanitize_text` strips every Unicode format
  character (bidi controls, zero-width characters, BOM) and every control
  character except tab/LF/CR, and raises `OversizedPayloadError` beyond the
  length cap. `ensure_payload_size` / `ensure_text_size` bound request sizes
  (default 1 MiB).
- **Fixed-root path containment** — `resolve_within_root(root, candidate)`
  resolves symlinks of every existing component and fails closed
  (`PathEscapeError`) on `..` traversal, absolute paths outside the root,
  cross-tree reads, and symlink escapes. This closes the deferred Task 3
  symlink finding; the MCP/API file surfaces must resolve every caller path
  through it.
- **Redaction** — `redact_text` / `redact_value` remove known secret shapes
  (API keys, AWS keys, GitHub/Slack tokens, private-key blocks, bearer/JWT
  tokens, credential assignments) and email PII. Apply on the write surface
  before persistence and on every read surface that leaves the process; the
  structured logger redacts all fields automatically.

## Observability

`src/know_your_context/observability.py`:

- `MetricsRegistry` — thread-safe counters/gauges, deterministic
  `snapshot()` and Prometheus-style `render_text()`.
  `collect_store_metrics(store, registry)` publishes store gauges
  (`event_log_events`, `knowledge_high_watermark`, `pending_journals`, ...).
- `StructuredLogger` — JSON-lines records with `ts`, `level`, `event`,
  `component` and caller fields; every field is redacted before it reaches
  the sink. Carry `correlation_id=<invocation_id>` (plus `audit_event_id` /
  `audit_event_seq` from the `WriteReceipt`) so any log line joins to exactly
  one final audit event.

## Health and readiness

- `check_health(store)` — liveness: `SELECT 1`, full hash-chain verification
  (`EventStore.verify_chain`), and applied migrations equal to the migrations
  on disk. Any failed check makes the report not-ok; probes never raise.
- `check_readiness(store, projector=..., stale_after_seconds=300)` — traffic
  gate: health **plus** no stale incomplete journals (heartbeat older than
  the threshold) **plus**, when a projector is attached, an active generation
  whose knowledge watermark equals the store's.

Run readiness before accepting traffic; run
`AuditLog.recover_incomplete(projection_repair=projector.repair_if_stale)` (or
`recover_projection(..., audit_log=...)`) at startup to close crashed
invocations exactly once.

## Backup and restore

`src/know_your_context/backup.py`:

- `backup_store(store, dest)` — SQLite online backup into a same-directory
  temp file, verified (`PRAGMA quick_check`, event count, chain-head anchor),
  then `os.replace` + fsync of file and directory. A crash never leaves a
  half-written backup at the destination. Returns a `BackupReceipt` with the
  event count, chain head, size and SHA-256.
- `inspect_backup(path)` — read-only integrity report; raises `BackupError`
  for a missing file.
- `restore_store(backup, dest, overwrite=False)` — verified, atomic restore;
  refuses to overwrite an existing non-empty database unless `overwrite=True`.
  Equivalence is proven by `test_backup_restore_equivalence` (row-for-row
  `event_log` equality and `verify_chain` on both copies).

Recommended cadence: backup before every relearn/apply (spec 13.3) and on a
schedule; keep receipts with the backup so rollback can assert the chain head.

## Projection recovery

- `diagnose_projection(store, projector)` classifies the active generation:
  `CURRENT`, `STALE` (watermark/pointer disagreement), `TORN` (missing
  artifacts or dangling `CURRENT`), `CORRUPT` (artifact bytes disagree with
  the manifest hashes), `MISSING` (no active generation).
- `recover_projection(store, projector, audit_log=None)` drives
  `GraphProjector.repair_if_stale` (a no-op when current), optionally runs
  `AuditLog.recover_incomplete` with the repair hook, and raises
  `BackupError` unless the projection is `CURRENT` afterwards. Because
  generations are content-addressed and deterministic, repair re-derives the
  same directory and restores torn/corrupted artifacts byte-identically.

## Source watch (spec 13.5)

`know-your-context serve --watch PATH` runs the doc-change watcher for the
server's lifespan (arch review F-1; `update_pipeline.SourceWatchLoop` +
`source_watcher.SourceWatcher`):

- **Detect-only, always gated.** The watcher polls the corpus root
  (`--watch-interval`, default 30s) for content-hash changes. A detected
  change rides the same gated path as `ops/relearn_scan`: the deterministic
  `RelearnOffer` is recorded and the gate becomes `RELEARN_PENDING` —
  audited as `knowledge.relearn_offer` with actor `source-watcher`. The
  watcher **never** applies, never selects a mode, never flips the
  operator-approved baseline; `relearn_preview` / `relearn_apply` stay
  mandatory. The first poll after startup records the `SOUL_CORPUS`
  detection baseline and emits no offer (the initial import stays
  operator-explicit).
- **Cooldown.** After an offer is emitted the watcher pauses
  (`--watch-cooldown`, default 300s). Edits during the pause accumulate and
  arrive as one batched offer — a save storm cannot mint a stream of offers,
  and no change is ever absorbed silently.
- **State file.** The watcher's snapshot defaults to `source-watch.json`
  alongside the store file (override with `--watch-state`). It must live
  *outside* the watched root — startup fails closed otherwise, because a
  state file inside would perturb the corpus fingerprint and mint spurious
  offers. The snapshot is anchored to the resolved root; a snapshot written
  for another root fails closed (`WatcherStateError`).
- **Failure isolation.** Watcher errors (corrupt/foreign state file,
  unreadable corpus) and gate-callback failures are logged
  (`know_your_context.update_pipeline`) and backed off exponentially — they
  never take the API down. Operator repair: remove or fix the state file;
  detection resumes on the next polls. The watch thread
  (`soul-source-watch`) starts and stops with the server.
- **Restart.** An unchanged corpus after a restart re-emits nothing (the
  persisted snapshot and the registry baseline agree); changes made while
  the server was down surface as an offer on the first poll.

## Export and analytics artifacts (ATM)

Two operational commands turn the **active generation** into derived
artifacts. Both are read-only against the store (plain `SELECT`s; the event
count is pinned unchanged by
`tests/test_export_cli.py::test_export_and_analytics_do_not_touch_the_store`),
never trigger a rebuild, and are OPS surface only — they are not knowledge
operations and are never dispatched over MCP or HTTP.

```bash
know-your-context --store soul.sqlite3 --output-root graph export --formats all
know-your-context --store bench.sqlite3 --output-root graph analytics --modules all
```

- `export` produces the **format** artifacts (22): `exports/node-index.json`,
  `exports/graph.dot` (Graphviz), `exports/graph.mmd` (Mermaid),
  `exports/nodes.csv` + `exports/edges.csv` (RFC 4180),
  `exports/graph.graphml`, `exports/graph.svg`, `exports/graph.html`
  (self-contained viewer, positions from the deterministic layout engine),
  the book `book/index.md` + `book/P1.md`..`book/P7.md` (one page per
  primary domain, fixed page set — empty domains get an explicit empty page),
  and the mega-wave 2 formats: `exports/graph.gexf` (GEXF 1.2draft, for
  Gephi), `exports/cytoscape.json` (Cytoscape.js elements),
  `exports/d3-force.json` (D3-force node-link with integer indices),
  `exports/adjacency.csv` (adjacency matrix) + `exports/edges.txt`
  (tab-separated edge list), and `exports/graph.tgf` (Trivial Graph Format).
- `analytics` produces the **analytics** artifacts (26):
  `analytics/domain-rollup.json`, `analytics/centrality.json`,
  `analytics/components.json`, `analytics/cycles.json`,
  `analytics/communities.json`, `analytics/bridges.json`,
  `analytics/review-signals.json`, `analytics/health.json`,
  `analytics/report-v2.json` + `analytics/graph-report-v2.md`,
  `analytics/generation-diff.json` + `analytics/DIFF.md`,
  `analytics/timeline.json` + `analytics/TIMELINE.md`, and the mega-wave 2
  suite: `analytics/graph-summary.json` (counts, degree histogram, per-domain
  breakdown), `analytics/hits.json` (HITS hubs/authorities),
  `analytics/eigenvector.json`, `analytics/katz.json` (truncated Katz
  prestige), `analytics/clustering.json` (local coefficient + transitivity),
  `analytics/louvain.json` (deterministic Louvain communities),
  `analytics/reachability.json` (all-pairs reachability summary),
  `analytics/motifs.json` (triangles + 16-class triad census),
  `analytics/assortativity.json` (degree assortativity),
  `analytics/paths.json` (a deterministic Yen k-shortest-paths probe between
  the lexicographically smallest and largest node ids),
  `analytics/aggregates.json` (per-domain P/M/E min/max/mean + frozen 10-bin
  histograms), and `analytics/near-duplicates.json` (proposal-only
  near-duplicate candidates — `auto_merge: false` is data, not policy text).
- `--formats` / `--modules` accept full artifact names, short aliases (`dot`,
  `mermaid`, `tabular`, `graphml`, `svg`, `html`, `book`, `gexf`,
  `cytoscape`, `d3force`, `adjacency`, `tgf`, `centrality`, `components`,
  `cycles`, `communities`, `bridges`, `review-signals`, `health`,
  `report-v2`, `generation-diff`, `timeline`, `graph-summary`, `hits`,
  `eigenvector`, `katz`, `clustering`, `louvain`, `reachability`, `motifs`,
  `assortativity`, `paths`, `aggregates`, `near-duplicates`), or `all`
  (every artifact of the command's kind). Unknown selections are usage errors
  (exit 2); an example with a real artifact name:
  `know-your-context ... export --formats exports/graph.dot`.
- Every artifact is produced fail-closed through the export registry
  (`exporters/orchestrator.py` + `exporters/builtin.py`): an explicit
  per-artifact byte cap, then a format-specific round-trip validator that
  parses the produced bytes back before anything is written. Writes are
  atomic and idempotent (unchanged bytes are not rewritten; the summary marks
  `written: false`).
- The summary on stdout is canonical JSON: `ok`, `generation`,
  `registry_fingerprint`, `output_dir`, and per-artifact
  `name/sha256/size/version/kind/written`.
- `--out DIR` redirects output (default `<output-root>/exports`); the path is
  contained by `resolve_within_root` and escapes fail closed (exit 2).
- **generation-diff** compares the active generation with the newest other
  on-disk generation (highest knowledge watermark, content-hash tie-break).
  When only one generation exists — or retention collected the older ones —
  the artifact reports `no_previous_generation` as a valid result, not an
  error.
- **timeline** replays the store's domain event log (`event_log WHERE
  kind='domain' ORDER BY event_seq`, read-only) into per-node/per-domain P/M/E
  learning series with the frozen scoring contract.

## Ops tooling: config, metrics, diagnose (mega-wave 2)

Three more OPS-only commands (`src/know_your_context/ops/{config,metrics,diagnose}.py`,
wired in `cli.py`). All are strictly read-only against the store — the store
file is byte-identical before and after (pinned by
`tests/test_wave2_integration.py::test_cli_ops_commands_never_touch_the_store`).

```bash
know-your-context config describe               # markdown: resolved value + provenance per key
know-your-context config effective              # canonical JSON: flat key=value (secrets redacted)
know-your-context config dump                   # canonical JSON: {key: {value, source}}
know-your-context config --config ops.toml describe   # extra TOML layer
know-your-context metrics                       # canonical JSON operational metrics
know-your-context metrics --format markdown     # human report
know-your-context diagnose                      # self-check; exit 0 = no errors, 1 = any error
know-your-context diagnose --format markdown --no-permissions
```

- **Configuration** (`ops/config.py`) resolves 20 declared keys through four
  layers: defaults < TOML file (`--config`) < environment (`KNOW_YOUR_CONTEXT_*`)
  < CLI flags. Every key is typed and fail-closed (`ConfigError` with a code:
  `UNKNOWN_KEY`, `TYPE_MISMATCH`, `OUT_OF_RANGE`, ...); a bad file is a usage
  error (exit 2). Secrets (`serve.api_key`) render as `<redacted>` on every
  surface.
- **Metrics** (`ops/metrics.py`) collects store counts (events, journals
  complete/in-progress/stale, watermarks), per-operation latency summaries
  from the audit journal (integer p50/p95, nearest-rank), publish-capacity
  utilization, active-projection sizes, and a weighted health score. The JSON
  is canonical and float-free.
- **Diagnose** (`ops/diagnose.py`) runs 11 fail-closed checks: store select,
  hash chain, migrations drift, stale journals, projection pointer, manifest
  load, manifest-vs-disk (re-hashes every artifact), watermark consistency,
  read-model validation, publish capacity, and file permissions
  (`--no-permissions` skips the last). A crashed check becomes a
  `CHECK_CRASHED` finding instead of an exception. The process exit code is
  the report's: 1 iff any `error`-severity finding exists — so CI can gate on
  `know-your-context diagnose` directly.

## Generation retention (ATM wave D)

`src/know_your_context/ops/retention.py` (SUB-69) implements deterministic
generation GC: `RetentionPolicy` (keep N newest + pinned), `plan_retention`
(pure plan), `run_retention` (delete only non-CURRENT, non-pinned generations
not referenced by any backup, with an audit event per deletion). Retention
never touches the store (the SSOT); it only removes derived generation
directories. Because `export`/`analytics` treat a missing previous
generation as an honest `no_previous_generation` result, retention and the
diff artifact compose safely.

## Lifecycle, doctor, migrate, watchtower (W12)

Four more OPS-only commands (`src/know_your_context/ops/{lifecycle,doctor,migrate,watchtower}.py`,
wired in `cli.py`). None of them joins the frozen knowledge/OPS operations
(pinned by `tests/test_cli_w12_tools.py`).

```bash
know-your-context lifecycle record                    # append one hash-chained health snapshot
know-your-context lifecycle trend                     # direction/streaks/flaky components (integer-only)
know-your-context lifecycle prune --keep 50           # retain the newest 50 snapshots (re-chained, atomic)
know-your-context lifecycle prune --keep 50 --dry-run # same plan, no rewrite
know-your-context lifecycle fragmentation             # page/freelist stats + vacuum recommendation
know-your-context lifecycle vacuum                    # safe compaction (staging + verify + atomic swap)
know-your-context doctor                              # deep diagnostic; exit 1 on any error finding
know-your-context doctor --format markdown --full-integrity
know-your-context migrate plan                        # pending schema steps for --store
know-your-context migrate apply                       # apply pending migrations, verify the chain
know-your-context migrate export --dest DIR           # portable hashed bundle of --store
know-your-context migrate verify --bundle DIR         # manifest + sha256 + payload checks; exit 1 on failure
know-your-context migrate import --bundle DIR [--dest PATH] [--overwrite]
know-your-context watchtower a.sqlite3 b.sqlite3      # multi-store status; exit 1 if any store is down/corrupt
know-your-context watchtower --target prod=/srv/prod.sqlite3 --format markdown
```

- **Lifecycle** (`ops/lifecycle.py`) tracks health history in an append-only,
  hash-chained JSONL ledger (default `lifecycle-health.jsonl` next to the
  store). A ledger that fails verification is never extended — recording is
  fail-closed. `trend` is a pure, integer-only function of the verified
  history (direction, drop streaks, flaky components, regressions). The
  declarative retention engine (`RetentionRule`: `max_age_seconds` /
  `max_entries`, protected items never evicted) also drives
  `lifecycle prune`, which rewrites the ledger atomically (temp file + fsync
  + `os.replace` + dir fsync) and re-chains the retained suffix, reporting
  both head hashes. `vacuum` compacts the store file without endangering the
  live handle: `VACUUM INTO` a same-directory staging file, verified-copy
  inspection (quick_check + event-count/chain-head anchor), then an atomic
  swap through the F-1 coordinated restore discipline, with a final
  hash-chain verification on the reopened handle. `:memory:` stores are
  refused; every SQLite failure is wrapped in `LifecycleError`.
- **Doctor** (`ops/doctor.py`) goes beyond `diagnose` (store/projection
  state) into the environment the store runs in: Python/SQLite version
  floors, runtime dependency presence (all nine declared distributions),
  store-directory writability probe (create/remove a 0600 file), free-space
  headroom, file permissions and process umask, `quick_check` (+ optional
  full `integrity_check` and `foreign_key_check`), hash chain, migration
  drift, read-latency probes (integer p50/p95), fragmentation and WAL
  growth, stale journals. Every finding carries structured `suggestions` —
  the markdown report doubles as a runbook. Runtime facts are injectable for
  deterministic tests; every check is fail-closed (`CHECK_CRASHED`).
- **Migrate** (`ops/migrate.py`) inspects a store's version strictly
  read-only (`mode=ro&immutable=1`, no WAL sidecars), classifying it as
  `current` / `upgradeable` / `newer-than-build` / `not-a-store` / `absent`
  against the full version ladder (file migrations plus the in-code v3/v4).
  A store newer than the build is refused *before* it is opened. Portable
  bundles are directories holding a verified backup copy plus a canonical
  `bundle-manifest.json` (sha256, size, event count, chain head, applied
  migrations, format version). `verify` re-checks every hash *and* runs the
  payload through the backup inspector, so re-signing a corrupt payload with
  an edited manifest still fails. `import` verifies first (fail-closed) and
  installs through `restore_store`.
- **Watchtower** (`ops/watchtower.py`) aggregates a fleet into one
  deterministic, name-sorted report: reachability, health score, chain and
  migration state, watermarks, event counts, stale journals, file size,
  projection presence. A probe never creates a missing store file and never
  raises — failures land in the report. Duplicate names or paths are a typed
  `WatchtowerError`. Exit 1 iff any store is unreachable, errored, or fails
  its chain/migration checks.


## Container

`deploy/Dockerfile`: two-stage build on `python:3.14-slim`, non-root `soul`
user, `/data` volume for the store and projections, no runtime network
requirement. Dependency installs differ by surface, deliberately:

- **Local / CI reproduction** uses `uv sync --frozen`, which installs the
  bit-exact locked environment from `uv.lock`.
- **The container build** runs `pip install .`, which resolves dependency
  ranges from `pyproject.toml` — pip does not read `uv.lock` (see the
  Dockerfile header note). For a bit-exact locked container build, generate a
  constraints file from the lock (`uv export`) or install with uv; the
  current image accepts the pyproject ranges by design.

The `know-your-context` entrypoint (console script, `cli.py`) is installed by
the build stage.

## Rust extraction kernel (optional, wave W5)

The code-ingestion hot path (`src/know_your_context/ingest/extractor.py`) routes
through an optional Rust kernel (`kernel-rust/`, crate `kys-kernel`) when it
is built; **the pure-Python tree-sitter walker is the automatic fallback**, so
every install works with or without a Rust toolchain — nothing in CI or in
the Python package depends on the kernel being present. Kill switch:
`KYS_DISABLE_RUST_KERNEL=<any value>` forces the Python path.

Prerequisites: Rust toolchain (`~/.cargo/bin`, e.g. via rustup) and a C
toolchain (`build-essential` on Debian/Ubuntu — the tree-sitter grammars are
C). Then, from the repo root with the project venv active:

```bash
cd kernel-rust
cargo build --release                    # standalone CLI: target/release/kys-kernel
cargo test                               # kernel unit tests
../.venv/bin/maturin develop --release   # Python module into .venv
# or: ../.venv/bin/maturin build --release   # wheel → target/wheels/*.whl
```

`maturin` is a pinned dev-group dependency (`maturin==1.15.0`), so
`uv sync --locked` already provides it in `.venv/bin/maturin`; it needs
`VIRTUAL_ENV` set (active venv) for `develop`. The bridge
(`ingest/kernel_bridge.py`) finds the kernel via (1) an installed
`know_your_context_kernel` module (`maturin develop` / wheel install), or (2) the
repo-local `kernel-rust/target/{release,debug}/libknow_your_context_kernel.so`.

Gotcha: a plain `cargo build` also writes a cdylib to
`target/*/libknow_your_context_kernel.so` that lacks the PyO3 module — always
refresh with `maturin build`/`develop` after plain cargo builds (the bridge
detects the stale artifact and falls back).

CI note: the workflow intentionally does **not** build the kernel — the test
suite passes on the pure-Python path by design, and kernel parity is covered
locally by `tests/test_kernel_rust*.py` when the kernel is built. A runner
that builds the kernel before `pytest` (Ubuntu: rustup + build-essential +
`maturin develop --release`; macOS best-effort) exercises the same suite
through the kernel hot path; both paths must stay green.

## Store path resolution

The CLI resolves the default `--store` path in this order:

1. `KNOW_YOUR_CONTEXT_STORE` (set by the container image to
   `/data/know-your-context.sqlite3`),
2. the built-in default `./know-your-context.sqlite3`.

All `KNOW_YOUR_SOUL_*` variables and the older `SOUL_STORE` fallback were
removed in the Know Your Context rename — there are no aliases.

`SOUL_CORPUS` is unrelated to the product store: it names the external SOUL
corpus input (`SourceKind.SOUL_CORPUS`) and is intentionally unchanged.

## Legacy `soul://` provenance scheme

The product's provenance URI scheme is `context://`. The previous `soul://`
scheme survives only inside byte-frozen artifacts that must never drift:
the frozen v1/v2 measurement fixtures (`tests/fixtures/*.json`) and the
digest-pinned contracts (`docs/contracts/*.md`, sha256-pinned by
`tests/test_contract_v3_shape.py` and `tests/test_extraction_contract_v4_shape.py`).
Those files are historical records; new data and new code use `context://`
exclusively.
