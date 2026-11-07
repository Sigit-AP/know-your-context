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
    graph: "Graph", activity: "Activity", search: "Search",
    metrics: "Metrics", analytics: "Analytics", audit: "Audit",
    proposals: "Proposals", debt: "Debt", codeintel: "Code Intel",
    extractions: "Extractions", system: "System",
    exports: "Exports", contracts: "Contracts", setup: "Setup"
  };

  var PRIMARY_INPUTS = {
    search: "search-q", activity: "activity-search",
    codeintel: "codeintel-symbol", exports: "export-node"
  };

  // ----------------------------------------------------- merged-tab modes
  // Search+Browse share one tab (two modes); Health/Store/System share one
  // System tab (three panels). The mode switch swaps panes inside the view;
  // the refresher for the tab serves whichever pane is visible.

  var SUBMODE = { search: "search", system: "health" };

  function wireModes(prefix, modes, onSwitch) {
    for (var i = 0; i < modes.length; i++) {
      (function (mode) {
        var btn = document.getElementById(prefix + "-seg-" + mode);
        if (!btn) return;
        btn.addEventListener("click", function () { switchMode(prefix, mode, modes, onSwitch); });
      })(modes[i]);
    }
  }

  function switchMode(prefix, mode, modes, onSwitch) {
    SUBMODE[prefix] = mode;
    for (var i = 0; i < modes.length; i++) {
      var btn = document.getElementById(prefix + "-seg-" + modes[i]);
      if (btn) btn.classList.toggle("active", modes[i] === mode);
      var pane = document.getElementById(prefix + "-pane-" + modes[i]);
      if (pane) pane.classList.toggle("hidden", modes[i] !== mode);
    }
    if (onSwitch) onSwitch(mode);
  }

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

  function plural(n, singular, pluralForm) {
    return n === 1 ? singular : (pluralForm || singular + "s");
  }

  // ---------------------------------------------------- visual primitives
  // Hand-made SVG charts (SUB-149; PATTERNS bab T). No chart library, no
  // CDN: every element is createElementNS with attributes only — store data
  // still never becomes markup (numbers/labels go through setAttribute and
  // textContent).

  function svgEl(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (var key in attrs) node.setAttribute(key, String(attrs[key]));
    }
    return node;
  }

  function svgRoot(width, height) {
    var svg = svgEl("svg", { viewBox: "0 0 " + width + " " + height, role: "img" });
    svg.setAttribute("class", "viz");
    return svg;
  }

  function svgText(x, y, text, cls, anchor) {
    var node = svgEl("text", { x: x, y: y, "text-anchor": anchor || "start" });
    node.setAttribute("class", cls);
    node.textContent = String(text);
    return node;
  }

  function fmtInt(value) {
    // Thousands separators (T8); integers only — the store serves int stats.
    var text = String(Math.round(value));
    var out = "";
    while (text.length > 3) {
      out = "," + text.slice(-3) + out;
      text = text.slice(0, -3);
    }
    return text + out;
  }

  function fmtBytes(value) {
    if (value >= 1048576) return (Math.round(value / 104857.6) / 10) + " MiB";
    if (value >= 1024) return (Math.round(value / 102.4) / 10) + " KiB";
    return String(value) + " B";
  }

  // T1: KPI card = label / value(+unit) / delta pills / baseline+period.
  // W20: optional spark = number series rendered stroke-only at the right of
  // the value row (foto-2 strip anatomy: value left, sparkline right).
  function kpiCard(opts) {
    var card = el("div", "kpi" + (opts.dark ? " dark" : ""));
    card.appendChild(el("div", "kpi-label", opts.label));
    var value = el("div", "kpi-value");
    value.appendChild(document.createTextNode(String(opts.value)));
    if (opts.unit) value.appendChild(el("span", "kpi-unit", opts.unit));
    if (opts.spark && opts.spark.length >= 2) {
      var valueRow = el("div", "kpi-value-row");
      valueRow.appendChild(value);
      valueRow.appendChild(sparkline(opts.spark));
      card.appendChild(valueRow);
    } else {
      card.appendChild(value);
    }
    var delta = el("div", "kpi-delta");
    var pills = opts.deltas || [];
    for (var i = 0; i < pills.length; i++) {
      // T9: arrow direction, color class and sign agree by construction.
      var dir = pills[i].dir; // "up" | "down" | "flat"
      var arrow = dir === "up" ? "▲ " : dir === "down" ? "▼ " : "";
      delta.appendChild(el("span", "delta-pill " + dir, arrow + pills[i].text));
    }
    if (!pills.length) delta.appendChild(el("span", "delta-pill flat", "—")); // R6: em-dash, no fake delta
    card.appendChild(delta);
    card.appendChild(el("div", "kpi-base", opts.baseline));
    if (opts.cta) {
      var cta = el("button", "kpi-cta", opts.cta.label);
      cta.type = "button";
      cta.addEventListener("click", opts.cta.onClick);
      card.appendChild(cta);
    }
    return card;
  }

  function chartCard(title, metaText) {
    var card = el("div", "chart-card");
    var head = el("div", "chart-head");
    head.appendChild(el("span", "chart-title", title));
    if (metaText !== null && metaText !== undefined && metaText !== "") head.appendChild(el("span", "chart-meta", metaText));
    card.appendChild(head);
    return card;
  }

  // T3: vertical bars, no gridlines, accent value tags floating above bars,
  // category labels below. Bars neutral; the tallest carries nothing extra —
  // emphasis comes from the tags, not hue.
  function barChart(items, opts) {
    opts = opts || {};
    var width = opts.width || 460;
    var height = opts.height || 180;
    var top = 26; // room for the value tags
    var bottom = 18; // category labels
    var plotH = height - top - bottom;
    var max = 0;
    for (var i = 0; i < items.length; i++) max = Math.max(max, items[i].value);
    var svg = svgRoot(width, height);
    if (max <= 0) return svg;
    var slot = width / items.length;
    var barW = Math.min(34, slot * 0.58); // w:gap ≈ 1.4:1 (foto-1)
    for (var j = 0; j < items.length; j++) {
      var item = items[j];
      var h = Math.max(2, Math.round((item.value / max) * plotH));
      var x = Math.round(slot * j + (slot - barW) / 2);
      var y = top + plotH - h;
      var bar = svgEl("rect", { x: x, y: y, width: barW, height: h, rx: 6 });
      bar.setAttribute("class", "viz-bar");
      svg.appendChild(bar);
      // value tag: rounded rect (r=6, NOT a full pill — T3 correction)
      var label = (opts.format || fmtInt)(item.value);
      var tagW = label.length * 6 + 12;
      var tag = svgEl("rect", {
        x: Math.round(slot * j + slot / 2 - tagW / 2), y: Math.max(0, y - 22),
        width: tagW, height: 18, rx: 6
      });
      tag.setAttribute("class", "viz-tag");
      svg.appendChild(tag);
      svg.appendChild(svgText(Math.round(slot * j + slot / 2), Math.max(0, y - 22) + 12.5, label, "viz-tag-text", "middle"));
      svg.appendChild(svgText(Math.round(slot * j + slot / 2), height - 5, item.label, "viz-cat", "middle"));
    }
    return svg;
  }

  // T4 minimal register: one thin spline (2px), no dots, no gridlines, soft
  // area tint, sparse day labels. W20 fix: the domain is padded (8% headroom,
  // or ±1 for a flat series) so a constant line never pins to the plot edge,
  // and min/max carry small muted labels so the magnitude stays readable.
  function lineChart(points, opts) {
    opts = opts || {};
    var width = opts.width || 460;
    var height = opts.height || 150;
    var top = 10, bottom = 16, padX = 4;
    var plotW = width - padX * 2, plotH = height - top - bottom;
    var max = 0;
    var min = Infinity;
    for (var i = 0; i < points.length; i++) {
      max = Math.max(max, points[i].value);
      min = Math.min(min, points[i].value);
    }
    var svg = svgRoot(width, height);
    if (max <= 0 || points.length < 2) return svg;
    if (min === Infinity) min = 0;
    var dataMax = max, dataMin = min;
    var span = max - min;
    if (span <= 0) { max = max + 1; min = Math.max(0, min - 1); span = max - min; }
    else { max += span * 0.08; min = Math.max(0, min - span * 0.08); span = max - min; }
    var step = plotW / (points.length - 1);
    function yOf(v) { return top + plotH - ((v - min) / span) * plotH; }
    var d = "";
    var area = "";
    for (var j = 0; j < points.length; j++) {
      var x = padX + step * j;
      var y = yOf(points[j].value);
      d += (j === 0 ? "M " : " L ") + x.toFixed(1) + " " + y.toFixed(1);
    }
    area = d + " L " + (padX + step * (points.length - 1)).toFixed(1) + " " + (top + plotH) +
      " L " + padX + " " + (top + plotH) + " Z";
    var areaPath = svgEl("path", { d: area });
    areaPath.setAttribute("class", "viz-area");
    svg.appendChild(areaPath);
    var line = svgEl("path", { d: d });
    line.setAttribute("class", "viz-line");
    svg.appendChild(line);
    // min/max magnitude labels (right edge, muted) — a flat line still reads.
    svg.appendChild(svgText(width - 2, yOf(dataMax) + 3, fmtInt(dataMax), "viz-cat", "end"));
    if (dataMin !== dataMax) {
      svg.appendChild(svgText(width - 2, yOf(dataMin) + 3, fmtInt(dataMin), "viz-cat", "end"));
    }
    // sparse x labels: first, middle, last
    var marks = [0, Math.floor((points.length - 1) / 2), points.length - 1];
    for (var m = 0; m < marks.length; m++) {
      var idx = marks[m];
      var anchor = idx === 0 ? "start" : idx === points.length - 1 ? "end" : "middle";
      svg.appendChild(svgText(padX + step * idx, height - 4, points[idx].label, "viz-cat", anchor));
    }
    return svg;
  }

  // T5: thick-stroke donut (ring ≈ 22px), round caps, % badge overlapping the
  // end of the largest arc, center total, legend built in DOM beside it.
  var DONUT_COLORS = ["#7C3AED", "#A78BFA", "#C4B5FD", "#DDD6FE", "#9CA3AF", "#D4D4D8"];

  function donut(segments, opts) {
    opts = opts || {};
    var size = opts.size || 150;
    var radius = (size - 26) / 2;
    var stroke = 22;
    var cx = size / 2, cy = size / 2;
    var total = 0;
    for (var i = 0; i < segments.length; i++) total += segments[i].value;
    var svg = svgRoot(size, size);
    if (total <= 0) return svg;
    var circumference = 2 * Math.PI * radius;
    var track = svgEl("circle", { cx: cx, cy: cy, r: radius, "stroke-width": stroke });
    track.setAttribute("class", "viz-track");
    svg.appendChild(track);
    var offset = circumference * 0.25; // start at 12 o'clock
    var largestIdx = 0;
    for (var k = 1; k < segments.length; k++) {
      if (segments[k].value > segments[largestIdx].value) largestIdx = k;
    }
    for (var j = 0; j < segments.length; j++) {
      var frac = segments[j].value / total;
      var len = Math.max(0, frac * circumference - 2); // 2px gap between arcs
      var arc = svgEl("circle", {
        cx: cx, cy: cy, r: radius, "stroke-width": stroke,
        "stroke-dasharray": len + " " + (circumference - len),
        "stroke-dashoffset": -offset,
        stroke: segments[j].color || DONUT_COLORS[j % DONUT_COLORS.length],
        transform: "rotate(-90 " + cx + " " + cy + ")"
      });
      arc.setAttribute("class", "viz-arc");
      svg.appendChild(arc);
      if (j === largestIdx) {
        // % badge at the arc's midpoint, sitting ON the ring (T5).
        var midAngle = ((offset + frac * circumference / 2) / circumference) * 2 * Math.PI - Math.PI / 2;
        var bx = cx + Math.cos(midAngle) * radius;
        var by = cy + Math.sin(midAngle) * radius;
        var pct = Math.round(frac * 100) + "%";
        var bw = pct.length * 6 + 10;
        var badgeRect = svgEl("rect", {
          x: (bx - bw / 2).toFixed(1), y: (by - 9).toFixed(1), width: bw, height: 18, rx: 9
        });
        badgeRect.setAttribute("class", "viz-pct-badge");
        svg.appendChild(badgeRect);
        svg.appendChild(svgText(bx.toFixed(1), (by + 3.5).toFixed(1), pct, "viz-pct-text", "middle"));
      }
      offset += frac * circumference;
    }
    svg.appendChild(svgText(cx, cy + 1, fmtInt(total), "viz-center", "middle"));
    svg.appendChild(svgText(cx, cy + 14, opts.centerLabel || "total", "viz-center-sub", "middle"));
    return svg;
  }

  function donutLegend(segments, total) {
    var legend = el("div", "donut-legend");
    for (var i = 0; i < segments.length; i++) {
      var row = el("div", "lg-row");
      var swatch = el("span", "lg-swatch");
      swatch.style.background = segments[i].color || DONUT_COLORS[i % DONUT_COLORS.length];
      row.appendChild(swatch);
      row.appendChild(el("span", "lg-label", segments[i].label));
      row.appendChild(el("span", "lg-value", fmtInt(segments[i].value)));
      row.appendChild(el("span", "lg-share", total > 0 ? Math.round((segments[i].value / total) * 100) + "%" : "—"));
      legend.appendChild(row);
    }
    return legend;
  }

  // T6: dot-matrix — columns = buckets, rows filled bottom-up, per-row ramp.
  function dotMatrix(columns, rowColors, opts) {
    opts = opts || {};
    var pitch = opts.pitch || 14;
    var dotR = opts.dotR || 5;
    var rows = opts.rows || 6;
    var width = columns.length * pitch + 8;
    var height = rows * pitch + 18;
    var svg = svgRoot(width, height);
    for (var c = 0; c < columns.length; c++) {
      var value = Math.min(rows, columns[c].value);
      for (var r = 0; r < rows; r++) {
        var cx = 4 + c * pitch + pitch / 2;
        var cy = 4 + (rows - 1 - r) * pitch + dotR; // bottom-up
        var color = r < value ? rowColors[Math.min(r, rowColors.length - 1)] : "#F4F4F5";
        var dot = svgEl("circle", { cx: cx, cy: cy, r: dotR, fill: color });
        dot.setAttribute("class", "viz-dot");
        svg.appendChild(dot);
      }
      svg.appendChild(svgText(4 + c * pitch + pitch / 2 - 2, height - 4, columns[c].label, "viz-cat", "middle"));
    }
    return svg;
  }

  // Bar-list row (leaderboards): mono label, track, fill, value.
  function barRow(label, value, max, valueText) {
    var row = el("div", "barrow");
    row.appendChild(el("span", "bl", label));
    var track = el("span", "bt");
    var fill = el("span", "bf");
    fill.style.width = (max > 0 ? Math.max(2, (value / max) * 100) : 0) + "%";
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("span", "bv", valueText || String(value)));
    return row;
  }

  // ------------------------------------------------ W20 complex visuals
  // SUB-150: the complex layer above the T1-T6 primitives — interactive
  // multi-series line (crosshair + tooltip + legend toggle), dot-matrix
  // heatmap with per-cell tooltip, stacked proportion bar with inline
  // legend, tiered funnel, heatmap table cells, KPI sparklines. All
  // hand-made SVG/DOM; store data still flows through textContent and
  // setAttribute only, and dynamic geometry goes through CSSOM style
  // properties (the CSP blocks setAttribute("style")).

  // Shared floating tooltip card. Parent must be position:relative.
  function makeTooltip(parent) {
    var tip = el("div", "viz-tip");
    tip.style.display = "none";
    parent.appendChild(tip);
    return {
      show: function (x, y, header, rows) {
        clear(tip);
        tip.appendChild(el("div", "tip-head", header));
        for (var i = 0; i < rows.length; i++) {
          var row = el("div", "tip-row");
          if (rows[i].color) {
            var sw = el("span", "mls-swatch");
            sw.style.background = rows[i].color;
            row.appendChild(sw);
          }
          row.appendChild(el("span", null, rows[i].label));
          row.appendChild(el("span", "tip-val", rows[i].value));
          tip.appendChild(row);
        }
        tip.style.display = "block";
        // Flip near the right/bottom edge so the card never clips out.
        var pw = parent.clientWidth || 600, ph = parent.clientHeight || 300;
        var tw = tip.offsetWidth || 140, th = tip.offsetHeight || 60;
        tip.style.left = Math.max(0, Math.min(x + 14, pw - tw - 4)) + "px";
        tip.style.top = Math.max(0, Math.min(y + 10, ph - th - 4)) + "px";
      },
      hide: function () { tip.style.display = "none"; }
    };
  }

  // E4 sparkline: stroke-only, no axes, end dot on the last value.
  function sparkline(values, opts) {
    opts = opts || {};
    var width = opts.width || 88, height = opts.height || 24;
    var svg = svgRoot(width, height);
    svg.setAttribute("class", "kpi-spark");
    svg.style.width = width + "px";
    svg.style.height = height + "px";
    if (!values || values.length < 2) return svg;
    var max = values[0], min = values[0];
    for (var i = 0; i < values.length; i++) {
      max = Math.max(max, values[i]);
      min = Math.min(min, values[i]);
    }
    var span = max - min || 1;
    var step = (width - 4) / (values.length - 1);
    var d = "";
    var lastX = 0, lastY = 0;
    for (var j = 0; j < values.length; j++) {
      var x = 2 + step * j;
      var y = 2 + (height - 5) * (1 - (values[j] - min) / span);
      d += (j === 0 ? "M " : " L ") + x.toFixed(1) + " " + y.toFixed(1);
      lastX = x; lastY = y;
    }
    var line = svgEl("path", { d: d });
    line.setAttribute("class", "spark-line");
    svg.appendChild(line);
    var dot = svgEl("circle", { cx: lastX.toFixed(1), cy: lastY.toFixed(1), r: 2.2 });
    dot.setAttribute("class", "spark-dot");
    svg.appendChild(dot);
    return svg;
  }

  // In-card segmented control (chart-scale skin of the merged-tab .seg).
  function segControl(modes, active, onSwitch) {
    var seg = el("span", "chart-seg");
    for (var i = 0; i < modes.length; i++) {
      (function (mode) {
        var btn = el("button", mode.key === active ? "active" : null, mode.label);
        btn.type = "button";
        btn.setAttribute("data-seg", mode.key);
        btn.addEventListener("click", function () {
          var buttons = seg.querySelectorAll("button");
          for (var b = 0; b < buttons.length; b++) {
            buttons[b].classList.toggle("active", buttons[b] === btn);
          }
          onSwitch(mode.key);
        });
        seg.appendChild(btn);
      })(modes[i]);
    }
    return seg;
  }

  // T4 interactive register: multi-series line with hairline y-grid, crosshair,
  // floating tooltip (header + one row per series) and a clickable legend.
  // series: [{ key, label, color, values: [number|null per xLabel], axis: 0|1 }]
  // Axis 1 (right side) exists only when a series declares it — two series
  // with different units (events/day vs health score) each get an honest scale.
  function multiLineChart(xLabels, series, opts) {
    opts = opts || {};
    var width = opts.width || 920, height = opts.height || 210;
    var hasAxis1 = false;
    for (var a = 0; a < series.length; a++) if (series[a].axis === 1) hasAxis1 = true;
    var padL = 34, padR = hasAxis1 ? 34 : 10, padT = 12, padB = 20;
    var plotW = width - padL - padR, plotH = height - padT - padB;
    var axisMax = [0, 0];
    for (var s = 0; s < series.length; s++) {
      var ax = series[s].axis === 1 ? 1 : 0;
      for (var i = 0; i < series[s].values.length; i++) {
        if (series[s].values[i] !== null && series[s].values[i] !== undefined) {
          axisMax[ax] = Math.max(axisMax[ax], series[s].values[i]);
        }
      }
    }
    var wrap = el("div", "mls-wrap");
    var svg = svgRoot(width, height);
    svg.style.width = "100%";
    svg.style.height = "auto";
    wrap.appendChild(svg);
    if (xLabels.length < 2 || axisMax[0] + axisMax[1] <= 0) return wrap;
    var stepX = plotW / (xLabels.length - 1);
    function xAt(i) { return padL + stepX * i; }
    function yAt(v, ax) { return padT + plotH - (v / (axisMax[ax] || 1)) * plotH; }
    // y grid: 4 hairlines; left labels = axis 0, right labels = axis 1 (T3).
    for (var g = 0; g <= 3; g++) {
      var gy = padT + plotH - (plotH / 3) * g;
      var grid = svgEl("line", { x1: padL, y1: gy, x2: width - padR, y2: gy });
      grid.setAttribute("class", "mls-grid");
      svg.appendChild(grid);
      if (axisMax[0] > 0) {
        svg.appendChild(svgText(2, gy + 3, fmtInt(Math.round((axisMax[0] / 3) * g)), "mls-ylabel", "start"));
      }
      if (hasAxis1 && axisMax[1] > 0) {
        svg.appendChild(svgText(width - 2, gy + 3, fmtInt(Math.round((axisMax[1] / 3) * g)), "mls-ylabel", "end"));
      }
    }
    // sparse x labels: first, middle, last
    var marks = [0, Math.floor((xLabels.length - 1) / 2), xLabels.length - 1];
    for (var m = 0; m < marks.length; m++) {
      var anchor = marks[m] === 0 ? "start" : marks[m] === xLabels.length - 1 ? "end" : "middle";
      svg.appendChild(svgText(xAt(marks[m]), height - 5, xLabels[marks[m]], "viz-cat", anchor));
    }
    var seriesEls = [];
    for (var k = 0; k < series.length; k++) {
      var ser = series[k];
      var serAxis = ser.axis === 1 ? 1 : 0;
      var d = "";
      var lastIdx = -1;
      for (var j = 0; j < ser.values.length; j++) {
        var v = ser.values[j];
        if (v === null || v === undefined) continue;
        d += (d.length ? " L " : " M ") + xAt(j).toFixed(1) + " " + yAt(v, serAxis).toFixed(1);
        lastIdx = j;
      }
      var groupEls = { line: null, area: null, dot: null };
      if (d.length) {
        if (k === 0) {
          var areaD = d + " L " + xAt(lastIdx).toFixed(1) + " " + (padT + plotH) +
            " L " + xAt(firstIdx(ser.values)).toFixed(1) + " " + (padT + plotH) + " Z";
          var area = svgEl("path", { d: areaD, fill: ser.color });
          area.setAttribute("class", "mls-area");
          svg.appendChild(area);
          groupEls.area = area;
        }
        var line = svgEl("path", { d: d, stroke: ser.color });
        line.setAttribute("class", "mls-line");
        line.setAttribute("data-series", ser.key);
        svg.appendChild(line);
        groupEls.line = line;
        var end = svgEl("circle", { cx: xAt(lastIdx).toFixed(1), cy: yAt(ser.values[lastIdx], serAxis).toFixed(1), r: 3.5, fill: ser.color });
        end.setAttribute("class", "mls-enddot");
        svg.appendChild(end);
        groupEls.dot = end;
      }
      seriesEls.push(groupEls);
    }
    // crosshair + focus dots (hidden until hover)
    var cross = svgEl("line", { x1: 0, y1: padT, x2: 0, y2: padT + plotH });
    cross.setAttribute("class", "mls-cross");
    cross.style.display = "none";
    svg.appendChild(cross);
    var focusDots = [];
    for (var f = 0; f < series.length; f++) {
      var fd = svgEl("circle", { r: 4, fill: series[f].color });
      fd.setAttribute("class", "mls-focus");
      fd.style.display = "none";
      svg.appendChild(fd);
      focusDots.push(fd);
    }
    var tip = makeTooltip(wrap);
    // Transparent hit rect over the plot: nearest-x snap on mousemove.
    var hit = svgEl("rect", { x: padL, y: padT, width: plotW, height: plotH });
    hit.setAttribute("class", "mls-hit");
    svg.appendChild(hit);
    function nearestIndex(event) {
      var rect = svg.getBoundingClientRect();
      var scaleX = rect.width ? width / rect.width : 1;
      var px = (event.clientX - rect.left) * scaleX;
      return Math.max(0, Math.min(xLabels.length - 1, Math.round((px - padL) / stepX)));
    }
    hit.addEventListener("mousemove", function (event) {
      var idx = nearestIndex(event);
      cross.setAttribute("x1", xAt(idx).toFixed(1));
      cross.setAttribute("x2", xAt(idx).toFixed(1));
      cross.style.display = "";
      var rows = [];
      for (var s2 = 0; s2 < series.length; s2++) {
        var val = series[s2].values[idx];
        if (val === null || val === undefined) {
          focusDots[s2].style.display = "none";
          continue;
        }
        focusDots[s2].setAttribute("cx", xAt(idx).toFixed(1));
        focusDots[s2].setAttribute("cy", yAt(val, series[s2].axis === 1 ? 1 : 0).toFixed(1));
        focusDots[s2].style.display = "";
        rows.push({ color: series[s2].color, label: series[s2].label, value: fmtInt(val) });
      }
      var rect2 = svg.getBoundingClientRect();
      var scaleX2 = rect2.width ? rect2.width / width : 1;
      tip.show(xAt(idx) * scaleX2, 10, xLabels[idx], rows);
    });
    hit.addEventListener("mouseleave", function () {
      cross.style.display = "none";
      for (var h = 0; h < focusDots.length; h++) focusDots[h].style.display = "none";
      tip.hide();
    });
    // Legend: swatch + label + latest value; click toggles the series.
    var legend = el("div", "mls-legend");
    for (var l = 0; l < series.length; l++) {
      (function (idx) {
        var ser = series[idx];
        var latest = null;
        for (var b = ser.values.length - 1; b >= 0; b--) {
          if (ser.values[b] !== null && ser.values[b] !== undefined) { latest = ser.values[b]; break; }
        }
        var btn = el("button", null);
        btn.type = "button";
        var sw = el("span", "mls-swatch");
        sw.style.background = ser.color;
        btn.appendChild(sw);
        btn.appendChild(document.createTextNode(ser.label + " "));
        btn.appendChild(el("span", "mls-latest", latest === null ? "—" : fmtInt(latest)));
        btn.addEventListener("click", function () {
          var off = btn.classList.toggle("off");
          var els = seriesEls[idx];
          for (var key in els) {
            if (els[key]) els[key].classList.toggle("mls-off", off);
          }
        });
        legend.appendChild(btn);
      })(l);
    }
    wrap.appendChild(legend);
    return wrap;
  }
  function firstIdx(values) {
    for (var i = 0; i < values.length; i++) {
      if (values[i] !== null && values[i] !== undefined) return i;
    }
    return 0;
  }

  // T6 + intensity heatmap: dots on a rows x cols grid, 5-step single-hue
  // ramp by count, error ring for cells containing errors, hover tooltip
  // with the exact count + level mix. Row labels left, column labels below.
  var HEAT_RAMP = ["#F4F4F5", "#EDE9FE", "#C4B5FD", "#A78BFA", "#7C3AED"];

  function dotHeatmap(config, opts) {
    opts = opts || {};
    var pitch = opts.pitch || 22, dotR = opts.dotR || 7;
    var rows = config.rowLabels.length, cols = config.colLabels.length;
    var labelW = opts.labelWidth || 56;
    var width = labelW + cols * pitch + 6;
    var height = rows * pitch + 20;
    var max = 1;
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) max = Math.max(max, config.cells[r][c].count);
    }
    var wrap = el("div", "dotheat");
    var svg = svgRoot(width, height);
    svg.style.width = width + "px";
    svg.style.height = height + "px";
    wrap.appendChild(svg);
    var tip = makeTooltip(wrap);
    for (var rr = 0; rr < rows; rr++) {
      svg.appendChild(svgText(labelW - 8, rr * pitch + pitch / 2 + 3.5, config.rowLabels[rr], "dh-row-label", "end"));
      for (var cc = 0; cc < cols; cc++) {
        var cell = config.cells[rr][cc];
        var cx = labelW + cc * pitch + pitch / 2;
        var cy = rr * pitch + pitch / 2;
        var step = cell.count <= 0 ? 0 : Math.min(4, 1 + Math.floor((cell.count / max) * 3.999));
        var dot = svgEl("circle", { cx: cx, cy: cy, r: dotR, fill: HEAT_RAMP[step] });
        dot.setAttribute("class", "dh-dot");
        (function (cellData, px, py) {
          dot.addEventListener("mousemove", function () {
            tip.show(px + 8, py - 6, cellData.title, [
              { label: "events", value: String(cellData.count) },
              { label: "info / warn / error", value: cellData.info + " / " + cellData.warn + " / " + cellData.error }
            ]);
          });
          dot.addEventListener("mouseleave", function () { tip.hide(); });
        })(cell, cx, cy);
        svg.appendChild(dot);
        if (cell.error > 0) {
          var ring = svgEl("circle", { cx: cx, cy: cy, r: dotR + 2.5 });
          ring.setAttribute("class", "dh-ring");
          ring.setAttribute("stroke", "#DC2626");
          svg.appendChild(ring);
        }
      }
    }
    var every = opts.colLabelEvery || Math.ceil(cols / 10);
    for (var lc = 0; lc < cols; lc += every) {
      svg.appendChild(svgText(labelW + lc * pitch + pitch / 2, height - 5, config.colLabels[lc], "dh-col-label", "middle"));
    }
    return wrap;
  }

  function dotHeatLegend() {
    var legend = el("div", "dotheat-legend");
    legend.appendChild(document.createTextNode("less"));
    for (var i = 0; i < HEAT_RAMP.length; i++) {
      var cellEl = el("span", "dh-cell");
      cellEl.style.background = HEAT_RAMP[i];
      legend.appendChild(cellEl);
    }
    legend.appendChild(document.createTextNode("more"));
    var ringNote = el("span", null);
    var ring = el("span", "dh-cell");
    ring.style.background = "transparent";
    ring.style.border = "1.5px solid #DC2626";
    ringNote.appendChild(ring);
    ringNote.appendChild(document.createTextNode(" errors present"));
    legend.appendChild(ringNote);
    return legend;
  }

  // Stacked proportion bar + inline legend (T10 anatomy, stacked): swatch,
  // optional avatar, label, mono value, right-aligned share %. Hover on a
  // segment spotlights it and floats the exact numbers.
  function proportionBar(segments, opts) {
    opts = opts || {};
    var total = 0;
    for (var i = 0; i < segments.length; i++) total += segments[i].value;
    var wrap = el("div", "propbar-card");
    var tip = makeTooltip(wrap);
    var bar = el("div", "propbar");
    for (var s = 0; s < segments.length; s++) {
      (function (seg) {
        if (seg.value <= 0 || total <= 0) return;
        var part = el("span", "pb-seg");
        part.style.width = (seg.value / total) * 100 + "%";
        part.style.background = seg.color;
        part.addEventListener("mousemove", function (event) {
          var rect = wrap.getBoundingClientRect();
          tip.show(event.clientX - rect.left, 20, seg.label, [
            { color: seg.color, label: "count", value: fmtInt(seg.value) },
            { label: "share", value: Math.round((seg.value / total) * 100) + "%" }
          ]);
        });
        part.addEventListener("mouseleave", function () { tip.hide(); });
        bar.appendChild(part);
      })(segments[s]);
    }
    wrap.appendChild(bar);
    var legend = el("div", "propbar-legend");
    for (var l = 0; l < segments.length; l++) {
      var seg2 = segments[l];
      var row = el("div", "pb-row");
      if (opts.avatars && seg2.avatar) {
        var av = el("span", "pb-avatar", seg2.avatar);
        av.style.background = seg2.color;
        row.appendChild(av);
      } else {
        var sw = el("span", "pb-swatch");
        sw.style.background = seg2.color;
        row.appendChild(sw);
      }
      row.appendChild(el("span", "pb-label", seg2.label));
      row.appendChild(el("span", "pb-value", fmtInt(seg2.value)));
      row.appendChild(el("span", "pb-share", total > 0 ? Math.round((seg2.value / total) * 100) + "%" : "—"));
      legend.appendChild(row);
    }
    wrap.appendChild(legend);
    return wrap;
  }

  // Tiered funnel: centered bars, width proportional to the stage value,
  // conversion % on the gaps. Non-monotonic input is reported honestly
  // (a stage bigger than its parent gets the parent width and a note).
  // Deep ramp: white text stays >= AA on every tier (auditor caught ~3:1 on
  // the light tier).
  var FUNNEL_COLORS = ["#5B21B6", "#6D28D9", "#7C3AED", "#8B5CF6"];

  function funnelChart(stages, opts) {
    opts = opts || {};
    var wrap = el("div", "funnel");
    var max = 0;
    for (var i = 0; i < stages.length; i++) max = Math.max(max, stages[i].value);
    if (max <= 0) return wrap;
    for (var s = 0; s < stages.length; s++) {
      if (s > 0) {
        var prev = stages[s - 1].value;
        var conv = el("div", "funnel-conv");
        conv.appendChild(el("span", "conv-arrow", "↓"));
        conv.appendChild(document.createTextNode(
          prev > 0 ? Math.round((stages[s].value / prev) * 100) + "% of " + stages[s - 1].label : "—"));
        wrap.appendChild(conv);
      }
      var tier = el("div", "funnel-tier");
      var bar = el("div", "funnel-bar");
      bar.style.width = Math.max(12, (stages[s].value / max) * 100) + "%";
      bar.style.background = FUNNEL_COLORS[Math.min(s, FUNNEL_COLORS.length - 1)];
      bar.appendChild(el("span", "fb-label", stages[s].label));
      bar.appendChild(el("span", "fb-value", fmtInt(stages[s].value)));
      tier.appendChild(bar);
      wrap.appendChild(tier);
    }
    return wrap;
  }

  // Heatmap table post-pass: numeric columns get an accent-alpha intensity
  // background proportional to the column max; the hottest cell goes bold.
  function heatmapCols(tbl, colIndexes) {
    var rows = tbl.querySelectorAll("tr");
    if (rows.length < 2) return;
    for (var c = 0; c < colIndexes.length; c++) {
      var col = colIndexes[c];
      var max = 0;
      var cells = [];
      for (var r = 1; r < rows.length; r++) {
        var td = rows[r].children[col];
        if (!td) continue;
        var v = parseFloat(td.textContent);
        if (isNaN(v)) continue;
        cells.push({ td: td, v: v });
        max = Math.max(max, v);
      }
      for (var k = 0; k < cells.length; k++) {
        var ratio = max > 0 ? cells[k].v / max : 0;
        cells[k].td.classList.add("heat");
        // 0.06..0.34 alpha: per-cell steps stay visible at table scale.
        cells[k].td.style.background = "rgba(124, 58, 237, " + (0.06 + ratio * 0.28).toFixed(3) + ")";
        if (ratio >= 0.999) cells[k].td.classList.add("heat-hi");
      }
    }
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
  var graphTileStyles = {}; // node id -> tile background (deterministic identity)
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

  // ------------------------------------------------------- tile identity
  // Curated gradient palette (foto-1 language): each node gets a unique
  // gradient so cards carry visual identity. Hierarchy endpoints (kind root
  // = chain start, kind leaf = terminal) stay dark ink — the anchor signal.
  var GTILE_DARK = "#1F2937";
  var GTILE_GRADIENTS = [
    ["#A855F7", "#EC4899", "#F59E0B"], // violet → pink → amber
    ["#3B82F6", "#F97316"], // blue → orange
    ["#34D399", "#8B5CF6"], // teal → violet
    ["#D946EF", "#8B5CF6"], // magenta → violet
    ["#EC4899", "#A78BFA"], // pink → lavender
    ["#FBBF24", "#F97316", "#EF4444"], // amber → orange → red
    ["#22D3EE", "#6366F1"], // cyan → indigo
    ["#84CC16", "#14B8A6"], // lime → teal
    ["#F472B6", "#FB923C"], // rose → orange
    ["#60A5FA", "#34D399"] // sky → emerald
  ];

  function gnodeTileIsDark(node) {
    var kind = String(node.kind || "leaf").toLowerCase();
    return kind === "root" || kind === "leaf";
  }

  // FNV-1a 32-bit — the same stable hash foto-1 uses for sha8; pure function
  // of the node id, so the identity survives re-layouts and reloads.
  function fnv1a(text) {
    var h = 0x811c9dc5;
    for (var i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = (h * 0x01000193) >>> 0;
    }
    return h >>> 0;
  }

  // Deterministic per-graph assignment: hash(node.id) picks the base slot;
  // collisions probe forward in ascending id order, so two distinct nodes
  // never share a gradient while the palette has free slots (palette reuse
  // only when nodes > palette size — documented ceiling).
  function assignTileGradients(nodes) {
    var styles = {};
    var taken = {};
    var sorted = nodes.slice().sort(function (a, b) {
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
    for (var i = 0; i < sorted.length; i++) {
      var node = sorted[i];
      if (gnodeTileIsDark(node)) {
        styles[node.id] = GTILE_DARK;
        continue;
      }
      var slot = fnv1a(node.id) % GTILE_GRADIENTS.length;
      while (taken[slot]) slot = (slot + 1) % GTILE_GRADIENTS.length;
      taken[slot] = true;
      styles[node.id] = "linear-gradient(135deg, " + GTILE_GRADIENTS[slot].join(", ") + ")";
    }
    return styles;
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
      // Markers are 22px circles: the path lane pitch (18px) is tighter than
      // the diameter, so markers use a wider pitch (26px) to never overlap.
      var off = lanes[e] * 26;
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
    var tile = el("span", "gnode-tile", node.domain || "?");
    // Deterministic identity (see assignTileGradients): a unique curated
    // gradient per node; hierarchy endpoints (root/leaf) stay dark ink.
    tile.style.background = graphTileStyles[node.id] || GTILE_DARK;
    card.appendChild(tile);
    var text = el("div", "gnode-text");
    text.appendChild(el("div", "gnode-eyebrow",
      (node.domain || "") + " · " + (node.kind || "leaf") + " · " + (node.lifecycle_status || "active")));
    text.appendChild(el("div", "gnode-title", node.label || node.id));
    card.appendChild(text);
    // Card affordance is always visible (foto-1), never hover-gated.
    var menu = el("span", "gnode-menu", "···");
    card.appendChild(menu);

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
    graphTileStyles = assignTileGradients(graphData.nodes);
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
      // Stats live in the bottom HUD strip next to the zoom pill (foto-1),
      // not in the toolbar — the canvas owns its own telemetry.
      document.getElementById("graph-stat-nodes").textContent = String(payload.total_nodes);
      document.getElementById("graph-stat-edges").textContent = String(payload.total_edges);
      document.getElementById("graph-stats").title =
        "layout " + payload.layout.mode + (payload.truncated ? " · TRUNCATED (showing a capped subset)" : "");
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
  // Chain verification is a heavy, explicitly user-initiated call (the same
  // endpoint the Audit tab uses); the HUD pill starts unverified and reports
  // the honest result.
  document.getElementById("graph-chain-verify").addEventListener("click", function (event) {
    event.stopPropagation();
    var pill = document.getElementById("graph-chain-verify");
    pill.textContent = "verifying…";
    pill.className = "hud-chain";
    api("/api/audit/verify").then(function (payload) {
      pill.textContent = payload.ok ? "valid · " + payload.checked : "BROKEN @ seq " + payload.first_bad_seq;
      pill.className = "hud-chain " + (payload.ok ? "hud-chain-ok" : "hud-chain-bad");
    }).catch(function (error) {
      pill.textContent = String(error.message || error);
      pill.className = "hud-chain hud-chain-bad";
    });
  });
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
      var seenRel = {};
      for (var r = 0; r < node.relations.length; r++) {
        seenRel[node.relations[r].kind + "→" + node.relations[r].target] = true;
        relations.appendChild(relationButton(node.relations[r]));
      }
      // The node API lists outgoing non-parent relations only; merge the
      // graph-level edges so the panel reports the same connection set the
      // expanded card shows (parent_of and incoming edges included).
      if (graphData) {
        for (var g = 0; g < graphData.edges.length; g++) {
          var edge = graphData.edges[g];
          if (edge.source === nodeId) {
            if (seenRel[edge.kind + "→" + edge.target]) continue;
            seenRel[edge.kind + "→" + edge.target] = true;
            relations.appendChild(relationButton({ kind: edge.kind, target: edge.target }));
          } else if (edge.target === nodeId) {
            if (seenRel[edge.kind + "←" + edge.source]) continue;
            seenRel[edge.kind + "←" + edge.source] = true;
            relations.appendChild(relationButton({ kind: edge.kind, target: edge.source, incoming: true }));
          }
        }
      }
      if (relations.children.length === 1) emptyNote(relations, "(none)"); // heading only

      var hierarchy = dpSection(body, "Hierarchy (callers / callees)");
      var hierarchyLoading = el("p", "empty", "loading…");
      hierarchy.appendChild(hierarchyLoading);
      api("/api/hierarchy/" + encodeURIComponent(node.node_id) + "?depth=1").then(function (hier) {
        // Remove ONLY the loading note — clearing the section would wipe the
        // eyebrow heading (the panel lost "Hierarchy" entirely that way).
        if (hierarchyLoading.parentNode) hierarchyLoading.parentNode.removeChild(hierarchyLoading);
        hierarchy.appendChild(hierarchyList("Callers", hier.callers.entries));
        hierarchy.appendChild(hierarchyList("Callees", hier.callees.entries));
      }).catch(function () {
        if (hierarchyLoading.parentNode) hierarchyLoading.parentNode.removeChild(hierarchyLoading);
        hierarchy.appendChild(el("p", "empty", "(no call-graph entry for this node)"));
      });
    }).catch(function (error) {
      clear(body);
      body.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  }

  function relationButton(rel) {
    var btn = el("button", "linkish", rel.kind + (rel.incoming ? " ← " : " → ") + rel.target);
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
    renderActivityDensity();
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

  // Activity density (W20): dot-matrix HEATMAP — rows = dayparts (or weekdays
  // in hourly mode), columns = recent days (or hours), dot intensity = event
  // count on a 5-step single-hue ramp, red ring = errors present in the cell,
  // hover = floating tooltip with the exact count + level mix. A segmented
  // control switches granularity. The right rail keeps two KPI cards (with
  // sparklines) and an agent proportion bar (avatar / label / value / %).
  var densityMode = "daily";

  function renderActivityDensity() {
    var box = document.getElementById("activity-density");
    if (!box) return;
    clear(box);
    if (!activity.entries.length) return;
    var DAYPARTS = ["00–06", "06–12", "12–18", "18–24"];
    var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    var daySet = {};
    var days = [];
    var levelByDay = {};
    var byPart = {}; // day -> [4 cells]
    var byHour = {}; // weekday 0-6 -> [24 cells]
    var perAgent = {};
    function blankCell() { return { count: 0, info: 0, warn: 0, error: 0 }; }
    for (var i = 0; i < activity.entries.length; i++) {
      var entry = activity.entries[i];
      var stamp = String(entry.occurred_at || "");
      var day = stamp.slice(0, 10);
      if (!day) continue;
      if (!daySet[day]) {
        daySet[day] = true;
        days.push(day);
        levelByDay[day] = { info: 0, warn: 0, error: 0 };
        byPart[day] = [blankCell(), blankCell(), blankCell(), blankCell()];
      }
      levelByDay[day][entry.level] = (levelByDay[day][entry.level] || 0) + 1;
      var hour = parseInt(stamp.slice(11, 13), 10);
      var date = new Date(stamp);
      var wd = isNaN(date.getTime()) ? 0 : (date.getDay() + 6) % 7; // Mon = 0
      if (!isNaN(hour)) {
        var part = Math.min(3, Math.floor(hour / 6));
        byPart[day][part].count += 1;
        byPart[day][part][entry.level] = (byPart[day][part][entry.level] || 0) + 1;
        if (!byHour[wd]) {
          byHour[wd] = [];
          for (var h = 0; h < 24; h++) byHour[wd].push(blankCell());
        }
        byHour[wd][hour].count += 1;
        byHour[wd][hour][entry.level] = (byHour[wd][hour][entry.level] || 0) + 1;
      }
      perAgent[entry.agent] = (perAgent[entry.agent] || 0) + 1;
    }
    days.sort();
    var recent = days.slice(-21);

    var card = el("div", "density-card");
    var head = el("div", "chart-head");
    head.appendChild(el("span", "chart-title", "Activity heatmap"));
    var seg = segControl(
      [{ key: "daily", label: "Daily" }, { key: "hourly", label: "Hourly" }],
      densityMode,
      function (mode) { densityMode = mode; renderActivityDensity(); });
    head.appendChild(seg);
    var peak = 0;
    for (var p = 0; p < recent.length; p++) {
      var sum = levelByDay[recent[p]].info + levelByDay[recent[p]].warn + levelByDay[recent[p]].error;
      peak = Math.max(peak, sum);
    }
    head.appendChild(el("span", "chart-meta", recent.length + " days · peak " + peak + "/day"));
    card.appendChild(head);

    var matWrap = el("div", "density-body");
    if (densityMode === "daily") {
      var cells = [];
      for (var r = 0; r < 4; r++) {
        var rowCells = [];
        for (var c = 0; c < recent.length; c++) {
          var cell = byPart[recent[c]][r];
          rowCells.push({
            count: cell.count, info: cell.info, warn: cell.warn, error: cell.error,
            title: recent[c] + " · " + DAYPARTS[r]
          });
        }
        cells.push(rowCells);
      }
      matWrap.appendChild(dotHeatmap({
        rowLabels: DAYPARTS, colLabels: recent.map(function (d) { return d.slice(5); }),
        cells: cells
      }, { pitch: 22, dotR: 7, labelWidth: 48, colLabelEvery: 2 }));
    } else {
      var hCells = [];
      var hLabels = [];
      for (var hh = 0; hh < 24; hh++) hLabels.push(String(hh));
      for (var wr = 0; wr < 7; wr++) {
        var wRow = [];
        for (var hc = 0; hc < 24; hc++) {
          var hCell = (byHour[wr] || [])[hc] || blankCell();
          wRow.push({
            count: hCell.count, info: hCell.info, warn: hCell.warn, error: hCell.error,
            title: WEEKDAYS[wr] + " · " + hc + ":00–" + (hc + 1) + ":00"
          });
        }
        hCells.push(wRow);
      }
      matWrap.appendChild(dotHeatmap({
        rowLabels: WEEKDAYS, colLabels: hLabels, cells: hCells
      }, { pitch: 18, dotR: 5.5, labelWidth: 40, colLabelEvery: 2 }));
    }

    // Right rail: two KPI cards (with per-day sparkline) + agent share bar.
    var stats = el("div", "density-stats");
    var totalAll = 0;
    var dayCounts = [];
    for (var s = 0; s < recent.length; s++) {
      var daySum = levelByDay[recent[s]].info + levelByDay[recent[s]].warn + levelByDay[recent[s]].error;
      totalAll += daySum;
      dayCounts.push(daySum);
    }
    var peakIdx = 0;
    for (var pk = 1; pk < recent.length; pk++) {
      if (dayCounts[pk] > dayCounts[peakIdx]) peakIdx = pk;
    }
    stats.appendChild(kpiCard({
      label: "Events in window", value: totalAll, deltas: [],
      baseline: recent.length + " days shown", spark: dayCounts
    }));
    stats.appendChild(kpiCard({
      label: "Peak day (events)", value: peak,
      deltas: [{ text: recent[peakIdx], dir: "flat" }],
      baseline: "avg " + (Math.round((totalAll / recent.length) * 10) / 10) + "/day",
      spark: dayCounts
    }));
    var agentNames = Object.keys(perAgent).sort();
    if (agentNames.length) {
      var AGENT_COLORS = ["#7C3AED", "#A78BFA", "#C4B5FD", "#DDD6FE", "#9CA3AF", "#D4D4D8"];
      var agentCard = el("div", "kpi");
      agentCard.appendChild(el("div", "kpi-label", "Events by agent"));
      agentCard.appendChild(proportionBar(agentNames.map(function (name, idx) {
        return {
          label: agentLabel(agentNames[idx]) + " · " + agentNames[idx],
          value: perAgent[agentNames[idx]],
          color: AGENT_COLORS[idx % AGENT_COLORS.length],
          avatar: String(idx + 1)
        };
      }), { avatars: true }));
      stats.appendChild(agentCard);
    }
    matWrap.appendChild(stats);
    card.appendChild(matWrap);
    card.appendChild(dotHeatLegend());
    box.appendChild(card);
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
      var maxScore = 0;
      for (var m = 0; m < payload.results.length; m++) {
        if (typeof payload.results[m].score === "number") maxScore = Math.max(maxScore, payload.results[m].score);
      }
      for (var i = 0; i < payload.results.length; i++) {
        results.appendChild(searchHit(payload.results[i], maxScore));
      }
    }).catch(function (error) {
      clear(results);
      results.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  });

  function searchHit(hit, maxScore) {
    // Result card: name, kind chip, mono location, score bar normalized to
    // the best hit of THIS result set (raw scores are engine-relative).
    var box = el("div", "hit");
    var head = el("div", "rc-head");
    head.style.display = "flex";
    head.style.alignItems = "center";
    head.style.gap = "10px";
    head.appendChild(el("span", "rc-title", hit.name));
    head.appendChild(badge("info", hit.kind));
    var score = typeof hit.score === "number" ? hit.score : 0;
    var track = el("span", "prio-track");
    track.style.width = "70px";
    var fill = el("span", "prio-fill");
    fill.style.background = "var(--accent)";
    fill.style.width = (maxScore > 0 ? Math.max(3, (score / maxScore) * 100) : 0) + "%";
    track.appendChild(fill);
    head.appendChild(track);
    head.appendChild(el("span", "muted mono", score ? score.toFixed(3) : ""));
    box.appendChild(head);
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

  // Browse mode opens with a visual size strip (one KPI card per table).
  var browseStatsLoaded = false;
  function loadBrowseStats() {
    if (browseStatsLoaded) return;
    browseStatsLoaded = true;
    var box = document.getElementById("browse-stats");
    api("/api/store/stats").then(function (stats) {
      clear(box);
      var tables = stats.tables || {};
      var names = Object.keys(tables).sort();
      if (!names.length) return;
      var grid = el("div", "kpi-grid");
      for (var i = 0; i < names.length; i++) {
        grid.appendChild(kpiCard({
          label: names[i], value: fmtInt(Number(tables[names[i]]) || 0), deltas: [],
          baseline: "rows"
        }));
      }
      box.appendChild(grid);
    }).catch(function () { browseStatsLoaded = false; });
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

  // ------------------------------------------------------------- metrics
  // Visual-first (SUB-149; PATTERNS T1-T8): KPI cards 4-layer, bar/line/donut
  // SVG hand-made, one dark priority card. Every number comes from the real
  // endpoints: /api/metrics, /api/metrics/series, /api/bench.

  function pctDelta(current, previous) {
    if (previous === 0) return current > 0 ? 100 : 0;
    return Math.round(((current - previous) / previous) * 100);
  }

  function deltaPair(relPct, absText) {
    // T9: dir drives arrow + color + sign together — never a mismatch.
    var dir = relPct > 0 ? "up" : relPct < 0 ? "down" : "flat";
    return [
      { text: (relPct > 0 ? "+" : "") + relPct + "%", dir: dir },
      { text: absText, dir: dir }
    ];
  }

  function renderMetricsVisual(metrics, series, bench) {
    var hero = document.getElementById("metrics-hero");
    var charts = document.getElementById("metrics-charts");
    var detail = document.getElementById("metrics-detail");
    clear(hero); clear(charts); clear(detail);

    var counts = metrics.counts || {};
    var health = metrics.health || { score: 0, max_score: 0 };
    var cap = metrics.publish_capacity || {};
    var eventSeries = (series && series.event_series) || [];
    var healthSeries = (series && series.health_series) || [];

    // ---- KPI 1: health score (delta vs previous recorded snapshot)
    var prevSnap = healthSeries.length >= 2 ? healthSeries[healthSeries.length - 2] : null;
    var healthDeltas = [];
    var healthBase;
    if (prevSnap) {
      var diff = health.score - prevSnap.score;
      healthDeltas = deltaPair(pctDelta(health.score, prevSnap.score || 1), (diff >= 0 ? "+" : "") + diff + " pts");
      healthBase = "vs prev. " + prevSnap.score + "/" + prevSnap.max_score + " · " +
        String(prevSnap.recorded_at || "").slice(0, 10);
    } else {
      healthBase = healthSeries.length === 1 ? "first snapshot recorded — no baseline yet" :
        "no health history recorded (state: " + ((series && series.health_history) || "absent") + ")";
    }
    // ---- KPI 2: events total (delta last 7d vs prior 7d — real buckets)
    var eventsDeltas = [];
    var eventsBase = "no daily buckets yet";
    if (eventSeries.length >= 2) {
      var last7 = 0, prior7 = 0;
      for (var i = 0; i < eventSeries.length; i++) {
        if (i >= eventSeries.length - 7) last7 += eventSeries[i].events;
        else if (i >= eventSeries.length - 14) prior7 += eventSeries[i].events;
      }
      if (prior7 > 0 || last7 > 0) {
        var absDiff = last7 - prior7;
        eventsDeltas = deltaPair(pctDelta(last7, prior7), (absDiff >= 0 ? "+" : "−") + fmtInt(Math.abs(absDiff)));
        eventsBase = "last 7d vs prior 7d · " + eventSeries.length + " days of history";
      } else {
        eventsBase = eventSeries.length + " days of history";
      }
    }
    // ---- KPI 3: publish headroom (baseline = guard; no history exists)
    var utilPct = Math.round((cap.utilization_permille || 0) / 10);
    var capDeltas = [
      { text: "util " + utilPct + "%", dir: cap.ok ? "flat" : "down" },
      { text: "est " + fmtBytes(cap.estimated_bytes || 0), dir: "flat" }
    ];
    // ---- dark priority card (T7: exactly one): worst live signal wins
    var dark;
    var goSystem = function () {
      showTab("system");
      switchMode("system", "health", ["health", "store", "ops"], loadSystemPanel);
    };
    var goAudit = function () { showTab("audit"); };
    if (health.score < health.max_score) {
      dark = kpiCard({
        label: "Priority — health attention", value: health.score, unit: "/ " + health.max_score,
        deltas: [{ text: (health.max_score - health.score) + " components failing", dir: "down" }],
        baseline: "worst signal on the board — see System › Health",
        dark: true, cta: { label: "Open Health ›", onClick: goSystem }
      });
    } else if (cap.ok === false) {
      dark = kpiCard({
        label: "Priority: publish capacity", value: fmtBytes(Math.abs(cap.headroom_bytes || 0)),
        unit: " over guard",
        deltas: [{ text: "guard exceeded", dir: "down" }],
        baseline: "guard " + fmtBytes(cap.guard_bytes || 0) + " · cap " + fmtBytes(cap.cap_bytes || 0),
        dark: true, cta: { label: "Open System ›", onClick: goSystem }
      });
    } else if ((counts.journals_stale || 0) > 0) {
      dark = kpiCard({
        label: "Priority — stale journals", value: counts.journals_stale,
        deltas: [{ text: "incomplete beyond ttl", dir: "down" }],
        baseline: counts.journals_in_progress + " in progress · " + counts.journals_complete + " complete",
        dark: true, cta: { label: "Open Audit ›", onClick: goAudit }
      });
    } else {
      dark = kpiCard({
        label: "Priority — all nominal", value: health.score, unit: "/ " + health.max_score,
        deltas: [{ text: "0 failing components", dir: "up" }],
        baseline: "chain verified from the Audit tab",
        dark: true, cta: { label: "Verify chain ›", onClick: goAudit }
      });
    }
    var grid = el("div", "kpi-grid");
    grid.appendChild(dark); // dark first = anchor (T7: eye lands on priority)
    // W20: sparklines from the real series (health snapshots, daily events).
    var healthSpark = [];
    for (var hs = 0; hs < healthSeries.length; hs++) healthSpark.push(healthSeries[hs].score);
    var eventSpark = [];
    for (var es = 0; es < eventSeries.length; es++) eventSpark.push(eventSeries[es].events);
    grid.appendChild(kpiCard({
      label: "Health score", value: health.score, unit: "/ " + health.max_score,
      deltas: healthDeltas, baseline: healthBase, spark: healthSpark
    }));
    grid.appendChild(kpiCard({
      label: "Events total", value: fmtInt(counts.events_total || 0),
      deltas: eventsDeltas, baseline: eventsBase, spark: eventSpark
    }));
    grid.appendChild(kpiCard({
      label: "Publish headroom", value: fmtBytes(cap.headroom_bytes || 0),
      deltas: capDeltas,
      baseline: "guard " + fmtBytes(cap.guard_bytes || 0) + " · cap " + fmtBytes(cap.cap_bytes || 0)
    }));
    hero.appendChild(grid);

    // ---- charts: multi-line trend (events + health, W20 interactive) + bar
    // (latency p50/op) + donut (event kinds)
    var chartGrid = el("div", "chart-grid");

    // W20: interactive multi-series trend (T4 interactive register). X = day;
    // events on the left axis, health score on the right axis (honest scales).
    var trendCard = chartCard("Activity & health over time", "");
    trendCard.classList.add("wide");
    var trendHost = el("div", null);
    trendCard.appendChild(trendHost);
    var daySet = {};
    var xDays = [];
    for (var ed = 0; ed < eventSeries.length; ed++) {
      if (!daySet[eventSeries[ed].day]) { daySet[eventSeries[ed].day] = true; xDays.push(eventSeries[ed].day); }
    }
    var healthByDay = {};
    for (var hd = 0; hd < healthSeries.length; hd++) {
      var hDay = String(healthSeries[hd].recorded_at || "").slice(0, 10);
      healthByDay[hDay] = healthSeries[hd].score;
      if (hDay && !daySet[hDay]) { daySet[hDay] = true; xDays.push(hDay); }
    }
    xDays.sort();
    var trendEvents = [], trendHealth = [];
    for (var xd = 0; xd < xDays.length; xd++) {
      var found = null;
      for (var ef = 0; ef < eventSeries.length; ef++) {
        if (eventSeries[ef].day === xDays[xd]) { found = eventSeries[ef].events; break; }
      }
      trendEvents.push(found);
      trendHealth.push(healthByDay[xDays[xd]] !== undefined ? healthByDay[xDays[xd]] : null);
    }
    function trendSeries(mode) {
      var out = [];
      if (mode !== "health") out.push({ key: "events", label: "events / day", color: "#7C3AED", values: trendEvents, axis: 0 });
      if (mode !== "events") out.push({ key: "health", label: "health score", color: "#9CA3AF", values: trendHealth, axis: 1 });
      return out;
    }
    function renderTrend(mode) {
      clear(trendHost);
      if (xDays.length < 2) {
        trendHost.appendChild(el("p", "chart-empty",
          "Fewer than 2 daily buckets — a trend needs at least two days of activity."));
        return;
      }
      trendHost.appendChild(multiLineChart(xDays, trendSeries(mode), { width: 920, height: 210 }));
    }
    var trendSeg = segControl(
      [{ key: "both", label: "Both" }, { key: "events", label: "Events" }, { key: "health", label: "Health" }],
      "both", renderTrend);
    trendCard.children[0].appendChild(trendSeg); // children[0] = .chart-head
    renderTrend("both");
    chartGrid.appendChild(trendCard);

    var ops = (bench && bench.operations) || [];
    var barCard = chartCard("Latency p50 by operation", "ms · from the invocation journal");
    if (!ops.length) {
      barCard.appendChild(el("p", "chart-empty", "No completed invocations recorded yet — run an operation to see latency."));
    } else {
      var top = ops.slice().sort(function (a, b) { return (b.p50_ms || 0) - (a.p50_ms || 0); }).slice(0, 8);
      barCard.appendChild(barChart(top.map(function (op) {
        return { label: op.operation.replace(/^knowledge\./, "").slice(0, 12), value: op.p50_ms || 0 };
      }), { width: 400, height: 170 }));
    }
    chartGrid.appendChild(barCard);

    var donutCard = chartCard("Event distribution", "audit vs domain");
    var auditN = counts.audit_events || 0;
    var domainN = counts.domain_events || 0;
    var otherN = Math.max(0, (counts.events_total || 0) - auditN - domainN);
    var segments = [
      { label: "audit", value: auditN },
      { label: "domain", value: domainN }
    ];
    if (otherN > 0) segments.push({ label: "other", value: otherN });
    var segTotal = auditN + domainN + otherN;
    if (segTotal <= 0) {
      donutCard.appendChild(el("p", "chart-empty", "No events recorded yet — the store is empty."));
    } else {
      var donutWrap = el("div", "donut-wrap");
      donutWrap.appendChild(donut(segments, { centerLabel: "events" }));
      donutWrap.appendChild(donutLegend(segments, segTotal));
      donutCard.appendChild(donutWrap);
    }
    chartGrid.appendChild(donutCard);
    charts.appendChild(chartGrid);

    // ---- detail: bench table (dense, heat-mapped numeric columns) + counters
    // + capacity (raw, mono)
    detail.appendChild(el("h3", "panel-title", "Bench — per-operation latency (ms)"));
    if (!ops.length) {
      emptyNote(detail, "(no completed invocations recorded yet)");
    } else {
      var benchTbl = table(detail, ["operation", "count", "min", "mean", "p50", "p95", "max"],
        ops.map(function (op) {
          return [op.operation, op.count, op.min_ms, op.mean_ms, op.p50_ms, op.p95_ms, op.max_ms];
        }), "dense");
      // W20: latency cells carry per-column intensity (hottest = bold) — the
      // slow column/row pops without reading every number.
      heatmapCols(benchTbl, [2, 3, 4, 5, 6]);
    }
    if (bench && bench.malformed_outcomes) {
      detail.appendChild(el("p", "muted", bench.malformed_outcomes + " malformed outcomes skipped"));
    }
    var cols = el("div", "cols-2");
    var countsCol = el("div");
    countsCol.appendChild(el("h3", "panel-title", "Counters"));
    var countRows = [];
    var keys = Object.keys(counts).sort();
    for (var c = 0; c < keys.length; c++) countRows.push([keys[c], counts[keys[c]]]);
    table(countsCol, ["metric", "value"], countRows, "dense");
    cols.appendChild(countsCol);
    var capCol = el("div");
    capCol.appendChild(el("h3", "panel-title", "Publish capacity"));
    table(capCol, ["field", "value"], [
      ["estimated_bytes", cap.estimated_bytes],
      ["guard_bytes", cap.guard_bytes],
      ["headroom_bytes", cap.headroom_bytes],
      ["utilization_permille", cap.utilization_permille],
      ["ok", cap.ok]
    ], "dense");
    cols.appendChild(capCol);
    detail.appendChild(cols);
  }

  function loadMetrics() {
    var body = document.getElementById("metrics-body");
    // keep the three section shells; only refill on success
    Promise.all([
      api("/api/metrics"),
      api("/api/metrics/series?limit=30"),
      api("/api/bench")
    ]).then(function (results) {
      renderMetricsVisual(results[0], results[1], results[2]);
      setStatus("");
    }).catch(function (error) {
      clear(body);
      body.appendChild(el("p", "bad-badge", String(error.message || error)));
      setStatus(String(error.message || error), true);
    });
  }

  function renderHealth(container, payload) {
    // Visual status panel: hero KPI row (status / errors / warnings / worst),
    // findings as severity-tinted cards (B4: tint + left border).
    var grid = el("div", "kpi-grid");
    var statusCard = el("div", "kpi" + (payload.ok ? "" : " dark"));
    statusCard.appendChild(el("div", "kpi-label", "Diagnostics status"));
    var sv = el("div", "kpi-value");
    sv.appendChild(badge(payload.ok ? "ok" : "error", payload.ok ? "OK" : "ERROR"));
    sv.style.paddingTop = "2px";
    statusCard.appendChild(sv);
    var sd = el("div", "kpi-delta");
    sd.appendChild(el("span", "delta-pill " + (payload.ok ? "up" : "down"),
      payload.ok ? "no error findings" : "exit " + payload.exit_code));
    statusCard.appendChild(sd);
    statusCard.appendChild(el("div", "kpi-base", "checks: " + (payload.checked || []).join(", ")));
    grid.appendChild(statusCard);
    grid.appendChild(kpiCard({
      label: "Error findings", value: payload.error_count,
      deltas: [{ text: payload.error_count > 0 ? "action required" : "clean", dir: payload.error_count > 0 ? "down" : "up" }],
      baseline: "worst severity: " + payload.worst_severity
    }));
    grid.appendChild(kpiCard({
      label: "Warnings", value: payload.warning_count,
      deltas: [{ text: payload.warning_count > 0 ? "review advised" : "clean", dir: payload.warning_count > 0 ? "down" : "up" }],
      baseline: "checks: " + (payload.checked || []).length + " ran"
    }));
    container.appendChild(grid);
    var checks = payload.checked || [];
    if (checks.length) {
      var checkRow = el("div", null);
      checkRow.appendChild(el("span", "muted", "checks run: "));
      for (var ch = 0; ch < checks.length; ch++) checkRow.appendChild(el("span", "chip", checks[ch]));
      container.appendChild(checkRow);
    }
    if (payload.findings.length === 0) {
      emptyNote(container, "(no findings — every check passed)");
      return;
    }
    container.appendChild(el("h3", "panel-title", "Findings (" + payload.findings.length + ")"));
    for (var i = 0; i < payload.findings.length; i++) {
      var f = payload.findings[i];
      var card = el("div", "rcard");
      var head = el("div", "rc-head");
      var dot = el("span", "sdot " + (f.severity === "error" ? "error" : f.severity === "warning" ? "warn" : "dim"));
      head.appendChild(dot);
      head.appendChild(el("span", "rc-title", f.code));
      head.appendChild(badge(f.severity === "error" ? "error" : f.severity === "warning" ? "warn" : "dim", f.severity));
      card.appendChild(head);
      card.appendChild(el("div", "rc-body", f.message));
      container.appendChild(card);
    }
  }

  function renderAnalytics(container, payload) {
    if (payload.partial) {
      container.appendChild(el("p", "bad-badge",
        "PARTIAL: graph exceeds the analytics cap; computed on the first " + payload.node_count +
        " of " + payload.total_nodes + " nodes"));
    }
    var pr = (payload.centrality.global && payload.centrality.global.pagerank) || [];
    var comms = payload.communities.communities || [];
    var bridges = payload.bridges.bridges || [];

    // KPI strip: scale of the analysis at a glance.
    var grid = el("div", "kpi-grid");
    grid.appendChild(kpiCard({
      label: "Nodes analyzed", value: fmtInt(payload.node_count || 0),
      deltas: payload.partial ? [{ text: "capped", dir: "down" }] : [],
      baseline: payload.partial ? "of " + fmtInt(payload.total_nodes) + " total" : "full graph"
    }));
    grid.appendChild(kpiCard({
      label: "Communities", value: payload.communities.community_count || 0,
      deltas: [],
      baseline: comms.length ? "largest: " + Math.max.apply(null, comms.map(function (c) { return c.size; })) + " members" : "no communities detected"
    }));
    grid.appendChild(kpiCard({
      label: "Bridges", value: bridges.length,
      deltas: [],
      baseline: "surprising cross-community connections"
    }));
    grid.appendChild(kpiCard({
      label: "Top PageRank", value: pr.length ? Number(pr[0].score).toFixed(4) : "—",
      deltas: [],
      baseline: pr.length ? pr[0].node_id : "no nodes"
    }));
    container.appendChild(grid);

    // W20: community size share as a proportion bar (sizes were badge text).
    if (comms.length) {
      var totalSize = 0;
      for (var cs = 0; cs < comms.length; cs++) totalSize += comms[cs].size || 0;
      if (totalSize > 0) {
        var sizeCard = chartCard("Community size share", totalSize + " members across " + comms.length + " communities");
        var sorted = comms.slice().sort(function (x, y) { return (y.size || 0) - (x.size || 0); });
        var segs = [];
        var topN = Math.min(5, sorted.length);
        for (var si = 0; si < topN; si++) {
          segs.push({
            label: sorted[si].label || sorted[si].id,
            value: sorted[si].size || 0,
            color: DONUT_COLORS[si % DONUT_COLORS.length]
          });
        }
        var rest = 0;
        for (var sr = topN; sr < sorted.length; sr++) rest += sorted[sr].size || 0;
        if (rest > 0) segs.push({ label: "other (" + (sorted.length - topN) + ")", value: rest, color: "#D4D4D8" });
        sizeCard.appendChild(proportionBar(segs));
        container.appendChild(sizeCard);
      }
    }

    // PageRank leaderboard as a bar-list (visual) — mono ids, tabular values.
    container.appendChild(el("h3", "panel-title", "Centrality — top by PageRank"));
    if (!pr.length) {
      emptyNote(container, "(no nodes)");
    } else {
      var top = pr.slice(0, 12);
      var max = 0;
      for (var i = 0; i < top.length; i++) max = Math.max(max, Number(top[i].score) || 0);
      for (var j = 0; j < top.length; j++) {
        container.appendChild(barRow(top[j].node_id, Number(top[j].score) || 0, max,
          Number(top[j].score).toFixed(4)));
      }
    }

    var cols = el("div", "cols-2");
    var commCol = el("div");
    commCol.appendChild(el("h3", "panel-title", "Communities (" + (payload.communities.community_count || 0) + ")"));
    if (comms.length === 0) {
      emptyNote(commCol, "(none)");
    } else {
      for (var c = 0; c < comms.length; c++) {
        var comm = comms[c];
        var card = el("div", "rcard");
        var head = el("div", "rc-head");
        head.appendChild(el("span", "rc-title", comm.label || comm.id));
        head.appendChild(badge("dim", comm.size + " nodes"));
        card.appendChild(head);
        var bodyRow = el("div", "rc-body");
        bodyRow.appendChild(document.createTextNode("cohesion "));
        var track = el("span", "bt");
        track.style.display = "inline-block";
        track.style.width = "120px";
        track.style.verticalAlign = "middle";
        var fill = el("span", "bf");
        fill.style.width = Math.max(0, Math.min(100, (Number(comm.cohesion) || 0) * 100)) + "%";
        track.appendChild(fill);
        bodyRow.appendChild(track);
        bodyRow.appendChild(document.createTextNode(" " + comm.cohesion));
        card.appendChild(bodyRow);
        card.appendChild(el("div", "muted mono", "sig " + comm.membership_sig));
        commCol.appendChild(card);
      }
    }
    cols.appendChild(commCol);
    var bridgeCol = el("div");
    bridgeCol.appendChild(el("h3", "panel-title", "Bridges (surprising connections)"));
    if (bridges.length === 0) {
      emptyNote(bridgeCol, "(none)");
    } else {
      for (var b = 0; b < bridges.length; b++) {
        var br = bridges[b];
        var bcard = el("div", "rcard");
        var bhead = el("div", "rc-head");
        bhead.appendChild(el("span", "rc-title", br.source + " → " + br.target));
        bhead.appendChild(badge("info", br.relation));
        bcard.appendChild(bhead);
        var bbody = el("div", "rc-body");
        bbody.appendChild(document.createTextNode("surprise "));
        var btrack = el("span", "bt");
        btrack.style.display = "inline-block";
        btrack.style.width = "120px";
        btrack.style.verticalAlign = "middle";
        var bfill = el("span", "bf");
        bfill.style.width = Math.max(0, Math.min(100, (Number(br.surprise_score) || 0) * 100)) + "%";
        btrack.appendChild(bfill);
        bbody.appendChild(btrack);
        bbody.appendChild(document.createTextNode(" " + br.surprise_score + " · " + br.kind));
        bcard.appendChild(bbody);
        bridgeCol.appendChild(bcard);
      }
    }
    cols.appendChild(bridgeCol);
    container.appendChild(cols);
  }

  function renderAudit(container, payload) {
    // Visual chain timeline (T10 rail): seq / type / actor / time / payload,
    // failures and conflicts tinted with a semantic rail dot (B4).
    container.appendChild(el("p", "muted",
      payload.count + " most recent audit events (redacted) · hash-chained, verify above"));
    if (payload.count === 0) {
      emptyNote(container, "(no audit events)");
    } else {
      var tl = el("div", "tl");
      for (var i = 0; i < payload.events.length; i++) {
        var event = payload.events[i];
        var cls = "tl-row";
        if (/failed/.test(event.event_type)) cls += " row-error";
        else if (/conflict/.test(event.event_type)) cls += " row-warn";
        var row = el("div", cls);
        row.appendChild(el("span", "tl-seq", "#" + event.event_seq));
        row.appendChild(el("span", "tl-type", event.event_type));
        row.appendChild(el("span", "tl-actor", event.actor));
        row.appendChild(el("span", "tl-time", String(event.occurred_at).replace("T", " ").slice(0, 19)));
        row.appendChild(el("span", "tl-payload", JSON.stringify(event.payload)));
        tl.appendChild(row);
      }
      container.appendChild(tl);
    }
    // Refs render after the ledgers (audit chain > journals > diagnostics).
    var refsBox = document.getElementById("audit-refs");
    if (refsBox) {
      clear(refsBox);
      refsBox.appendChild(el("p", "empty", "loading refs…"));
      api("/api/refs").then(function (refs) {
        clear(refsBox);
        renderRefs(refsBox, refs);
      }).catch(function () {
        clear(refsBox);
      });
    }
  }

  function renderRefs(container, payload) {
    // W20: the raw status/count table becomes (1) a stacked proportion bar
    // with semantic colors + inline legend, and (2) a kind × status heatmap
    // matrix built from the three paged status endpoints (real rows).
    var mixCard = chartCard("Resolution mix", payload.total + " refs tracked");
    if (payload.total > 0) {
      mixCard.appendChild(proportionBar([
        { label: "resolved", value: payload.resolved, color: "#059669" },
        { label: "pending", value: payload.pending, color: "#D97706" },
        { label: "failed", value: payload.failed, color: "#DC2626" }
      ]));
    } else {
      emptyNote(mixCard, "(no unresolved references tracked)");
    }
    container.appendChild(mixCard);

    var matrixCard = chartCard("Refs by kind × status", "count per cell · intensity = share of column");
    var matrixHost = el("div", null);
    matrixHost.appendChild(el("p", "empty", "loading matrix…"));
    matrixCard.appendChild(matrixHost);
    container.appendChild(matrixCard);
    Promise.all([
      api("/api/refs/pending?limit=200"),
      api("/api/refs/resolved?limit=200"),
      api("/api/refs/failed?limit=200")
    ]).then(function (results) {
      clear(matrixHost);
      var kinds = {};
      var matrix = {}; // kind -> {pending, resolved, failed}
      var STATUSES = ["pending", "resolved", "failed"];
      for (var r = 0; r < 3; r++) {
        var refs = results[r].refs || [];
        for (var i = 0; i < refs.length; i++) {
          var kind = refs[i].kind || "?";
          if (!matrix[kind]) { matrix[kind] = { pending: 0, resolved: 0, failed: 0 }; kinds[kind] = true; }
          matrix[kind][STATUSES[r]] += 1;
        }
      }
      var names = Object.keys(kinds).sort();
      if (!names.length) {
        emptyNote(matrixHost, "(no refs in the window)");
        return;
      }
      var rows = names.map(function (kind) {
        var m = matrix[kind];
        return [kind, m.pending, m.resolved, m.failed, m.pending + m.resolved + m.failed];
      });
      var tbl = table(matrixHost, ["kind", "pending", "resolved", "failed", "total"], rows, "dense");
      heatmapCols(tbl, [1, 2, 3]);
    }).catch(function () {
      clear(matrixHost);
      emptyNote(matrixHost, "(matrix unavailable — paged ref endpoints failed)");
    });

    var cols = el("div", "cols-2");
    var left = el("div");
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

  function renderProposals(container, payload, improvements) {
    container.appendChild(el("p", "muted",
      payload.count + " review " + plural(payload.count, "candidate") + " (proposal-only; nothing here mutates the store)"));

    // W20: improvement pipeline funnel — proposed → applied, from the event
    // log (/api/improvements is part of this tab's feature contract).
    var proposed = improvements && improvements.count || 0;
    var applied = 0;
    if (improvements && improvements.proposals) {
      for (var a = 0; a < improvements.proposals.length; a++) {
        if (improvements.proposals[a].status === "applied") applied += 1;
      }
    }
    var pipeCard = chartCard("Improvement pipeline", "proposed → applied (event log)");
    if (proposed > 0) {
      pipeCard.appendChild(funnelChart([
        { label: "proposed", value: proposed },
        { label: "applied", value: applied }
      ]));
      pipeCard.appendChild(el("p", "muted",
        payload.count + " open review " + plural(payload.count, "signal") +
        " below may become the next " + plural(payload.count, "proposal")));
    } else {
      pipeCard.appendChild(el("p", "chart-empty",
        "No improvement proposals in the event log yet — the funnel fills when " +
        "knowledge.propose_improvement events land."));
    }
    container.appendChild(pipeCard);

    if (payload.count === 0) {
      emptyNote(container, "(no candidates — the review signal scan found nothing)");
      return;
    }
    var counts = payload.signal_counts || {};
    var countKeys = Object.keys(counts).sort();
    if (countKeys.length) {
      // W20: signal mix as a stacked proportion bar (was: plain KPI cards).
      var mixCard = chartCard("Signal mix", payload.count + " " + plural(payload.count, "candidate"));
      var SIGNAL_COLORS = ["#7C3AED", "#A78BFA", "#C4B5FD", "#DDD6FE", "#9CA3AF"];
      mixCard.appendChild(proportionBar(countKeys.map(function (key, idx) {
        return { label: key, value: counts[key], color: SIGNAL_COLORS[idx % SIGNAL_COLORS.length] };
      })));
      container.appendChild(mixCard);
    }
    for (var i = 0; i < payload.signals.length; i++) {
      var signal = payload.signals[i];
      var card = el("div", "rcard");
      var head = el("div", "rc-head");
      head.appendChild(el("span", "rc-title", signal.signal));
      head.appendChild(badge(signal.low_confidence ? "dim" : "info",
        signal.low_confidence ? "hint" : "primary"));
      card.appendChild(head);
      var body = el("div", "rc-body");
      var reasons = signal.reason_codes || [];
      for (var r = 0; r < reasons.length; r++) body.appendChild(el("span", "chip", reasons[r]));
      card.appendChild(body);
      var nodes = signal.source_node_ids || [];
      if (nodes.length) {
        var nodeRow = el("div", null);
        for (var n = 0; n < nodes.length; n++) {
          (function (nodeId) {
            var link = el("button", "linkish", nodeId);
            link.type = "button";
            link.style.display = "inline-block";
            link.style.marginRight = "10px";
            link.addEventListener("click", function () { showTab("graph"); selectNode(nodeId); });
            nodeRow.appendChild(link);
          })(nodes[n]);
        }
        card.appendChild(nodeRow);
      }
      container.appendChild(card);
    }
  }

  function renderDebt(container, payload) {
    if (payload.disabled) {
      emptyNote(container, "debt queue disabled: " + (payload.reason || ""));
      return;
    }
    // Visual priority queue: KPI strip (candidates/budget/overflow) + ranked
    // cards with a proportional priority bar.
    var grid = el("div", "kpi-grid");
    grid.appendChild(kpiCard({
      label: "Debt candidates", value: payload.candidate_count, deltas: [],
      baseline: "proposals only; nothing here mutates the store"
    }));
    grid.appendChild(kpiCard({
      label: "Budget", value: payload.budget, deltas: [],
      baseline: "per review pass"
    }));
    grid.appendChild(kpiCard({
      label: "Overflow", value: payload.overflow_count,
      deltas: [{ text: payload.overflow_count > 0 ? "beyond budget" : "within budget", dir: payload.overflow_count > 0 ? "down" : "up" }],
      baseline: "candidates past the budget line"
    }));
    container.appendChild(grid);
    if (!payload.items || payload.items.length === 0) {
      emptyNote(container, "(no debt candidates — the queue is clear)");
      return;
    }
    // W20: action mix as a proportion bar (was implicit in per-card badges).
    var actionCounts = {};
    for (var ac = 0; ac < payload.items.length; ac++) {
      var act = payload.items[ac].action || "?";
      actionCounts[act] = (actionCounts[act] || 0) + 1;
    }
    var actionNames = Object.keys(actionCounts).sort();
    if (actionNames.length > 1) {
      var ACTION_COLORS = ["#7C3AED", "#A78BFA", "#C4B5FD", "#DDD6FE", "#9CA3AF"];
      var actionCard = chartCard("Candidates by action", payload.items.length + " items");
      actionCard.appendChild(proportionBar(actionNames.map(function (name, idx) {
        return { label: name, value: actionCounts[name], color: ACTION_COLORS[idx % ACTION_COLORS.length] };
      })));
      container.appendChild(actionCard);
    }
    var maxPrio = 0;
    for (var i = 0; i < payload.items.length; i++) maxPrio = Math.max(maxPrio, Number(payload.items[i].priority) || 0);
    for (var j = 0; j < payload.items.length; j++) {
      var item = payload.items[j];
      var card = el("div", "rcard");
      var head = el("div", "rc-head");
      head.appendChild(el("span", "rank-badge", item.rank));
      head.appendChild(el("span", "rc-title", item.node_id));
      head.appendChild(badge("dim", item.action));
      var prioTrack = el("span", "prio-track");
      var prioFill = el("span", "prio-fill");
      prioFill.style.width = (maxPrio > 0 ? Math.max(3, (Number(item.priority) / maxPrio) * 100) : 0) + "%";
      prioTrack.appendChild(prioFill);
      head.appendChild(prioTrack);
      // 12-dp quantized priority is storage detail; 4dp keeps the ranking readable.
      head.appendChild(el("span", "muted mono", Number(item.priority).toFixed(4)));
      card.appendChild(head);
      var body = el("div", "rc-body");
      var signals = item.signals || [];
      for (var s = 0; s < signals.length; s++) body.appendChild(el("span", "chip", signals[s]));
      if (item.merge_target) {
        body.appendChild(el("span", "muted", "merge → "));
        body.appendChild(el("span", "mono", item.merge_target));
      }
      card.appendChild(body);
      container.appendChild(card);
    }
  }

  function renderExtractions(container, payload, summary) {
    // W20: queue funnel from /api/extractions/summary — submitted → reviewed →
    // applied (reviewed = applied + rejected; the in-review remainder is a
    // note, not a fake stage, because partitions are not stages).
    if (summary && summary.state === "ok" && summary.queue_size > 0) {
      var counts = summary.counts || {};
      var inReview = (counts.pending_review || 0) + (counts.corroborated || 0) + (counts.contradiction_flagged || 0);
      var reviewed = (counts.applied || 0) + (counts.rejected || 0);
      var funnelCard = chartCard("Review queue funnel", "extraction intake (contract v4)");
      funnelCard.appendChild(funnelChart([
        { label: "submitted", value: summary.queue_size },
        { label: "decided", value: reviewed },
        { label: "applied", value: counts.applied || 0 }
      ]));
      // The funnel's stage-to-stage gaps are joined by the end-to-end number
      // here — the headline rate belongs next to the funnel, not nowhere.
      var endToEnd = summary.queue_size > 0
        ? Math.round(((counts.applied || 0) / summary.queue_size) * 100) : 0;
      funnelCard.appendChild(el("p", "muted",
        "end-to-end: " + (counts.applied || 0) + "/" + summary.queue_size + " applied (" + endToEnd + "%)" +
        (inReview > 0 ?
          " · " + inReview + " still in review (" +
          (counts.pending_review || 0) + " pending · " +
          (counts.corroborated || 0) + " corroborated · " +
          (counts.contradiction_flagged || 0) + " flagged)" : "") +
        (counts.rejected ? " · " + counts.rejected + " rejected at review" : "")));
      container.appendChild(funnelCard);
    }
    // Provenance cards: status badge, confidence reported→effective as a dual
    // bar (marker = reported), corroboration +/- pills.
    container.appendChild(el("p", "muted",
      payload.count + " " + plural(payload.count, "submission") +
      " across all statuses (queue size " + payload.queue_size + ")"));
    if (!payload.rows || payload.rows.length === 0) {
      // K1 empty state: visual + heading + cause + next step.
      var empty = el("div", "empty-state");
      empty.appendChild(svgEmptyQueue());
      empty.appendChild(el("div", "empty-title", "Queue empty"));
      empty.appendChild(el("div", "empty-body",
        "No submissions are waiting for review. The queue fills when an agent " +
        "submits an extraction via the intake contract (v4)."));
      var hint = el("p", "muted", "Inspect the intake surface: ");
      hint.appendChild(el("code", null, "know-your-context extraction review"));
      empty.appendChild(hint);
      container.appendChild(empty);
      return;
    }
    for (var i = 0; i < payload.rows.length; i++) {
      var row = payload.rows[i];
      var card = el("div", "rcard");
      var head = el("div", "rc-head");
      head.appendChild(el("span", "rc-title", row.receipt_id));
      head.appendChild(badge(
        row.status === "applied" ? "ok" : row.status === "corroborated" ? "info" :
        row.status === "rejected" || row.status === "contradiction_flagged" ? "error" :
        row.status === "pending_review" ? "warn" : "dim", row.status));
      card.appendChild(head);
      var body = el("div", "rc-body");
      body.appendChild(el("span", "muted", row.actor + " · " + row.agent + " · " +
        row.nodes + " " + plural(row.nodes, "node") + " / " + row.edges + " " + plural(row.edges, "edge")));
      card.appendChild(body);
      var confRow = el("div", "conf-pair");
      confRow.appendChild(el("span", "muted", "confidence"));
      var track = el("span", "conf-track");
      var fill = el("span", "conf-fill");
      fill.style.width = Math.max(0, Math.min(100, (Number(row.confidence_effective) || 0) * 100)) + "%";
      track.appendChild(fill);
      var mark = el("span", "conf-mark");
      mark.style.left = Math.max(0, Math.min(100, (Number(row.confidence_reported) || 0) * 100)) + "%";
      track.appendChild(mark);
      confRow.appendChild(track);
      // 12-dp quantized strings are storage detail; the UI shows 2dp.
      confRow.appendChild(el("span", null,
        Number(row.confidence_reported).toFixed(2) + " → " + Number(row.confidence_effective).toFixed(2)));
      card.appendChild(confRow);
      var corr = el("div", "rc-body");
      // Zero evidence is neutral, not a gain (auditor: "+0" in green lies).
      corr.appendChild(el("span", "delta-pill " + (row.corroborators > 0 ? "up" : "flat"),
        "+" + row.corroborators + " " + plural(row.corroborators, "corroborator")));
      corr.appendChild(document.createTextNode(" "));
      corr.appendChild(el("span", "delta-pill " + (row.contradictions > 0 ? "down" : "flat"),
        "−" + row.contradictions + " " + plural(row.contradictions, "contradiction")));
      card.appendChild(corr);
      container.appendChild(card);
    }
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
      // W20 fix (audit): cards rank by rows desc — the biggest tables earn the
      // cards, not the alphabet (unresolved_refs used to lose to 1-row tables).
      var byRows = Object.keys(tables).map(function (name) {
        return { name: name, rows: Number(tables[name]) || 0 };
      }).sort(function (a, b) { return b.rows - a.rows; });
      byRows.slice(0, 8).forEach(function (entry) {
        pairs.push([entry.name, String(entry.rows) + (entry.rows === 1 ? " row" : " rows")]);
      });
      cardGrid(statsBox, pairs);
      // W20: table size share as a proportion bar above the raw tables list —
      // the store's weight distribution at a glance (top 6 + other).
      var tablePairs = byRows;
      var totalRows = 0;
      for (var tr = 0; tr < tablePairs.length; tr++) totalRows += tablePairs[tr].rows;
      if (totalRows > 0) {
        var shareCard = chartCard("Table size share", fmtInt(totalRows) + " rows across " + tablePairs.length + " tables");
        var segs = [];
        var topN = Math.min(6, tablePairs.length);
        for (var ti = 0; ti < topN; ti++) {
          segs.push({ label: tablePairs[ti].name, value: tablePairs[ti].rows, color: DONUT_COLORS[ti % DONUT_COLORS.length] });
        }
        var restRows = 0;
        for (var ri = topN; ri < tablePairs.length; ri++) restRows += tablePairs[ri].rows;
        if (restRows > 0) segs.push({ label: "other (" + (tablePairs.length - topN) + ")", value: restRows, color: "#D4D4D8" });
        shareCard.appendChild(proportionBar(segs));
        statsBox.appendChild(shareCard);
      }
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
      // W20: a score-history line above the table (T4 minimal register) — nine
      // identical rows read instantly as a flat line; a dip would pop.
      var snaps = payload.snapshots || [];
      if (snaps.length >= 2) {
        var histCard = chartCard("Health score history",
          String(snaps[0].recorded_at).slice(0, 10) + " → " + String(snaps[snaps.length - 1].recorded_at).slice(0, 10));
        histCard.appendChild(lineChart(snaps.map(function (snap) {
          return { label: String(snap.recorded_at).slice(5, 10), value: snap.score };
        }), { width: 640, height: 120 }));
        healthBox.appendChild(histCard);
      }
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
    // Structured panel: surface state KPI row, grouped op chips, rule notes.
    var grid = el("div", "kpi-grid");
    grid.appendChild(kpiCard({
      label: "Surface", value: payload.frozen ? "frozen" : "live",
      deltas: [{ text: payload.frozen ? "contract-locked" : "mutable", dir: payload.frozen ? "up" : "down" }],
      baseline: "frozen surface = every change is a contract version"
    }));
    grid.appendChild(kpiCard({
      label: "Knowledge operations", value: payload.knowledge_operations.length, deltas: [],
      baseline: "the pinned knowledge surface"
    }));
    grid.appendChild(kpiCard({
      label: "Ops operations", value: payload.ops_operations.length, deltas: [],
      baseline: "operational surface (serve/system)"
    }));
    container.appendChild(grid);
    container.appendChild(el("h3", "panel-title",
      "Knowledge operations (" + payload.knowledge_operations.length + ")"));
    var kwrap = el("div", null);
    for (var i = 0; i < payload.knowledge_operations.length; i++) {
      kwrap.appendChild(el("span", "chip", payload.knowledge_operations[i]));
    }
    container.appendChild(kwrap);
    container.appendChild(el("h3", "panel-title", "Ops operations (" + payload.ops_operations.length + ")"));
    var owrap = el("div", null);
    for (var j = 0; j < payload.ops_operations.length; j++) {
      owrap.appendChild(el("span", "chip", payload.ops_operations[j]));
    }
    container.appendChild(owrap);
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
      var level = progress.state === "done" ? "ok" : progress.state === "failed" ? "error" : "warn";
      // N6: progress twice — status badge text AND a proportional bar.
      var headRow = el("p", null);
      headRow.appendChild(badge(level, progress.state));
      if (progress.updated_at) headRow.appendChild(el("span", "muted", " · updated " + progress.updated_at));
      progressBox.appendChild(headRow);
      if (!progress.steps || !progress.steps.length) {
        var note = el("p", "empty", "(no install recorded — run: ");
        note.appendChild(el("code", null, "know-your-context setup"));
        note.appendChild(document.createTextNode(")"));
        progressBox.appendChild(note);
      } else {
        var done = 0;
        for (var c = 0; c < progress.steps.length; c++) {
          if (progress.steps[c].status === "done") done++;
        }
        var track = el("div", "progress-track");
        var fill = el("div", "progress-fill");
        fill.style.width = Math.round((done / progress.steps.length) * 100) + "%";
        track.appendChild(fill);
        progressBox.appendChild(track);
        progressBox.appendChild(el("p", "progress-note",
          done + " / " + progress.steps.length + " steps done"));
        // E8 stepper: numbered state circles, SVG check for done.
        var stepper = el("div", "stepper");
        for (var i = 0; i < progress.steps.length; i++) {
          var step = progress.steps[i];
          var row = el("div", "stepper-row " + step.status);
          var ico = el("span", "step-ico");
          if (step.status === "done") {
            ico.appendChild(svgIconCheck());
          } else if (step.status === "failed") {
            ico.textContent = "×";
          } else if (step.status === "skipped") {
            ico.textContent = "–";
          } else {
            ico.textContent = String(i + 1);
          }
          row.appendChild(ico);
          row.appendChild(el("span", "name", step.name));
          row.appendChild(badge(step.status === "done" ? "ok" : step.status === "failed" ? "error" :
            step.status === "skipped" ? "dim" : "warn", step.status));
          if (step.detail) row.appendChild(el("span", "detail", step.detail));
          stepper.appendChild(row);
        }
        progressBox.appendChild(stepper);
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

  function svgEmptyQueue() {
    // K1 visual: low-contrast funnel outline (same family as the nav icon).
    var svg = svgEl("svg", { viewBox: "0 0 48 48", width: 48, height: 48 });
    svg.appendChild(svgEl("path", {
      d: "M8 10h32L26 28v10l-6 3V28L8 10Z", fill: "none", stroke: "#D4D4D8",
      "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round"
    }));
    return svg;
  }

  function svgIconCheck() {
    // SVG check mark — a text check glyph would trip the no-emoji invariant.
    var svg = svgEl("svg", { viewBox: "0 0 12 12", width: 12, height: 12 });
    var path = svgEl("path", {
      d: "M2.5 6.2 5 8.7 9.5 3.6", fill: "none", stroke: "#fff",
      "stroke-width": 1.6, "stroke-linecap": "round", "stroke-linejoin": "round"
    });
    svg.appendChild(path);
    return svg;
  }

  // ------------------------------------------------------------------ exports

  document.getElementById("export-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var nodeId = document.getElementById("export-node").value.trim();
    var domain = document.getElementById("export-domain").value.trim();
    var kind = document.getElementById("export-kind").value;
    var body = document.getElementById("exports-body");
    clear(body); // also drops the static empty state
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
      var card = el("div", "rcard");
      var head = el("div", "rc-head");
      head.appendChild(el("span", "sdot ok"));
      head.appendChild(el("span", "rc-title", kind + " export ready"));
      card.appendChild(head);
      var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      var link = el("a", null, "download " + kind + " export (JSON)");
      link.href = URL.createObjectURL(blob);
      link.download = "kyc-" + kind + "-export.json";
      var linkRow = el("div", "rc-body");
      linkRow.appendChild(link);
      card.appendChild(linkRow);
      var nodeCount = payload.node_count !== undefined ? payload.node_count :
        (payload.nodes ? payload.nodes.length : "?");
      card.appendChild(el("p", "muted mono", "export built: " + nodeCount + " nodes"));
      body.appendChild(card);
    }).catch(function (error) {
      clear(body);
      body.appendChild(el("p", "bad-badge", String(error.message || error)));
    });
  });

  // ------------------------------------------------------------- code intel

  function renderCodeintel(container, op, payload) {
    if (payload.kind && payload.kind.indexOf("analytics.code_intel.") === 0) {
      container.appendChild(el("p", "muted", payload.kind + " — nodes " + payload.node_count +
        ", edges " + payload.edge_count + (payload.guards && payload.guards.length ?
        " — GUARDS: " + payload.guards.join(", ") : "")));
    }
    if (op === "callers" || op === "callees" || op === "impact") {
      // Result diagram: root card + one lane per depth (chips, accent left rail).
      if (payload.root) {
        var rootCard = el("div", "ci-root-card",
          payload.root.name + " (" + payload.root.kind + ") — " + payload.root.path + ":" + payload.root.line +
          " — total " + payload.total);
        container.appendChild(rootCard);
      }
      if (payload.impacted_files) {
        // W20: impacted files as chips (was one comma-joined text run).
        var filesNote = el("div", null);
        filesNote.appendChild(el("span", "muted", "impacted files (" + payload.impacted_file_count + "): "));
        for (var fi = 0; fi < payload.impacted_files.length; fi++) {
          filesNote.appendChild(el("span", "chip", payload.impacted_files[fi]));
        }
        container.appendChild(filesNote);
      }
      var entries = payload.entries || [];
      if (entries.length === 0) {
        emptyNote(container, op === "callers" ? "(no callers)" : op === "callees" ? "(no callees)" : "(no impacted nodes)");
        return;
      }
      var lanes = el("div", "ci-lanes");
      var byDepth = {};
      var depths = [];
      for (var i = 0; i < entries.length; i++) {
        var d = entries[i].depth;
        if (!byDepth[d]) { byDepth[d] = []; depths.push(d); }
        byDepth[d].push(entries[i]);
      }
      depths.sort(function (a, b) { return a - b; });
      for (var j = 0; j < depths.length; j++) {
        var lane = el("div", "ci-lane");
        lane.appendChild(el("div", "ci-lane-title", "depth " + depths[j]));
        var list = byDepth[depths[j]];
        for (var k = 0; k < list.length; k++) {
          var chip = el("div", "ci-chip");
          var chipHead = el("div", "rc-head");
          chipHead.appendChild(el("span", "rc-title", list[k].name));
          chipHead.appendChild(badge("info", list[k].kind));
          chip.appendChild(chipHead);
          var meta = el("div", "meta");
          meta.appendChild(el("span", "chip", list[k].path + ":" + list[k].line));
          meta.appendChild(el("span", "chip", "via " + list[k].via_edge_kind +
            (list[k].via_line ? " @ " + list[k].via_line : "")));
          chip.appendChild(meta);
          lane.appendChild(chip);
        }
        lanes.appendChild(lane);
      }
      container.appendChild(lanes);
      return;
    }
    if (op === "path") {
      if (!payload.paths || !payload.paths.length) {
        emptyNote(container, "(no dependency path)");
        return;
      }
      // Each path renders as a horizontal node chain with arrows.
      payload.paths.forEach(function (p) {
        container.appendChild(el("p", "muted", "cost " + p.cost));
        var chain = el("div", "ci-path");
        for (var i = 0; i < p.node_ids.length; i++) {
          if (i > 0) chain.appendChild(el("span", "ci-arrow", "→"));
          chain.appendChild(el("span", "ci-chip", p.node_ids[i]));
        }
        container.appendChild(chain);
      });
      return;
    }
    if (op === "co-change") {
      container.appendChild(el("p", "muted", payload.change_set_count + " change sets, " +
        payload.total_pairs + " pairs"));
      var pairs = payload.pairs || [];
      if (pairs.length === 0) {
        emptyNote(container, "(no co-change pairs above the support threshold)");
        return;
      }
      var maxConf = 0;
      for (var m = 0; m < pairs.length; m++) {
        maxConf = Math.max(maxConf, Number(pairs[m].confidence_a_to_b) || 0, Number(pairs[m].confidence_b_to_a) || 0);
      }
      for (var q = 0; q < pairs.length; q++) {
        var pair = pairs[q];
        var card = el("div", "rcard");
        var head = el("div", "rc-head");
        head.appendChild(el("span", "rc-title", pair.file_a + " ↔ " + pair.file_b));
        head.appendChild(badge("dim", "support " + pair.support));
        card.appendChild(head);
        var body = el("div", "rc-body");
        body.appendChild(barRow("a→b", Number(pair.confidence_a_to_b) || 0, maxConf, String(pair.confidence_a_to_b)));
        body.appendChild(barRow("b→a", Number(pair.confidence_b_to_a) || 0, maxConf, String(pair.confidence_b_to_a)));
        card.appendChild(body);
        container.appendChild(card);
      }
      return;
    }
    if (op === "boundary") {
      container.appendChild(el("p", "muted", payload.total_violations + " violations, " +
        payload.unlayered_nodes + " unlayered nodes, " + payload.skipped_unlayered_edges +
        " skipped edges"));
      var violations = payload.violations || [];
      if (violations.length === 0) {
        emptyNote(container, "(no boundary violations)");
        return;
      }
      for (var v = 0; v < violations.length; v++) {
        var violation = violations[v];
        var vcard = el("div", "rcard");
        var vhead = el("div", "rc-head");
        vhead.appendChild(el("span", "sdot error"));
        vhead.appendChild(el("span", "rc-title",
          violation.from_layer + " → " + violation.to_layer));
        vhead.appendChild(badge("error", violation.edge_kind));
        vcard.appendChild(vhead);
        vcard.appendChild(el("div", "rc-body mono",
          violation.source_path + " → " + violation.target_path));
        container.appendChild(vcard);
      }
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

  // ------------------------------------------------------------- system tab
  // Health + Store + System merged into one tab with three panels (SUB-149).

  function loadSystemPanel(mode) {
    mode = mode || SUBMODE.system;
    if (mode === "health") loadPanel("health-body", "/api/health", renderHealth);
    else if (mode === "store") renderStore();
    else renderSystem();
  }

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
  refreshers.search = function () {
    // merged tab: the active mode decides what refreshes
    if (SUBMODE.search === "browse") loadBrowse();
    // search mode is interactive; it polls nothing
  };
  refreshers.metrics = function () { loadMetrics(); };
  refreshers.analytics = function () { loadPanel("analytics-body", "/api/analytics", renderAnalytics); };
  refreshers.audit = function () {
    loadPanel("audit-body", "/api/audit?limit=100", renderAudit);
    renderLedgers();
  };
  refreshers.proposals = function () {
    // W20: the tab needs both payloads — signals + the improvement lifecycle.
    var container = document.getElementById("proposals-body");
    clear(container);
    container.appendChild(el("p", "empty", "loading…"));
    Promise.all([api("/api/proposals"), api("/api/improvements")]).then(function (results) {
      clear(container);
      renderProposals(container, results[0], results[1]);
      setStatus("");
    }).catch(function (error) {
      clear(container);
      container.appendChild(el("p", "bad-badge", String(error.message || error)));
      setStatus(String(error.message || error), true);
    });
  };
  refreshers.debt = function () { loadPanel("debt-body", "/api/debt", renderDebt); };
  refreshers.codeintel = function () { /* interactive form; runs on submit */ };
  refreshers.extractions = function () {
    // W20: cards + the queue-status summary for the funnel.
    var container = document.getElementById("extractions-body");
    clear(container);
    container.appendChild(el("p", "empty", "loading…"));
    Promise.all([api("/api/extractions"), api("/api/extractions/summary")]).then(function (results) {
      clear(container);
      renderExtractions(container, results[0], results[1]);
      setStatus("");
    }).catch(function (error) {
      clear(container);
      container.appendChild(el("p", "bad-badge", String(error.message || error)));
      setStatus(String(error.message || error), true);
    });
  };
  refreshers.system = function () { loadSystemPanel(); };
  refreshers.exports = function () { /* interactive form; runs on submit */ };
  refreshers.contracts = function () { loadPanel("contracts-body", "/api/contracts", renderContracts); };
  refreshers.setup = function () { renderSetup(); };

  // Merged-tab mode wiring: Search = search|browse; System = health|store|ops.
  wireModes("search", ["search", "browse"], function (mode) {
    if (mode === "browse") { loadBrowseStats(); loadBrowse(); }
    document.getElementById("search-seg-note").textContent = mode === "browse" ?
      "paged raw tables: nodes, relations, code edges, symbols, refs" :
      "symbol search across the code graph · browse paged tables";
  });
  wireModes("system", ["health", "store", "ops"], function (mode) {
    loadSystemPanel(mode);
    document.getElementById("system-seg-note").textContent =
      mode === "health" ? "diagnostics: findings, checks, severity" :
      mode === "store" ? "store introspection: stats, tables, schema versions" :
      "runtime: hardware, relearn gate, switches, config, routes";
  });

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
