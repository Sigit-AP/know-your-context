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
  setAttribute(k, v) { this.attributes[k] = String(v); }
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

const TABS = ["graph", "activity", "search", "browse", "metrics", "health", "analytics", "audit",
  "proposals", "debt", "codeintel", "extractions", "store", "system", "bench", "ledgers",
  "exports", "contracts", "setup"];
for (const t of TABS) register(new Elem("button"), null, "tab").setAttribute("data-tab", t);
for (const t of TABS) register(new Elem("section"), "view-" + t, t === "graph" ? "view active" : "view");
register(new Elem("aside"), "detail-panel", "hidden");
register(new Elem("div"), "detail-body");
register(new Elem("span"), "detail-title");
register(new Elem("button"), "detail-close");

const documentShim = {
  getElementById(id) { return BY_ID[id] || register(new Elem("div"), id); },
  createElement(tag) { return new Elem(tag); },
  createElementNS(ns, tag) { return new Elem(tag); },
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
  if (base.startsWith("/api/node/")) return NODE_DETAIL;
  if (base.startsWith("/api/hierarchy/")) return { callers: { entries: [] }, callees: { entries: [] } };
  if (base === "/api/activity" || base === "/api/activity/tail") return { entries: [], agents: [] };
  if (base === "/api/leases") return { count: 0, leases: [] };
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
const gnodes = () => documentShim.querySelectorAll(".gnode");
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
check("stats line populated", byId("graph-stats").textContent.includes("4 nodes"));
check("fit-to-view applied a transform", (byId("graph-canvas").style.transform || "").includes("scale("));
check("zoom label set", /%$/.test(byId("graph-zoom-label").textContent));

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

// --- edge midpoint marker is inert but honest --------------------------------
byId("detail-panel").classList.add("hidden");
documentShim.querySelectorAll(".edge-add")[0].click();
check("edge + keeps the panel closed", !byId("detail-panel").classList.contains("open"));
check("edge + explains read-only", byId("status").textContent.includes("read-only"));

// --- shell: tab switching drives views + breadcrumb ---------------------------
documentShim.querySelectorAll(".tab").find((b) => b.getAttribute("data-tab") === "activity").click();
await tick(); await tick();
check("tab click activates the view", byId("view-activity").classList.contains("active"));
check("tab click deactivates the old view", !byId("view-graph").classList.contains("active"));
check("breadcrumb follows the tab", byId("crumb-tab").textContent === "Activity");

let failed = 0;
for (const [name, ok] of results) {
  if (!ok) failed++;
  console.log((ok ? "PASS" : "FAIL") + ": " + name);
}
console.log(`${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
