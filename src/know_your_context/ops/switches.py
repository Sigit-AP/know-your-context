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

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from know_your_context.enums import CapabilityMode, SwitchState

__all__ = [
    "CONFIG_KNOBS",
    "ENV_CAPABILITY_MODE",
    "ENV_DISABLE_HELPER_TIER",
    "ENV_DISABLE_NAME_MATCH",
    "ENV_DISABLE_RUST_KERNEL",
    "ENV_DISABLE_SIMULATION",
    "ENV_DISABLE_HW_AUTOSCALER",
    "ENV_DISABLE_HW_TUNING",
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
    "KNOB_KINDS",
    "SYNTH_PASS_ENV",
    "TRUE_VALUES",
    "ConfigKnob",
    "KillSwitch",
    "KnobBoard",
    "SwitchBoard",
    "SwitchError",
    "describe_board",
    "describe_knobs",
    "describe_switches",
    "load_knobs",
    "load_switches",
    "parse_knob_value",
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
ENV_DISABLE_HW_TUNING = "KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING"
ENV_DISABLE_HW_AUTOSCALER = "KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER"

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
    KillSwitch(
        name="storage.hw_tuning",
        env=ENV_DISABLE_HW_TUNING,
        affects="hardware-aware storage pragmas (mmap_size, page_size hint, synchronous=NORMAL on SSD)",
        fail_safe="disabled: the store uses the historical pragma recipe exactly (no mmap, engine page size, FULL)",
    ),
    KillSwitch(
        name="hw.autoscaler",
        env=ENV_DISABLE_HW_AUTOSCALER,
        affects="the adaptive scale-up parallelism advisor (hardware.autoscaler)",
        fail_safe="disabled: a static single worker; the advisor never runs",
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

    def hw_tuning_disabled(self) -> bool:
        return self._states.get("storage.hw_tuning", False)

    def hw_autoscaler_disabled(self) -> bool:
        return self._states.get("hw.autoscaler", False)

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
    states["storage.hw_tuning"] = (
        parse_switch_bool(ENV_DISABLE_HW_TUNING, raw)
        if (raw := environ.get(ENV_DISABLE_HW_TUNING)) is not None
        else False
    )
    states["hw.autoscaler"] = (
        parse_switch_bool(ENV_DISABLE_HW_AUTOSCALER, raw)
        if (raw := environ.get(ENV_DISABLE_HW_AUTOSCALER)) is not None
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


# ------------------------------------------------------------------ config knobs
# W15 (SUB-131): typed config knobs for the webview REST surface. Unlike kill
# switches (boolean, fail-safe to a simpler mode), knobs carry typed numeric
# limits/intervals/rates with inclusive bounds; every knob has exactly one
# ``KNOW_YOUR_CONTEXT_WEBVIEW_*`` env var, parsed strictly (fail-closed) and
# consumed by ``serve.webview`` — no knob is decorative.

KNOB_KINDS: tuple[str, ...] = ("int", "float", "bool")

_INT_TEXT_RE = re.compile(r"^-?\d+$")


@dataclass(frozen=True)
class ConfigKnob:
    """One typed configuration knob with an exclusive env override."""

    name: str
    env: str
    kind: str
    default: int | float | bool
    affects: str
    minimum: float | None = None
    maximum: float | None = None
    minimum_exclusive: bool = False

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.lower() or "." not in self.name:
            raise SwitchError("INVALID_SPEC", f"knob name {self.name!r} must be dotted lowercase")
        if not self.env.startswith(("KNOW_YOUR_CONTEXT_", "KYS_")):
            raise SwitchError("INVALID_SPEC", f"knob {self.name!r} env {self.env!r} misses the prefix")
        if self.kind not in KNOB_KINDS:
            raise SwitchError("INVALID_SPEC", f"knob {self.name!r} declares unknown kind {self.kind!r}")
        if not self.affects or not self.affects.strip():
            raise SwitchError("INVALID_SPEC", f"knob {self.name!r} must document affects")
        if self.kind == "bool" and not isinstance(self.default, bool):
            raise SwitchError("INVALID_SPEC", f"knob {self.name!r} bool kind needs a bool default")
        if self.kind != "bool" and isinstance(self.default, bool):
            raise SwitchError("INVALID_SPEC", f"knob {self.name!r} numeric kind cannot default to bool")


#: Env names for the knob registry (one constant per knob; greppable).
ENV_WEBVIEW_MAX_GRAPH_NODES = "KNOW_YOUR_CONTEXT_WEBVIEW_MAX_GRAPH_NODES"
ENV_WEBVIEW_ANALYTICS_MAX_NODES = "KNOW_YOUR_CONTEXT_WEBVIEW_ANALYTICS_MAX_NODES"
ENV_WEBVIEW_FRAME_WIDTH = "KNOW_YOUR_CONTEXT_WEBVIEW_FRAME_WIDTH"
ENV_WEBVIEW_FRAME_HEIGHT = "KNOW_YOUR_CONTEXT_WEBVIEW_FRAME_HEIGHT"
ENV_WEBVIEW_TOP_RANKING = "KNOW_YOUR_CONTEXT_WEBVIEW_TOP_RANKING"
ENV_WEBVIEW_SEARCH_LIMIT_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_SEARCH_LIMIT_DEFAULT"
ENV_WEBVIEW_SEARCH_LIMIT_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_SEARCH_LIMIT_MAX"
ENV_WEBVIEW_HIERARCHY_DEPTH_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_HIERARCHY_DEPTH_DEFAULT"
ENV_WEBVIEW_HIERARCHY_DEPTH_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_HIERARCHY_DEPTH_MAX"
ENV_WEBVIEW_QUERY_LIMIT_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_QUERY_LIMIT_DEFAULT"
ENV_WEBVIEW_QUERY_LIMIT_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_QUERY_LIMIT_MAX"
ENV_WEBVIEW_AUDIT_LIMIT_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_AUDIT_LIMIT_DEFAULT"
ENV_WEBVIEW_AUDIT_LIMIT_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_AUDIT_LIMIT_MAX"
ENV_WEBVIEW_AUDIT_TAIL_LIMIT_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_AUDIT_TAIL_LIMIT_DEFAULT"
ENV_WEBVIEW_AUDIT_TAIL_LIMIT_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_AUDIT_TAIL_LIMIT_MAX"
ENV_WEBVIEW_SERIES_LIMIT_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_SERIES_LIMIT_DEFAULT"
ENV_WEBVIEW_SERIES_LIMIT_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_SERIES_LIMIT_MAX"
ENV_WEBVIEW_PAGE_SIZE_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_PAGE_SIZE_DEFAULT"
ENV_WEBVIEW_PAGE_SIZE_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_PAGE_SIZE_MAX"
ENV_WEBVIEW_REFS_SAMPLES_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_REFS_SAMPLES_MAX"
ENV_WEBVIEW_EVIDENCE_LIMIT_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_EVIDENCE_LIMIT_MAX"
ENV_WEBVIEW_EXPORT_DEPTH_DEFAULT = "KNOW_YOUR_CONTEXT_WEBVIEW_EXPORT_DEPTH_DEFAULT"
ENV_WEBVIEW_EXPORT_DEPTH_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_EXPORT_DEPTH_MAX"
ENV_WEBVIEW_EXPORT_NODES_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_EXPORT_NODES_MAX"
ENV_WEBVIEW_EXPORT_MAX_BYTES = "KNOW_YOUR_CONTEXT_WEBVIEW_EXPORT_MAX_BYTES"
ENV_WEBVIEW_TRACE_CAPACITY = "KNOW_YOUR_CONTEXT_WEBVIEW_TRACE_CAPACITY"
ENV_WEBVIEW_SHUTDOWN_JOIN_SECONDS = "KNOW_YOUR_CONTEXT_WEBVIEW_SHUTDOWN_JOIN_SECONDS"
ENV_WEBVIEW_HEARTBEAT_POLL_SECONDS = "KNOW_YOUR_CONTEXT_WEBVIEW_HEARTBEAT_POLL_SECONDS"
ENV_WEBVIEW_IMPROVEMENTS_SCAN_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_IMPROVEMENTS_SCAN_MAX"
ENV_WEBVIEW_BENCH_ROWS_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_BENCH_ROWS_MAX"
ENV_WEBVIEW_LEDGER_TAIL_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_LEDGER_TAIL_MAX"
ENV_WEBVIEW_SYMBOLS_PAGE_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_SYMBOLS_PAGE_MAX"
ENV_WEBVIEW_NODES_PAGE_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_NODES_PAGE_MAX"
ENV_WEBVIEW_EDGES_PAGE_MAX = "KNOW_YOUR_CONTEXT_WEBVIEW_EDGES_PAGE_MAX"
ENV_WEBVIEW_RATE_ENABLED = "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_ENABLED"
ENV_WEBVIEW_RATE_READ_PER_SECOND = "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_READ_PER_SECOND"
ENV_WEBVIEW_RATE_READ_BURST = "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_READ_BURST"
ENV_WEBVIEW_RATE_HEAVY_PER_SECOND = "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_HEAVY_PER_SECOND"
ENV_WEBVIEW_RATE_HEAVY_BURST = "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_HEAVY_BURST"
ENV_WEBVIEW_RATE_EXPORT_PER_SECOND = "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_EXPORT_PER_SECOND"
ENV_WEBVIEW_RATE_EXPORT_BURST = "KNOW_YOUR_CONTEXT_WEBVIEW_RATE_EXPORT_BURST"
ENV_WEBVIEW_ROUTES_EXPORT_ENABLED = "KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_EXPORT_ENABLED"
ENV_WEBVIEW_ROUTES_CONFIG_ENABLED = "KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_CONFIG_ENABLED"
ENV_WEBVIEW_ROUTES_DEBT_ENABLED = "KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_DEBT_ENABLED"
ENV_WEBVIEW_ROUTES_PROVENANCE_ENABLED = "KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_PROVENANCE_ENABLED"
ENV_WEBVIEW_ROUTES_LEDGER_ENABLED = "KNOW_YOUR_CONTEXT_WEBVIEW_ROUTES_LEDGER_ENABLED"

#: Hardware-aware storage + governor knobs (W15, SUB-134; GPU-VERDICT plan §5).
ENV_STORAGE_MMAP_ENABLED = "KNOW_YOUR_CONTEXT_STORAGE_MMAP_ENABLED"
ENV_STORAGE_MMAP_MAX_BYTES = "KNOW_YOUR_CONTEXT_STORAGE_MMAP_MAX_BYTES"
ENV_STORAGE_SYNCHRONOUS_NORMAL = "KNOW_YOUR_CONTEXT_STORAGE_SYNCHRONOUS_NORMAL"
ENV_STORAGE_PAGE_SIZE = "KNOW_YOUR_CONTEXT_STORAGE_PAGE_SIZE"
ENV_STORAGE_AGGRESSIVE_CACHE = "KNOW_YOUR_CONTEXT_STORAGE_AGGRESSIVE_CACHE"
ENV_STORAGE_CACHE_MAX_KIB = "KNOW_YOUR_CONTEXT_STORAGE_CACHE_MAX_KIB"
ENV_HW_AUTOSCALE_ENABLED = "KNOW_YOUR_CONTEXT_HW_AUTOSCALE_ENABLED"
ENV_HW_MEM_PER_WORKER_MIB = "KNOW_YOUR_CONTEXT_HW_MEM_PER_WORKER_MIB"
ENV_HW_MIN_WORKERS = "KNOW_YOUR_CONTEXT_HW_MIN_WORKERS"
ENV_HW_GPU_OFFLOAD = "KYS_GPU_OFFLOAD"

CONFIG_KNOBS: tuple[ConfigKnob, ...] = (
    ConfigKnob(
        "webview.max_graph_nodes",
        ENV_WEBVIEW_MAX_GRAPH_NODES,
        "int",
        4096,
        "cap on nodes served by /api/graph",
        minimum=16,
        maximum=65536,
    ),
    ConfigKnob(
        "webview.analytics_max_nodes",
        ENV_WEBVIEW_ANALYTICS_MAX_NODES,
        "int",
        2048,
        "cap on nodes fed to analytics/proposals payloads",
        minimum=16,
        maximum=32768,
    ),
    ConfigKnob(
        "webview.frame_width",
        ENV_WEBVIEW_FRAME_WIDTH,
        "float",
        1600.0,
        "layout frame width for /api/graph",
        minimum=100.0,
        maximum=100000.0,
    ),
    ConfigKnob(
        "webview.frame_height",
        ENV_WEBVIEW_FRAME_HEIGHT,
        "float",
        1000.0,
        "layout frame height for /api/graph",
        minimum=100.0,
        maximum=100000.0,
    ),
    ConfigKnob(
        "webview.top_ranking",
        ENV_WEBVIEW_TOP_RANKING,
        "int",
        50,
        "default top-N for centrality rankings",
        minimum=1,
        maximum=500,
    ),
    ConfigKnob(
        "webview.search_limit_default",
        ENV_WEBVIEW_SEARCH_LIMIT_DEFAULT,
        "int",
        10,
        "default limit for /api/search",
        minimum=1,
        maximum=1000,
    ),
    ConfigKnob(
        "webview.search_limit_max",
        ENV_WEBVIEW_SEARCH_LIMIT_MAX,
        "int",
        100,
        "maximum limit for /api/search",
        minimum=1,
        maximum=1000,
    ),
    ConfigKnob(
        "webview.hierarchy_depth_default",
        ENV_WEBVIEW_HIERARCHY_DEPTH_DEFAULT,
        "int",
        1,
        "default depth for /api/hierarchy",
        minimum=1,
        maximum=32,
    ),
    ConfigKnob(
        "webview.hierarchy_depth_max",
        ENV_WEBVIEW_HIERARCHY_DEPTH_MAX,
        "int",
        8,
        "maximum depth for /api/hierarchy",
        minimum=1,
        maximum=32,
    ),
    ConfigKnob(
        "webview.query_limit_default",
        ENV_WEBVIEW_QUERY_LIMIT_DEFAULT,
        "int",
        50,
        "default limit for /api/query",
        minimum=1,
        maximum=2000,
    ),
    ConfigKnob(
        "webview.query_limit_max",
        ENV_WEBVIEW_QUERY_LIMIT_MAX,
        "int",
        200,
        "maximum limit for /api/query",
        minimum=1,
        maximum=2000,
    ),
    ConfigKnob(
        "webview.audit_limit_default",
        ENV_WEBVIEW_AUDIT_LIMIT_DEFAULT,
        "int",
        50,
        "default limit for /api/audit",
        minimum=1,
        maximum=5000,
    ),
    ConfigKnob(
        "webview.audit_limit_max",
        ENV_WEBVIEW_AUDIT_LIMIT_MAX,
        "int",
        500,
        "maximum limit for /api/audit",
        minimum=1,
        maximum=5000,
    ),
    ConfigKnob(
        "webview.audit_tail_limit_default",
        ENV_WEBVIEW_AUDIT_TAIL_LIMIT_DEFAULT,
        "int",
        100,
        "default limit for /api/audit/tail",
        minimum=1,
        maximum=5000,
    ),
    ConfigKnob(
        "webview.audit_tail_limit_max",
        ENV_WEBVIEW_AUDIT_TAIL_LIMIT_MAX,
        "int",
        500,
        "maximum limit for /api/audit/tail",
        minimum=1,
        maximum=5000,
    ),
    ConfigKnob(
        "webview.series_limit_default",
        ENV_WEBVIEW_SERIES_LIMIT_DEFAULT,
        "int",
        90,
        "default limit for /api/metrics/series",
        minimum=1,
        maximum=3660,
    ),
    ConfigKnob(
        "webview.series_limit_max",
        ENV_WEBVIEW_SERIES_LIMIT_MAX,
        "int",
        366,
        "maximum limit for /api/metrics/series",
        minimum=1,
        maximum=3660,
    ),
    ConfigKnob(
        "webview.page_size_default",
        ENV_WEBVIEW_PAGE_SIZE_DEFAULT,
        "int",
        50,
        "default page size for paged list routes",
        minimum=1,
        maximum=5000,
    ),
    ConfigKnob(
        "webview.page_size_max",
        ENV_WEBVIEW_PAGE_SIZE_MAX,
        "int",
        500,
        "maximum page size for paged list routes",
        minimum=1,
        maximum=5000,
    ),
    ConfigKnob(
        "webview.refs_samples_max",
        ENV_WEBVIEW_REFS_SAMPLES_MAX,
        "int",
        20,
        "sample rows served by /api/refs",
        minimum=1,
        maximum=500,
    ),
    ConfigKnob(
        "webview.evidence_limit_max",
        ENV_WEBVIEW_EVIDENCE_LIMIT_MAX,
        "int",
        200,
        "maximum evidence rows per node served by /api/node/{id}/evidence",
        minimum=1,
        maximum=2000,
    ),
    ConfigKnob(
        "webview.export_depth_default",
        ENV_WEBVIEW_EXPORT_DEPTH_DEFAULT,
        "int",
        2,
        "default ego-net depth for /api/export/subgraph",
        minimum=0,
        maximum=16,
    ),
    ConfigKnob(
        "webview.export_depth_max",
        ENV_WEBVIEW_EXPORT_DEPTH_MAX,
        "int",
        4,
        "maximum ego-net depth for /api/export/subgraph",
        minimum=0,
        maximum=16,
    ),
    ConfigKnob(
        "webview.export_nodes_max",
        ENV_WEBVIEW_EXPORT_NODES_MAX,
        "int",
        1024,
        "maximum nodes selected by /api/export/subgraph",
        minimum=1,
        maximum=65536,
    ),
    ConfigKnob(
        "webview.export_max_bytes",
        ENV_WEBVIEW_EXPORT_MAX_BYTES,
        "int",
        262144,
        "per-artifact byte cap enforced by /api/export/subgraph",
        minimum=1024,
        maximum=16777216,
    ),
    ConfigKnob(
        "webview.trace_capacity",
        ENV_WEBVIEW_TRACE_CAPACITY,
        "int",
        64,
        "request timing ring capacity (Server-Timing middleware)",
        minimum=1,
        maximum=4096,
    ),
    ConfigKnob(
        "webview.shutdown_join_seconds",
        ENV_WEBVIEW_SHUTDOWN_JOIN_SECONDS,
        "float",
        5.0,
        "serve-thread join timeout on shutdown",
        minimum=0.0,
        maximum=60.0,
        minimum_exclusive=True,
    ),
    ConfigKnob(
        "webview.heartbeat_poll_seconds",
        ENV_WEBVIEW_HEARTBEAT_POLL_SECONDS,
        "float",
        0.5,
        "default async serve-loop heartbeat poll interval",
        minimum=0.0,
        maximum=60.0,
        minimum_exclusive=True,
    ),
    ConfigKnob(
        "webview.improvements_scan_max",
        ENV_WEBVIEW_IMPROVEMENTS_SCAN_MAX,
        "int",
        100000,
        "cap on event rows scanned by the improvements routes",
        minimum=100,
        maximum=10000000,
    ),
    ConfigKnob(
        "webview.bench_rows_max",
        ENV_WEBVIEW_BENCH_ROWS_MAX,
        "int",
        256,
        "cap on raw duration samples served by /api/bench/{operation}",
        minimum=1,
        maximum=10000,
    ),
    ConfigKnob(
        "webview.ledger_tail_max",
        ENV_WEBVIEW_LEDGER_TAIL_MAX,
        "int",
        500,
        "maximum entries served by /api/ledger/* tails",
        minimum=1,
        maximum=10000,
    ),
    ConfigKnob(
        "webview.symbols_page_max",
        ENV_WEBVIEW_SYMBOLS_PAGE_MAX,
        "int",
        500,
        "maximum page size for /api/symbols",
        minimum=1,
        maximum=5000,
    ),
    ConfigKnob(
        "webview.nodes_page_max",
        ENV_WEBVIEW_NODES_PAGE_MAX,
        "int",
        1000,
        "maximum page size for /api/nodes",
        minimum=1,
        maximum=10000,
    ),
    ConfigKnob(
        "webview.edges_page_max",
        ENV_WEBVIEW_EDGES_PAGE_MAX,
        "int",
        1000,
        "maximum page size for /api/edges and /api/graph/edges",
        minimum=1,
        maximum=10000,
    ),
    ConfigKnob(
        "webview.rate.enabled",
        ENV_WEBVIEW_RATE_ENABLED,
        "bool",
        True,
        "per-class token-bucket rate limiting on the REST surface",
    ),
    ConfigKnob(
        "webview.rate.read_per_second",
        ENV_WEBVIEW_RATE_READ_PER_SECOND,
        "float",
        100.0,
        "sustained request rate for read-class routes",
        minimum=0.0,
        maximum=10000.0,
        minimum_exclusive=True,
    ),
    ConfigKnob(
        "webview.rate.read_burst",
        ENV_WEBVIEW_RATE_READ_BURST,
        "int",
        200,
        "burst capacity for read-class routes",
        minimum=1,
        maximum=100000,
    ),
    ConfigKnob(
        "webview.rate.heavy_per_second",
        ENV_WEBVIEW_RATE_HEAVY_PER_SECOND,
        "float",
        20.0,
        "sustained request rate for heavy-class routes (graph/analytics/health/debt)",
        minimum=0.0,
        maximum=10000.0,
        minimum_exclusive=True,
    ),
    ConfigKnob(
        "webview.rate.heavy_burst",
        ENV_WEBVIEW_RATE_HEAVY_BURST,
        "int",
        40,
        "burst capacity for heavy-class routes",
        minimum=1,
        maximum=100000,
    ),
    ConfigKnob(
        "webview.rate.export_per_second",
        ENV_WEBVIEW_RATE_EXPORT_PER_SECOND,
        "float",
        10.0,
        "sustained request rate for export-class routes",
        minimum=0.0,
        maximum=1000.0,
        minimum_exclusive=True,
    ),
    ConfigKnob(
        "webview.rate.export_burst",
        ENV_WEBVIEW_RATE_EXPORT_BURST,
        "int",
        20,
        "burst capacity for export-class routes",
        minimum=1,
        maximum=10000,
    ),
    ConfigKnob(
        "webview.routes.export_enabled",
        ENV_WEBVIEW_ROUTES_EXPORT_ENABLED,
        "bool",
        True,
        "serve the /api/export/* route group (false: fail-closed 404)",
    ),
    ConfigKnob(
        "webview.routes.config_enabled",
        ENV_WEBVIEW_ROUTES_CONFIG_ENABLED,
        "bool",
        True,
        "serve the /api/config route group (false: fail-closed 404)",
    ),
    ConfigKnob(
        "webview.routes.debt_enabled",
        ENV_WEBVIEW_ROUTES_DEBT_ENABLED,
        "bool",
        True,
        "serve the /api/debt/report route (false: fail-closed 404)",
    ),
    ConfigKnob(
        "webview.routes.provenance_enabled",
        ENV_WEBVIEW_ROUTES_PROVENANCE_ENABLED,
        "bool",
        True,
        "serve the /api/provenance/* route group (false: fail-closed 404)",
    ),
    ConfigKnob(
        "webview.routes.ledger_enabled",
        ENV_WEBVIEW_ROUTES_LEDGER_ENABLED,
        "bool",
        True,
        "serve the /api/ledger/* + /api/audit/verify route group (false: fail-closed 404)",
    ),
    ConfigKnob(
        "storage.mmap_enabled",
        ENV_STORAGE_MMAP_ENABLED,
        "bool",
        True,
        "mmap_size = min(db_size, ½ MemAvailable) on file-backed stores (verdict plan §5.3)",
    ),
    ConfigKnob(
        "storage.mmap_max_bytes",
        ENV_STORAGE_MMAP_MAX_BYTES,
        "int",
        1073741824,
        "hard cap on the computed mmap_size (bytes)",
        minimum=0,
        maximum=2199023255552,
    ),
    ConfigKnob(
        "storage.synchronous_normal",
        ENV_STORAGE_SYNCHRONOUS_NORMAL,
        "bool",
        False,
        "allow synchronous=NORMAL when the store device is non-rotational (default FULL = historical)",
    ),
    ConfigKnob(
        "storage.page_size",
        ENV_STORAGE_PAGE_SIZE,
        "int",
        0,
        "page_size hint for freshly created databases (0 = engine default 4096; power of two 512..65536)",
        minimum=0,
        maximum=65536,
    ),
    ConfigKnob(
        "storage.aggressive_cache",
        ENV_STORAGE_AGGRESSIVE_CACHE,
        "bool",
        True,
        "size the SQLite page cache from available RAM (⅛ of MemAvailable, capped) instead of the 2 MiB engine default",
    ),
    ConfigKnob(
        "storage.cache_max_kib",
        ENV_STORAGE_CACHE_MAX_KIB,
        "int",
        4194304,
        "hard cap on the computed page-cache size (KiB)",
        minimum=0,
        maximum=67108864,
    ),
    ConfigKnob(
        "hw.autoscale_enabled",
        ENV_HW_AUTOSCALE_ENABLED,
        "bool",
        True,
        "adaptive scale-up parallelism advisor (workers follow cores; never throttles down)",
    ),
    ConfigKnob(
        "hw.mem_per_worker_mib",
        ENV_HW_MEM_PER_WORKER_MIB,
        "int",
        512,
        "RAM allowance per worker: workers <= cores AND <= MemAvailable / this",
        minimum=64,
        maximum=16384,
    ),
    ConfigKnob(
        "hw.min_workers",
        ENV_HW_MIN_WORKERS,
        "int",
        1,
        "floor on advised workers (idle machines stay warm at this level)",
        minimum=1,
        maximum=64,
    ),
    ConfigKnob(
        "hw.gpu_offload",
        ENV_HW_GPU_OFFLOAD,
        "bool",
        False,
        "allow GPU offload when the verdict formula passes (default NO-GPU; consumed by hardware.gpu_gate)",
    ),
)

_KNOB_BY_NAME: dict[str, ConfigKnob] = {knob.name: knob for knob in CONFIG_KNOBS}

#: Paired (default, max) knobs that must stay ordered; checked at load time.
_KNOB_ORDER_PAIRS: tuple[tuple[str, str], ...] = (
    ("webview.search_limit_default", "webview.search_limit_max"),
    ("webview.query_limit_default", "webview.query_limit_max"),
    ("webview.audit_limit_default", "webview.audit_limit_max"),
    ("webview.audit_tail_limit_default", "webview.audit_tail_limit_max"),
    ("webview.series_limit_default", "webview.series_limit_max"),
    ("webview.hierarchy_depth_default", "webview.hierarchy_depth_max"),
    ("webview.page_size_default", "webview.page_size_max"),
    ("webview.export_depth_default", "webview.export_depth_max"),
)


def parse_knob_value(knob: ConfigKnob, raw: str) -> int | float | bool:
    """Strictly parse one env string for *knob*; anything ambiguous fails closed."""
    value: int | float | bool
    if knob.kind == "bool":
        value = parse_switch_bool(knob.env, raw)
    elif knob.kind == "int":
        text = raw.strip()
        if not _INT_TEXT_RE.match(text):
            raise SwitchError("INVALID_VALUE", f"env {knob.env} for {knob.name!r} is not an integer: {raw!r}")
        value = int(text)
    else:  # float
        text = raw.strip()
        try:
            parsed = float(text)
        except ValueError:
            raise SwitchError("INVALID_VALUE", f"env {knob.env} for {knob.name!r} is not a float: {raw!r}") from None
        if not text or not math.isfinite(parsed):
            raise SwitchError("INVALID_VALUE", f"env {knob.env} for {knob.name!r} is not a finite float: {raw!r}")
        value = parsed
    if knob.kind != "bool":
        numeric = float(value)
        if knob.minimum is not None:
            if knob.minimum_exclusive and not numeric > knob.minimum:
                raise SwitchError(
                    "OUT_OF_RANGE", f"env {knob.env} for {knob.name!r} must be > {knob.minimum}, got {value}"
                )
            if not knob.minimum_exclusive and not numeric >= knob.minimum:
                raise SwitchError(
                    "OUT_OF_RANGE", f"env {knob.env} for {knob.name!r} must be >= {knob.minimum}, got {value}"
                )
        if knob.maximum is not None and not numeric <= knob.maximum:
            raise SwitchError(
                "OUT_OF_RANGE", f"env {knob.env} for {knob.name!r} must be <= {knob.maximum}, got {value}"
            )
    return value


class KnobBoard:
    """The resolved value + provenance of every config knob; immutable snapshot."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        unknown = set(values) - set(_KNOB_BY_NAME)
        if unknown:
            raise SwitchError("UNKNOWN_KNOB", f"unknown knob names: {sorted(unknown)}")
        missing = set(_KNOB_BY_NAME) - set(values)
        if missing:
            raise SwitchError("INVALID_SPEC", f"missing knob values: {sorted(missing)}")
        self._values = dict(values)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._values))

    def value(self, name: str) -> int | float | bool:
        if name not in _KNOB_BY_NAME:
            raise SwitchError("UNKNOWN_KNOB", f"unknown config knob {name!r}")
        return self._values[name][0]

    def source(self, name: str) -> str:
        if name not in _KNOB_BY_NAME:
            raise SwitchError("UNKNOWN_KNOB", f"unknown config knob {name!r}")
        return self._values[name][1]

    def active(self) -> tuple[ConfigKnob, ...]:
        """Knobs whose value came from the environment (not the default)."""
        return tuple(knob for knob in CONFIG_KNOBS if self._values[knob.name][1] == "env")

    def dump(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for knob in CONFIG_KNOBS:
            value, source = self._values[knob.name]
            out[knob.name] = {
                "value": value,
                "source": source,
                "env": knob.env,
                "kind": knob.kind,
                "default": knob.default,
                "minimum": knob.minimum,
                "maximum": knob.maximum,
                "affects": knob.affects,
            }
        return out


def load_knobs(env: Mapping[str, str] | None = None) -> KnobBoard:
    """Resolve every config knob from *env* (default: the process environment).

    Like :func:`load_switches`, reads are explicit per knob so every env name
    is greppable at exactly one read site; invalid values fail closed.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    values: dict[str, tuple[int | float | bool, str]] = {}

    def _read(knob: ConfigKnob, raw: str | None) -> None:
        if raw is None:
            values[knob.name] = (knob.default, "default")
        else:
            values[knob.name] = (parse_knob_value(knob, raw), "env")

    _read(_KNOB_BY_NAME["webview.max_graph_nodes"], environ.get(ENV_WEBVIEW_MAX_GRAPH_NODES))
    _read(_KNOB_BY_NAME["webview.analytics_max_nodes"], environ.get(ENV_WEBVIEW_ANALYTICS_MAX_NODES))
    _read(_KNOB_BY_NAME["webview.frame_width"], environ.get(ENV_WEBVIEW_FRAME_WIDTH))
    _read(_KNOB_BY_NAME["webview.frame_height"], environ.get(ENV_WEBVIEW_FRAME_HEIGHT))
    _read(_KNOB_BY_NAME["webview.top_ranking"], environ.get(ENV_WEBVIEW_TOP_RANKING))
    _read(_KNOB_BY_NAME["webview.search_limit_default"], environ.get(ENV_WEBVIEW_SEARCH_LIMIT_DEFAULT))
    _read(_KNOB_BY_NAME["webview.search_limit_max"], environ.get(ENV_WEBVIEW_SEARCH_LIMIT_MAX))
    _read(_KNOB_BY_NAME["webview.hierarchy_depth_default"], environ.get(ENV_WEBVIEW_HIERARCHY_DEPTH_DEFAULT))
    _read(_KNOB_BY_NAME["webview.hierarchy_depth_max"], environ.get(ENV_WEBVIEW_HIERARCHY_DEPTH_MAX))
    _read(_KNOB_BY_NAME["webview.query_limit_default"], environ.get(ENV_WEBVIEW_QUERY_LIMIT_DEFAULT))
    _read(_KNOB_BY_NAME["webview.query_limit_max"], environ.get(ENV_WEBVIEW_QUERY_LIMIT_MAX))
    _read(_KNOB_BY_NAME["webview.audit_limit_default"], environ.get(ENV_WEBVIEW_AUDIT_LIMIT_DEFAULT))
    _read(_KNOB_BY_NAME["webview.audit_limit_max"], environ.get(ENV_WEBVIEW_AUDIT_LIMIT_MAX))
    _read(_KNOB_BY_NAME["webview.audit_tail_limit_default"], environ.get(ENV_WEBVIEW_AUDIT_TAIL_LIMIT_DEFAULT))
    _read(_KNOB_BY_NAME["webview.audit_tail_limit_max"], environ.get(ENV_WEBVIEW_AUDIT_TAIL_LIMIT_MAX))
    _read(_KNOB_BY_NAME["webview.series_limit_default"], environ.get(ENV_WEBVIEW_SERIES_LIMIT_DEFAULT))
    _read(_KNOB_BY_NAME["webview.series_limit_max"], environ.get(ENV_WEBVIEW_SERIES_LIMIT_MAX))
    _read(_KNOB_BY_NAME["webview.page_size_default"], environ.get(ENV_WEBVIEW_PAGE_SIZE_DEFAULT))
    _read(_KNOB_BY_NAME["webview.page_size_max"], environ.get(ENV_WEBVIEW_PAGE_SIZE_MAX))
    _read(_KNOB_BY_NAME["webview.refs_samples_max"], environ.get(ENV_WEBVIEW_REFS_SAMPLES_MAX))
    _read(_KNOB_BY_NAME["webview.evidence_limit_max"], environ.get(ENV_WEBVIEW_EVIDENCE_LIMIT_MAX))
    _read(_KNOB_BY_NAME["webview.export_depth_default"], environ.get(ENV_WEBVIEW_EXPORT_DEPTH_DEFAULT))
    _read(_KNOB_BY_NAME["webview.export_depth_max"], environ.get(ENV_WEBVIEW_EXPORT_DEPTH_MAX))
    _read(_KNOB_BY_NAME["webview.export_nodes_max"], environ.get(ENV_WEBVIEW_EXPORT_NODES_MAX))
    _read(_KNOB_BY_NAME["webview.export_max_bytes"], environ.get(ENV_WEBVIEW_EXPORT_MAX_BYTES))
    _read(_KNOB_BY_NAME["webview.trace_capacity"], environ.get(ENV_WEBVIEW_TRACE_CAPACITY))
    _read(_KNOB_BY_NAME["webview.shutdown_join_seconds"], environ.get(ENV_WEBVIEW_SHUTDOWN_JOIN_SECONDS))
    _read(_KNOB_BY_NAME["webview.heartbeat_poll_seconds"], environ.get(ENV_WEBVIEW_HEARTBEAT_POLL_SECONDS))
    _read(_KNOB_BY_NAME["webview.improvements_scan_max"], environ.get(ENV_WEBVIEW_IMPROVEMENTS_SCAN_MAX))
    _read(_KNOB_BY_NAME["webview.bench_rows_max"], environ.get(ENV_WEBVIEW_BENCH_ROWS_MAX))
    _read(_KNOB_BY_NAME["webview.ledger_tail_max"], environ.get(ENV_WEBVIEW_LEDGER_TAIL_MAX))
    _read(_KNOB_BY_NAME["webview.symbols_page_max"], environ.get(ENV_WEBVIEW_SYMBOLS_PAGE_MAX))
    _read(_KNOB_BY_NAME["webview.nodes_page_max"], environ.get(ENV_WEBVIEW_NODES_PAGE_MAX))
    _read(_KNOB_BY_NAME["webview.edges_page_max"], environ.get(ENV_WEBVIEW_EDGES_PAGE_MAX))
    _read(_KNOB_BY_NAME["webview.rate.enabled"], environ.get(ENV_WEBVIEW_RATE_ENABLED))
    _read(_KNOB_BY_NAME["webview.rate.read_per_second"], environ.get(ENV_WEBVIEW_RATE_READ_PER_SECOND))
    _read(_KNOB_BY_NAME["webview.rate.read_burst"], environ.get(ENV_WEBVIEW_RATE_READ_BURST))
    _read(_KNOB_BY_NAME["webview.rate.heavy_per_second"], environ.get(ENV_WEBVIEW_RATE_HEAVY_PER_SECOND))
    _read(_KNOB_BY_NAME["webview.rate.heavy_burst"], environ.get(ENV_WEBVIEW_RATE_HEAVY_BURST))
    _read(_KNOB_BY_NAME["webview.rate.export_per_second"], environ.get(ENV_WEBVIEW_RATE_EXPORT_PER_SECOND))
    _read(_KNOB_BY_NAME["webview.rate.export_burst"], environ.get(ENV_WEBVIEW_RATE_EXPORT_BURST))
    _read(_KNOB_BY_NAME["webview.routes.export_enabled"], environ.get(ENV_WEBVIEW_ROUTES_EXPORT_ENABLED))
    _read(_KNOB_BY_NAME["webview.routes.config_enabled"], environ.get(ENV_WEBVIEW_ROUTES_CONFIG_ENABLED))
    _read(_KNOB_BY_NAME["webview.routes.debt_enabled"], environ.get(ENV_WEBVIEW_ROUTES_DEBT_ENABLED))
    _read(_KNOB_BY_NAME["webview.routes.provenance_enabled"], environ.get(ENV_WEBVIEW_ROUTES_PROVENANCE_ENABLED))
    _read(_KNOB_BY_NAME["webview.routes.ledger_enabled"], environ.get(ENV_WEBVIEW_ROUTES_LEDGER_ENABLED))
    _read(_KNOB_BY_NAME["storage.mmap_enabled"], environ.get(ENV_STORAGE_MMAP_ENABLED))
    _read(_KNOB_BY_NAME["storage.mmap_max_bytes"], environ.get(ENV_STORAGE_MMAP_MAX_BYTES))
    _read(_KNOB_BY_NAME["storage.synchronous_normal"], environ.get(ENV_STORAGE_SYNCHRONOUS_NORMAL))
    _read(_KNOB_BY_NAME["storage.page_size"], environ.get(ENV_STORAGE_PAGE_SIZE))
    _read(_KNOB_BY_NAME["storage.aggressive_cache"], environ.get(ENV_STORAGE_AGGRESSIVE_CACHE))
    _read(_KNOB_BY_NAME["storage.cache_max_kib"], environ.get(ENV_STORAGE_CACHE_MAX_KIB))
    _read(_KNOB_BY_NAME["hw.autoscale_enabled"], environ.get(ENV_HW_AUTOSCALE_ENABLED))
    _read(_KNOB_BY_NAME["hw.mem_per_worker_mib"], environ.get(ENV_HW_MEM_PER_WORKER_MIB))
    _read(_KNOB_BY_NAME["hw.min_workers"], environ.get(ENV_HW_MIN_WORKERS))
    _read(_KNOB_BY_NAME["hw.gpu_offload"], environ.get(ENV_HW_GPU_OFFLOAD))

    board = KnobBoard(values)
    for default_name, max_name in _KNOB_ORDER_PAIRS:
        if float(board.value(default_name)) > float(board.value(max_name)):
            raise SwitchError(
                "OUT_OF_RANGE",
                f"knob {default_name!r} ({board.value(default_name)}) exceeds {max_name!r} "
                f"({board.value(max_name)}); raise the max or lower the default",
            )
    return board


def describe_knobs() -> str:
    """The knob registry as a deterministic markdown table."""
    lines = [
        "# Configuration Knobs",
        "",
        "Every tunable has exactly one env override, grouped by family",
        "(webview.*, storage.*, hw.*). Parsing is strict; invalid or",
        "out-of-range values fail closed at load time.",
        "",
        "| knob | env | kind | default | bounds | affects |",
        "|---|---|---|---|---|---|",
    ]
    for knob in CONFIG_KNOBS:
        bounds = ""
        if knob.minimum is not None or knob.maximum is not None:
            low = f"{'>' if knob.minimum_exclusive else '>='}{knob.minimum}" if knob.minimum is not None else ""
            high = f"<={knob.maximum}" if knob.maximum is not None else ""
            bounds = " ".join(part for part in (low, high) if part)
        lines.append(
            f"| `{knob.name}` | `{knob.env}` | {knob.kind} | `{knob.default}` | {bounds or '—'} | {knob.affects} |"
        )
    lines.append("")
    return "\n".join(lines)
