"""Tests for the W15 config-knob registry in ``ops.switches`` (SUB-131).

Every webview tunable has exactly one declared knob with a dedicated
``KNOW_YOUR_CONTEXT_WEBVIEW_*`` env var, parsed strictly (fail-closed on
anything ambiguous), range-checked, and consumed by the real server. The
registry is self-documenting (``describe_knobs``) and each knob's default
equals the historical hard-coded constant it replaces.
"""

from __future__ import annotations

import pytest

from know_your_context.ops import switches
from know_your_context.ops.switches import (
    CONFIG_KNOBS,
    ConfigKnob,
    KnobBoard,
    SwitchError,
    describe_knobs,
    load_knobs,
    parse_knob_value,
)
from know_your_context.serve import webview

ENV_PREFIX = "KNOW_YOUR_CONTEXT_WEBVIEW_"

#: W15 SUB-134 extended the registry beyond webview: storage.* + hw.* knobs.
KNOB_NAME_FAMILIES = ("webview.", "storage.", "hw.")
KNOB_ENV_PREFIXES = ("KNOW_YOUR_CONTEXT_WEBVIEW_", "KNOW_YOUR_CONTEXT_STORAGE_", "KNOW_YOUR_CONTEXT_HW_", "KYS_")


# ------------------------------------------------------------------ registry


class TestKnobRegistry:
    def test_at_least_forty_knobs(self) -> None:
        assert len(CONFIG_KNOBS) >= 40

    def test_names_unique(self) -> None:
        names = [knob.name for knob in CONFIG_KNOBS]
        assert len(names) == len(set(names))

    def test_env_names_unique(self) -> None:
        envs = [knob.env for knob in CONFIG_KNOBS]
        assert len(envs) == len(set(envs))

    def test_env_prefix(self) -> None:
        for knob in CONFIG_KNOBS:
            assert knob.env.startswith(KNOB_ENV_PREFIXES), knob.env

    def test_names_dotted_lowercase(self) -> None:
        for knob in CONFIG_KNOBS:
            assert knob.name == knob.name.lower()
            assert "." in knob.name
            assert knob.name.startswith(KNOB_NAME_FAMILIES), knob.name

    def test_kinds_known(self) -> None:
        for knob in CONFIG_KNOBS:
            assert knob.kind in ("int", "float", "bool")

    def test_defaults_within_bounds(self) -> None:
        for knob in CONFIG_KNOBS:
            if knob.minimum is not None:
                assert float(knob.default) >= knob.minimum, knob.name
            if knob.maximum is not None:
                assert float(knob.default) <= knob.maximum, knob.name

    def test_affects_documented(self) -> None:
        for knob in CONFIG_KNOBS:
            assert knob.affects.strip(), knob.name

    def test_bool_knobs_have_bool_defaults(self) -> None:
        for knob in CONFIG_KNOBS:
            if knob.kind == "bool":
                assert isinstance(knob.default, bool)
            else:
                assert not isinstance(knob.default, bool)

    def test_malformed_spec_rejected(self) -> None:
        with pytest.raises(SwitchError):
            ConfigKnob(name="Bad", env="KNOW_YOUR_CONTEXT_WEBVIEW_X", kind="int", default=1, affects="x")
        with pytest.raises(SwitchError):
            ConfigKnob(name="ok.name", env="OTHER_PREFIX_X", kind="int", default=1, affects="x")
        with pytest.raises(SwitchError):
            ConfigKnob(name="ok.name", env="KNOW_YOUR_CONTEXT_WEBVIEW_Y", kind="banana", default=1, affects="x")
        with pytest.raises(SwitchError):
            ConfigKnob(name="ok.name", env="KNOW_YOUR_CONTEXT_WEBVIEW_Z", kind="int", default=1, affects="")


# ------------------------------------------------------------------ parsing


def _knob(name: str) -> ConfigKnob:
    for knob in CONFIG_KNOBS:
        if knob.name == name:
            return knob
    raise AssertionError(f"knob {name} not declared")


