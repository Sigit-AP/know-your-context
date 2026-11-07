// Minimal DOM harness for the webview graph interaction tests (W18 SUB-146).
//
// Loads the real app.js against a tiny DOM shim (no jsdom dependency, no
// network — fetch is stubbed with canned API payloads) and exercises the
// graph mechanics DOM-level: node click expands the card + slides the
// properties panel open, the panel × collapses it, clicking empty canvas
// collapses it, edges are cubic beziers re-anchored on expand, HUD zoom
// controls update the transform, and tab clicks drive the shell.
//
// Exit code 0 = every assertion passed; failures print "FAIL: <name>".

class ClassList {
  constructor(el) { this.el = el; }
  _set() { return new Set(this.el.className.split(/\s+/).filter(Boolean)); }
  add(...cs) { const s = this._set(); cs.forEach((c) => s.add(c)); this.el.className = [...s].join(" "); }
  remove(...cs) { const s = this._set(); cs.forEach((c) => s.delete(c)); this.el.className = [...s].join(" "); }
  toggle(c, force) {
    const s = this._set();
    const want = force === undefined ? !s.has(c) : !!force;
    if (want) s.add(c); else s.delete(c);
    this.el.className = [...s].join(" ");
    return want;
  }
  contains(c) { return this._set().has(c); }
}

const ALL = [];

class Elem {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.className = "";
    this.children = [];
    this.parentNode = null;
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.title = "";
    this.type = "";
    ALL.push(this);
  }
  get classList() { return new ClassList(this); }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  get firstChild() { return this.children[0] || null; }
  setAttribute(k, v) { this.attributes[k] = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatch(type, extra) {
    const event = extra || {};
    event.target = event.target || this;
    event.currentTarget = this;
    event.stopPropagation = event.stopPropagation || (() => { event._stopped = true; });
    event.preventDefault = event.preventDefault || (() => {});
    for (const fn of this.listeners[type] || []) fn(event);
    return event;
  }
  click() { return this.dispatch("click"); }
  _matchesOne(part) {
    part = part.trim();
    if (part.startsWith("#")) return this.getAttribute("id") === part.slice(1);
    if (part.startsWith(".")) return this.classList.contains(part.slice(1));
    return this.tagName === part.toUpperCase();
  }
  matches(sel) { return sel.split(",").some((p) => this._matchesOne(p.trim())); }
  closest(sel) { let e = this; while (e) { if (e.matches && e.matches(sel)) return e; e = e.parentNode; } return null; }
  _descendants(out) { for (const c of this.children) { out.push(c); c._descendants(out); } return out; }
  querySelectorAll(sel) { return this._descendants([]).filter((e) => e.matches(sel)); }
  get offsetWidth() { return this.classList.contains("gnode") ? (this.classList.contains("expanded") ? 330 : 232) : 100; }
  get offsetHeight() { return this.classList.contains("gnode") ? (this.classList.contains("expanded") ? 220 : 54) : 40; }
  get clientWidth() { return 1200; }
  get clientHeight() { return 800; }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight }; }
}

const BY_ID = {};
function register(el, id, cls) {
  if (cls) el.className = cls;
  if (id) { el.id = id; el.setAttribute("id", id); BY_ID[id] = el; }
  return el;
}

const TABS = ["graph", "activity", "search", "metrics", "analytics", "audit",
  "proposals", "debt", "codeintel", "extractions", "system",
  "exports", "contracts", "setup"];
for (const t of TABS) register(new Elem("button"), null, "tab").setAttribute("data-tab", t);
for (const t of TABS) register(new Elem("section"), "view-" + t, t === "graph" ? "view active" : "view");
register(new Elem("aside"), "detail-panel", "hidden");
register(new Elem("div"), "detail-body");
register(new Elem("span"), "detail-title");
register(new Elem("button"), "detail-close");
// index.html pins these classes/ids in the graph HUD strip (SUB-147).
register(new Elem("div"), "graph-stats", "hud-stats");
register(new Elem("button"), "graph-chain-verify", "hud-chain");
// Merged-tab mode controls (SUB-149): registered BEFORE app.js loads because
// the wiring happens at load time.
for (const [id, cls] of [["search-seg-search", "seg-btn active"], ["search-seg-browse", "seg-btn"]])
  register(new Elem("button"), id, cls);
