"""Operator kill switches: one canonical registry for every optional mechanism.

Every mechanism that can be switched off has exactly one entry in
:data:`KILL_SWITCHES`, and every switch is **fail-safe**: the default (env var
absent or explicitly false) is the historical, safe behavior; setting the
switch returns the system to a strictly simpler mode.

Two parsing regimes, by design:

* **New switches** (owned here) parse *strictly* via :func:`parse_switch_bool`:
  only ``1/true/yes/on`` and ``0/false/no/off`` (case-insensitive, stripped)
  are accepted; anything else raises :class:`SwitchError` — an ambiguous
  operator intent is never guessed.
* **Mirrored legacy switches** (owned by ``ingest.watcher``,
  ``serve.superpower``, ``ingest.kernel_bridge``) adopt their owner's
  historical semantics exactly, so this board reports what the owner will do.
  A test pins the mirrored env names to the owner constants so they cannot
  drift.

The capability-mode override is a *choice* switch (``off``/``enforce``), not a
boolean; it fails closed on any other value.

This module never touches the store and imports nothing beyond :mod:`enums`
and the stdlib, so it can be wired into any layer without import cycles.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from know_your_context.enums import CapabilityMode, SwitchState

__all__ = [
    "ENV_CAPABILITY_MODE",
    "ENV_DISABLE_HELPER_TIER",
    "ENV_DISABLE_NAME_MATCH",
    "ENV_DISABLE_RUST_KERNEL",
    "ENV_DISABLE_SIMULATION",
    "ENV_DISABLE_SYNTH_CALLBACK_EDGES",
    "ENV_DISABLE_SYNTH_DEPENDENCY_INJECTION",
    "ENV_DISABLE_SYNTH_EVENT_HANDLER_WIRING",
    "ENV_DISABLE_SYNTH_FACTORY_FUNCTIONS",
    "ENV_DISABLE_SYNTH_FRAMEWORK_ROUTES",
    "ENV_DISABLE_SYNTH_INTERFACE_IMPLEMENTATION",
    "ENV_DISABLE_SYNTH_METHOD_CLASS_CONTAINMENT",
    "ENV_DISABLE_SYNTH_MODULE_MEMBER_CALLS",
    "ENV_DISABLE_SYNTH_REEXPORT_CHAINS",
    "ENV_DISABLE_SYNTH_REEXPORT_PROPAGATION",
    "ENV_DISABLE_SYNTH_TEST_SUBJECT_EDGES",
    "ENV_DISABLE_SYNTH_TYPE_HINT_EDGES",
    "ENV_DISABLE_SYNTHESIS",
    "ENV_DISABLE_WEBVIEW",
    "ENV_FORCE_WATCH",
    "ENV_NO_GROUP_COMMIT",
    "ENV_NO_WATCH",
    "FALSE_VALUES",
    "KILL_SWITCHES",
    "SYNTH_PASS_ENV",
    "TRUE_VALUES",
    "KillSwitch",
    "SwitchBoard",
    "SwitchError",
    "describe_board",
    "describe_switches",
    "load_switches",
    "parse_switch_bool",
]

# ------------------------------------------------------------------ env names
# New switches (owned here; strict parsing).

ENV_DISABLE_SYNTHESIS = "KNOW_YOUR_CONTEXT_DISABLE_SYNTHESIS"
ENV_DISABLE_HELPER_TIER = "KNOW_YOUR_CONTEXT_DISABLE_HELPER_TIER"
ENV_DISABLE_NAME_MATCH = "KNOW_YOUR_CONTEXT_DISABLE_NAME_MATCH"
ENV_CAPABILITY_MODE = "KNOW_YOUR_CONTEXT_CAPABILITY_MODE"
ENV_DISABLE_WEBVIEW = "KNOW_YOUR_CONTEXT_DISABLE_WEBVIEW"
ENV_DISABLE_SIMULATION = "KNOW_YOUR_CONTEXT_DISABLE_SIMULATION"

ENV_DISABLE_SYNTH_METHOD_CLASS_CONTAINMENT = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_METHOD_CLASS_CONTAINMENT"
ENV_DISABLE_SYNTH_REEXPORT_CHAINS = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_REEXPORT_CHAINS"
ENV_DISABLE_SYNTH_TYPE_HINT_EDGES = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_TYPE_HINT_EDGES"
ENV_DISABLE_SYNTH_MODULE_MEMBER_CALLS = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_MODULE_MEMBER_CALLS"
ENV_DISABLE_SYNTH_REEXPORT_PROPAGATION = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_REEXPORT_PROPAGATION"
ENV_DISABLE_SYNTH_DEPENDENCY_INJECTION = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_DEPENDENCY_INJECTION"
ENV_DISABLE_SYNTH_INTERFACE_IMPLEMENTATION = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_INTERFACE_IMPLEMENTATION"
ENV_DISABLE_SYNTH_FRAMEWORK_ROUTES = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_FRAMEWORK_ROUTE_REGISTRATION"
ENV_DISABLE_SYNTH_EVENT_HANDLER_WIRING = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_EVENT_HANDLER_WIRING"
ENV_DISABLE_SYNTH_TEST_SUBJECT_EDGES = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_TEST_SUBJECT_EDGES"
ENV_DISABLE_SYNTH_FACTORY_FUNCTIONS = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_FACTORY_FUNCTION_EDGES"
ENV_DISABLE_SYNTH_CALLBACK_EDGES = "KNOW_YOUR_CONTEXT_DISABLE_SYNTH_CALLBACK_EDGES"

#: Pass name (``resolve.synthesis.SYNTH_PASS_ORDER`` vocabulary) → kill-switch
#: env var. Mirrored as literal constants above so operators can grep them.
SYNTH_PASS_ENV: dict[str, str] = {
    "method_class_containment": ENV_DISABLE_SYNTH_METHOD_CLASS_CONTAINMENT,
    "reexport_chains": ENV_DISABLE_SYNTH_REEXPORT_CHAINS,
    "type_hint_edges": ENV_DISABLE_SYNTH_TYPE_HINT_EDGES,
    "module_member_calls": ENV_DISABLE_SYNTH_MODULE_MEMBER_CALLS,
    "reexport_propagation": ENV_DISABLE_SYNTH_REEXPORT_PROPAGATION,
    "dependency_injection": ENV_DISABLE_SYNTH_DEPENDENCY_INJECTION,
    "interface_implementation": ENV_DISABLE_SYNTH_INTERFACE_IMPLEMENTATION,
    "framework_route_registration": ENV_DISABLE_SYNTH_FRAMEWORK_ROUTES,
    "event_handler_wiring": ENV_DISABLE_SYNTH_EVENT_HANDLER_WIRING,
    "test_subject_edges": ENV_DISABLE_SYNTH_TEST_SUBJECT_EDGES,
    "factory_function_edges": ENV_DISABLE_SYNTH_FACTORY_FUNCTIONS,
    "callback_edges": ENV_DISABLE_SYNTH_CALLBACK_EDGES,
}

# Mirrored legacy switches (owned elsewhere; owner semantics apply). The names
# are pinned to the owner constants by tests (test_ops_switches).
ENV_NO_WATCH = "KNOW_YOUR_CONTEXT_NO_WATCH"
ENV_FORCE_WATCH = "KNOW_YOUR_CONTEXT_FORCE_WATCH"
ENV_NO_GROUP_COMMIT = "KNOW_YOUR_CONTEXT_NO_GROUP_COMMIT"
ENV_DISABLE_RUST_KERNEL = "KYS_DISABLE_RUST_KERNEL"

#: Accepted spellings for strict boolean parsing (case-insensitive, stripped).
TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})


class SwitchError(RuntimeError):
    """A kill-switch contract violation (typed, fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class KillSwitch:
    """One operator switch.

    ``owned_here`` is False for mirrored legacy switches: this board reports
    their state with the owner module's historical semantics and never
    re-interprets them.
    """

    name: str
    env: str
    affects: str
    fail_safe: str
    owned_here: bool = True

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.lower():
            raise SwitchError("INVALID_SPEC", f"switch name {self.name!r} must be non-empty lowercase")
        if not self.env.startswith(("KNOW_YOUR_CONTEXT_", "KYS_")):
            raise SwitchError("INVALID_SPEC", f"switch {self.name!r} env {self.env!r} misses the prefix")
        if not self.affects or not self.fail_safe:
            raise SwitchError("INVALID_SPEC", f"switch {self.name!r} must document affects and fail_safe")


KILL_SWITCHES: tuple[KillSwitch, ...] = (
    *(
        KillSwitch(
            name=f"synthesis.{pass_name}",
            env=env_name,
            affects=f"resolve synthesis pass {pass_name!r}",
            fail_safe="disabled: the pass is skipped; edges it would mint are simply absent",
        )
        for pass_name, env_name in SYNTH_PASS_ENV.items()
    ),
    KillSwitch(
        name="synthesis",
        env=ENV_DISABLE_SYNTHESIS,
        affects="the whole resolve synthesis pipeline (all 12 passes)",
        fail_safe="disabled: no synthesis pass runs; only parse/resolver edges exist",
    ),
    KillSwitch(
        name="resolver.helper_tier",
        env=ENV_DISABLE_HELPER_TIER,
        affects="the per-language module_helper resolver tier (W12)",
        fail_safe="disabled: helper candidates are not consulted; ambiguous imports fail closed",
    ),
    KillSwitch(
        name="resolver.name_match",
        env=ENV_DISABLE_NAME_MATCH,
        affects="the name-match resolver tier in resolve_and_synthesize",
        fail_safe="disabled: the tier is skipped; unmatched refs keep their prior status",
    ),
    KillSwitch(
        name="daemon.capability_mode",
        env=ENV_CAPABILITY_MODE,
        affects="DaemonConfig.capability_mode when the constructor left it at the default",
        fail_safe="values: off|enforce; invalid values fail closed; env never weakens an explicit 'enforce'",
    ),
    KillSwitch(
        name="webview",
        env=ENV_DISABLE_WEBVIEW,
        affects="the loopback web viewer (serve.webview)",
        fail_safe="disabled: the viewer refuses to start (exit 2); the store is untouched",
    ),
    KillSwitch(
        name="simulation",
        env=ENV_DISABLE_SIMULATION,
        affects="the DST-lite deterministic simulation entry point",
        fail_safe="disabled: the simulator refuses to run (exit 2); nothing is simulated",
    ),
    KillSwitch(
        name="watcher",
        env=ENV_NO_WATCH,
        affects="the source watcher (ingest.watcher)",
        fail_safe="disabled: no polling; owner semantics (truthy 1/true/yes/on)",
        owned_here=False,
    ),
    KillSwitch(
        name="watcher.force",
        env=ENV_FORCE_WATCH,
        affects="the source watcher's WSL/remote-mount refusal",
        fail_safe="disabled: refusals stand; owner semantics (truthy 1/true/yes/on)",
        owned_here=False,
    ),
    KillSwitch(
        name="daemon.group_commit",
        env=ENV_NO_GROUP_COMMIT,
        affects="the daemon writer's group-commit batching",
        fail_safe="disabled ('=1'): one transaction per mutation (slower, simplest)",
        owned_here=False,
    ),
    KillSwitch(
        name="ingest.rust_kernel",
        env=ENV_DISABLE_RUST_KERNEL,
        affects="the rust parse kernel bridge",
        fail_safe="disabled (any non-empty value): the python engine parses instead",
        owned_here=False,
    ),
    KillSwitch(
        name="debt_queue",
        env="KYS_DEBT_QUEUE",
        affects="the knowledge debt queue (analytics.debt, ops.debt CLI, webview /api/debt)",
        fail_safe="disabled (0/false/no/off or unparseable): the queue is an empty typed envelope; nothing is proposed",
    ),
)

_SWITCH_BY_NAME: dict[str, KillSwitch] = {entry.name: entry for entry in KILL_SWITCHES}
_SWITCH_BY_ENV: dict[str, KillSwitch] = {entry.env: entry for entry in KILL_SWITCHES}


def parse_switch_bool(env_name: str, raw: str) -> bool:
    """Strict boolean parse for owned switches; ambiguous input fails closed."""
    lowered = raw.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise SwitchError(
        "INVALID_VALUE",
        f"env {env_name} is not a boolean ({raw!r}); use one of "
        f"{'/'.join(sorted(TRUE_VALUES))} or {'/'.join(sorted(FALSE_VALUES))}",
    )


def _legacy_truthy(raw: str | None) -> bool:
    """watcher semantics: only 1/true/yes/on count; other values never raise."""
    return raw is not None and raw.strip().lower() in TRUE_VALUES


class SwitchBoard:
    """The resolved state of every switch; immutable snapshot of one env."""

    def __init__(self, states: Mapping[str, bool], capability_mode: CapabilityMode | None) -> None:
        unknown = set(states) - set(_SWITCH_BY_NAME)
        if unknown:
            raise SwitchError("UNKNOWN_SWITCH", f"unknown switch names: {sorted(unknown)}")
        self._states = {name: bool(value) for name, value in states.items()}
        self._capability_mode = capability_mode

    # --------------------------------------------------------- generic access

    def is_disabled(self, name: str) -> bool:
        if name not in _SWITCH_BY_NAME:
            raise SwitchError("UNKNOWN_SWITCH", f"unknown kill switch {name!r}")
        if name == "daemon.capability_mode":
            return self._capability_mode is not None
        return self._states.get(name, False)

    def state(self, name: str) -> SwitchState:
        return SwitchState.DISABLED if self.is_disabled(name) else SwitchState.ENABLED

    # --------------------------------------------------------- typed accessors

    def synthesis_passes_disabled(self) -> frozenset[str]:
        if self._states.get("synthesis", False):
            return frozenset(SYNTH_PASS_ENV)
        return frozenset(pass_name for pass_name in SYNTH_PASS_ENV if self._states.get(f"synthesis.{pass_name}", False))

    def synthesis_disabled(self) -> bool:
        return self._states.get("synthesis", False)

    def helper_tier_disabled(self) -> bool:
        return self._states.get("resolver.helper_tier", False)

    def name_match_disabled(self) -> bool:
        return self._states.get("resolver.name_match", False)

    def webview_disabled(self) -> bool:
        return self._states.get("webview", False)

    def simulation_disabled(self) -> bool:
        return self._states.get("simulation", False)

    def watcher_disabled(self) -> bool:
        return self._states.get("watcher", False)

    def watcher_forced(self) -> bool:
        return self._states.get("watcher.force", False)

    def group_commit_disabled(self) -> bool:
        return self._states.get("daemon.group_commit", False)

    def rust_kernel_disabled(self) -> bool:
        return self._states.get("ingest.rust_kernel", False)

    def capability_mode_override(self) -> CapabilityMode | None:
        return self._capability_mode

    # ------------------------------------------------------------- reporting

    def active(self) -> tuple[KillSwitch, ...]:
        """Switches whose resolved state differs from the safe default."""
        return tuple(entry for entry in KILL_SWITCHES if self.is_disabled(entry.name))

    def announcements(self) -> tuple[str, ...]:
        """Human-readable notes naming each active switch and its effect."""
        notes = []
        for entry in self.active():
            if entry.name == "daemon.capability_mode":
                notes.append(f"{entry.env}={self._capability_mode}: overrides {entry.affects}")
            else:
                notes.append(f"{entry.env}=1: {entry.affects} disabled ({entry.fail_safe})")
        return tuple(notes)


def load_switches(env: Mapping[str, str] | None = None) -> SwitchBoard:
    """Resolve the board from *env* (default: the real process environment).

    Owned switches parse strictly; mirrored legacy switches follow their
    owner's semantics. Reads are explicit per switch so every env name is
    greppable at exactly one read site.
    """
    environ: Mapping[str, str] = os.environ if env is None else env

    states: dict[str, bool] = {}
    states["synthesis.method_class_containment"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_METHOD_CLASS_CONTAINMENT, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_METHOD_CLASS_CONTAINMENT)) is not None
        else False
    )
    states["synthesis.reexport_chains"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_REEXPORT_CHAINS, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_REEXPORT_CHAINS)) is not None
        else False
    )
    states["synthesis.type_hint_edges"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_TYPE_HINT_EDGES, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_TYPE_HINT_EDGES)) is not None
        else False
    )
    states["synthesis.module_member_calls"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_MODULE_MEMBER_CALLS, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_MODULE_MEMBER_CALLS)) is not None
        else False
    )
    states["synthesis.reexport_propagation"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_REEXPORT_PROPAGATION, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_REEXPORT_PROPAGATION)) is not None
        else False
    )
    states["synthesis.dependency_injection"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_DEPENDENCY_INJECTION, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_DEPENDENCY_INJECTION)) is not None
        else False
    )
    states["synthesis.interface_implementation"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_INTERFACE_IMPLEMENTATION, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_INTERFACE_IMPLEMENTATION)) is not None
        else False
    )
    states["synthesis.framework_route_registration"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_FRAMEWORK_ROUTES, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_FRAMEWORK_ROUTES)) is not None
        else False
    )
    states["synthesis.event_handler_wiring"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_EVENT_HANDLER_WIRING, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_EVENT_HANDLER_WIRING)) is not None
        else False
    )
    states["synthesis.test_subject_edges"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_TEST_SUBJECT_EDGES, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_TEST_SUBJECT_EDGES)) is not None
        else False
    )
    states["synthesis.factory_function_edges"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_FACTORY_FUNCTIONS, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_FACTORY_FUNCTIONS)) is not None
        else False
    )
    states["synthesis.callback_edges"] = (
        parse_switch_bool(ENV_DISABLE_SYNTH_CALLBACK_EDGES, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTH_CALLBACK_EDGES)) is not None
        else False
    )
    states["synthesis"] = (
        parse_switch_bool(ENV_DISABLE_SYNTHESIS, raw)
        if (raw := environ.get(ENV_DISABLE_SYNTHESIS)) is not None
        else False
    )
    states["resolver.helper_tier"] = (
        parse_switch_bool(ENV_DISABLE_HELPER_TIER, raw)
        if (raw := environ.get(ENV_DISABLE_HELPER_TIER)) is not None
        else False
    )
    states["resolver.name_match"] = (
        parse_switch_bool(ENV_DISABLE_NAME_MATCH, raw)
        if (raw := environ.get(ENV_DISABLE_NAME_MATCH)) is not None
        else False
    )
    states["webview"] = (
        parse_switch_bool(ENV_DISABLE_WEBVIEW, raw) if (raw := environ.get(ENV_DISABLE_WEBVIEW)) is not None else False
    )
    states["simulation"] = (
        parse_switch_bool(ENV_DISABLE_SIMULATION, raw)
        if (raw := environ.get(ENV_DISABLE_SIMULATION)) is not None
        else False
    )
    # Mirrored legacy switches: owner semantics, never strict.
    states["watcher"] = _legacy_truthy(environ.get(ENV_NO_WATCH))
    states["watcher.force"] = _legacy_truthy(environ.get(ENV_FORCE_WATCH))
    states["daemon.group_commit"] = environ.get(ENV_NO_GROUP_COMMIT) == "1"
    states["ingest.rust_kernel"] = bool(environ.get(ENV_DISABLE_RUST_KERNEL))

    capability_mode: CapabilityMode | None = None
    raw_mode = environ.get(ENV_CAPABILITY_MODE)
    if raw_mode is not None:
        lowered = raw_mode.strip().lower()
        try:
            capability_mode = CapabilityMode(lowered)
        except ValueError:
            raise SwitchError(
                "INVALID_VALUE",
                f"env {ENV_CAPABILITY_MODE} must be 'off' or 'enforce', got {raw_mode!r}",
            ) from None

    return SwitchBoard(states, capability_mode)


# ------------------------------------------------------------------ self docs


def describe_switches() -> str:
    """The registry as a deterministic markdown table (states not resolved)."""
    lines = [
        "# Operator Kill Switches",
        "",
        "Every optional mechanism has exactly one switch. Default = historical",
        "safe behavior. Owned switches parse strictly; invalid values fail closed.",
        "",
        "| switch | env | default | affects | fail-safe |",
        "|---|---|---|---|---|",
    ]
    for entry in KILL_SWITCHES:
        lines.append(f"| `{entry.name}` | `{entry.env}` | enabled | {entry.affects} | {entry.fail_safe} |")
    lines.append("")
    return "\n".join(lines)


def describe_board(board: SwitchBoard) -> str:
    """The registry annotated with the resolved state of one board."""
    lines = [
        "# Kill Switch States",
        "",
        "| switch | env | state | affects |",
        "|---|---|---|---|",
    ]
    for entry in KILL_SWITCHES:
        lines.append(f"| `{entry.name}` | `{entry.env}` | {board.state(entry.name)} | {entry.affects} |")
    lines.append("")
    return "\n".join(lines)
