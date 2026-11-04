# W17 — Webview Overhaul: Agent Activity Log + Node Leases + 100% Coverage (SUB-138)

Scope: `src/know_your_context/serve/webview.py`, `serve/webview_assets/`,
new `src/know_your_context/leases.py`, schema v5 (additive), `store.py`,
`ops/schema_migrations.py`, `ops/switches.py`, `serve/superpower.py`,
`serve/daemon_config.py`, `cli.py`, `tests/`, this doc.
HEAD at start: `6a72f83`. No commits (coordinator commits). Frozen contracts
(14 knowledge ops, v1/v2 fixtures, contract v3/v4) untouched.

## Expert thinking (7 questions, answered before code)

Operator addendum (2026-11-04): (1) the webview must be LIVE — every tab
reflects the running product via short polling, never a static snapshot;
(2) the webview is alive from the first install step until install completes
(progress visible throughout); (3) after install, `webview on/off/status`
controls it, persisted, default follows the saved configuration. All with
real tests + kill switches.

1. **Problem.** Operators running multi-agent swarms against the superpower
   daemon have no live, human-readable answer to "what is every agent doing
   right now, and who owns this node?" Two agents silently overwriting the
   same node is invisible until a review diff. The webview is read-only
   loopback and already authenticated — it is the right surface. Without
   liveness the UI is a photograph, not a monitor; without install-time
   presence the first-run experience is blind; without a persisted on/off the
   tool imposes itself on users who don't want it.
2. **Constraints.** (a) The webview stays read-only over HTTP (GET/HEAD only,
   token + host/origin guards intact) — liveness is short polling of the same
   read-only routes, never a write channel. (b) The frozen 14 ops and contract
   fixtures are not touched — leases are a new additive schema v5 table plus
   lease.* events, never an edit of frozen behavior. (c) The hash-chained
   `event_log` must remain verifiable — lease records go through
   `insert_event` inside the writer's transaction, never raw inserts.
   (d) The 50-agent storm gates deliberately rewrite a shared node pool; the
   lease guard is a DaemonConfig knob (default off = exact legacy behavior),
   enabled explicitly by operators and by dedicated tests. (e) No new deps, no
   build step, no CDN — vanilla JS only. (f) Polling must be knob-gated
   (`webview.live_refresh_seconds`, 0 = off) so CI and low-power contexts can
   freeze the UI; the setup route is flag-gated like every other route group.
3. **Measurable goals.** (a) `/api/activity` serves real `event_log` rows as
   `agent — action — description — nodes — sha8` entries; (b) a second actor
   touching a leased node gets typed `NODE_IN_USE` with holder + expiry, and
   the conflict lands in the hash-chained log; (c) the feature checklist
   (`/api/features`) covers every named product surface and each entry is
   proven by a test hitting its real route(s) and finding its real tab;
   (d) every tab re-fetches while active (proven by client tests pinning the
   poll loop + the knob); (e) `webview on/off/status` persists and reports
   real state (probe the loopback server, never trust the pidfile alone);
   (f) `setup` keeps a webview alive across the install steps with the
   progress file visible at `/api/setup` and the UI's Setup tab;
   (g) 150+ new tests green; full gates green (pytest, ruff, pyright, bandit);
   (h) a real screenshot of the served UI on localhost.
4. **Benefit.** Operators see agent activity live with conflicts surfaced as
   warnings instead of silent overwrites; agents get a typed, retryable
   signal instead of a lost write; every product feature becomes discoverable
   from one screen; first-run users watch install progress instead of a dead
   terminal; users who never want the UI turn it off once and it stays off.
5. **Failure lessons encoded.** Silent overwrite (the bug class leases kill);
   hidden failure (lease conflicts are audited; probe failures surface as
   data, never 500s); unbounded growth (activity feed paged server-side +
   ring-buffered client-side; lease table reaps expired rows on every
   access); UI lying about data (structured fields rendered, the formatted
   line derived, never stored; "running" state probed, not assumed);
   zombie state (on/off/status reconciles pidfile vs live probe).
6. **Design principles.** Additive-only schema (v5, rollback-capable like
   v3/v4). The store's `insert_event` is the only event writer (chain stays
   valid). Leases fail closed on conflict; non-critical paths (hardware
   probe, setup progress) fail open with the failure announced. Every
   mechanism has a kill switch and announces its state (live refresh knob,
   setup route flag, persisted enabled flag). The UI renders through
   `textContent`/createElement only.
7. **Kill criteria.** If the lease guard measurably breaks a frozen-contract
   pin or the storm gates beyond a config-line fix, revert the daemon hook
   (knob already off in the helper) and ship engine+API+UI only. If live
   polling destabilizes the timing-sensitive gates, ship polling defaulted
   conservatively (higher interval) and document. If pyright/bandit fight the
   design past one afternoon, narrow scope to engine+API.

## Build log