register(new Elem("div"), "search-pane-search");
register(new Elem("div"), "search-pane-browse", "hidden");
register(new Elem("span"), "search-seg-note");
for (const [id, cls] of [["system-seg-health", "seg-btn active"], ["system-seg-store", "seg-btn"], ["system-seg-ops", "seg-btn"]])
  register(new Elem("button"), id, cls);
register(new Elem("div"), "system-pane-health");
register(new Elem("div"), "system-pane-store", "hidden");
register(new Elem("div"), "system-pane-ops", "hidden");
register(new Elem("span"), "system-seg-note");
register(new Elem("div"), "metrics-hero");
register(new Elem("div"), "metrics-charts");
register(new Elem("div"), "metrics-detail");

const documentShim = {
  getElementById(id) { return BY_ID[id] || register(new Elem("div"), id); },
  createElement(tag) { return new Elem(tag); },
  createElementNS(ns, tag) { return new Elem(tag); },
  createTextNode(text) { const e = new Elem("span"); e.textContent = String(text); return e; },
  querySelectorAll(sel) { return ALL.filter((e) => e.matches(sel)); },
  addEventListener() {},
};

const GRAPH = {
  schema_version: 1,
  nodes: [
    { id: "n1", label: "alpha lesson", domain: "P1", kind: "root", lifecycle_status: "active", version: 1,
      scores: { p: "0.500000000000", m: "0.400000000000", e: "0.300000000000", uncertainty: "0.4", confidence: "0.6" },
      provenance: ["context://corpus/lesson.md"], x: 100, y: 100 },
    { id: "n2", label: "beta lesson", domain: "P2", kind: "child", lifecycle_status: "active", version: 2,
      scores: { p: "0.1", m: "0.2", e: "0.3", uncertainty: "0.4", confidence: "0.5" }, provenance: [], x: 420, y: 100 },
    { id: "n3", label: "gamma lesson", domain: "P1", kind: "child", lifecycle_status: "active", version: 1,
      scores: { p: "0", m: "0", e: "0", uncertainty: "1", confidence: "0" }, provenance: [], x: 250, y: 420 },
    { id: "n4", label: "close neighbour", domain: "P2", kind: "child", lifecycle_status: "active", version: 1,
      scores: { p: "0", m: "0", e: "0", uncertainty: "1", confidence: "0" }, provenance: [], x: 200, y: 120 },
  ],
  edges: [
    { source: "n1", target: "n2", kind: "supports" },
    { source: "n1", target: "n3", kind: "parent_of" },
    { source: "n1", target: "n3", kind: "supports" }, // parallel pair: lanes bend apart
  ],
  layout: { mode: "force", width: 800, height: 600 },
  total_nodes: 4, total_edges: 3, truncated: false,
};
const NODE_DETAIL = {
  node_id: "n1", summary: "alpha lesson", domain: "P1", kind: "root", lifecycle_status: "active",
  version: 1, professional_level: "novice", symbol: null,
  scores: { p: "0.5", m: "0.4", e: "0.3", uncertainty: "0.4", confidence: "0.6" },
  provenance: ["context://corpus/lesson.md"], relations: [{ kind: "supports", target: "n2" }],
};