class TestParseKnobValue:
    def test_int_accepts_plain_integer(self) -> None:
        knob = _knob("webview.max_graph_nodes")
        assert parse_knob_value(knob, "8192") == 8192

    def test_int_rejects_float_text(self) -> None:
        knob = _knob("webview.max_graph_nodes")
        with pytest.raises(SwitchError) as excinfo:
            parse_knob_value(knob, "8192.5")
        assert excinfo.value.code == "INVALID_VALUE"

    def test_int_rejects_garbage(self) -> None:
        knob = _knob("webview.max_graph_nodes")
        for raw in ("", "abc", "12a", "0x10", "1_000", "yes"):
            with pytest.raises(SwitchError):
                parse_knob_value(knob, raw)

    def test_int_range_fail_closed(self) -> None:
        knob = _knob("webview.max_graph_nodes")
        with pytest.raises(SwitchError) as excinfo:
            parse_knob_value(knob, "1")  # below minimum
        assert excinfo.value.code == "OUT_OF_RANGE"
        with pytest.raises(SwitchError):
            parse_knob_value(knob, "99999999")  # above maximum

    def test_float_accepts_int_text(self) -> None:
        knob = _knob("webview.frame_width")
        assert parse_knob_value(knob, "2000") == 2000.0

    def test_float_rejects_non_finite(self) -> None:
        knob = _knob("webview.frame_width")
        for raw in ("nan", "inf", "-inf"):
            with pytest.raises(SwitchError):
                parse_knob_value(knob, raw)

    def test_float_range_fail_closed(self) -> None:
        knob = _knob("webview.shutdown_join_seconds")
        with pytest.raises(SwitchError):
            parse_knob_value(knob, "0")  # exclusive minimum
        with pytest.raises(SwitchError):
            parse_knob_value(knob, "-1")

    def test_bool_strict_spellings(self) -> None:
        knob = _knob("webview.rate.enabled")
        for raw in ("1", "true", "TRUE", " yes ", "on"):
            assert parse_knob_value(knob, raw) is True
        for raw in ("0", "false", "NO", " off "):
            assert parse_knob_value(knob, raw) is False

    def test_bool_rejects_ambiguous(self) -> None:
        knob = _knob("webview.rate.enabled")
        for raw in ("", "2", "maybe", "enabled"):
            with pytest.raises(SwitchError):
                parse_knob_value(knob, raw)


# ------------------------------------------------------------------ board


class TestKnobBoard:
    def test_defaults_resolve_without_env(self) -> None:
        board = load_knobs(env={})
        assert board.value("webview.max_graph_nodes") == webview.MAX_GRAPH_NODES
        assert board.value("webview.search_limit_max") == webview.SEARCH_LIMIT_MAX
        assert board.value("webview.audit_limit_max") == webview.AUDIT_LIMIT_MAX
        assert board.value("webview.query_limit_max") == webview.QUERY_LIMIT_MAX
        assert board.value("webview.series_limit_max") == webview.SERIES_LIMIT_MAX
        assert board.value("webview.analytics_max_nodes") == webview.ANALYTICS_MAX_NODES
        assert board.value("webview.hierarchy_depth_max") == webview.HIERARCHY_DEPTH_MAX
        assert board.source("webview.max_graph_nodes") == "default"

    def test_env_overrides_and_marks_source(self) -> None:
        knob = _knob("webview.max_graph_nodes")
        board = load_knobs(env={knob.env: "8192"})
        assert board.value("webview.max_graph_nodes") == 8192
        assert board.source("webview.max_graph_nodes") == "env"

    def test_invalid_env_fails_closed(self) -> None:
        knob = _knob("webview.max_graph_nodes")
        with pytest.raises(SwitchError):
            load_knobs(env={knob.env: "not-a-number"})

    def test_out_of_range_env_fails_closed(self) -> None:
        knob = _knob("webview.trace_capacity")
        with pytest.raises(SwitchError):
            load_knobs(env={knob.env: "0"})

    def test_unknown_name_raises(self) -> None:
        board = load_knobs(env={})
        with pytest.raises(SwitchError) as excinfo:
            board.value("webview.does_not_exist")
        assert excinfo.value.code == "UNKNOWN_KNOB"

    def test_names_sorted(self) -> None:
        board = load_knobs(env={})
        names = board.names()
        assert names == tuple(sorted(names))
        assert len(names) == len(CONFIG_KNOBS)

    def test_active_lists_env_sourced_only(self) -> None:
        knob = _knob("webview.rate.read_burst")
        assert load_knobs(env={}).active() == ()
        board = load_knobs(env={knob.env: "7"})
        assert [k.name for k in board.active()] == ["webview.rate.read_burst"]

    def test_dump_shape(self) -> None:
        board = load_knobs(env={})
        dumped = board.dump()
        assert set(dumped) == {knob.name for knob in CONFIG_KNOBS}
        entry = dumped["webview.max_graph_nodes"]
        assert entry["value"] == webview.MAX_GRAPH_NODES
        assert entry["source"] == "default"
        assert entry["env"].startswith(ENV_PREFIX)

    def test_board_rejects_unknown_construction(self) -> None:
        with pytest.raises(SwitchError):
            KnobBoard({"webview.invented": 1})

    def test_board_rejects_missing_construction(self) -> None:
        with pytest.raises(SwitchError):
            KnobBoard({})

    def test_default_le_max_cross_pairs(self) -> None:
        """Paired *_default / *_max knobs must stay ordered, env included."""
        pairs = [
            ("webview.search_limit_default", "webview.search_limit_max"),
            ("webview.query_limit_default", "webview.query_limit_max"),
            ("webview.audit_limit_default", "webview.audit_limit_max"),
            ("webview.audit_tail_limit_default", "webview.audit_tail_limit_max"),
            ("webview.series_limit_default", "webview.series_limit_max"),
            ("webview.hierarchy_depth_default", "webview.hierarchy_depth_max"),
            ("webview.page_size_default", "webview.page_size_max"),
            ("webview.export_depth_default", "webview.export_depth_max"),
        ]
        for default_name, max_name in pairs:
            default_knob = _knob(default_name)
            max_knob = _knob(max_name)
            board = load_knobs(env={})
            assert board.value(default_name) <= board.value(max_name), default_name
            # Push the default above the (default) max: ordering must fail closed.
            broken = str(int(float(max_knob.default)) + 1)
            with pytest.raises(SwitchError) as excinfo:
                load_knobs(env={default_knob.env: broken})
            assert excinfo.value.code == "OUT_OF_RANGE"

    def test_load_knobs_ignores_unrelated_env(self) -> None:
        board = load_knobs(env={"HOME": "/root", "KNOW_YOUR_CONTEXT_DISABLE_WEBVIEW": "1"})
        assert board.value("webview.max_graph_nodes") == webview.MAX_GRAPH_NODES


