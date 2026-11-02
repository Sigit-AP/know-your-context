"""Tests for ``know_your_context.ops.switches`` and its wiring (W13 SUB-125).

Every optional mechanism has an operator kill switch; the registry is the
single source of truth. New switches parse strictly (fail closed on ambiguous
values); mirrored legacy switches (watcher, group commit, rust kernel) adopt
their owner's historical semantics exactly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from know_your_context.enums import CapabilityMode, SwitchState
from know_your_context.ops import switches
from know_your_context.ops.switches import (
    KILL_SWITCHES,
    SYNTH_PASS_ENV,
    KillSwitch,
    SwitchBoard,
    SwitchError,
)
from know_your_context.resolve.synthesis import SYNTH_PASS_ORDER

SEEN = "2026-01-01T00:00:00+00:00"


# ------------------------------------------------------------------ registry


class TestRegistry:
    def test_every_synthesis_pass_has_a_switch(self) -> None:
        assert set(SYNTH_PASS_ENV) == set(SYNTH_PASS_ORDER)
        assert len(SYNTH_PASS_ENV) == 12

    def test_pass_env_names_follow_convention(self) -> None:
        for pass_name, env_name in SYNTH_PASS_ENV.items():
            assert env_name == f"KNOW_YOUR_CONTEXT_DISABLE_SYNTH_{pass_name.upper()}"

    def test_registry_entries_are_wellformed(self) -> None:
        for entry in KILL_SWITCHES:
            assert isinstance(entry, KillSwitch)
            assert entry.name and entry.name == entry.name.lower()
            assert entry.affects and entry.fail_safe
            assert entry.env.startswith(("KNOW_YOUR_CONTEXT_", "KYS_"))

    def test_registry_names_unique(self) -> None:
        names = [entry.name for entry in KILL_SWITCHES]
        assert len(names) == len(set(names))

    def test_registry_covers_documented_legacy_switches(self) -> None:
        envs = {entry.env for entry in KILL_SWITCHES}
        assert switches.ENV_NO_WATCH in envs
        assert switches.ENV_FORCE_WATCH in envs
        assert switches.ENV_NO_GROUP_COMMIT in envs
        assert switches.ENV_DISABLE_RUST_KERNEL in envs

    def test_mirrored_env_names_match_their_owners(self) -> None:
        from know_your_context.ingest import kernel_bridge, watcher
        from know_your_context.serve import superpower

        assert switches.ENV_NO_WATCH == watcher.ENV_NO_WATCH
        assert switches.ENV_FORCE_WATCH == watcher.ENV_FORCE_WATCH
        assert switches.ENV_NO_GROUP_COMMIT == superpower._NO_GROUP_COMMIT_ENV
        assert switches.ENV_DISABLE_RUST_KERNEL == kernel_bridge.KILL_SWITCH_ENV

    def test_describe_switches_documents_every_env(self) -> None:
        doc = switches.describe_switches()
        for entry in KILL_SWITCHES:
            assert entry.env in doc
            assert entry.name in doc
        assert "fail-safe" in doc or "fail_safe" in doc


# ------------------------------------------------------------------ parsing


class TestParsing:
    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "TRUE", " On "])
    def test_true_spellings(self, raw: str) -> None:
        assert switches.parse_switch_bool("KNOW_YOUR_CONTEXT_X", raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", " OFF "])
    def test_false_spellings(self, raw: str) -> None:
        assert switches.parse_switch_bool("KNOW_YOUR_CONTEXT_X", raw) is False

    @pytest.mark.parametrize("raw", ["", "maybe", "2", "-1", "enabled", "y"])
    def test_invalid_spellings_fail_closed(self, raw: str) -> None:
        with pytest.raises(SwitchError) as info:
            switches.parse_switch_bool("KNOW_YOUR_CONTEXT_X", raw)
        assert info.value.code == "INVALID_VALUE"
        assert "KNOW_YOUR_CONTEXT_X" in str(info.value)

    def test_switch_error_is_typed(self) -> None:
        error = SwitchError("UNKNOWN_SWITCH", "nope")
        assert isinstance(error, RuntimeError)
        assert error.code == "UNKNOWN_SWITCH"
        assert "UNKNOWN_SWITCH" in str(error)


# ------------------------------------------------------------------ defaults


class TestDefaults:
    def test_defaults_enable_everything(self) -> None:
        board = switches.load_switches(env={})
        assert board.synthesis_passes_disabled() == frozenset()
        assert board.synthesis_disabled() is False
        assert board.helper_tier_disabled() is False
        assert board.name_match_disabled() is False
        assert board.webview_disabled() is False
        assert board.simulation_disabled() is False
        assert board.watcher_disabled() is False
        assert board.watcher_forced() is False
        assert board.group_commit_disabled() is False
        assert board.rust_kernel_disabled() is False
        assert board.capability_mode_override() is None
        assert board.active() == ()
        assert board.announcements() == ()

    def test_default_states_are_enabled(self) -> None:
        board = switches.load_switches(env={})
        for entry in KILL_SWITCHES:
            if entry.env == switches.ENV_CAPABILITY_MODE:
                continue  # choice switch, not boolean
            assert board.state(entry.name) is SwitchState.ENABLED, entry.name


# ------------------------------------------------------------- bool switches


class TestBoolSwitches:
    def test_disable_all_synthesis(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_SYNTHESIS: "1"})
        assert board.synthesis_disabled() is True
        assert board.synthesis_passes_disabled() == frozenset(SYNTH_PASS_ORDER)

    def test_disable_all_synthesis_false_keeps_passes(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_SYNTHESIS: "0"})
        assert board.synthesis_passes_disabled() == frozenset()

    @pytest.mark.parametrize("pass_name", sorted(SYNTH_PASS_ORDER))
    def test_per_pass_disable(self, pass_name: str) -> None:
        board = switches.load_switches(env={SYNTH_PASS_ENV[pass_name]: "1"})
        assert board.synthesis_passes_disabled() == frozenset({pass_name})

    @pytest.mark.parametrize("pass_name", sorted(SYNTH_PASS_ORDER))
    def test_per_pass_disable_explicit_false(self, pass_name: str) -> None:
        board = switches.load_switches(env={SYNTH_PASS_ENV[pass_name]: "false"})
        assert board.synthesis_passes_disabled() == frozenset()

    def test_per_pass_and_global_combine(self) -> None:
        target = sorted(SYNTH_PASS_ORDER)[0]
        board = switches.load_switches(env={SYNTH_PASS_ENV[target]: "1", switches.ENV_DISABLE_HELPER_TIER: "1"})
        assert board.synthesis_passes_disabled() == frozenset({target})
        assert board.helper_tier_disabled() is True

    def test_per_pass_invalid_value_fails_closed(self) -> None:
        target = sorted(SYNTH_PASS_ORDER)[0]
        with pytest.raises(SwitchError):
            switches.load_switches(env={SYNTH_PASS_ENV[target]: "perhaps"})

    def test_helper_tier_switch(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_HELPER_TIER: "1"})
        assert board.helper_tier_disabled() is True

    def test_name_match_switch(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_NAME_MATCH: "1"})
        assert board.name_match_disabled() is True

    def test_webview_switch(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_WEBVIEW: "1"})
        assert board.webview_disabled() is True

    def test_simulation_switch(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_SIMULATION: "1"})
        assert board.simulation_disabled() is True

    def test_invalid_new_switch_value_raises(self) -> None:
        with pytest.raises(SwitchError):
            switches.load_switches(env={switches.ENV_DISABLE_WEBVIEW: "2"})


# ------------------------------------------------------- legacy (mirrored)


class TestLegacySemantics:
    def test_no_watch_truthy(self) -> None:
        assert switches.load_switches(env={switches.ENV_NO_WATCH: "1"}).watcher_disabled() is True

    def test_no_watch_non_truthy_is_not_disabled(self) -> None:
        # Owner semantics (watcher._truthy): only 1/true/yes/on count; other
        # values never raise, they are simply not a disable.
        assert switches.load_switches(env={switches.ENV_NO_WATCH: "maybe"}).watcher_disabled() is False

    def test_force_watch_truthy(self) -> None:
        assert switches.load_switches(env={switches.ENV_FORCE_WATCH: "on"}).watcher_forced() is True

    def test_group_commit_only_exact_1_disables(self) -> None:
        assert switches.load_switches(env={switches.ENV_NO_GROUP_COMMIT: "1"}).group_commit_disabled() is True
        assert switches.load_switches(env={switches.ENV_NO_GROUP_COMMIT: "true"}).group_commit_disabled() is False

    def test_rust_kernel_any_non_empty_disables(self) -> None:
        assert switches.load_switches(env={switches.ENV_DISABLE_RUST_KERNEL: "anything"}).rust_kernel_disabled() is True
        assert switches.load_switches(env={switches.ENV_DISABLE_RUST_KERNEL: ""}).rust_kernel_disabled() is False


# ---------------------------------------------------------- capability mode


class TestCapabilityModeOverride:
    def test_override_off(self) -> None:
        board = switches.load_switches(env={switches.ENV_CAPABILITY_MODE: "off"})
        assert board.capability_mode_override() is CapabilityMode.OFF

    def test_override_enforce(self) -> None:
        board = switches.load_switches(env={switches.ENV_CAPABILITY_MODE: "enforce"})
        assert board.capability_mode_override() is CapabilityMode.ENFORCE

    def test_override_invalid_fails_closed(self) -> None:
        with pytest.raises(SwitchError) as info:
            switches.load_switches(env={switches.ENV_CAPABILITY_MODE: "audit"})
        assert info.value.code == "INVALID_VALUE"

    def test_override_empty_fails_closed(self) -> None:
        with pytest.raises(SwitchError):
            switches.load_switches(env={switches.ENV_CAPABILITY_MODE: ""})


# ------------------------------------------------------------------ reporting


class TestReporting:
    def test_state_disabled(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_HELPER_TIER: "1"})
        assert board.state("resolver.helper_tier") is SwitchState.DISABLED

    def test_state_unknown_name_fails_closed(self) -> None:
        board = switches.load_switches(env={})
        with pytest.raises(SwitchError) as info:
            board.state("nope")
        assert info.value.code == "UNKNOWN_SWITCH"

    def test_active_lists_only_non_default(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_WEBVIEW: "1"})
        assert [entry.name for entry in board.active()] == ["webview"]

    def test_announcements_name_env_and_effect(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_SYNTHESIS: "1"})
        announcements = board.announcements()
        assert any(switches.ENV_DISABLE_SYNTHESIS in note for note in announcements)
        assert any("synthesis" in note for note in announcements)

    def test_describe_board_renders_states(self) -> None:
        board = switches.load_switches(env={switches.ENV_DISABLE_SIMULATION: "1"})
        doc = switches.describe_board(board)
        assert switches.ENV_DISABLE_SIMULATION in doc
        assert "disabled" in doc

    def test_reads_process_environment_when_env_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(switches.ENV_DISABLE_NAME_MATCH, "1")
        board = switches.load_switches()
        assert board.name_match_disabled() is True


# ------------------------------------------------------------- wiring: types


class TestBoardType:
    def test_board_is_switch_board(self) -> None:
        assert isinstance(switches.load_switches(env={}), SwitchBoard)


# -------------------------------------------------- wiring: synthesis passes


@pytest.fixture
def store(tmp_path: Path):
    from know_your_context.store import EventStore

    instance = EventStore(tmp_path / "w13.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _seed_method_containment(store) -> None:
    store.upsert_file(
        path="pkg/mod.py",
        sha256=hashlib.sha256(b"pkg/mod.py").hexdigest(),
        size=10,
        mtime=100,
        language="python",
        generated_hint=False,
        extraction_version=1,
    )
    store.upsert_symbol(node_id="sym:pkg/mod.py:Klass", name="Widget", kind="class", path="pkg/mod.py", line=1)
    store.upsert_symbol(node_id="sym:pkg/mod.py:render", name="Widget.render", kind="method", path="pkg/mod.py", line=5)


class TestSynthesisWiring:
    def test_containment_pass_emits_by_default(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve.synthesis import PASS_METHOD_CLASS_CONTAINMENT, run_synthesis

        for name in SYNTH_PASS_ENV.values():
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(switches.ENV_DISABLE_SYNTHESIS, raising=False)
        _seed_method_containment(store)
        report = run_synthesis(store)
        containment = next(p for p in report.passes if p.name == PASS_METHOD_CLASS_CONTAINMENT)
        assert containment.inserted == 1

    def test_containment_pass_disabled_by_env(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve.synthesis import (
            PASS_METHOD_CLASS_CONTAINMENT,
            Synthesizer,
        )

        monkeypatch.setenv(SYNTH_PASS_ENV[PASS_METHOD_CLASS_CONTAINMENT], "1")
        _seed_method_containment(store)
        synthesizer = Synthesizer()
        report = synthesizer.run(store)
        containment = next(p for p in report.passes if p.name == PASS_METHOD_CLASS_CONTAINMENT)
        assert containment.candidates == 0
        assert containment.inserted == 0
        assert report.inserted == 0
        # The disable is announced, never silent.
        assert PASS_METHOD_CLASS_CONTAINMENT in synthesizer.disabled_by_env

    def test_disable_all_synthesis_announces_every_pass(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve.synthesis import Synthesizer

        monkeypatch.setenv(switches.ENV_DISABLE_SYNTHESIS, "1")
        _seed_method_containment(store)
        synthesizer = Synthesizer()
        report = synthesizer.run(store)
        assert report.inserted == 0
        assert synthesizer.disabled_by_env == frozenset(SYNTH_PASS_ORDER)
        assert all(p.inserted == 0 for p in report.passes)
        # Report shape unchanged: all 12 passes still present.
        assert tuple(p.name for p in report.passes) == SYNTH_PASS_ORDER

    def test_explicit_enabled_passes_still_honor_env(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve.synthesis import (
            PASS_METHOD_CLASS_CONTAINMENT,
            SynthesisConfig,
            Synthesizer,
        )

        monkeypatch.setenv(SYNTH_PASS_ENV[PASS_METHOD_CLASS_CONTAINMENT], "1")
        _seed_method_containment(store)
        config = SynthesisConfig(enabled_passes=frozenset({PASS_METHOD_CLASS_CONTAINMENT}))
        report = Synthesizer(config).run(store)
        # Operator env wins over config: the kill switch is an operator override.
        assert report.inserted == 0

    def test_invalid_switch_env_fails_synthesis_closed(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve.synthesis import run_synthesis

        monkeypatch.setenv(switches.ENV_DISABLE_SYNTHESIS, "bogus")
        with pytest.raises(SwitchError):
            run_synthesis(store)


# ------------------------------------------------------- wiring: helper tier


class TestHelperTierWiring:
    BASE = "sym:src/main.rs:main"

    def _seed_rust(self, store) -> None:
        store.upsert_file(
            path="src/main.rs",
            sha256=hashlib.sha256(b"src/main.rs").hexdigest(),
            size=10,
            mtime=100,
            language="rust",
            generated_hint=False,
            extraction_version=1,
        )
        store.upsert_file(
            path="src/foo/bar.rs",
            sha256=hashlib.sha256(b"src/foo/bar.rs").hexdigest(),
            size=10,
            mtime=100,
            language="rust",
            generated_hint=False,
            extraction_version=1,
        )
        store.upsert_symbol(node_id=self.BASE, name="main", kind="function", path="src/main.rs", line=1)
        store.insert_unresolved_ref(
            source_node_id=self.BASE, target_name="crate::foo::bar", kind="imports", name_tail=None, first_seen=SEEN
        )

    def test_helper_tier_resolves_by_default(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve import ReferenceResolver

        monkeypatch.delenv(switches.ENV_DISABLE_HELPER_TIER, raising=False)
        self._seed_rust(store)
        report = ReferenceResolver().resolve_pending(store)
        assert report.resolved == 1

    def test_helper_tier_disabled_by_env(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve import ReferenceResolver

        monkeypatch.setenv(switches.ENV_DISABLE_HELPER_TIER, "1")
        self._seed_rust(store)
        resolver = ReferenceResolver()
        assert resolver.helper_tier_active is False
        report = resolver.resolve_pending(store)
        assert report.resolved == 0
        assert report.failed == 1

    def test_helper_tier_explicit_config_still_env_overridden(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve import ReferenceResolver, ResolverConfig

        monkeypatch.setenv(switches.ENV_DISABLE_HELPER_TIER, "1")
        resolver = ReferenceResolver(ResolverConfig(helper_tier_enabled=True))
        assert resolver.helper_tier_active is False

    def test_helper_tier_active_property_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.resolve import ReferenceResolver

        monkeypatch.delenv(switches.ENV_DISABLE_HELPER_TIER, raising=False)
        assert ReferenceResolver().helper_tier_active is True


# --------------------------------------------- wiring: capability mode (daemon)


class TestCapabilityModeWiring:
    def test_env_override_applies_to_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.serve.daemon_config import DaemonConfig

        monkeypatch.setenv(switches.ENV_CAPABILITY_MODE, "enforce")
        config = DaemonConfig(db_path=tmp_path / "s.sqlite3", graph_root=tmp_path / "g")
        assert config.capability_mode == "enforce"

    def test_env_does_not_weaken_explicit_enforce(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.serve.daemon_config import DaemonConfig

        monkeypatch.setenv(switches.ENV_CAPABILITY_MODE, "off")
        config = DaemonConfig(db_path=tmp_path / "s.sqlite3", graph_root=tmp_path / "g", capability_mode="enforce")
        assert config.capability_mode == "enforce"

    def test_invalid_override_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.serve.daemon_config import DaemonConfig

        monkeypatch.setenv(switches.ENV_CAPABILITY_MODE, "audit")
        with pytest.raises(SwitchError):
            DaemonConfig(db_path=tmp_path / "s.sqlite3", graph_root=tmp_path / "g")


# ------------------------------------------------------- wiring: simulation


class TestSimulationSwitch:
    def test_simulation_refuses_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from know_your_context.serve import simulation

        monkeypatch.setenv(switches.ENV_DISABLE_SIMULATION, "1")
        code = simulation.main(["7", "10"])
        assert code == 2
        assert switches.ENV_DISABLE_SIMULATION in capsys.readouterr().err

    def test_simulation_runs_by_default(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        from know_your_context.serve import simulation

        monkeypatch.delenv(switches.ENV_DISABLE_SIMULATION, raising=False)
        code = simulation.main(["7", "10"])
        assert code == 0