function payloadFor(path) {
  const base = path.split("?")[0];
  if (base === "/api/setup") return { live_refresh_seconds: 0 };
  if (base === "/api/graph") return GRAPH;
  if (base === "/api/audit/verify") return { ok: true, checked: 12 };
  if (base.startsWith("/api/node/")) return NODE_DETAIL;
  if (base.startsWith("/api/hierarchy/")) return { callers: { entries: [] }, callees: { entries: [] } };
  if (base === "/api/activity") return { entries: [
    // 2 days x mixed dayparts/levels so the heatmap has real cells (SUB-150).
    { seq: 1, level: "info", agent: "agent-1", action: "recall", description: "recall ok", nodes: [], sha8: "a1b2c3d4", occurred_at: "2026-11-05T09:15:00+00:00" },
    { seq: 2, level: "warn", agent: "agent-2", agent_name: "agent-2", action: "publish", description: "budget near", nodes: [], sha8: "b2c3d4e5", occurred_at: "2026-11-05T14:40:00+00:00" },
    { seq: 3, level: "error", agent: "agent-2", action: "publish", description: "budget exceeded", nodes: ["n1"], sha8: "c3d4e5f6", occurred_at: "2026-11-06T10:05:00+00:00" },
    { seq: 4, level: "info", agent: "agent-1", action: "remember", description: "stored", nodes: ["n2"], sha8: "d4e5f6a7", occurred_at: "2026-11-06T22:30:00+00:00" }
  ], agents: ["agent-1", "agent-2"] };
  if (base === "/api/activity/tail") return { entries: [], agents: [] };
  if (base === "/api/leases") return { count: 0, leases: [] };
  // SUB-149: canned metrics payloads for the visual-first tab assertions.
  if (base === "/api/metrics") return {
    counts: { audit_events: 8, domain_events: 6, events_total: 14, journals_complete: 5,
      journals_in_progress: 0, journals_stale: 0 },
    health: { score: 7, max_score: 8, components: {} },
    latency_ms: {},
    publish_capacity: { cap_bytes: 65536, estimated_bytes: 12000, guard_bytes: 32768,
      headroom_bytes: 20768, ok: true, utilization_permille: 183 },
  };
  if (base === "/api/metrics/series") return {
    event_series: [
      { day: "2026-11-01", events: 2 }, { day: "2026-11-02", events: 5 },
      { day: "2026-11-03", events: 7 },
    ],
    health_series: [
      { recorded_at: "2026-11-02T00:00:00+00:00", score: 6, max_score: 8, event_count: 10 },
      { recorded_at: "2026-11-03T00:00:00+00:00", score: 7, max_score: 8, event_count: 14 },
    ],
    health_history: "ok",
  };
  if (base === "/api/bench") return {
    operations: [
      { operation: "knowledge.query", count: 3, min_ms: 2, max_ms: 9, mean_ms: 5, p50_ms: 4, p95_ms: 9 },
      { operation: "knowledge.remember", count: 2, min_ms: 6, max_ms: 12, mean_ms: 9, p50_ms: 9, p95_ms: 12 },
    ],
    malformed_outcomes: 0,
  };
  // SUB-150: canned payloads for the complex-visual endpoints.
  if (base === "/api/proposals") return {
    count: 1,
    signals: [{ signal: "isolated_node", low_confidence: false, reason_codes: ["ISOLATED_NODE"], source_node_ids: ["n3"] }],
    signal_counts: { isolated_node: 1, low_confidence_edge: 2 },
  };
  if (base === "/api/improvements") return {
    count: 3,
    proposals: [
      { proposal_id: "imp-1", node_id: "n1", impact: "medium", proposed_at: "2026-11-01T00:00:00Z", status: "applied" },
      { proposal_id: "imp-2", node_id: "n2", impact: "low", proposed_at: "2026-11-02T00:00:00Z", status: "proposed" },
      { proposal_id: "imp-3", node_id: "n3", impact: "low", proposed_at: "2026-11-03T00:00:00Z", status: "proposed" },
    ],
  };
  if (base === "/api/extractions") return { count: 0, queue_size: 4, rows: [] };
  if (base === "/api/extractions/summary") return {
    state: "ok", queue_size: 4,
    counts: { applied: 1, pending_review: 1, rejected: 2 },
  };
  if (base === "/api/ledger/health") return {
    state: "ok",
    snapshots: [
      { recorded_at: "2026-11-05T00:00:00+00:00", score: 6, max_score: 8, event_count: 10, hash: "abc123def456" },
      { recorded_at: "2026-11-06T00:00:00+00:00", score: 7, max_score: 8, event_count: 14, hash: "def456abc123" },
    ],
  };
  if (base === "/api/ledger/debt") return { state: "ok", entries: [] };
  if (base === "/api/ledger/experience") return { state: "ok", entries: [], verification: { ok: true, checked: 0 } };
  if (base === "/api/audit") return { count: 0, events: [] };
  if (base === "/api/refs") return { pending: 1, resolved: 2, failed: 1, total: 4, top_failed: [], failed_samples: [] };
  if (base === "/api/refs/pending") return { status: "pending", total: 1, refs: [{ ref_id: 1, source_node_id: "n1", target_name: "gamma_fn", name_tail: "gamma_fn", kind: "calls", status: "pending", attempts: 1, failure_reason: null, first_seen: "2026-11-05" }] };
  if (base === "/api/refs/resolved") return { status: "resolved", total: 2, refs: [
    { ref_id: 2, source_node_id: "n1", target_name: "eta_mod", name_tail: "eta_mod", kind: "imports", status: "resolved", attempts: 1, failure_reason: null, first_seen: "2026-11-05" },
    { ref_id: 3, source_node_id: "n2", target_name: "kappa_fn", name_tail: "kappa_fn", kind: "calls", status: "resolved", attempts: 1, failure_reason: null, first_seen: "2026-11-05" },
  ] };
  if (base === "/api/refs/failed") return { status: "failed", total: 1, refs: [{ ref_id: 4, source_node_id: "n2", target_name: "delta_fn", name_tail: "delta_fn", kind: "calls", status: "failed", attempts: 2, failure_reason: "no_match", first_seen: "2026-11-05" }] };
  return {};
}