# ------------------------------------------------------------------ self docs


class TestDescribeKnobs:
    def test_markdown_table_covers_every_knob(self) -> None:
        text = describe_knobs()
        assert text.startswith("# ")
        for knob in CONFIG_KNOBS:
            assert knob.name in text
            assert knob.env in text

    def test_describe_is_deterministic(self) -> None:
        assert describe_knobs() == describe_knobs()


# ------------------------------------------------- consumption by the server


class TestKnobConsumption:
    """The knobs are real: they change observable server behavior."""

    def test_trace_capacity_drives_timing_middleware(self, tmp_path) -> None:
        path = self._make_store(tmp_path)
        knob = _knob("webview.trace_capacity")
        server = webview.WebviewServer(path, port=0, token="t" * 16, knobs=load_knobs(env={knob.env: "5"}))
        try:
            assert server._timings._trace.maxlen == 5  # noqa: SLF001
        finally:
            server.shutdown()

    def test_search_limit_max_drives_validation(self, tmp_path) -> None:
        from know_your_context.store import EventStore

        path = tmp_path / "s.sqlite3"
        EventStore(path).close()
        app = webview.WebviewApp(
            path,
            knobs=load_knobs(
                env={
                    "KNOW_YOUR_CONTEXT_WEBVIEW_SEARCH_LIMIT_MAX": "3",
                    "KNOW_YOUR_CONTEXT_WEBVIEW_SEARCH_LIMIT_DEFAULT": "2",
                }
            ),
        )
        try:
            with pytest.raises(webview._BadRequest):  # noqa: SLF001
                app.search_payload("x", kind=None, lang=None, limit=4)
            # ... and the new ceiling is accepted
            payload = app.search_payload("x", kind=None, lang=None, limit=3)
            assert payload["limit"] == 3
        finally:
            app.close()

    @staticmethod
    def _make_store(tmp_path):
        from know_your_context.store import EventStore

        path = tmp_path / "store.sqlite3"
        EventStore(path).close()
        return path


class TestEveryKnobRoundtrip:
    """Parametrized: each knob's env accepts its own default spelled out."""

    @pytest.mark.parametrize("knob", list(CONFIG_KNOBS), ids=lambda k: k.name)
    def test_default_spelled_out_loads_as_env(self, knob: ConfigKnob) -> None:
        raw = str(knob.default).lower() if isinstance(knob.default, bool) else str(knob.default)
        board = load_knobs(env={knob.env: raw})
        assert board.value(knob.name) == knob.default
        assert board.source(knob.name) == "env"

    @pytest.mark.parametrize("knob", list(CONFIG_KNOBS), ids=lambda knob: knob.name)
    def test_garbage_fails_closed(self, knob: ConfigKnob) -> None:
        with pytest.raises(SwitchError):
            load_knobs(env={knob.env: "\x00garbage\x00"})


class TestSwitchRegistryUnchanged:
    """The 19 kill switches predate W15; the knob registry is additive."""

    def test_kill_switches_untouched(self) -> None:
        names = [entry.name for entry in switches.KILL_SWITCHES]
        assert "webview" in names
        # 23 pre-W15 switches + storage.hw_tuning + hw.governor (W15 SUB-134, additive).
        assert len(names) == 25

    def test_knob_envs_do_not_collide_with_switch_envs(self) -> None:
        switch_envs = {entry.env for entry in switches.KILL_SWITCHES}
        for knob in CONFIG_KNOBS:
            assert knob.env not in switch_envs