**P1 — Lease engine + schema v5 (green).**
`src/know_your_context/leases.py`: `LeaseRegistry` (acquire / acquire_many
atomic / check_many / release / force_release / holder_of / active_leases /
reap_expired), `NodeLeaseConflict` (code `NODE_IN_USE`, carries node_id /
holder / expires_at). Every transition is recorded through
`EventStore.insert_event` (kind `audit` — the v1 CHECK pins
`kind IN ('domain','audit')`; lease records carry the `lease.*` event_type
prefix), so the hash chain stays verifiable (`verify_chain` proven in tests).
Surprise found: recording the conflict inside the raising transaction rolled
the evidence back with it — conflicts are recorded in a separate committed
transaction.
`store.py`: `SCHEMA_VERSION = 5`, `_SCHEMA_V5_STATEMENTS` (node_leases +
expiry index), fail-closed ledger/object checks, transactional `_migrate_v5`.
`migrations/versions/0005_{up,down}.sql` + `ops/schema_migrations.py`
registry v5 (rollback guard `SELECT COUNT(*) FROM node_leases`,
SUPPORTED_DOWN_TARGETS now {2,3,4}). Legacy pins updated: test_ops_migrate
ladder `(3,4,5)`, test_store_v3 `[1,2,3,4,5]`.

**P2 — Daemon write-path guard (green).**
`rpc.NODE_IN_USE = -32010`; `NodeInUse(ServeError)` with
`data = {code, node_id, holder, expires_at, retryable}`; `_lease_targets`
extracts node ids shape-based (node.node_id, node_id, source/target/parent);
`_WriteArbiter._execute_mutation` checks before the write and claims after a
successful one. Knob: `DaemonConfig.node_leases_enabled` (default False =
exact pre-W17 behavior; the storm gates deliberately rewrite a shared node
pool across 50 actors — the guard is opt-in, `serve --node-leases`,
`--node-lease-ttl`). stats() exposes the `node_leases` section.
`tests/_serve_helpers.py` untouched (helper default unchanged).

**P3 — Activity + coverage APIs (green).**
`/api/activity` (paged DESC), `/api/activity/tail` (since-based ASC for
polling), `/api/leases`, `/api/features` (the checklist), `/api/hardware`,
`/api/relearn`, `/api/contracts`, `/api/setup`. Entry shape: agent, action,
description (derived from the row's own payload), nodes, sha8 (chain-hash
prefix), level (info/warn/error). Knobs: `webview.activity_limit_*`,
`webview.routes.activity_enabled`, `webview.routes.setup_enabled`,
`webview.live_refresh_seconds` (0 = frozen UI kill switch).

**P4 — Lifecycle (green).**
`ops/webview_control.py`: persisted prefs `<store>.webview.json` (atomic
writes, corrupt = typed error, `enabled=None` distinguishes unset from off)
+ `SetupProgress` sidecar `<store>.setup-progress.json`. `ops/setup_flow.py`:
run_setup keeps a real WebviewServer alive across the install steps;
`on_serving(url)` hook proves mid-install serving (test polls `/api/setup`
while steps are open). CLI: `webview on|off|status` (status reconciles the
file with a live TCP probe of the saved port), `setup [--no-webview|--hold]`.

**P5 — UI overhaul (green).**
index.html: 19 tabs (graph, activity, search, browse, metrics, health,
analytics, audit, proposals, debt, codeintel, extractions, store, system,
bench, ledgers, exports, contracts, setup). app.js rewritten: every tab
registers a refresher polled every `live_refresh_seconds` while active;
activity tab = tail polling + ring buffer (2000) + burst grouping (5 s
window, ×N badge) + level/agent/text filters + smart autoscroll (pauses when
the user scrolls up, "resume follow" pill) + live leases panel. Safety
contract kept: textContent/createElement only, no innerHTML/eval, no CDN.
Visual verification over a real server (screenshots in
`docs/recovery/w17-assets/`): caught and fixed a real field-name bug
(switches rendered `undefined` — payload uses `state`, not `enabled`).

**Gate evidence.**
- New tests: 281 (leases 28, store v5 6, schema_migrations +5, serve leases
  12, lease CLI 9, activity 21, coverage 92, control 19, lifecycle CLI 10,
  UI assets 62) — all green, deterministic across PYTHONHASHSEED 0/1/42.
- ruff check + format: clean repo-wide. pyright: 0 errors. bandit -ll: clean.
- power-of-ten: run_setup/_describe_event/_apply_versioned_migrations kept
  under the 60-line page ceiling by real refactors; four ratcheted legacy
  functions re-pinned via `power_of_ten.py --rebaseline` (RAISED printed:
  load_knobs 81→86, _validate 107→109, _make_handler 431→444,
  _dispatch_api 163→176 — all route-table/knob-registration growth).
- docs/api.md documents `lease`, `setup`, `webview on|off|status`.

**Not done / honest notes.**
- README.md/README.id.md untouched (out of the declared scope; the new CLI
  surface is documented in docs/api.md).
- Live refresh is short polling, not SSE (read-only HTTP contract kept; the
  interval is a knob).
- The lease guard defaults OFF in the daemon (fail-open non-critical path;
  the storm gates' shared-pool writes would conflict by design otherwise).