globalThis.window = { addEventListener() {}, requestAnimationFrame(fn) { fn(); } };
globalThis.document = documentShim;
globalThis.location = { search: "?token=harness-token" };
globalThis.fetch = (path) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payloadFor(path)) });

const results = [];
function check(name, cond) { results.push([name, !!cond]); }
const tick = () => new Promise((r) => setTimeout(r, 0));

const { createRequire } = await import("node:module");
const require = createRequire(import.meta.url);
require("../src/know_your_context/serve/webview_assets/app.js");
await tick(); await tick();

const byId = (id) => documentShim.getElementById(id);
// A reload detaches old cards (parentNode null) but the shim's global
// registry keeps them — always read the LIVE tree (attached to a parent).
const gnodes = () => documentShim.querySelectorAll(".gnode").filter((e) => e.parentNode);
const gnode = (id) => gnodes().find((e) => e.getAttribute("data-node-id") === id);
const paths = () => byId("graph-edges").children;

// --- initial render -------------------------------------------------------
const boxOf = (e) => ({ x: parseFloat(e.style.left), y: parseFloat(e.style.top), w: e.offsetWidth, h: e.offsetHeight });
const intersects = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
check("graph renders one card per node", gnodes().length === 4);
check("graph renders one svg path per edge", paths().length === 3);
check("edges are cubic beziers", [...paths()].every((p) => (p.getAttribute("d") || "").includes(" C ")));
check("parallel edges bend apart", paths()[1].getAttribute("d") !== paths()[2].getAttribute("d"));
check("edge-add markers only on edges with room", documentShim.querySelectorAll(".edge-add").length === 2);
// Stats live in the bottom HUD strip (SUB-147), not the toolbar.
check("hud stats populated", byId("graph-stat-nodes").textContent === "4" && byId("graph-stat-edges").textContent === "3");
check("hud stats element is the graph-stats id", byId("graph-stats").classList.contains("hud-stats"));
check("fit-to-view applied a transform", (byId("graph-canvas").style.transform || "").includes("scale("));
check("zoom label set", /%$/.test(byId("graph-zoom-label").textContent));

