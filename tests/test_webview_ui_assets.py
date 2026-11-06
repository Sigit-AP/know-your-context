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


class TestSidebarFidelity:
    """SUB-148 pins: the sidebar must hold the professional standard of
    PATTERNS.md A2/A4 (shell) and I1-I4 (icon system).

    (1) One inline-SVG outline icon set for all 19 nav items (16px grid,
    1.5px stroke, round caps, currentColor) — no text/Unicode glyphs.
    (2) Active state = neutral soft-gray pill #F3F4F6 + darker ink; accent
    purple is reserved for small badges, not the active row.
    (3) Section labels: 11px TITLE CASE (not uppercase), muted #98A2B3,
    aligned with the icon column (sidebar-nav-design hard rule).
    (4) Item metrics: 32px tall, 34px pitch, 8px radius, 10px padding,
    10px icon gap.
    (5) Logo row = bordered switcher pill (38px, radius 10px) aligned to
    the icon column; gradient brand tile; small tidy badge.
    """

    @staticmethod
    def _nav_block() -> str:
        return HTML.split('id="tabs"', 1)[1].split("</nav>", 1)[0]

    @staticmethod
    def _css_rule(selector: str) -> str:
        block = re.search(re.escape(selector) + r"\s*\{(?P<body>[^}]*)\}", CSS)
        assert block is not None, f"rule missing: {selector}"
        return block.group("body")

    def test_every_nav_item_has_inline_svg_icon(self) -> None:
        nav = self._nav_block()
        buttons = re.findall(r"<button[^>]*nav-item[^>]*>.*?</button>", nav, re.DOTALL)
        assert len(buttons) == 19
        for button in buttons:
            assert "<svg" in button, button[:80]

    def test_icons_share_one_outline_spec(self) -> None:
        nav = self._nav_block()
        svgs = re.findall(r"<svg[^>]*>", nav)
        assert len(svgs) == 19
        for svg in svgs:
            assert 'viewBox="0 0 16 16"' in svg
            assert 'fill="none"' in svg
            assert 'stroke="currentColor"' in svg
            assert 'stroke-width="1.5"' in svg
            assert 'stroke-linecap="round"' in svg
            assert 'stroke-linejoin="round"' in svg

    def test_no_text_glyph_icons_left_in_nav(self) -> None:
        nav = self._nav_block()
        for glyph in "◈⌕≡≣↗⌗□◷⇥∑▤⌁▦§⇓¶◐▸":
            assert glyph not in nav, f"legacy glyph {glyph} still in nav"
        assert re.search(r">\s*\+\s*<", nav) is None  # bare "+" was Health

    def test_icons_render_at_16px_fixed_box(self) -> None:
        body = self._css_rule(".nav-ico")
        assert "width: 16px" in body
        assert "height: 16px" in body
        svg_body = self._css_rule(".nav-ico svg")
        assert "display: block" in svg_body

    def test_active_state_is_neutral_pill_not_accent(self) -> None:
        body = self._css_rule(".nav-item.active")
        assert "background: var(--active-bg)" in body
        assert "color: var(--text-primary)" in body
        assert "--accent-soft" not in body
        assert "var(--accent)" not in body
        ico = self._css_rule(".nav-item.active .nav-ico")
        assert "var(--accent)" not in ico
        assert "color: var(--text-primary)" in ico

    def test_active_pill_token_is_soft_gray(self) -> None:
        assert "--active-bg: #F3F4F6" in CSS
        assert "--text-primary: #101828" in CSS
        assert "--text-nav: #344054" in CSS
        assert "--icon: #667085" in CSS
        assert "--text-muted: #98A2B3" in CSS

    def test_accent_survives_only_as_small_badges(self) -> None:
        # The logo badge keeps the one accent moment (foto-1 CEO badge).
        badge = self._css_rule(".logo-badge")
        assert "var(--accent)" in badge
        assert "border-radius: 999px" in badge

    def test_section_label_spec(self) -> None:
        body = self._css_rule(".nav-label")
        assert "font-size: 11px" in body
        assert "font-weight: 500" in body
        assert "letter-spacing: 0.015em" in body
        assert "color: var(--text-muted)" in body
        assert "text-transform" not in body  # title case, NOT uppercase
        assert "margin: 10px 0 4px 10px" in body  # left edge = icon column

    def test_no_divider_lines_between_sections(self) -> None:
        body = self._css_rule(".nav-label")
        assert "border" not in body
        # .nav-section carries no visual chrome at all (whitespace only).
        block = re.search(r"\.nav-section\s*\{[^}]*\}", CSS)
        assert block is None or "border" not in block.group(0)

    def test_nav_item_metrics(self) -> None:
        # Dense-nav deviation, reasoned from the skill's density principle:
        # 19 items -> 28px rows (skill range for 15+ items: 26-30px) so the
        # whole set fits at 900px without scrolling.
        body = self._css_rule(".nav-item")
        assert "height: 28px" in body
        assert "gap: 10px" in body
        assert "padding: 0 10px" in body
        assert "border-radius: 7px" in body
        assert "font-size: 13.5px" in body
        assert "color: var(--text-nav)" in body
        gap = self._css_rule(".nav-item + .nav-item")
        assert "margin-top: 2px" in gap  # 28px + 2px = 30px pitch

    def test_icon_color_token(self) -> None:
        body = self._css_rule(".nav-ico")
        assert "width: 16px" in body
        assert "height: 16px" in body
        assert "color: var(--icon)" in body

    def test_logo_row_is_switcher_pill_aligned_to_icons(self) -> None:
        body = self._css_rule(".logo-row")
        assert "height: 38px" in body
        assert "border-radius: 10px" in body
        assert "border: 1px solid var(--border-strong)" in body
        assert "margin: 8px 10px 4px" in body
        assert "padding: 0 10px" in body  # 10+10 = 20px = icon column
        mark = self._css_rule(".logo-mark")
        assert "linear-gradient(135deg" in mark  # brand tile gradient
        badge = self._css_rule(".logo-badge")
        assert "font-size: 9.5px" in badge

    def test_nav_scrollbar_is_slim_not_native_arrows(self) -> None:
        assert ".nav::-webkit-scrollbar" in CSS
        assert ".nav::-webkit-scrollbar-button" in CSS  # kills the ▲▼ chrome

    def test_promo_icon_is_svg_not_glyph(self) -> None:
        promo = HTML.split('class="promo"', 1)[1].split('class="user-row"', 1)[0]
        assert "⌁" not in promo
        assert "<svg" in promo


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
