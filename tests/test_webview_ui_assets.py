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

    def test_dark_theme_declared(self) -> None:
        assert 'data-theme="dark"' in HTML
        assert "--bg: #0d1117" in CSS

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