// --- tile identity: deterministic unique gradient per node, dark anchors ---
const tileOf = (id) => gnode(id).querySelectorAll(".gnode-tile")[0];
check("root node tile stays dark ink", tileOf("n1").style.background === "#1F2937");
check("child node tiles carry gradients",
  ["n2", "n3", "n4"].every((id) => (tileOf(id).style.background || "").startsWith("linear-gradient(135deg")));
const gradSet = new Set(["n2", "n3", "n4"].map((id) => tileOf(id).style.background));
check("distinct nodes get distinct gradients", gradSet.size === 3);
check("every card shows the always-visible menu", gnodes().every((g) => g.querySelectorAll(".gnode-menu").length === 1));
// Reload must reproduce the identical assignment (deterministic from node id).
const gradBefore = ["n1", "n2", "n3", "n4"].map((id) => tileOf(id).style.background).join("|");
byId("graph-reload").click();
await tick(); await tick();
check("reload keeps one live card per node", gnodes().length === 4);
check("tile identity stable across reload", ["n1", "n2", "n3", "n4"].map((id) => tileOf(id).style.background).join("|") === gradBefore);

// --- click node: expand + panel slides in ---------------------------------
const before = paths()[0].getAttribute("d");
gnode("n1").click();
await tick(); await tick();
check("click expands the card", gnode("n1").classList.contains("expanded"));
check("click marks the card selected", gnode("n1").classList.contains("selected"));
check("other cards stay collapsed", !gnode("n2").classList.contains("expanded"));
check("expanded card shows full-content block", gnode("n1").querySelectorAll(".gnode-content").length === 1);
check("expanded card shows meta badges", gnode("n1").querySelectorAll(".gnode-stat").length === 5);
check("expanded card lists connections", gnode("n1").querySelectorAll(".gnode-link").length === 3);
check("panel slides open", byId("detail-panel").classList.contains("open"));
check("panel not hidden", !byId("detail-panel").classList.contains("hidden"));
check("panel title shows the summary", byId("detail-title").textContent === "alpha lesson");
check("panel has structured kv rows", byId("detail-body").querySelectorAll(".dp-kv").length >= 5);
check("panel has score bars", byId("detail-body").querySelectorAll(".scorebar").length === 5);
// Panel completeness (SUB-147): Relations merges graph edges (parent_of +
// incoming), so it matches the card's connection list — n1 has supports→n2
// plus parent_of→n3 and supports→n3.
check("panel relations match the card connections",
  byId("detail-body").querySelectorAll(".dp-section")[3].querySelectorAll(".linkish").length === 3);
// The hierarchy heading must survive the async resolve (clear() used to wipe it).
await tick(); await tick();
const eyebrows = byId("detail-body").querySelectorAll(".dp-eyebrow").map((e) => e.textContent);
check("hierarchy heading survives async load", eyebrows.includes("Hierarchy (callers / callees)"));
const textOf = (e) => (e.textContent || "") + e.children.map(textOf).join("");
check("hierarchy empty state is honest", textOf(byId("detail-body")).includes("(none)"));
check("hot edges highlight the selection", [...paths()].some((p) => p.getAttribute("class") === "hot"));
check("edges re-bend after expand", paths()[0].getAttribute("d") !== before);
check("expanded card never covers a neighbour", !intersects(boxOf(gnode("n1")), boxOf(gnode("n4"))));

// --- navigate via in-card link ---------------------------------------------
const link = gnode("n1").querySelectorAll(".gnode-link")[0];
link.click();
await tick(); await tick();
check("connection link moves the selection", gnode("n2").classList.contains("expanded") && !gnode("n1").classList.contains("expanded"));

// --- panel × collapses ------------------------------------------------------
byId("detail-close").click();
check("× collapses the card", !gnode("n2").classList.contains("expanded"));
check("× closes the panel", !byId("detail-panel").classList.contains("open") && byId("detail-panel").classList.contains("hidden"));
check("× clears edge highlight", [...paths()].every((p) => p.getAttribute("class") !== "hot"));

