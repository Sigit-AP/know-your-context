"""UI asset structural contract (W17 SUB-138).

The UI is a shipped artifact with no build step, so its invariants are tested
directly against the files: tab/view pairing, the no-markup-injection safety
contract, no emoji, dark theme, the activity-log interaction surface
(filters, grouping, autoscroll, ring buffer), the live-refresh poll loop, and
JS syntax (node --check when a node binary is available).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "src" / "know_your_context" / "serve" / "webview_assets"
HTML = (ASSETS / "index.html").read_text(encoding="utf-8")
CSS = (ASSETS / "style.css").read_text(encoding="utf-8")
JS = (ASSETS / "app.js").read_text(encoding="utf-8")

EXPECTED_TABS = [
    "graph",
    "activity",
    "search",
    "browse",
    "metrics",
    "health",
    "analytics",
    "audit",
    "proposals",
    "debt",
    "codeintel",
    "extractions",
    "store",
    "system",
    "bench",
    "ledgers",
    "exports",
    "contracts",
    "setup",
]

_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]"  # pictographs, dingbats, variation selector
)


class TestTabs:
    @pytest.mark.parametrize("tab", EXPECTED_TABS)
    def test_tab_button_exists(self, tab: str) -> None:
        assert f'data-tab="{tab}"' in HTML

    @pytest.mark.parametrize("tab", EXPECTED_TABS)
    def test_view_section_exists(self, tab: str) -> None:
        assert f'id="view-{tab}"' in HTML

    def test_every_button_has_a_view(self) -> None:
        buttons = set(re.findall(r'data-tab="([a-z-]+)"', HTML))
        views = set(re.findall(r'id="view-([a-z-]+)"', HTML))
        assert buttons == views

    def test_every_tab_has_a_refresher_registered(self) -> None:
        for tab in EXPECTED_TABS:
            assert f"refreshers.{tab}" in JS, tab


class TestSafetyContract:
    def test_no_inner_html(self) -> None:
        assert "innerHTML" not in JS

    def test_no_eval(self) -> None:
        assert "eval(" not in JS

    def test_no_cdn_or_external_subresource(self) -> None:
        assert "integrity=" not in HTML
        body = HTML.lower().replace("https://www.w3.org", "")
        assert "https://" not in body
        assert "http://" not in body

    def test_light_theme_declared(self) -> None:
        # W18 SUB-146: the webview is restyled to the operator-approved light
        # theme (docs/design/graph-proto tokens). The invariant is unchanged
        # in strength: exactly one declared theme, pinned to its tokens.
        assert 'data-theme="light"' in HTML
        assert "--bg-page: #EBEDF1" in CSS
        assert "--accent: #7C3AED" in CSS

    def test_no_emoji_anywhere(self) -> None:
        for name, text in (("index.html", HTML), ("style.css", CSS), ("app.js", JS)):
            assert not _EMOJI_RE.search(text), f"emoji in {name}"


class TestActivitySurface:
    def test_level_filters(self) -> None:
        for level in ("info", "warn", "error"):
            assert f'id="activity-level-{level}"' in HTML

    def test_search_grouping_autoscroll_controls(self) -> None:
        for element_id in (
            "activity-search",
            "activity-group",
            "activity-autoscroll",
            "activity-agent",
            "activity-clear",
            "activity-resume",
            "leases-body",
        ):
            assert f'id="{element_id}"' in HTML, element_id

    def test_ring_buffer_bound_in_client(self) -> None:
        assert "ACTIVITY_CAP = 2000" in JS
        assert "splice(0, activity.entries.length - ACTIVITY_CAP)" in JS

    def test_tail_polling_uses_since(self) -> None:
        assert "/api/activity/tail?since=" in JS

    def test_grouping_collapses_bursts(self) -> None:
        assert "GROUP_WINDOW_MS" in JS
        assert "last.entry.agent === entry.agent && last.entry.action === entry.action" in JS

    def test_agent_numbering(self) -> None:
        assert '"agent #" + (index >= 0 ? index + 1 : "?")' in JS

    def test_lease_row_renders_holder_and_expiry(self) -> None:
        assert "leased to" in JS
        assert "lease.expires_at" in JS

    def test_smart_autoscroll_pauses_when_scrolled_up(self) -> None:
        assert "userScrolledUp" in JS
        assert "scrollHeight - els.list.scrollTop - els.list.clientHeight < 40" in JS


class TestLiveness:
    def test_poll_loop_registered(self) -> None:
        assert "setInterval(" in JS
        assert "refreshers[ACTIVE_TAB]" in JS

    def test_interval_comes_from_the_knob(self) -> None:
        assert "live_refresh_seconds" in JS
        assert "/api/setup" in JS

    def test_zero_freezes_polling(self) -> None:
        assert "if (LIVE.seconds <= 0) return;" in JS

    def test_liveness_indicator_present(self) -> None:
        assert 'id="liveness"' in HTML


class TestGraphMechanics:
    """Structural pins for the DOM-graph mechanics (W18 SUB-146).

    Behavioural coverage lives in test_webview_graph_dom.py (node harness);
    these pins guard the load-bearing strings so a refactor cannot silently
    drop a mechanic while keeping the DOM test green by accident.
    """

    def test_nodes_are_dom_cards_not_canvas(self) -> None:
        assert 'id="graph-canvas"' in HTML
        assert "getContext" not in JS  # no <canvas> 2D context anymore
        assert "gnodeElement" in JS
        assert 'el("div", "gnode")' in JS

    def test_edges_are_bezier_svg_paths(self) -> None:
        assert "createElementNS" in JS
        assert 'createElementNS(SVG_NS, "path")' in JS
        assert '" C "' in JS  # cubic bezier command in the path data

    def test_click_expands_and_selects(self) -> None:
        assert 'classList.toggle("expanded"' in JS
        assert 'classList.toggle("selected"' in JS

    def test_panel_slides_open_and_close_collapses(self) -> None:
        assert 'panel.classList.add("open")' in JS
        assert 'panel.classList.add("hidden")' in JS
        assert "deselectGraph" in JS

    def test_click_outside_collapses(self) -> None:
        assert 'closest(".gnode, .edge-add, #detail-panel, .hud")' in JS

    def test_pan_zoom_fit_and_hud(self) -> None:
        assert "fitView" in JS
        assert "applyGraphTransform" in JS
        for element_id in ("graph-zoom-in", "graph-zoom-out", "graph-zoom-label", "graph-hud-fit"):
            assert f'id="{element_id}"' in HTML, element_id

    def test_edge_midpoint_markers(self) -> None:
        assert 'el("button", "edge-add", "+")' in JS

    def test_dot_grid_canvas(self) -> None:
        assert "radial-gradient(circle, var(--dot) 1px" in CSS
        assert "22px 22px" in CSS

    def test_expanded_card_full_content_and_meta_badges(self) -> None:
        assert "gnode-content" in JS  # full summary block, not truncated
        assert "gnode-stat" in JS  # footer meta badges (kind/score/version)
        assert "gnode-link" in JS  # connections list

    def test_properties_panel_structure(self) -> None:
        assert "dpSection" in JS
        assert "dp-kv" in JS
        assert "dp-eyebrow" in JS


class TestGraphFidelity:
    """SUB-147 pins: the three measured fidelity gaps must stay closed.

    (1) deterministic per-node gradient tile identity, (2) hairline cards
    with an always-visible menu affordance, (3) stats in the bottom HUD
    strip next to the zoom pill.
    """

    def test_tile_identity_is_deterministic_from_node_id(self) -> None:
        assert "assignTileGradients" in JS
        assert "fnv1a" in JS  # stable hash of the node id, same family as foto-1 sha8
        assert "GTILE_GRADIENTS" in JS
        assert "linear-gradient(135deg" in JS
        assert "tile.style.background = graphTileStyles[node.id]" in JS

    def test_hierarchy_endpoints_stay_dark(self) -> None:
        assert 'kind === "root" || kind === "leaf"' in JS
        assert '"#1F2937"' in JS  # GTILE_DARK

    def test_cards_have_no_shadow_only_hairline(self) -> None:
        block = re.search(r"\.gnode\s*\{(?P<body>[^}]*)\}", CSS)
        assert block is not None
        assert "box-shadow: none" in block.group("body")
        assert ".gnode:hover { box-shadow" not in CSS
        assert ".gnode.selected {" in CSS and "0 0 0 3px" not in CSS

    def test_card_menu_always_visible(self) -> None:
        assert 'el("span", "gnode-menu"' in JS
        menu_rules = re.findall(r"\.gnode-menu[^{]*\{[^}]*\}", CSS)
        assert menu_rules, "gnode-menu rule missing"
        for rule in menu_rules:
            assert ":hover" not in rule
            assert "display: none" not in rule

    def test_stats_live_in_bottom_hud_not_toolbar(self) -> None:
        assert 'id="graph-stats" class="muted"' not in HTML  # gone from the toolbar
        assert 'class="hud-stats" id="graph-stats"' in HTML
        for element_id in ("graph-stat-nodes", "graph-stat-edges", "graph-chain-verify"):
            assert f'id="{element_id}"' in HTML, element_id
        assert 'class="hud" id="graph-hud"' in HTML
        hud_block = HTML.split('class="hud" id="graph-hud"', 1)[1]
        assert "hud-stats" in hud_block.split("</section>", 1)[0]
        assert "/api/audit/verify" in JS  # chain pill is wired, not decorative

    def test_panel_lists_one_row_per_relation(self) -> None:
        # linkish buttons must be block-level: inline flow concatenated the
        # relations rows ("supports → n-deltaparent_of → n-delta").
        block = re.search(r"\.linkish\s*\{(?P<body>[^}]*)\}", CSS)
        assert block is not None
        assert "display: block" in block.group("body")
        assert 'classList.add("open")' in JS  # panel still slides open


class TestSyntax:
    def test_app_js_parses(self) -> None:
        node = shutil.which("node") or shutil.which("node", path="/root/.hermes/tools/node-26.12.1-linux-x64/bin")
        if node is None:
            pytest.skip("no node binary on this host")
        result = subprocess.run(  # noqa: S603 - fixed binary path, fixed args
            [node, "--check", str(ASSETS / "app.js")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    def test_css_braces_balanced(self) -> None:
        assert CSS.count("{") == CSS.count("}")
