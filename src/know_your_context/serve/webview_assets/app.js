/* Know Your Context webview client — vanilla JS, no build step, no CDN, offline.
 *
 * Safety contract: store data is rendered exclusively through textContent /
 * createElement; no string of store data ever becomes markup. Layout
 * coordinates come from the server (viz.layout); the client only applies a
 * pan/zoom transform.
 *
 * Liveness contract (W17): every tab registers a refresher; while the tab is
 * active it is re-fetched every live_refresh_seconds (learned from
 * /api/setup; the knob webview.live_refresh_seconds=0 freezes the UI). The
 * activity tab tails /api/activity/tail with a bounded client-side ring
 * buffer — the page never grows without limit.
 */
(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var TOKEN = params.get("token") || "";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setStatus(message, isError) {
    var status = document.getElementById("status");
    status.textContent = message || "ready";
    status.className = isError ? "error" : "";
  }

  function api(path) {
    var joiner = path.indexOf("?") === -1 ? "?" : "&";
    return fetch(path + joiner + "token=" + encodeURIComponent(TOKEN), {
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) {
          var code = payload && payload.error ? payload.error.code : String(response.status);
          var message = payload && payload.error ? payload.error.message : "request failed";
          throw new Error(code + ": " + message);
        }
        return payload;
      });
    });
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // ------------------------------------------------------------ live refresh

  var LIVE = { seconds: 2.0, timer: null };
  var ACTIVE_TAB = "graph";
  var refreshers = {}; // tab name -> function(quiet:boolean)

  function setLivenessLabel() {
    var node = document.getElementById("liveness");
    if (LIVE.seconds > 0) {
      node.textContent = "live · " + LIVE.seconds + "s";
      node.className = "promo-new live";
    } else {
      node.textContent = "frozen";
      node.className = "promo-new frozen";
    }
  }

  function startPolling() {
    if (LIVE.timer) clearInterval(LIVE.timer);
    if (LIVE.seconds <= 0) return;
    LIVE.timer = setInterval(function () {
      var fn = refreshers[ACTIVE_TAB];
      if (fn) fn(true); // quiet refresh: keep scroll/selection state
    }, LIVE.seconds * 1000);
  }

  // ------------------------------------------------------------------ tabs

  var loadedOnce = {};

  var TAB_LABELS = {
    graph: "Graph", activity: "Activity", search: "Search", browse: "Browse",
    metrics: "Metrics", health: "Health", analytics: "Analytics", audit: "Audit",
    proposals: "Proposals", debt: "Debt", codeintel: "Code Intel",
    extractions: "Extractions", store: "Store", system: "System", bench: "Bench",
    ledgers: "Ledgers", exports: "Exports", contracts: "Contracts", setup: "Setup"
  };

  var PRIMARY_INPUTS = {
    search: "search-q", browse: "browse-filter", activity: "activity-search",
    codeintel: "codeintel-symbol", exports: "export-node"
  };

  function focusPrimaryInput() {
    var id = PRIMARY_INPUTS[ACTIVE_TAB];
    if (!id) return;
    var input = document.getElementById(id);
    if (input && input.focus) input.focus();
  }

  function showTab(name) {
    ACTIVE_TAB = name;
    var tabs = document.querySelectorAll(".tab");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle("active", tabs[i].getAttribute("data-tab") === name);
    }
    var crumb = document.getElementById("crumb-tab");
    if (crumb) crumb.textContent = TAB_LABELS[name] || name;
    var views = document.querySelectorAll(".view");
    for (var j = 0; j < views.length; j++) {
      views[j].classList.toggle("active", views[j].id === "view-" + name);
    }
    if (name === "graph") requestDraw();
    var fn = refreshers[name];
    if (fn && !loadedOnce[name]) {
      loadedOnce[name] = true;
      fn(false);
    } else if (fn && name !== "activity" && name !== "graph") {
      fn(true);
    }
  }

  function loadPanel(elementId, path, renderer) {
    var container = document.getElementById(elementId);
    clear(container);
    container.appendChild(el("p", "empty", "loading…"));
    api(path).then(function (payload) {
      clear(container);
      renderer(container, payload);
      setStatus("");
    }).catch(function (error) {
      clear(container);
      container.appendChild(el("p", "bad-badge", String(error.message || error)));
      setStatus(String(error.message || error), true);
    });
  }

  // ----------------------------------------------------------- generic parts

  function nw(text) {
    // Machine token that must never wrap mid-token (timestamp, actor, type).
    return el("span", "nw", text);
  }

  function table(parent, headers, rows, cls) {
    var tbl = el("table", cls ? "data " + cls : "data");
    var head = el("tr");
    for (var i = 0; i < headers.length; i++) head.appendChild(el("th", null, headers[i]));
    tbl.appendChild(head);
    for (var r = 0; r < rows.length; r++) {
      var row = el("tr");
      for (var c = 0; c < rows[r].length; c++) {
        var cell = rows[r][c];
        var td = el("td");
        if (cell && typeof cell === "object" && cell.tagName) td.appendChild(cell);
        else td.textContent = cell === undefined || cell === null || cell === "" ? "—" : String(cell);
        row.appendChild(td);
      }
      tbl.appendChild(row);
    }
    parent.appendChild(tbl);
    return tbl;
  }

  function cardGrid(parent, pairs) {
    var grid = el("div", "card-grid");
    for (var i = 0; i < pairs.length; i++) {
      var card = el("div", "card");
      card.appendChild(el("div", "k", pairs[i][0]));
      card.appendChild(el("div", "v", pairs[i][1]));
      grid.appendChild(card);
    }
    parent.appendChild(grid);
  }

  function badge(level, text) {
    var cls = level === "error" ? "badge error" : level === "warn" || level === "warning" ? "badge warn" :
      level === "ok" ? "badge ok" : level === "info" ? "badge info" : "badge dim";
    return el("span", cls, text);
  }

  function emptyNote(parent, text) {
    parent.appendChild(el("p", "empty", text));
  }

  // ----------------------------------------------------------------- graph
  //
  // DOM graph (prototype mechanics): nodes are absolutely positioned cards
  // inside a pan/zoom container over a dot-grid; edges are flexible cubic
  // bezier SVG paths anchored to the side facing the neighbour. Clicking a
  // node expands the card in place (full summary + meta badges) and slides
  // the properties panel in from the right; clicking empty canvas or the
  // panel's × collapses everything back. The "+" marker on each edge
  // midpoint reports the read-only status. Layout coordinates come from the
  // server (viz.layout); the client only positions and transforms.

  var SVG_NS = "http://www.w3.org/2000/svg";
  var graphData = null;
  var graphView = { x: 0, y: 0, scale: 1 };
  var graphEls = {
    wrap: document.getElementById("graph-wrap"),
    canvas: document.getElementById("graph-canvas"),
    edges: document.getElementById("graph-edges")
  };
  var graphNodeEls = {}; // node id -> card element
  var graphEdgeButtons = [];
  var graphSelectedId = null;
  var GNODE_W = 232;
  var GNODE_W_EXP = 330;
  var GNODE_H = 54;
  var GNODE_H_EXP = 220;

  function graphNodeById(id) {
    if (!graphData) return null;
    for (var i = 0; i < graphData.nodes.length; i++) {
      if (graphData.nodes[i].id === id) return graphData.nodes[i];
    }
    return null;
  }

  function shortScore(raw) {
    var value = parseFloat(raw);
    return isNaN(value) ? String(raw) : value.toFixed(2);
  }

  // Node (x, y) from the server layout is the card CENTER (same semantics as
  // the old canvas renderer); the card's box derives from it. Once a card is
  // placed, its style.left/top is the truth (the expand resolver may shift
  // an expanded card off-center to avoid covering its neighbours).
  function gnodeBox(node) {
    var card = graphNodeEls[node.id];
    var expanded = !!(card && card.classList.contains("expanded"));
    var w = card && card.offsetWidth ? card.offsetWidth : (expanded ? GNODE_W_EXP : GNODE_W);
    var h = card && card.offsetHeight ? card.offsetHeight : (expanded ? GNODE_H_EXP : GNODE_H);
    var x = node.x - w / 2, y = node.y - h / 2;
    if (card) {
      var lx = parseFloat(card.style.left), ly = parseFloat(card.style.top);
      if (!isNaN(lx) && !isNaN(ly)) { x = lx; y = ly; }
    }
    return { x: x, y: y, w: w, h: h };
  }

  function cardBox(card, fallbackW, fallbackH) {
    return {
      x: parseFloat(card.style.left) || 0,
      y: parseFloat(card.style.top) || 0,
      w: card.offsetWidth || fallbackW,
      h: card.offsetHeight || fallbackH
    };
  }

  function boxesIntersect(a, b) {
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }

  // An expanded card (330px) can cover a close neighbour: shift it the
  // minimal amount along the axis that resolves the overlap, bounded
  // iterations, until no card pair intersects.
  function resolveExpandedOverlap(id) {
    var card = graphNodeEls[id];
    if (!card) return;
    for (var iter = 0; iter < 8; iter++) {
      var box = cardBox(card, GNODE_W_EXP, GNODE_H_EXP);
      var moved = false;
      for (var nid in graphNodeEls) {
        if (nid === id) continue;
        var other = cardBox(graphNodeEls[nid], GNODE_W, GNODE_H);
        if (!boxesIntersect(box, other)) continue;
        var pushLeft = box.x + box.w - other.x;
        var pushRight = other.x + other.w - box.x;
        var pushUp = box.y + box.h - other.y;
        var pushDown = other.y + other.h - box.y;
        var m = Math.min(pushLeft, pushRight, pushUp, pushDown);
        if (m === pushLeft) box.x -= pushLeft + 8;
        else if (m === pushRight) box.x += pushRight + 8;
        else if (m === pushUp) box.y -= pushUp + 8;
        else box.y += pushDown + 8;
        moved = true;
      }
      card.style.left = box.x + "px";
      card.style.top = box.y + "px";
      if (!moved) break;
    }
  }

  // Anchor each edge endpoint to the card side facing its neighbour, then
  // bend a cubic bezier along the dominant axis (lentur, not kaku).
  function edgeAnchors(a, b) {
    var ax = a.x + a.w / 2, ay = a.y + a.h / 2;
    var bx = b.x + b.w / 2, by = b.y + b.h / 2;
    if (Math.abs(by - ay) >= Math.abs(bx - ax)) {
      if (by >= ay) return { x1: ax, y1: a.y + a.h, x2: bx, y2: b.y, axis: "v" };
      return { x1: ax, y1: a.y, x2: bx, y2: b.y + b.h, axis: "v" };
    }
    if (bx >= ax) return { x1: a.x + a.w, y1: ay, x2: b.x, y2: by, axis: "h" };
    return { x1: a.x, y1: ay, x2: b.x + b.w, y2: by, axis: "h" };
  }

  function edgePathData(a, b, lane) {
    // lane: parallel-edge index offset (duplicate source→target pairs bend apart)
    var off = (lane || 0) * 18;
    var an = edgeAnchors(a, b);
    if (an.axis === "v") {
      var dy = Math.max(40, Math.abs(an.y2 - an.y1) * 0.55) * (an.y2 >= an.y1 ? 1 : -1);
      return "M " + (an.x1 + off) + " " + an.y1 + " C " + (an.x1 + off) + " " + (an.y1 + dy) +
        ", " + (an.x2 + off) + " " + (an.y2 - dy) + ", " + (an.x2 + off) + " " + an.y2;
    }
    var dx = Math.max(40, Math.abs(an.x2 - an.x1) * 0.55) * (an.x2 >= an.x1 ? 1 : -1);
    return "M " + an.x1 + " " + (an.y1 + off) + " C " + (an.x1 + dx) + " " + (an.y1 + off) +
      ", " + (an.x2 - dx) + " " + (an.y2 + off) + ", " + an.x2 + " " + (an.y2 + off);
  }

  // Parallel edges between the same pair bend apart so each stays visible.
  function edgeLanes() {
    var groups = {};
    var lanes = [];
    if (!graphData) return lanes;
    for (var i = 0; i < graphData.edges.length; i++) {
      var key = graphData.edges[i].source + "→" + graphData.edges[i].target;
      groups[key] = (groups[key] || 0) + 1;
      lanes.push(groups[key] - 1);
    }
    return lanes.map(function (lane, i) {
      var key = graphData.edges[i].source + "→" + graphData.edges[i].target;
      return lane - (groups[key] - 1) / 2;
    });
  }

  function drawEdges() {
    if (!graphData) return;
    clear(graphEls.edges);
    var lanes = edgeLanes();
    for (var i = 0; i < graphData.edges.length; i++) {
      var edge = graphData.edges[i];
      var source = graphNodeById(edge.source);
      var target = graphNodeById(edge.target);
      if (!source || !target) continue;
      var path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", edgePathData(gnodeBox(source), gnodeBox(target), lanes[i]));
      path.setAttribute("data-from", edge.source);
      path.setAttribute("data-to", edge.target);
      if (graphSelectedId && (edge.source === graphSelectedId || edge.target === graphSelectedId)) {
        path.setAttribute("class", "hot");
      }
      graphEls.edges.appendChild(path);
    }
  }

  function drawEdgeButtons() {
    for (var i = 0; i < graphEdgeButtons.length; i++) {
      if (graphEdgeButtons[i].parentNode) graphEdgeButtons[i].parentNode.removeChild(graphEdgeButtons[i]);
    }
    graphEdgeButtons = [];
    if (!graphData) return;
    var lanes = edgeLanes();
    for (var e = 0; e < graphData.edges.length; e++) {
      var edge = graphData.edges[e];
      var source = graphNodeById(edge.source);
      var target = graphNodeById(edge.target);
      if (!source || !target) continue;
      var an = edgeAnchors(gnodeBox(source), gnodeBox(target));
      // Skip the midpoint marker on short edges: there is no room between
      // the cards and the marker would overlap them (spacing discipline).
      var span = Math.hypot(an.x2 - an.x1, an.y2 - an.y1);
      if (span < 130) continue;
      var off = lanes[e] * 18;
      var btn = el("button", "edge-add", "+");
      btn.type = "button";
      btn.title = edge.source + " " + edge.kind + " " + edge.target + " (read-only viewer)";
      if (an.axis === "v") {
        btn.style.left = (an.x1 + an.x2) / 2 + off + "px";
        btn.style.top = (an.y1 + an.y2) / 2 + "px";
      } else {
        btn.style.left = (an.x1 + an.x2) / 2 + "px";
        btn.style.top = (an.y1 + an.y2) / 2 + off + "px";
      }
      btn.addEventListener("click", function (event) {
        event.stopPropagation();
        setStatus("read-only viewer — edges are derived from the store", false);
      });
      graphEls.canvas.appendChild(btn);
      graphEdgeButtons.push(btn);
    }
  }

  function graphLinksFor(nodeId) {
    var links = [];
    if (!graphData) return links;
    for (var i = 0; i < graphData.edges.length && links.length < 6; i++) {
      var edge = graphData.edges[i];
      var otherId = null;
      var label = null;
      if (edge.source === nodeId) { otherId = edge.target; label = "→ " + edge.target + " (" + edge.kind + ")"; }
      else if (edge.target === nodeId) { otherId = edge.source; label = "← " + edge.source + " (" + edge.kind + ")"; }
      if (otherId === null || links.some(function (l) { return l.otherId === otherId && l.label === label; })) continue;
      links.push({ otherId: otherId, label: label });
    }
    return links;
  }

  function gnodeElement(node) {
    var card = el("div", "gnode");
    card.setAttribute("data-node-id", node.id);
    card.appendChild(el("span", "gnode-tile", node.domain || "?"));
    var text = el("div", "gnode-text");
    text.appendChild(el("div", "gnode-eyebrow",
      (node.domain || "") + " · " + (node.kind || "leaf") + " · " + (node.lifecycle_status || "active")));
    text.appendChild(el("div", "gnode-title", node.label || node.id));
    card.appendChild(text);

    // Expanded body (foto-2): full summary content block + meta badges.
    var body = el("div", "gnode-body");
    var content = el("div", "gnode-content");
    content.appendChild(el("span", "gnode-content-tag", "summary"));
    content.appendChild(el("p", null, node.label || node.id));
    body.appendChild(content);
    var stats = el("div", "gnode-stats");
    stats.appendChild(el("span", "gnode-stat", node.kind || "leaf"));
    stats.appendChild(el("span", "gnode-stat", "P " + shortScore(node.scores.p)));
    stats.appendChild(el("span", "gnode-stat", "M " + shortScore(node.scores.m)));
    stats.appendChild(el("span", "gnode-stat", "E " + shortScore(node.scores.e)));
    stats.appendChild(el("span", "gnode-stat", "v" + node.version));
    body.appendChild(stats);
    var links = graphLinksFor(node.id);
    if (links.length) {
      body.appendChild(el("div", "gnode-links-title", "connected"));
      links.forEach(function (link) {
        var btn = el("button", "gnode-link", link.label);
        btn.type = "button";
        btn.addEventListener("click", function (event) {
          event.stopPropagation();
          selectNode(link.otherId);
        });
        body.appendChild(btn);
      });
    }
    card.appendChild(body);

    var box = { x: node.x - GNODE_W / 2, y: node.y - GNODE_H / 2 };
    card.style.left = box.x + "px";
    card.style.top = box.y + "px";
    card.addEventListener("click", function (event) {
      event.stopPropagation();
      selectNode(node.id);
    });
    return card;
  }

  function buildGraph() {
    var oldSelected = graphSelectedId;
    for (var id in graphNodeEls) {
      if (graphNodeEls[id].parentNode) graphNodeEls[id].parentNode.removeChild(graphNodeEls[id]);
    }
    graphNodeEls = {};
    if (!graphData) return;
    for (var i = 0; i < graphData.nodes.length; i++) {
      var card = gnodeElement(graphData.nodes[i]);
      graphNodeEls[graphData.nodes[i].id] = card;
      graphEls.canvas.appendChild(card);
    }
    if (oldSelected && graphNodeEls[oldSelected]) {
      var sel = graphNodeEls[oldSelected];
      sel.classList.add("expanded");
      sel.classList.add("selected");
      var selNode = graphNodeById(oldSelected);
      sel.style.left = selNode.x - (sel.offsetWidth || GNODE_W_EXP) / 2 + "px";
      resolveExpandedOverlap(oldSelected);
    }
    drawEdges();
    drawEdgeButtons();
  }

  function selectNode(id) {
    if (!graphNodeById(id)) { showDetail(id); return; }
    graphSelectedId = id;
    var node = graphNodeById(id);
    for (var nodeId in graphNodeEls) {
      graphNodeEls[nodeId].classList.toggle("expanded", nodeId === id);
      graphNodeEls[nodeId].classList.toggle("selected", nodeId === id);
    }
    // Grow around the node's center, then resolve any neighbour overlap.
    var card = graphNodeEls[id];
    card.style.left = node.x - (card.offsetWidth || GNODE_W_EXP) / 2 + "px";
    resolveExpandedOverlap(id);
    drawEdges(); // card height changed: re-bend the edges
    drawEdgeButtons();
    showDetail(id);
    ensureNodeVisible(id);
  }

  // The properties panel covers the right 340px of the canvas: on select,
  // refit the whole graph into the remaining visible area so no card —
  // selected or not — is clipped by the panel or the viewport.
  function ensureNodeVisible(id) {
    if (!graphData || !graphData.nodes.length) return;
    var w = graphEls.wrap.clientWidth || 800;
    var h = graphEls.wrap.clientHeight || 600;
    var availW = Math.max(200, w - 340 - 48);
    var availH = Math.max(200, h - 96);
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (var i = 0; i < graphData.nodes.length; i++) {
      var box = gnodeBox(graphData.nodes[i]);
      minX = Math.min(minX, box.x); minY = Math.min(minY, box.y);
      maxX = Math.max(maxX, box.x + box.w); maxY = Math.max(maxY, box.y + box.h);
    }
    var bw = maxX - minX, bh = maxY - minY;
    graphView.scale = Math.max(0.2, Math.min(graphView.scale, availW / bw, availH / bh, 1.25));
    graphView.x = 24 + (availW - bw * graphView.scale) / 2 - minX * graphView.scale;
    graphView.y = 48 + (availH - bh * graphView.scale) / 2 - minY * graphView.scale;
    applyGraphTransform();
  }

  function deselectGraph() {
    graphSelectedId = null;
    for (var nodeId in graphNodeEls) {
      graphNodeEls[nodeId].classList.remove("expanded");
      graphNodeEls[nodeId].classList.remove("selected");
      // Restore the centered collapsed position (expand may have shifted it).
      var node = graphNodeById(nodeId);
      if (node) {
        graphNodeEls[nodeId].style.left = node.x - GNODE_W / 2 + "px";
        graphNodeEls[nodeId].style.top = node.y - GNODE_H / 2 + "px";
      }
    }
    var panel = document.getElementById("detail-panel");
    panel.classList.remove("open");
    panel.classList.add("hidden");
    drawEdges();
    drawEdgeButtons();
    fitView(); // restore the full-canvas fit
  }

  function loadGraph() {
    return api("/api/graph").then(function (payload) {
      var hadData = graphData !== null;
      graphData = payload;
      var stats = document.getElementById("graph-stats");
      stats.textContent = payload.total_nodes + " nodes · " + payload.total_edges + " edges · layout " +
        payload.layout.mode + (payload.truncated ? " · TRUNCATED" : "");
      stats.className = "muted mono";
      buildGraph();
      if (!hadData) fitView();
      setStatus("");
    }).catch(function (error) {
      setStatus(String(error.message || error), true);
    });
  }

  function fitView() {
    if (!graphData || !graphData.nodes.length) return;
    var pad = 60;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (var i = 0; i < graphData.nodes.length; i++) {
      var box = gnodeBox(graphData.nodes[i]);
      minX = Math.min(minX, box.x); minY = Math.min(minY, box.y);
      maxX = Math.max(maxX, box.x + box.w); maxY = Math.max(maxY, box.y + box.h);
    }
    minX -= pad; minY -= pad; maxX += pad; maxY += pad;
    var w = graphEls.wrap.clientWidth || 800;
    var h = graphEls.wrap.clientHeight || 600;
    graphView.scale = Math.min(1.25, Math.max(0.2, Math.min(w / (maxX - minX), h / (maxY - minY))));
    graphView.x = (w - (maxX - minX) * graphView.scale) / 2 - minX * graphView.scale;
    graphView.y = (h - (maxY - minY) * graphView.scale) / 2 - minY * graphView.scale;
    applyGraphTransform();
  }

  function applyGraphTransform() {
    graphEls.canvas.style.transform =
      "translate(" + graphView.x + "px, " + graphView.y + "px) scale(" + graphView.scale + ")";
    document.getElementById("graph-zoom-label").textContent = Math.round(graphView.scale * 100) + "%";
  }

  // requestDraw is the quiet-refresh entry point: re-bend edges without
  // touching pan/zoom or selection.
  function requestDraw() {
    drawEdges();
    drawEdgeButtons();
  }

  function resizeCanvas() {
    // The DOM graph has no backing store to resize; edges re-derive on demand.
    requestDraw();
  }

  var graphDrag = null;
  var graphDragMoved = false;
  graphEls.wrap.addEventListener("mousedown", function (event) {
    if (event.target.closest && event.target.closest(".gnode, .edge-add, #detail-panel, .hud")) return;
    graphDrag = { x: event.clientX, y: event.clientY };
    graphDragMoved = false;
    graphEls.wrap.classList.add("dragging");
  });
  window.addEventListener("mousemove", function (event) {
    if (!graphDrag) return;
    var dx = event.clientX - graphDrag.x;
    var dy = event.clientY - graphDrag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) graphDragMoved = true;
    graphView.x += dx;
    graphView.y += dy;
    graphDrag = { x: event.clientX, y: event.clientY };
    applyGraphTransform();
  });
  window.addEventListener("mouseup", function () {
    graphDrag = null;
    graphEls.wrap.classList.remove("dragging");
  });
  graphEls.wrap.addEventListener("wheel", function (event) {
    event.preventDefault();
    var factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    var rect = graphEls.wrap.getBoundingClientRect();
    var cx = event.clientX - rect.left;
    var cy = event.clientY - rect.top;
    graphView.x = cx - (cx - graphView.x) * factor;
    graphView.y = cy - (cy - graphView.y) * factor;
    graphView.scale = Math.min(2.5, Math.max(0.15, graphView.scale * factor));
    applyGraphTransform();
  }, { passive: false });
  graphEls.wrap.addEventListener("click", function (event) {
    // Click on empty canvas (not a node, panel, HUD, or edge marker) collapses.
    if (graphDragMoved) { graphDragMoved = false; return; }
    if (event.target.closest && event.target.closest(".gnode, .edge-add, #detail-panel, .hud")) return;
    deselectGraph();
  });

  document.getElementById("graph-zoom-in").addEventListener("click", function () {
    graphView.scale = Math.min(2.5, graphView.scale + 0.15);
    applyGraphTransform();
  });
  document.getElementById("graph-zoom-out").addEventListener("click", function () {
    graphView.scale = Math.max(0.15, graphView.scale - 0.15);
    applyGraphTransform();
  });
  document.getElementById("graph-hud-fit").addEventListener("click", fitView);
  document.getElementById("graph-fit").addEventListener("click", fitView);
  document.getElementById("graph-reload").addEventListener("click", function () { loadGraph(); });
  window.addEventListener("resize", resizeCanvas);

  // ---------------------------------------------------------- detail panel

  function scoreBar(parent, label, raw) {
    var value = parseFloat(raw);
    var row = el("div", "scorebar");
    row.appendChild(el("span", "label", label));
    var bar = el("div", "bar");
    var fill = el("div", "fill");
    fill.style.width = (isNaN(value) ? 0 : Math.max(0, Math.min(1, value)) * 100) + "%";
    bar.appendChild(fill);
    row.appendChild(bar);
    row.appendChild(el("span", "value", isNaN(value) ? String(raw) : value.toFixed(3)));
    parent.appendChild(row);
  }

  function dpSection(body, title) {
    var section = el("div", "dp-section");
    section.appendChild(el("div", "dp-eyebrow", title));
    body.appendChild(section);
    return section;
  }

  function dpKv(parent, key, value) {
    var row = el("div", "dp-kv");
    row.appendChild(el("span", null, key));
    row.appendChild(el("span", null, value));
    parent.appendChild(row);
  }

  function showDetail(nodeId) {
    var panel = document.getElementById("detail-panel");
    panel.classList.remove("hidden");
    panel.classList.add("open");
    var title = document.getElementById("detail-title");
    var body = document.getElementById("detail-body");
    title.textContent = nodeId;
    clear(body);
    body.appendChild(el("p", "empty", "loading…"));
    api("/api/node/" + encodeURIComponent(nodeId)).then(function (node) {
      clear(body);
      title.textContent = node.summary || node.node_id;

      body.appendChild(el("div", "dp-eyebrow", node.domain + " · " + node.kind));
      body.appendChild(el("div", "dp-name", node.node_id));

      var general = dpSection(body, "General");
      dpKv(general, "node id", node.node_id);
      dpKv(general, "kind", node.kind);
      dpKv(general, "domain", node.domain);
      dpKv(general, "status", node.lifecycle_status);
      dpKv(general, "version", node.version);
      dpKv(general, "prof. level", node.professional_level);
      if (node.symbol) dpKv(general, "symbol", node.symbol.kind + " @ " + node.symbol.path + ":" + node.symbol.line);

      var scores = dpSection(body, "Scores (P / M / E)");
      scoreBar(scores, "P (proficiency)", node.scores.p);
      scoreBar(scores, "M (mastery)", node.scores.m);
      scoreBar(scores, "E (evidence)", node.scores.e);
      scoreBar(scores, "uncertainty", node.scores.uncertainty);
      scoreBar(scores, "confidence", node.scores.confidence);

      var provenance = dpSection(body, "Provenance");
      if (node.provenance.length === 0) {
        emptyNote(provenance, "(none)");
      } else {
        for (var i = 0; i < node.provenance.length; i++) {
          provenance.appendChild(el("span", "chip", node.provenance[i]));
        }
      }

      var relations = dpSection(body, "Relations");
      if (node.relations.length === 0) emptyNote(relations, "(none)");
      for (var r = 0; r < node.relations.length; r++) {
        relations.appendChild(relationButton(node.relations[r]));
      }

      var hierarchy = dpSection(body, "Hierarchy (callers / callees)");
      hierarchy.appendChild(el("p", "empty", "loading…"));
      api("/api/hierarchy/" + encodeURIComponent(node.node_id) + "?depth=1").then(function (hier) {
        clear(hierarchy);
        hierarchy.appendChild(hierarchyList("Callers", hier.callers.entries));
        hierarchy.appendChild(hierarchyList("Callees", hier.callees.entries));
      }).catch(function () {
        clear(hierarchy);
        hierarchy.appendChild(el("p", "empty", "(no call-graph entry for this node)"));
      });
    }).catch(function (error) {
      clear(body);
      body.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  }

  function relationButton(rel) {
    var btn = el("button", "linkish", rel.kind + " → " + rel.target);
    btn.type = "button";
    btn.addEventListener("click", function () { selectNode(rel.target); });
    return btn;
  }

  function hierarchyList(title, entries) {
    var box = el("div");
    box.appendChild(el("h3", "detail-h", title + " (" + entries.length + ")"));
    if (entries.length === 0) {
      box.appendChild(el("p", "empty", "(none)"));
      return box;
    }
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var label = "d" + entry.depth + " " + entry.name + " (" + entry.kind + ") via " + entry.via_edge_kind;
      var btn = el("button", "linkish", label);
      btn.type = "button";
      btn.setAttribute("data-node", entry.node_id);
      btn.addEventListener("click", function (event) {
        selectNode(event.currentTarget.getAttribute("data-node"));
      });
      box.appendChild(btn);
      box.appendChild(el("br"));
    }
    return box;
  }

  document.getElementById("detail-close").addEventListener("click", function () {
    deselectGraph();
  });

  // --------------------------------------------------------------- activity

  var ACTIVITY_CAP = 2000; // client-side ring buffer bound
  var GROUP_WINDOW_MS = 5000;
  var activity = {
    entries: [],        // ascending by seq, bounded at ACTIVITY_CAP
    lastSeq: 0,
    agents: [],         // sorted unique agent names (agent #N = index + 1)
    userScrolledUp: false,
    filter: "",
    levels: { info: true, warn: true, error: true },
    agent: "",
    group: true
  };

  function activityEls() {
    return {
      list: document.getElementById("activity-list"),
      resume: document.getElementById("activity-resume"),
      count: document.getElementById("activity-count"),
      search: document.getElementById("activity-search"),
      agent: document.getElementById("activity-agent"),
      group: document.getElementById("activity-group"),
      autoscroll: document.getElementById("activity-autoscroll")
    };
  }

  function agentLabel(agent) {
    var index = activity.agents.indexOf(agent);
    return "agent #" + (index >= 0 ? index + 1 : "?");
  }

  function activityLoad() {
    api("/api/activity?limit=200").then(function (payload) {
      activity.entries = payload.entries.slice().reverse(); // DESC -> ascending
      activity.lastSeq = activity.entries.length ? activity.entries[activity.entries.length - 1].seq : 0;
      activity.agents = payload.agents || [];
      rebuildAgentSelect();
      renderActivity();
      refreshLeases();
      setStatus("");
    }).catch(function (error) {
      setStatus(String(error.message || error), true);
    });
  }

  function activityPoll() {
    refreshLeases();
    api("/api/activity/tail?since=" + activity.lastSeq + "&limit=200").then(function (payload) {
      if (!payload.entries.length) return;
      for (var i = 0; i < payload.entries.length; i++) {
        activity.entries.push(payload.entries[i]);
        if (activity.agents.indexOf(payload.entries[i].agent) === -1) {
          activity.agents.push(payload.entries[i].agent);
          activity.agents.sort();
          rebuildAgentSelect();
        }
      }
      activity.lastSeq = payload.entries[payload.entries.length - 1].seq;
      // Ring buffer: drop the oldest beyond the cap.
      if (activity.entries.length > ACTIVITY_CAP) {
        activity.entries.splice(0, activity.entries.length - ACTIVITY_CAP);
      }
      renderActivity();
    }).catch(function () { /* transient poll failure: next tick retries */ });
  }

  function rebuildAgentSelect() {
    var select = activityEls().agent;
    var current = select.value;
    clear(select);
    select.appendChild(el("option", null, "all agents"));
    select.firstChild.value = "";
    for (var i = 0; i < activity.agents.length; i++) {
      var opt = el("option", null, activity.agents[i]);
      opt.value = activity.agents[i];
      select.appendChild(opt);
    }
    select.value = current;
  }

  function entryMatches(entry) {
    if (!activity.levels[entry.level]) return false;
    if (activity.agent && entry.agent !== activity.agent) return false;
    if (activity.filter) {
      var hay = (entry.agent + " " + entry.action + " " + entry.description + " " +
        entry.nodes.join(" ") + " " + entry.sha8).toLowerCase();
      if (hay.indexOf(activity.filter.toLowerCase()) === -1) return false;
    }
    return true;
  }

  function groupEntries(entries) {
    // Burst grouping: consecutive same agent+action within the window collapse
    // into one row with a ×N badge (anti-spam; the raw rows remain in the buffer).
    if (!activity.group) return entries.map(function (e) { return { entry: e, count: 1 }; });
    var groups = [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var last = groups.length ? groups[groups.length - 1] : null;
      if (last && last.entry.agent === entry.agent && last.entry.action === entry.action &&
          Math.abs(Date.parse(entry.occurred_at) - Date.parse(last.entry.occurred_at)) <= GROUP_WINDOW_MS) {
        last.count += 1;
        last.entry = entry; // show the newest of the burst
      } else {
        groups.push({ entry: entry, count: 1 });
      }
    }
    return groups;
  }

  function renderActivity() {
    var els = activityEls();
    var list = els.list;
    var visible = [];
    for (var i = 0; i < activity.entries.length; i++) {
      if (entryMatches(activity.entries[i])) visible.push(activity.entries[i]);
    }
    clear(list);
    var groups = groupEntries(visible);
    for (var g = 0; g < groups.length; g++) {
      var entry = groups[g].entry;
      var row = el("div", "activity-row lvl-" + entry.level);
      row.appendChild(el("span", "agent", agentLabel(entry.agent)));
      row.appendChild(el("span", "action", entry.action));
      row.appendChild(el("span", "desc", entry.description));
      if (entry.nodes.length) row.appendChild(el("span", "nodes", entry.nodes.join(" ")));
      if (groups[g].count > 1) row.appendChild(el("span", "burst", "×" + groups[g].count));
      row.appendChild(el("span", "sha", entry.sha8));
      list.appendChild(row);
    }
    els.count.textContent = visible.length + " shown · " + activity.entries.length + " buffered";
    maybeAutoscroll();
  }

  function maybeAutoscroll() {
    var els = activityEls();
    if (!els.autoscroll.checked || activity.userScrolledUp) {
      els.resume.classList.remove("hidden");
      return;
    }
    els.resume.classList.add("hidden");
    els.list.scrollTop = els.list.scrollHeight;
  }

  function refreshLeases() {
    api("/api/leases").then(function (payload) {
      var body = document.getElementById("leases-body");
      clear(body);
      if (!payload.count) {
        emptyNote(body, "(no active leases — every node is free)");
        return;
      }
      for (var i = 0; i < payload.leases.length; i++) {
        var lease = payload.leases[i];
        var row = el("div", "lease-row");
        row.appendChild(el("span", "node", lease.node_id));
        row.appendChild(el("span", null, "leased to"));
        row.appendChild(el("span", "holder", lease.actor));
        row.appendChild(el("span", null, "until"));
        row.appendChild(el("span", "expiry", lease.expires_at));
        body.appendChild(row);
      }
    }).catch(function () { /* leases panel is advisory */ });
  }

  (function wireActivityControls() {
    var els = activityEls();
    els.search.addEventListener("input", function () {
      activity.filter = els.search.value;
      renderActivity();
    });
    ["info", "warn", "error"].forEach(function (level) {
      document.getElementById("activity-level-" + level).addEventListener("change", function (event) {
        activity.levels[level] = event.target.checked;
        renderActivity();
      });
    });
    els.agent.addEventListener("change", function () {
      activity.agent = els.agent.value;
      renderActivity();
    });
    els.group.addEventListener("change", function () {
      activity.group = els.group.checked;
      renderActivity();
    });
    els.autoscroll.addEventListener("change", function () {
      activity.userScrolledUp = !els.autoscroll.checked;
      maybeAutoscroll();
    });
    els.list.addEventListener("scroll", function () {
      var nearBottom = els.list.scrollHeight - els.list.scrollTop - els.list.clientHeight < 40;
      activity.userScrolledUp = !nearBottom;
      if (nearBottom) els.resume.classList.add("hidden");
    });
    els.resume.addEventListener("click", function () {
      activity.userScrolledUp = false;
      els.autoscroll.checked = true;
      maybeAutoscroll();
    });
    document.getElementById("activity-clear").addEventListener("click", function () {
      activity.entries = [];
      renderActivity();
    });
  })();

  // ----------------------------------------------------------------- search

  document.getElementById("search-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var q = document.getElementById("search-q").value;
    var kind = document.getElementById("search-kind").value.trim();
    var lang = document.getElementById("search-lang").value.trim();
    var path = "/api/search?q=" + encodeURIComponent(q);
    if (kind) path += "&kind=" + encodeURIComponent(kind);
    if (lang) path += "&lang=" + encodeURIComponent(lang);
    var results = document.getElementById("search-results");
    clear(results);
    results.appendChild(el("p", "empty", "searching…"));
    api(path).then(function (payload) {
      clear(results);
      if (payload.count === 0) {
        results.appendChild(el("p", "empty", "no matches"));
        return;
      }
      for (var i = 0; i < payload.results.length; i++) {
        results.appendChild(searchHit(payload.results[i]));
      }
    }).catch(function (error) {
      clear(results);
      results.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  });

  function searchHit(hit) {
    var box = el("div", "hit");
    box.appendChild(el("span", "score", typeof hit.score === "number" ? hit.score.toFixed(3) : ""));
    box.appendChild(el("div", "name", hit.name));
    box.appendChild(el("div", "meta",
      hit.kind + " · " + hit.path + ":" + hit.line + " · via " + hit.source));
    box.addEventListener("click", function () {
      showTab("graph");
      selectNode(hit.node_id);
    });
    return box;
  }

  // ------------------------------------------------------------------ browse

  var browse = { offset: 0, limit: 50, filter: "" };

  function browseColumns(tableName) {
    if (tableName === "nodes") return ["id", "kind", "domain", "version", "summary"];
    if (tableName === "edges") return ["source", "relation", "target"];
    if (tableName === "graph/edges") return ["source", "kind", "target", "path", "line"];
    if (tableName === "symbols") return ["name", "kind", "path", "line", "node_id"];
    return ["source_node_id", "target_name", "kind", "status"];
  }

  function browseRow(tableName, row) {
    if (tableName === "nodes") return [row.id, row.kind, row.domain, row.version, row.label || ""];
    if (tableName === "edges") return [row.source, row.relation || row.kind, row.target];
    if (tableName === "graph/edges") return [row.source, row.kind, row.target, row.path || "", row.line || ""];
    if (tableName === "symbols") return [row.name, row.kind, row.path, row.line, row.node_id];
    return [row.source_node_id, row.target_name, row.kind, row.status || ""];
  }

  function loadBrowse() {
    var tableName = document.getElementById("browse-table").value;
    var body = document.getElementById("browse-body");
    clear(body);
    body.appendChild(el("p", "empty", "loading…"));
    api("/api/" + tableName + "?offset=" + browse.offset + "&limit=" + browse.limit).then(function (payload) {
      clear(body);
      var rows = payload.nodes || payload.edges || payload.symbols || payload.refs || payload.items || [];
      var filter = browse.filter.toLowerCase();
      var rendered = [];
      for (var i = 0; i < rows.length; i++) {
        var cells = browseRow(tableName, rows[i]);
        if (filter && cells.join(" ").toLowerCase().indexOf(filter) === -1) continue;
        if (tableName === "nodes") {
          // Node ids open the same properties panel as a graph click.
          var link = el("button", "linkish", String(cells[0]));
          link.type = "button";
          (function (nodeId, button) {
            button.addEventListener("click", function () { showTab("graph"); selectNode(nodeId); });
          })(rows[i].id, link);
          cells[0] = link;
        }
        rendered.push(cells);
      }
      document.getElementById("browse-page").textContent =
        "offset " + browse.offset + " · " + rendered.length + " rows" +
        (payload.total !== undefined ? " · total " + payload.total : "");
      if (!rendered.length) {
        emptyNote(body, "(no rows)");
        return;
      }
      table(body, browseColumns(tableName), rendered);
    }).catch(function (error) {
      clear(body);
      body.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  }

  document.getElementById("browse-table").addEventListener("change", function () {
    browse.offset = 0;
    loadBrowse();
  });
  document.getElementById("browse-filter").addEventListener("input", function (event) {
    browse.filter = event.target.value;
    loadBrowse();
  });
  document.getElementById("browse-prev").addEventListener("click", function () {
    browse.offset = Math.max(0, browse.offset - browse.limit);
    loadBrowse();
  });
  document.getElementById("browse-next").addEventListener("click", function () {
    browse.offset += browse.limit;
    loadBrowse();
  });

  // ---------------------------------------------------- json panel renderers

  function renderMetrics(container, payload) {
    cardGrid(container, [
      ["health score", payload.health.score + " / " + payload.health.max_score],
      ["publish ok", String(payload.publish_capacity.ok)],
      ["headroom bytes", String(payload.publish_capacity.headroom_bytes)]
    ]);
    var cols = el("div", "cols-2");
    var countRows = [];
    var keys = Object.keys(payload.counts).sort();
    for (var i = 0; i < keys.length; i++) countRows.push([keys[i], payload.counts[keys[i]]]);
    var countsCol = el("div");
    countsCol.appendChild(el("h3", "panel-title", "Counts"));
    table(countsCol, ["metric", "value"], countRows);
    cols.appendChild(countsCol);
    var cap = payload.publish_capacity;
    var capCol = el("div");
    capCol.appendChild(el("h3", "panel-title", "Publish capacity"));
    table(capCol, ["field", "value"], [
      ["estimated_bytes", cap.estimated_bytes],
      ["guard_bytes", cap.guard_bytes],
      ["headroom_bytes", cap.headroom_bytes],
      ["utilization_permille", cap.utilization_permille],
      ["ok", cap.ok]
    ]);
    cols.appendChild(capCol);
    container.appendChild(cols);
    capCol.appendChild(el("h3", "panel-title", "Latency (ms)"));
    var ops = Object.keys(payload.latency_ms).sort();
    if (ops.length === 0) {
      emptyNote(capCol, "(no completed invocations recorded)");
    } else {
      var rows = [];
      for (var j = 0; j < ops.length; j++) {
        var s = payload.latency_ms[ops[j]];
        rows.push([ops[j], s.count, s.min, s.mean, s.p50, s.p95, s.max]);
      }
      table(capCol, ["operation", "count", "min", "mean", "p50", "p95", "max"], rows);
    }
  }

  function renderHealth(container, payload) {
    var box = el("p");
    box.appendChild(badge(payload.ok ? "ok" : "error",
      payload.ok ? "OK — no error findings" : "ERROR findings present (exit " + payload.exit_code + ")"));
    container.appendChild(box);
    container.appendChild(el("p", null,
      "errors: " + payload.error_count + " · warnings: " + payload.warning_count +
      " · worst: " + payload.worst_severity));
    if (payload.findings.length === 0) {
      emptyNote(container, "(no findings)");
      return;
    }
    var rows = [];
    for (var i = 0; i < payload.findings.length; i++) {
      var f = payload.findings[i];
      rows.push([f.severity, f.code, f.message]);
    }
    table(container, ["severity", "code", "message"], rows);
  }

  function renderAnalytics(container, payload) {
    if (payload.partial) {
      container.appendChild(el("p", "bad-badge",
        "PARTIAL: graph exceeds the analytics cap; computed on the first " + payload.node_count +
        " of " + payload.total_nodes + " nodes"));
    }
    var pr = (payload.centrality.global && payload.centrality.global.pagerank) || [];
    var cols = el("div", "cols-2");
    var centCol = el("div");
    centCol.appendChild(el("h3", "panel-title", "Centrality — top by PageRank"));
    table(centCol, ["node", "pagerank"],
      pr.slice(0, 15).map(function (entry) { return [entry.node_id, entry.score]; }));
    cols.appendChild(centCol);
    var comms = payload.communities.communities || [];
    var commCol = el("div");
    commCol.appendChild(el("h3", "panel-title", "Communities (" + payload.communities.community_count + ")"));
    if (comms.length === 0) emptyNote(commCol, "(none)");
    table(commCol, ["id", "size", "label", "cohesion", "signature"],
      comms.map(function (c) { return [c.id, c.size, c.label, c.cohesion, c.membership_sig]; }));
    cols.appendChild(commCol);
    container.appendChild(cols);
    container.appendChild(el("h3", "panel-title", "Bridges (surprising connections)"));
    var bridges = payload.bridges.bridges || [];
    if (bridges.length === 0) emptyNote(container, "(none)");
    table(container, ["source", "target", "relation", "kind", "surprise"],
      bridges.map(function (b) { return [b.source, b.target, b.relation, b.kind, b.surprise_score]; }));
  }

  function renderAudit(container, payload) {
    container.appendChild(el("p", null, payload.count + " most recent audit events (redacted)"));
    if (payload.count === 0) {
      emptyNote(container, "(no audit events)");
    } else {
      var rows = payload.events.map(function (event) {
        return [nw(String(event.event_seq)), nw(event.event_type), nw(event.actor), nw(event.occurred_at),
          JSON.stringify(event.payload)];
      });
      var tbl = table(container, ["seq", "type", "actor", "at", "payload"], rows);
      // State rows: failures tint red, conflicts amber, with a left border.
      var trs = tbl.querySelectorAll("tr");
      for (var i = 1; i < trs.length; i++) {
        var typeCell = trs[i].children[1];
        if (!typeCell) continue;
        if (/failed/.test(typeCell.textContent)) trs[i].className = "row-error";
        else if (/conflict/.test(typeCell.textContent)) trs[i].className = "row-warn";
      }
    }
    var refsBox = el("div");
    refsBox.appendChild(el("p", "empty", "loading refs…"));
    container.appendChild(refsBox);
    api("/api/refs").then(function (refs) {
      clear(refsBox);
      renderRefs(refsBox, refs);
    }).catch(function () {
      clear(refsBox);
    });
  }

  function renderRefs(container, payload) {
    var cols = el("div", "cols-2");
    var left = el("div");
    left.appendChild(el("h3", "panel-title", "Unresolved references"));
    table(left, ["status", "count"], [
      ["pending", payload.pending],
      ["resolved", payload.resolved],
      ["failed", payload.failed],
      ["total", payload.total]
    ]);
    left.appendChild(el("h3", "panel-title", "Top failed name tails"));
    if (payload.top_failed.length === 0) emptyNote(left, "(none)");
    table(left, ["name_tail", "count"],
      payload.top_failed.map(function (row) { return [row.name_tail, row.count]; }));
    var right = el("div");
    right.appendChild(el("h3", "panel-title", "Failed samples"));
    if (payload.failed_samples.length === 0) emptyNote(right, "(none)");
    table(right, ["source", "target", "kind", "reason"],
      payload.failed_samples.map(function (row) {
        return [row.source_node_id, row.target_name, row.kind, row.failure_reason || ""];
      }));
    cols.appendChild(left);
    cols.appendChild(right);
    container.appendChild(cols);
  }

  function renderProposals(container, payload) {
    container.appendChild(el("p", null,
      payload.count + " review candidates (proposal-only; nothing here mutates the store)"));
    if (payload.count === 0) {
      // Skeleton header keeps the table's shape visible in the empty state.
      table(container, ["signal", "reasons", "nodes", "level"], []);
      emptyNote(container, "(no candidates)");
      return;
    }
    var rows = payload.signals.map(function (signal) {
      return [signal.signal, (signal.reason_codes || []).join(", "),
        (signal.source_node_ids || []).join(", "),
        signal.low_confidence ? "hint" : "primary"];
    });
    table(container, ["signal", "reasons", "nodes", "level"], rows);
  }

  function renderDebt(container, payload) {
    if (payload.disabled) {
      emptyNote(container, "debt queue disabled: " + (payload.reason || ""));
      return;
    }
    container.appendChild(el("p", null,
      payload.candidate_count + " debt candidates (budget " + payload.budget +
      ", overflow " + payload.overflow_count + ") — proposals only; nothing here mutates the store"));
    if (!payload.items || payload.items.length === 0) {
      table(container, ["rank", "node", "action", "priority", "signals", "merge target"], []);
      emptyNote(container, "(no debt candidates)");
      return;
    }
    var rows = payload.items.map(function (item) {
      return [item.rank, item.node_id, item.action, item.priority,
        (item.signals || []).join(", "), item.merge_target || ""];
    });
    table(container, ["rank", "node", "action", "priority", "signals", "merge target"], rows);
  }

  function renderExtractions(container, payload) {
    container.appendChild(el("p", null,
      payload.count + " queued submissions (queue size " + payload.queue_size + ")"));
    if (!payload.rows || payload.rows.length === 0) {
      table(container, ["receipt", "status", "actor", "agent", "size", "confidence", "corroboration"], []);
      emptyNote(container, "(queue empty)");
      return;
    }
    var rows = payload.rows.map(function (row) {
      return [row.receipt_id, row.status, row.actor, row.agent,
        row.nodes + " nodes / " + row.edges + " edges",
        row.confidence_reported + " → " + row.confidence_effective,
        "+" + row.corroborators + " / -" + row.contradictions];
    });
    table(container, ["receipt", "status", "actor", "agent", "size", "confidence", "corroboration"], rows);
  }

  function renderStore(container) {
    var statsBox = document.getElementById("store-stats");
    var tablesBox = document.getElementById("store-tables");
    var schemaBox = document.getElementById("store-schema");
    clear(statsBox); clear(tablesBox); clear(schemaBox);
    api("/api/store/stats").then(function (stats) {
      var pairs = [
        ["store schema", "v" + stats.store_schema_version],
        ["file size", Math.round(stats.file_size_bytes / 1024) + " KiB"],
        ["pages", String(stats.page_count)],
        ["journal mode", stats.journal_mode]
      ];
      var tables = stats.tables || {};
      Object.keys(tables).sort().slice(0, 8).forEach(function (name) {
        var count = Number(tables[name]);
        pairs.push([name, String(tables[name]) + (count === 1 ? " row" : " rows")]);
      });
      cardGrid(statsBox, pairs);
    }).catch(function (error) {
      statsBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
    api("/api/store/tables").then(function (payload) {
      var rows = (payload.tables || []).map(function (t) {
        return [t.name, t.rows];
      });
      table(tablesBox, ["table", "rows"], rows);
    }).catch(function (error) {
      tablesBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
    api("/api/store/schema").then(function (schema) {
      var rows = (schema.migrations || []).map(function (migration) {
        return ["v" + migration.version, migration.name, migration.description || ""];
      });
      schemaBox.appendChild(el("p", "muted", "recorded: v" + schema.recorded +
        " · versions " + (schema.schema_versions || []).join(", ")));
      table(schemaBox, ["version", "name", "description"], rows);
    }).catch(function (error) {
      schemaBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  }

  function renderSystem() {
    var hardwareBox = document.getElementById("system-hardware");
    var relearnBox = document.getElementById("system-relearn");
    var switchesBox = document.getElementById("system-switches");
    var configBox = document.getElementById("system-config");
    var routesBox = document.getElementById("system-routes");
    clear(hardwareBox); clear(relearnBox); clear(switchesBox); clear(configBox); clear(routesBox);

    api("/api/hardware").then(function (payload) {
      if (payload.available === false) {
        hardwareBox.appendChild(el("p", "bad-badge", "probe failed: " + payload.error.message));
        return;
      }
      var profile = payload.profile;
      cardGrid(hardwareBox, [
        ["cores", String(profile.cpu_count)],
        ["memory", Math.round(profile.memory.total_bytes / (1024 * 1024)) + " MiB"],
        ["disk", profile.disk.kind + (profile.disk.rotational ? " (rotational)" : "")],
        ["gpu", profile.gpu.kind],
        ["base workers", String(payload.plan.base_workers)],
        ["queue scale", String(payload.plan.queue_scale)],
        ["io buffer", Math.round(payload.plan.io_buffer_bytes / 1024) + " KiB"],
        ["query cache", String(payload.plan.query_cache_entries)]
      ]);
    }).catch(function (error) {
      hardwareBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });

    api("/api/relearn").then(function (payload) {
      var box = el("p");
      box.appendChild(badge(payload.state ? "warn" : "ok", payload.state || "CLEAR"));
      relearnBox.appendChild(box);
      table(relearnBox, ["field", "value"], [
        ["state", payload.state || "CLEAR"],
        ["pending offer", payload.pending_offer ? payload.pending_offer.offer_id : "(none)"],
        ["active source version", payload.active_source_version_id || "(none)"],
        ["quarantined evidence", String((payload.quarantined_evidence_ids || []).length)]
      ]);
    }).catch(function (error) {
      relearnBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });

    api("/api/switches").then(function (payload) {
      var rows = (payload.switches || []).map(function (sw) {
        return [sw.name, sw.state, sw.affects || ""];
      });
      table(switchesBox, ["switch", "state", "affects"], rows, "dense");
    }).catch(function (error) {
      switchesBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });

    api("/api/config").then(function (payload) {
      var knobs = payload.knobs || [];
      var rows = knobs.map(function (knob) {
        return [nw(knob.name), nw(String(knob.value))];
      });
      table(configBox, ["knob", "value"], rows, "dense");
    }).catch(function (error) {
      configBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });

    api("/api/routes").then(function (payload) {
      var rows = (payload.routes || []).map(function (route) {
        return [nw(route.method), nw(route.path), nw(route.route_class), route.summary];
      });
      table(routesBox, ["method", "path", "class", "summary"], rows, "dense");
    }).catch(function (error) {
      routesBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  }

  function renderBench(container, payload) {
    var ops = payload.operations || [];
    if (!ops.length) {
      emptyNote(container, "(no completed invocations recorded yet)");
      return;
    }
    var rows = ops.map(function (op) {
      return [op.operation, op.count, op.min_ms, op.mean_ms, op.p50_ms, op.p95_ms, op.max_ms];
    });
    table(container, ["operation", "count", "min", "mean", "p50", "p95", "max"], rows);
    if (payload.malformed_outcomes) {
      container.appendChild(el("p", "muted", payload.malformed_outcomes + " malformed outcomes skipped"));
    }
  }

  function ledgerTable(box, entries, emptyText) {
    // Generic hash-chain entry rendering: known fields first, the payload as JSON.
    if (!entries.length) {
      table(box, ["seq", "recorded at", "hash", "payload"], []); // skeleton header
      emptyNote(box, emptyText);
      return;
    }
    var rows = entries.map(function (entry) {
      return [
        entry.seq !== undefined ? entry.seq : "",
        entry.recorded_at || entry.occurred_at || "",
        entry.hash || entry.entry_hash ? String(entry.hash || entry.entry_hash).slice(0, 12) : "",
        JSON.stringify(entry.payload || entry).slice(0, 160)
      ];
    });
    table(box, ["seq", "recorded at", "hash", "payload"], rows);
  }

  function renderLedgers() {
    var healthBox = document.getElementById("ledgers-health");
    var debtBox = document.getElementById("ledgers-debt");
    var experienceBox = document.getElementById("ledgers-experience");
    clear(healthBox); clear(debtBox); clear(experienceBox);

    api("/api/ledger/health").then(function (payload) {
      var rows = (payload.snapshots || []).map(function (snap) {
        return [snap.recorded_at, snap.score + " / " + snap.max_score, snap.event_count,
          String(snap.hash || "").slice(0, 12)];
      });
      if (!rows.length) {
        table(healthBox, ["recorded at", "score", "events", "hash"], []); // skeleton header
        emptyNote(healthBox, "(no health history recorded — state: " + payload.state + ")");
      } else {
        table(healthBox, ["recorded at", "score", "events", "hash"], rows);
      }
    }).catch(function (error) {
      healthBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });

    api("/api/ledger/debt").then(function (payload) {
      ledgerTable(debtBox, payload.entries || [], "(no debt ledger entries — state: " + payload.state + ")");
    }).catch(function (error) {
      debtBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });

    api("/api/ledger/experience").then(function (payload) {
      var verification = payload.verification || {};
      var box = el("p");
      var verified = verification.ok && verification.checked > 0;
      box.appendChild(badge(verified ? "ok" : "dim",
        verified ? "journal verified (" + verification.checked + " checked)" :
          verification.ok ? "journal empty (0 checked)" :
          "verification FAILED at seq " + verification.first_bad_seq));
      box.appendChild(el("span", "muted", " · state: " + payload.state));
      experienceBox.appendChild(box);
      ledgerTable(experienceBox, payload.entries || [], "(no rerank journal entries)");
    }).catch(function (error) {
      experienceBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  }

  function renderContracts(container, payload) {
    var box = el("p");
    box.appendChild(badge("info", payload.frozen ? "frozen surface" : "live surface"));
    container.appendChild(box);
    container.appendChild(el("h3", "panel-title",
      "Knowledge operations (" + payload.knowledge_operations.length + ")"));
    for (var i = 0; i < payload.knowledge_operations.length; i++) {
      container.appendChild(el("span", "chip", payload.knowledge_operations[i]));
    }
    container.appendChild(el("h3", "panel-title", "Ops operations (" + payload.ops_operations.length + ")"));
    for (var j = 0; j < payload.ops_operations.length; j++) {
      container.appendChild(el("span", "chip", payload.ops_operations[j]));
    }
    container.appendChild(el("h3", "panel-title", "Contract rules"));
    for (var n = 0; n < payload.notes.length; n++) {
      container.appendChild(el("p", "muted", payload.notes[n]));
    }
  }

  function renderSetup() {
    var progressBox = document.getElementById("setup-progress");
    var prefsBox = document.getElementById("setup-prefs");
    api("/api/setup").then(function (payload) {
      clear(prefsBox);
      clear(progressBox);
      var progress = payload.progress;
      var box = el("p");
      var level = progress.state === "done" ? "ok" : progress.state === "failed" ? "error" : "warn";
      box.appendChild(badge(level, progress.state));
      if (progress.updated_at) box.appendChild(el("span", "muted", " · updated " + progress.updated_at));
      progressBox.appendChild(box);
      if (!progress.steps || !progress.steps.length) {
        var note = el("p", "empty", "(no install recorded — run: ");
        note.appendChild(el("code", null, "know-your-context setup"));
        note.appendChild(document.createTextNode(")"));
        progressBox.appendChild(note);
      } else {
        for (var i = 0; i < progress.steps.length; i++) {
          var step = progress.steps[i];
          var row = el("div", "step-row");
          row.appendChild(el("span", "name", step.name));
          row.appendChild(badge(step.status === "done" ? "ok" : step.status === "failed" ? "error" :
            step.status === "skipped" ? "dim" : "warn", step.status));
          if (step.detail) row.appendChild(el("span", "detail", step.detail));
          progressBox.appendChild(row);
        }
      }
      var prefs = payload.prefs;
      table(prefsBox, ["field", "value"], [
        ["enabled", prefs.enabled === null ? "(unset — default off)" : String(prefs.enabled)],
        ["port", prefs.port === null ? "(auto)" : String(prefs.port)],
        ["updated at", prefs.updated_at || "(never)"]
      ]);
      var hint = el("p", "muted", "Toggle with: ");
      hint.appendChild(el("code", null, "know-your-context webview on | off | status"));
      prefsBox.appendChild(hint);
    }).catch(function (error) {
      clear(progressBox);
      progressBox.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  }

  // ------------------------------------------------------------------ exports

  document.getElementById("export-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var nodeId = document.getElementById("export-node").value.trim();
    var domain = document.getElementById("export-domain").value.trim();
    var kind = document.getElementById("export-kind").value;
    var body = document.getElementById("exports-body");
    clear(body);
    var path;
    if (kind === "node") {
      if (!nodeId) {
        body.appendChild(el("p", "bad-badge", "node export needs a node id"));
        return;
      }
      path = "/api/export/node/" + encodeURIComponent(nodeId);
    } else {
      if (!nodeId && !domain) {
        body.appendChild(el("p", "bad-badge", "subgraph export needs a node id or a domain"));
        return;
      }
      path = "/api/export/subgraph?";
      if (nodeId) path += "node_id=" + encodeURIComponent(nodeId);
      if (domain) path += (nodeId ? "&" : "") + "domain=" + encodeURIComponent(domain);
    }
    body.appendChild(el("p", "empty", "exporting…"));
    api(path).then(function (payload) {
      clear(body);
      var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      var link = el("a", null, "download " + kind + " export (JSON)");
      link.href = URL.createObjectURL(blob);
      link.download = "kyc-" + kind + "-export.json";
      body.appendChild(link);
      var nodeCount = payload.node_count !== undefined ? payload.node_count :
        (payload.nodes ? payload.nodes.length : "?");
      body.appendChild(el("p", "muted mono", "export built: " + nodeCount + " nodes"));
    }).catch(function (error) {
      clear(body);
      body.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  });

  // ------------------------------------------------------------- code intel

  function renderCodeintel(container, op, payload) {
    if (payload.kind && payload.kind.indexOf("analytics.code_intel.") === 0) {
      container.appendChild(el("p", null, payload.kind + " — nodes " + payload.node_count +
        ", edges " + payload.edge_count + (payload.guards && payload.guards.length ?
        " — GUARDS: " + payload.guards.join(", ") : "")));
    }
    if (op === "callers" || op === "callees" || op === "impact") {
      if (payload.root) {
        container.appendChild(el("p", null, "root: " + payload.root.name + " (" + payload.root.kind +
          ") — " + payload.root.path + ":" + payload.root.line + " — total " + payload.total));
      }
      if (payload.impacted_files) {
        container.appendChild(el("p", null, "impacted files (" + payload.impacted_file_count + "): " +
          payload.impacted_files.join(", ")));
      }
      var rows = (payload.entries || []).map(function (entry) {
        return ["d" + entry.depth, entry.name, entry.kind, entry.path + ":" + entry.line,
          entry.via_edge_kind + (entry.via_line ? " @ " + entry.via_line : "")];
      });
      if (rows.length === 0) {
        emptyNote(container, op === "callers" ? "(no callers)" : op === "callees" ? "(no callees)" : "(no impacted nodes)");
        return;
      }
      table(container, ["depth", "name", "kind", "location", "via"], rows);
      return;
    }
    if (op === "path") {
      if (!payload.paths || payload.paths.length === 0) {
        emptyNote(container, "(no dependency path)");
        return;
      }
      payload.paths.forEach(function (p) {
        container.appendChild(el("p", null, "cost " + p.cost + ": " + p.node_ids.join(" → ")));
      });
      return;
    }
    if (op === "co-change") {
      container.appendChild(el("p", null, payload.change_set_count + " change sets, " +
        payload.total_pairs + " pairs"));
      var pairs = (payload.pairs || []).map(function (pair) {
        return [pair.support, pair.file_a, pair.file_b, pair.confidence_a_to_b, pair.confidence_b_to_a];
      });
      if (pairs.length === 0) {
        emptyNote(container, "(no co-change pairs above the support threshold)");
        return;
      }
      table(container, ["support", "file a", "file b", "conf a→b", "conf b→a"], pairs);
      return;
    }
    if (op === "boundary") {
      container.appendChild(el("p", null, payload.total_violations + " violations, " +
        payload.unlayered_nodes + " unlayered nodes, " + payload.skipped_unlayered_edges +
        " skipped edges"));
      var violations = (payload.violations || []).map(function (violation) {
        return [violation.from_layer + " → " + violation.to_layer, violation.edge_kind,
          violation.source_path, violation.target_path];
      });
      if (violations.length === 0) {
        emptyNote(container, "(no boundary violations)");
        return;
      }
      table(container, ["flow", "kind", "source", "target"], violations);
    }
  }

  function runCodeintel(event) {
    if (event) event.preventDefault();
    var op = document.getElementById("codeintel-op").value;
    var symbol = document.getElementById("codeintel-symbol").value.trim();
    var target = document.getElementById("codeintel-target").value.trim();
    var layers = document.getElementById("codeintel-layers").value.trim();
    var container = document.getElementById("codeintel-body");
    var path = "/api/codeintel?op=" + encodeURIComponent(op);
    if (symbol) path += "&symbol=" + encodeURIComponent(symbol);
    if (op === "path") {
      path += "&source=" + encodeURIComponent(symbol) + "&target=" + encodeURIComponent(target);
    }
    if (op === "boundary" && layers) path += "&layers=" + encodeURIComponent(layers);
    clear(container);
    container.appendChild(el("p", "empty", "loading…"));
    api(path).then(function (payload) {
      clear(container);
      renderCodeintel(container, op, payload);
      setStatus("");
    }).catch(function (error) {
      clear(container);
      container.appendChild(el("p", "bad-badge", String(error.message || error)));
      setStatus(String(error.message || error), true);
    });
  }

  document.getElementById("codeintel-form").addEventListener("submit", runCodeintel);

  // ------------------------------------------------------------------- audit

  document.getElementById("audit-verify").addEventListener("click", function () {
    var out = document.getElementById("audit-verify-out");
    out.textContent = "verifying…";
    api("/api/audit/verify").then(function (payload) {
      out.textContent = payload.ok ? "chain OK — " + payload.checked + " events" :
        "BROKEN at seq " + payload.first_bad_seq + " (checked " + payload.checked + ")";
      out.className = payload.ok ? "ok-badge" : "bad-badge";
    }).catch(function (error) {
      out.textContent = String(error.message || error);
      out.className = "bad-badge";
    });
  });

  // ------------------------------------------------------------------- wiring

  refreshers.graph = function () { loadGraph(); };
  refreshers.activity = function (quiet) {
    if (quiet && activity.lastSeq > 0) activityPoll();
    else activityLoad();
  };
  refreshers.search = function () { /* interactive; polls nothing */ };
  refreshers.browse = function () { loadBrowse(); };
  refreshers.metrics = function () { loadPanel("metrics-body", "/api/metrics", renderMetrics); };
  refreshers.health = function () { loadPanel("health-body", "/api/health", renderHealth); };
  refreshers.analytics = function () { loadPanel("analytics-body", "/api/analytics", renderAnalytics); };
  refreshers.audit = function () { loadPanel("audit-body", "/api/audit?limit=100", renderAudit); };
  refreshers.proposals = function () { loadPanel("proposals-body", "/api/proposals", renderProposals); };
  refreshers.debt = function () { loadPanel("debt-body", "/api/debt", renderDebt); };
  refreshers.codeintel = function () { /* interactive form; runs on submit */ };
  refreshers.extractions = function () { loadPanel("extractions-body", "/api/extractions", renderExtractions); };
  refreshers.store = function () { renderStore(); };
  refreshers.system = function () { renderSystem(); };
  refreshers.bench = function () { loadPanel("bench-body", "/api/bench", renderBench); };
  refreshers.ledgers = function () { renderLedgers(); };
  refreshers.exports = function () { /* interactive form; runs on submit */ };
  refreshers.contracts = function () { loadPanel("contracts-body", "/api/contracts", renderContracts); };
  refreshers.setup = function () { renderSetup(); };

  var tabButtons = document.querySelectorAll(".tab");
  for (var t = 0; t < tabButtons.length; t++) {
    (function (button) {
      button.addEventListener("click", function () {
        showTab(button.getAttribute("data-tab"));
      });
    })(tabButtons[t]);
  }

  document.getElementById("topbar-refresh").addEventListener("click", function () {
    var fn = refreshers[ACTIVE_TAB];
    if (fn) fn(false);
  });
  document.getElementById("cmdk").addEventListener("click", focusPrimaryInput);
  document.addEventListener("keydown", function (event) {
    if ((event.metaKey || event.ctrlKey) && (event.key === "k" || event.key === "K")) {
      event.preventDefault();
      focusPrimaryInput();
    }
  });

  if (!TOKEN) {
    setStatus("missing token — open the URL printed by the CLI", true);
  }

  // Boot: learn the live-refresh interval, then start polling.
  api("/api/setup").then(function (payload) {
    if (typeof payload.live_refresh_seconds === "number") {
      LIVE.seconds = Math.max(0, payload.live_refresh_seconds);
    }
  }).catch(function () { /* default interval stands */ }).finally(function () {
    setLivenessLabel();
    startPolling();
  });
  resizeCanvas();
  loadGraph();
})();