// --- click empty canvas collapses -------------------------------------------
gnode("n3").click();
await tick(); await tick();
check("re-select works", gnode("n3").classList.contains("expanded"));
byId("graph-wrap").dispatch("click");
check("click outside collapses the card", !gnode("n3").classList.contains("expanded"));
check("click outside closes the panel", !byId("detail-panel").classList.contains("open"));

// --- HUD zoom ----------------------------------------------------------------
const labelBefore = byId("graph-zoom-label").textContent;
byId("graph-zoom-in").click();
check("zoom-in updates the label", byId("graph-zoom-label").textContent !== labelBefore);
check("zoom-in updates the transform", byId("graph-canvas").style.transform.includes("scale("));

// --- chain verification pill is wired to the real audit/verify endpoint ------
byId("graph-chain-verify").click();
await tick(); await tick();
check("chain pill reports the verified chain", byId("graph-chain-verify").textContent === "valid · 12");
check("chain pill takes the ok styling", byId("graph-chain-verify").classList.contains("hud-chain-ok"));

// --- edge midpoint marker is inert but honest --------------------------------
byId("detail-panel").classList.add("hidden");
documentShim.querySelectorAll(".edge-add")[0].click();
check("edge + keeps the panel closed", !byId("detail-panel").classList.contains("open"));
check("edge + explains read-only", byId("status").textContent.includes("read-only"));
// SUB-149: parallel-edge markers use a 26px pitch (22px circles never overlap).
const markers = documentShim.querySelectorAll(".edge-add");
const markerDist = Math.hypot(
  parseFloat(markers[0].style.left) - parseFloat(markers[1].style.left),
  parseFloat(markers[0].style.top) - parseFloat(markers[1].style.top));
check("parallel-edge markers never overlap (26px pitch)", markerDist >= 26);

// --- shell: tab switching drives views + breadcrumb ---------------------------
documentShim.querySelectorAll(".tab").find((b) => b.getAttribute("data-tab") === "activity").click();
await tick(); await tick();
check("tab click activates the view", byId("view-activity").classList.contains("active"));
check("tab click deactivates the old view", !byId("view-graph").classList.contains("active"));
check("breadcrumb follows the tab", byId("crumb-tab").textContent === "Activity");

// --- SUB-149: merged Search tab = two modes -----------------------------------
// (mode controls registered before app.js load, above)
byId("search-seg-browse").click();
await tick(); await tick();
check("browse mode activates the browse pane", !byId("search-pane-browse").classList.contains("hidden"));
check("browse mode hides the search pane", byId("search-pane-search").classList.contains("hidden"));
check("browse mode marks the seg button active", byId("search-seg-browse").classList.contains("active"));
byId("system-seg-store").click();
await tick(); await tick();
check("store panel activates on segment click", !byId("system-pane-store").classList.contains("hidden"));
check("health panel hides on segment switch", byId("system-pane-health").classList.contains("hidden"));

// --- SUB-149: metrics visual-first render (canned real payloads) --------------
documentShim.querySelectorAll(".tab").find((b) => b.getAttribute("data-tab") === "metrics").click();
await tick(); await tick(); await tick();
const heroKpis = byId("metrics-hero").querySelectorAll(".kpi");
check("metrics hero renders 4 KPI cards", heroKpis.length === 4);
check("exactly one dark priority card", heroKpis.filter((k) => k.classList.contains("dark")).length === 1);
check("KPI cards carry the 4 layers",
  heroKpis.every((k) => ["kpi-label", "kpi-value", "kpi-delta", "kpi-base"]
    .every((cls) => k.querySelectorAll("." + cls).length === 1)));
check("health KPI shows the real score",
  byId("metrics-hero").querySelectorAll(".kpi-value").some((v) => textOf(v).includes("7")));
const charts = byId("metrics-charts");
const barSvg = charts.querySelectorAll("svg").map((s) => s.querySelectorAll(".viz-bar").length);
check("bar chart renders one bar per bench op", barSvg.some((n) => n === 2));
check("bar chart floats accent value tags", charts.querySelectorAll(".viz-tag").length === 2);
check("donut renders one arc per event kind", charts.querySelectorAll(".viz-arc").length === 2);
check("donut renders the % badge on the largest arc", charts.querySelectorAll(".viz-pct-badge").length === 1);
check("bench table lands in the detail section", byId("metrics-detail").querySelectorAll("table").length >= 1);

// --- SUB-150: interactive multi-series trend replaces the flat line ---------
// NOTE: the shim matches only #id / .class / TAG — no descendant or compound
// selectors — so composed queries go through closest() filters.
const segButtons = (root) => root.querySelectorAll("button").filter((b) => b.closest(".chart-seg"));
const legendButtons = (root) => root.querySelectorAll("button").filter((b) => b.closest(".mls-legend"));
check("multi-line renders both series", charts.querySelectorAll(".mls-line").length === 2);
check("multi-line keeps the soft area tint on series 1", charts.querySelectorAll(".mls-area").length === 1);
check("multi-line marks series endpoints", charts.querySelectorAll(".mls-enddot").length === 2);
check("multi-line has a crosshair (hidden until hover)", charts.querySelectorAll(".mls-cross").length === 1);
check("multi-line has a floating tooltip host", charts.querySelectorAll(".viz-tip").length === 1);
check("multi-line legend has one button per series", legendButtons(charts).length === 2);
check("trend card has the segmented control (Both/Events/Health)", segButtons(charts).length === 3);
check("y gridlines are hairlines with labels", charts.querySelectorAll(".mls-grid").length === 4);
// Segmented control: Events mode keeps only the events series.
segButtons(charts).find((b) => b.getAttribute("data-seg") === "events").click();
await tick();
check("seg 'Events' re-renders with one series", charts.querySelectorAll(".mls-line").length === 1);
check("seg 'Events' keeps the events series", charts.querySelectorAll(".mls-line")[0].getAttribute("data-series") === "events");
segButtons(charts).find((b) => b.getAttribute("data-seg") === "both").click();
await tick();
check("seg 'Both' restores both series", charts.querySelectorAll(".mls-line").length === 2);
// Legend toggle dims its series without deleting it.
legendButtons(charts)[1].click();
check("legend toggle dims the series (mls-off)", charts.querySelectorAll(".mls-line")[1].classList.contains("mls-off"));
legendButtons(charts)[1].click();
check("legend re-toggle restores the series", !charts.querySelectorAll(".mls-line")[1].classList.contains("mls-off"));
// Hover: crosshair + tooltip appear with real values, then hide on leave.
const hit = charts.querySelectorAll(".mls-hit")[0];
hit.dispatch("mousemove", { clientX: 600 }); // snaps to the middle day (all series non-null)
check("hover shows the crosshair", charts.querySelectorAll(".mls-cross")[0].style.display !== "none");
const tip = charts.querySelectorAll(".viz-tip")[0];
check("hover shows the tooltip", tip.style.display !== "none");
check("tooltip carries the day header", tip.querySelectorAll(".tip-head")[0].textContent.length === 10);
check("tooltip lists one row per series", tip.querySelectorAll(".tip-row").length === 2);
hit.dispatch("mouseleave", {});
check("mouseleave hides the tooltip", tip.style.display === "none");
check("mouseleave hides the crosshair", charts.querySelectorAll(".mls-cross")[0].style.display === "none");
// Sparklines ride the health + events KPI cards (foto-2 strip anatomy).
check("KPI cards carry sparklines", byId("metrics-hero").querySelectorAll(".kpi-spark").length === 2);
check("sparkline is stroke-only with an end dot", byId("metrics-hero").querySelectorAll(".spark-line").length === 2);
// Bench table numeric columns are heat-mapped (hottest cell bold).
check("bench latency cells carry intensity", byId("metrics-detail").querySelectorAll(".heat").length === 10);
check("bench hottest cell per column is bold", byId("metrics-detail").querySelectorAll(".heat-hi").length === 5);

// --- SUB-150: activity dot-matrix heatmap ------------------------------------
const density = byId("activity-density");
check("heatmap renders one dot per daypart x day cell", density.querySelectorAll(".dh-dot").length === 8); // 4 x 2 days
check("heatmap rings cells containing errors", density.querySelectorAll(".dh-ring").length === 1);
check("heatmap has the Daily/Hourly segmented control", segButtons(density).length === 2);
check("heatmap stat cards carry per-day sparklines", density.querySelectorAll(".kpi-spark").length === 2);
check("agent share bar renders one segment per agent", density.querySelectorAll(".pb-seg").length === 2);
check("agent share legend carries avatars", density.querySelectorAll(".pb-avatar").length === 2);
check("agent share legend reports values + %", density.querySelectorAll(".pb-value").length === 2 && density.querySelectorAll(".pb-share").length === 2);
// Hover a dot: tooltip shows the exact count + level mix.
const firstDot = density.querySelectorAll(".dh-dot")[0];
firstDot.dispatch("mousemove", {});
const dTip = density.querySelectorAll(".viz-tip")[0];
check("dot hover shows the cell tooltip", dTip.style.display !== "none");
check("dot tooltip reports count and level mix", dTip.querySelectorAll(".tip-row").length === 2);
firstDot.dispatch("mouseleave", {});
check("dot leave hides the tooltip", dTip.style.display === "none");
// Hourly mode: 7 weekdays x 24 hours.
segButtons(density).find((b) => b.getAttribute("data-seg") === "hourly").click();
check("hourly mode renders the weekday x hour grid", density.querySelectorAll(".dh-dot").length === 168);
segButtons(density).find((b) => b.getAttribute("data-seg") === "daily").click();
check("daily mode restores the daypart grid", density.querySelectorAll(".dh-dot").length === 8);

// --- SUB-150: proposals funnel + signal mix ----------------------------------
documentShim.querySelectorAll(".tab").find((b) => b.getAttribute("data-tab") === "proposals").click();
await tick(); await tick(); await tick();
const proposalsBody = byId("proposals-body");
check("proposals funnel renders proposed -> applied tiers", proposalsBody.querySelectorAll(".funnel-bar").length === 2);
check("proposals funnel shows the conversion gap", proposalsBody.querySelectorAll(".funnel-conv").length === 1);
check("proposals funnel conversion is honest (1/3 = 33%)", textOf(proposalsBody.querySelectorAll(".funnel-conv")[0]).includes("33%"));
check("signal mix proportion bar renders per-signal segments", proposalsBody.querySelectorAll(".pb-seg").length === 2);

// --- SUB-150: extractions funnel ---------------------------------------------
documentShim.querySelectorAll(".tab").find((b) => b.getAttribute("data-tab") === "extractions").click();
await tick(); await tick(); await tick();
const extractionsBody = byId("extractions-body");
check("extractions funnel renders submitted -> decided -> applied", extractionsBody.querySelectorAll(".funnel-bar").length === 3);
check("extractions funnel reports two conversion gaps", extractionsBody.querySelectorAll(".funnel-conv").length === 2);

// --- SUB-150: audit refs mix + kind x status matrix + health history ---------
documentShim.querySelectorAll(".tab").find((b) => b.getAttribute("data-tab") === "audit").click();
await tick(); await tick(); await tick(); await tick(); await tick();
const refsBody = byId("audit-refs");
check("refs resolution mix renders semantic segments", refsBody.querySelectorAll(".pb-seg").length === 3);
check("refs matrix renders all three tables", refsBody.querySelectorAll("table").length === 3);
check("refs matrix covers both kinds", textOf(refsBody).includes("calls") && textOf(refsBody).includes("imports"));
check("refs matrix cells carry intensity", refsBody.querySelectorAll(".heat").length >= 3);
check("health ledger shows the score history line", byId("ledgers-health").querySelectorAll(".viz-line").length === 1);

let failed = 0;
for (const [name, ok] of results) {
  if (!ok) failed++;
  console.log((ok ? "PASS" : "FAIL") + ": " + name);
}
console.log(`${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
