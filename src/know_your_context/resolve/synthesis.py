"""Bounded synthesis passes over the resolved graph (wave W7, SUB-103).

Re-implements the *applicable* subset of codegraph's 45 ``SYNTH_PASSES``
(RESEARCH-COVERAGE-AUDIT §4.1) — rewritten against the schema-v4 store, not
ported. The four passes close graph gaps that static extraction + primary
resolution structurally cannot see:

* ``method_class_containment`` — generalizes codegraph's fixed
  ``goMethodContains`` synth step: an orphan ``method`` symbol (no
  ``contains`` parent from the parse) gains one from its qualified-name owner
  (``Greeter.greet`` → ``Greeter``, confidence 9/10) or, failing that, the
  nearest preceding class in the same file (confidence 7/10). Ambiguity
  (0 or 2+ owners, same-line classes, would-be cycle) skips the candidate.
* ``reexport_chains`` — import A from B; B re-exports from C → A references C.
  An ``imports`` edge landing on another ``import`` node *is* a re-export;
  the chain is walked (bounded depth, cycle-safe) to the terminal non-import
  symbol and minted as a ``references`` edge (confidence 4/5).
* ``type_hint_edges`` — parked ``type_hint`` refs (a ref kind the primary
  cascade refuses as ``unknown_ref_kind``) resolve to type-kind symbols
  (``class``/``interface``/``type``) and mint ``type_of`` edges
  (confidence 9/10).
* ``callback_edges`` — named callbacks passed by reference (resolved
  ``function_ref`` refs) are linked from the registrar's *callers* to the
  callback: the dynamic-dispatch channel codegraph's callback-synthesizer
  covers with field/event pairing. Our store has no field/channel data, so
  the registrar itself is the hub; channels are capped
  (``max_callbacks_per_channel`` / ``max_dispatchers_per_channel``, the
  ``MAX_CALLBACKS_PER_CHANNEL`` / ``EVENT_FANOUT_CAP`` pattern) and the whole
  channel is skipped — never truncated — when a cap trips (confidence 2/5).

Invariants (all pinned by tests):

* provenance is always ``synthesis:<pass>`` — never the parse stamp
  (``ingest:*``) and never a resolver stamp, so synthesized edges can never
  masquerade as parsed or primarily-resolved ones;
* synthesis never creates a second edge where any same-(source,target,kind)
  edge exists, whatever its provenance (parse edges are untouchable);
* every pass is idempotent (a second run inserts zero) and deterministic
  (same store in → same rows out), and each pass can be disabled via
  :class:`SynthesisConfig`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from know_your_context.domain import canonical_json
from know_your_context.ops.switches import load_switches
from know_your_context.resolve.name_matcher import NameMatchConfig, NameMatchReport, NameMatchResolver
from know_your_context.resolve.resolver import BatchReport, ReferenceResolver, ResolverConfig
from know_your_context.store import EventStore, UnresolvedRefRow

__all__ = [
    "PASS_CALLBACK_EDGES",
    "PASS_DEPENDENCY_INJECTION",
    "PASS_EVENT_HANDLER_WIRING",
    "PASS_FACTORY_FUNCTIONS",
    "PASS_FRAMEWORK_ROUTES",
    "PASS_INTERFACE_IMPLEMENTATION",
    "PASS_METHOD_CLASS_CONTAINMENT",
    "PASS_MODULE_MEMBER_CALLS",
    "PASS_REEXPORT_CHAINS",
    "PASS_REEXPORT_PROPAGATION",
    "PASS_TEST_SUBJECT_EDGES",
    "PASS_TYPE_HINT_EDGES",
    "SYNTH_PASS_ORDER",
    "SYNTHESIS_VERSION",
    "PassReport",
    "PipelineReport",
    "SynthesisConfig",
    "SynthesisError",
    "SynthesisReport",
    "Synthesizer",
    "pass_provenance",
    "resolve_and_synthesize",
    "run_synthesis",
]

SYNTHESIS_VERSION: int = 2

PASS_METHOD_CLASS_CONTAINMENT: str = "method_class_containment"
PASS_REEXPORT_CHAINS: str = "reexport_chains"
PASS_TYPE_HINT_EDGES: str = "type_hint_edges"
PASS_CALLBACK_EDGES: str = "callback_edges"
#: W12 (SUB-121): eight further passes from the codegraph synth-pass list,
#: rewritten against the schema-v4 store (docs/recovery/w12-depth.md §4).
PASS_MODULE_MEMBER_CALLS: str = "module_member_calls"
PASS_REEXPORT_PROPAGATION: str = "reexport_propagation"
PASS_DEPENDENCY_INJECTION: str = "dependency_injection"
PASS_INTERFACE_IMPLEMENTATION: str = "interface_implementation"
PASS_FRAMEWORK_ROUTES: str = "framework_route_registration"
PASS_EVENT_HANDLER_WIRING: str = "event_handler_wiring"
PASS_TEST_SUBJECT_EDGES: str = "test_subject_edges"
PASS_FACTORY_FUNCTIONS: str = "factory_function_edges"

#: Fixed pass order = merge order (codegraph's SYNTH_PASSES rule). Containment
#: first: later passes may read hierarchy. ``type_hint_edges`` precedes
#: ``dependency_injection`` (which consumes its ``type_of`` edges).
#: ``callback_edges`` last: it consumes edges the resolver tier minted.
SYNTH_PASS_ORDER: tuple[str, ...] = (
    PASS_METHOD_CLASS_CONTAINMENT,
    PASS_REEXPORT_CHAINS,
    PASS_TYPE_HINT_EDGES,
    PASS_MODULE_MEMBER_CALLS,
    PASS_REEXPORT_PROPAGATION,
    PASS_DEPENDENCY_INJECTION,
    PASS_INTERFACE_IMPLEMENTATION,
    PASS_FRAMEWORK_ROUTES,
    PASS_EVENT_HANDLER_WIRING,
    PASS_TEST_SUBJECT_EDGES,
    PASS_FACTORY_FUNCTIONS,
    PASS_CALLBACK_EDGES,
)

#: Symbol kinds a ``type_hint`` ref may land on.
_TYPE_KINDS: frozenset[str] = frozenset({"class", "interface", "type"})

#: Symbol kinds that may act as callback registrars / dispatchers.
_CALLABLE_KINDS: frozenset[str] = frozenset({"function", "method"})


class SynthesisError(ValueError):
    """Invalid synthesis configuration (fail-closed)."""


def pass_provenance(pass_name: str) -> str:
    """The provenance stamp of one pass: ``synthesis:<name>``."""
    if pass_name not in SYNTH_PASS_ORDER:
        raise SynthesisError(f"unknown synthesis pass {pass_name!r}")
    return f"synthesis:{pass_name}"


def _check_cap(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SynthesisError(f"{label} must be an int >= 1, got {value!r}")


@dataclass(frozen=True)
class SynthesisConfig:
    """Tuning knobs; ``enabled_passes=None`` enables every pass."""

    enabled_passes: frozenset[str] | None = None
    max_callbacks_per_channel: int = 40
    max_dispatchers_per_channel: int = 6
    reexport_max_depth: int = 8
    ancestor_cap: int = 64
    skip_generated: bool = True
    #: W12 caps (SUB-121): fanout ceilings; a tripped cap skips the whole
    #: candidate — never truncated.
    max_reexport_fanout: int = 32
    max_injected_deps: int = 16
    max_handlers_per_event: int = 16
    max_emitters_per_event: int = 8
    max_interface_methods: int = 32

    def __post_init__(self) -> None:
        if self.enabled_passes is not None:
            if not isinstance(self.enabled_passes, frozenset):
                raise SynthesisError("enabled_passes must be a frozenset or None")
            unknown = self.enabled_passes - frozenset(SYNTH_PASS_ORDER)
            if unknown:
                raise SynthesisError(f"unknown synthesis passes: {sorted(unknown)}")
        for label, value in (
            ("max_callbacks_per_channel", self.max_callbacks_per_channel),
            ("max_dispatchers_per_channel", self.max_dispatchers_per_channel),
            ("reexport_max_depth", self.reexport_max_depth),
            ("ancestor_cap", self.ancestor_cap),
            ("max_reexport_fanout", self.max_reexport_fanout),
            ("max_injected_deps", self.max_injected_deps),
            ("max_handlers_per_event", self.max_handlers_per_event),
            ("max_emitters_per_event", self.max_emitters_per_event),
            ("max_interface_methods", self.max_interface_methods),
        ):
            _check_cap(value, label)
        if not isinstance(self.skip_generated, bool):
            raise SynthesisError("skip_generated must be a bool")

    def is_enabled(self, pass_name: str) -> bool:
        if pass_name not in SYNTH_PASS_ORDER:
            raise SynthesisError(f"unknown synthesis pass {pass_name!r}")
        return self.enabled_passes is None or pass_name in self.enabled_passes


@dataclass(frozen=True)
class PassReport:
    """Per-pass deterministic counts."""

    name: str
    candidates: int
    emitted: int
    inserted: int
    skipped_existing: int
    skipped_uncertain: int


@dataclass(frozen=True)
class SynthesisReport:
    """One full synthesis run; ``passes`` follows ``SYNTH_PASS_ORDER``."""

    passes: tuple[PassReport, ...]
    emitted: int
    inserted: int


@dataclass(frozen=True)
class PipelineReport:
    """The composed resolve → name-match → synthesis run (SUB-103 wiring)."""

    resolver: BatchReport
    name_match: NameMatchReport
    synthesis: SynthesisReport


def _ref_row(row: sqlite3.Row) -> UnresolvedRefRow:
    reason = row["failure_reason"]
    return UnresolvedRefRow(
        ref_id=int(row["ref_id"]),
        source_node_id=str(row["source_node_id"]),
        target_name=str(row["target_name"]),
        name_tail=str(row["name_tail"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        first_seen=str(row["first_seen"]),
        attempts=int(row["attempts"]),
        failure_reason=None if reason is None else str(reason),
    )


def _edge_exists(store: EventStore, source: str, target: str, kind: str) -> bool:
    """Identity-level existence, provenance-agnostic (the non-overwrite rule)."""
    with store.read() as conn:
        row = conn.execute(
            "SELECT 1 FROM graph_edges WHERE source = ? AND target = ? AND kind = ? LIMIT 1",
            (source, target, kind),
        ).fetchone()
    return row is not None


def _metadata(pass_name: str, confidence: str, **extra: object) -> str:
    return canonical_json({"synthesized_by": pass_name, "confidence": confidence, **extra})


class _Pass:
    """Accumulates one pass's deterministic counts."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.candidates = 0
        self.emitted = 0
        self.skipped_existing = 0
        self.skipped_uncertain = 0
        self.inserted = 0
        self.payloads: dict[tuple[str, str, str], dict[str, object]] = {}
        #: Optional bookkeeping run after the edge transaction (type_hint
        #: resolves its refs there, mirroring the resolver's write order).
        self.after_insert: Callable[[EventStore], None] | None = None

    def emit(self, store: EventStore, source: str, target: str, kind: str, metadata: str) -> None:
        key = (source, target, kind)
        if key in self.payloads:
            return
        if _edge_exists(store, source, target, kind):
            self.skipped_existing += 1
            return
        self.payloads[key] = {
            "source": source,
            "target": target,
            "kind": kind,
            "metadata": metadata,
            "provenance": pass_provenance(self.name),
        }
        self.emitted += 1

    def emit_existing(self) -> None:
        """Count a hit whose edge already exists (answer known, nothing minted)."""
        self.skipped_existing += 1

    def insert(self, store: EventStore) -> int:
        ordered = [self.payloads[key] for key in sorted(self.payloads)]
        self.inserted = store.add_graph_edges(ordered) if ordered else 0
        if self.after_insert is not None:
            self.after_insert(store)
        return self.inserted

    def report(self) -> PassReport:
        return PassReport(
            name=self.name,
            candidates=self.candidates,
            emitted=self.emitted,
            inserted=self.inserted,
            skipped_existing=self.skipped_existing,
            skipped_uncertain=self.skipped_uncertain,
        )


# ------------------------------------------------------- pass implementations


def _run_method_class_containment(store: EventStore, config: SynthesisConfig) -> _Pass:
    result = _Pass(PASS_METHOD_CLASS_CONTAINMENT)
    generated_filter = "AND f.generated_hint = 0" if config.skip_generated else ""
    with store.read() as conn:
        methods = conn.execute(
            "SELECT s.* FROM symbols s JOIN files f ON f.path = s.path "
            f"WHERE s.kind = 'method' {generated_filter} ORDER BY s.node_id"  # nosec B608 -- generated_filter is a fixed internal fragment
        ).fetchall()
    for row in methods:
        node_id = str(row["node_id"])
        name = str(row["name"])
        path = str(row["path"])
        line = int(row["line"])
        incoming = store.incoming_edges(node_id, kinds=("contains",))
        own = pass_provenance(PASS_METHOD_CLASS_CONTAINMENT)
        if any(edge.provenance != own for edge in incoming):
            continue  # a real (parse/resolver) parent exists — untouchable
        result.candidates += 1
        owner: str | None = None
        separator = "." if "." in name else ("::" if "::" in name else None)
        if separator is not None:
            prefix = name.rsplit(separator, 1)[0]
            classes = [
                symbol for symbol in store.symbols_in_path(path) if symbol.kind == "class" and symbol.name == prefix
            ]
            if len(classes) != 1:
                result.skipped_uncertain += 1
                continue
            owner = classes[0].node_id
            confidence = "9/10"
            rule = "qualified_prefix"
        else:
            classes = [
                symbol for symbol in store.symbols_in_path(path) if symbol.kind == "class" and symbol.line < line
            ]
            if not classes:
                continue
            best_line = max(symbol.line for symbol in classes)
            nearest = [symbol for symbol in classes if symbol.line == best_line]
            if len(nearest) != 1:
                result.skipped_uncertain += 1
                continue
            owner = nearest[0].node_id
            confidence = "7/10"
            rule = "nearest_preceding_class"
        if _is_descendant(store, node_id, owner, cap=config.ancestor_cap):
            result.skipped_uncertain += 1  # would close a contains cycle
            continue
        result.emit(
            store,
            owner,
            node_id,
            "contains",
            _metadata(PASS_METHOD_CLASS_CONTAINMENT, confidence, rule=rule),
        )
    return result


def _is_contained_below(store: EventStore, ancestor: str, node: str, *, cap: int) -> bool:
    """True when ``node`` sits in the ``contains`` subtree of ``ancestor`` (bounded BFS)."""
    if ancestor == node:
        return True
    frontier = [ancestor]
    seen = {ancestor}
    steps = 0
    while frontier and steps < cap:
        steps += 1
        current = frontier.pop(0)
        for edge in store.outgoing_edges(current, kinds=("contains",)):
            if edge.target == node:
                return True
            if edge.target not in seen:
                seen.add(edge.target)
                frontier.append(edge.target)
    return False


def _is_descendant(store: EventStore, ancestor: str, node: str, *, cap: int) -> bool:
    return _is_contained_below(store, ancestor, node, cap=cap)


def _run_reexport_chains(store: EventStore, config: SynthesisConfig) -> _Pass:
    result = _Pass(PASS_REEXPORT_CHAINS)
    with store.read() as conn:
        import_nodes = conn.execute("SELECT node_id FROM symbols WHERE kind = 'import' ORDER BY node_id").fetchall()
    for row in import_nodes:
        start = str(row["node_id"])
        result.candidates += 1
        outgoing = store.outgoing_edges(start, kinds=("imports",))
        if len(outgoing) != 1:
            continue
        first = outgoing[0].target
        first_symbol = store.symbol_by_node(first)
        if first_symbol is None or first_symbol.kind != "import":
            continue  # direct import, not a re-export
        # Walk the re-export chain to its terminal non-import symbol.
        visited = {start, first}
        via = [first]
        current = first
        terminal: str | None = None
        uncertain = False
        for _depth in range(config.reexport_max_depth):
            hops = store.outgoing_edges(current, kinds=("imports",))
            if len(hops) != 1:
                uncertain = len(hops) > 1  # dangling re-export: just skip
                break
            nxt = hops[0].target
            if nxt in visited:
                uncertain = True  # cycle
                break
            nxt_symbol = store.symbol_by_node(nxt)
            if nxt_symbol is not None and nxt_symbol.kind == "import":
                visited.add(nxt)
                via.append(nxt)
                current = nxt
                continue
            terminal = nxt
            break
        else:
            uncertain = True  # depth cap tripped
        if uncertain:
            result.skipped_uncertain += 1
            continue
        if terminal is None or terminal == start:
            continue
        result.emit(
            store,
            start,
            terminal,
            "references",
            _metadata(PASS_REEXPORT_CHAINS, "4/5", via=via),
        )
    return result


def _run_type_hint_edges(store: EventStore, config: SynthesisConfig) -> _Pass:
    result = _Pass(PASS_TYPE_HINT_EDGES)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM unresolved_refs WHERE kind = 'type_hint' AND "
            "(status = 'pending' OR (status = 'failed' AND failure_reason = 'unknown_ref_kind')) "
            "ORDER BY first_seen, ref_id"
        ).fetchall()
    refs = [_ref_row(row) for row in rows]
    result.candidates = len(refs)
    hits: list[tuple[UnresolvedRefRow, str]] = []
    for ref in refs:
        targets = [symbol for symbol in store.symbols_named(ref.target_name) if symbol.kind in _TYPE_KINDS]
        if len(targets) > 1:
            result.skipped_uncertain += 1  # ambiguity never guesses
            continue
        if not targets:
            continue  # unknown type: leave parked for a later ingestion wave
        hits.append((ref, targets[0].node_id))
    for ref, target in hits:
        if not _edge_exists(store, ref.source_node_id, target, "type_of"):
            result.emit(
                store,
                ref.source_node_id,
                target,
                "type_of",
                _metadata(PASS_TYPE_HINT_EDGES, "9/10", ref_id=ref.ref_id),
            )
        else:
            result.emit_existing()

    def _resolve_hits(store: EventStore) -> None:
        # Resolve the refs whose answer is now persisted (edge minted or
        # already present): batch edges first, then one recorded attempt per
        # ref — the resolver's crash-safe write discipline.
        for ref, _target in hits:
            current = store.unresolved_ref(ref.ref_id)
            if current is None or current.status == "resolved":
                continue
            if current.status == "failed":
                store.retry_failed_ref(ref.ref_id)
            store.record_resolution_attempt(ref.ref_id, outcome="resolved")

    result.after_insert = _resolve_hits
    return result


def _run_callback_edges(store: EventStore, config: SynthesisConfig) -> _Pass:
    result = _Pass(PASS_CALLBACK_EDGES)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM unresolved_refs WHERE kind = 'function_ref' AND status = 'resolved' ORDER BY ref_id"
        ).fetchall()
    refs = [_ref_row(row) for row in rows]
    # registrar node -> ordered callback targets (via the resolver-minted edge
    # whose metadata carries this ref's identity).
    channels: dict[str, list[str]] = {}
    for ref in refs:
        registrar = ref.source_node_id
        callback: str | None = None
        for edge in store.outgoing_edges(registrar, kinds=("calls",)):
            if edge.metadata is None:
                continue
            try:
                payload = json.loads(edge.metadata)
            except ValueError, TypeError:
                continue
            if payload.get("ref_id") == ref.ref_id:
                callback = edge.target
                break
        if callback is None:
            continue
        channels.setdefault(registrar, [])
        if callback not in channels[registrar]:
            channels[registrar].append(callback)
    for registrar in sorted(channels):
        result.candidates += 1
        registrar_symbol = store.symbol_by_node(registrar)
        if registrar_symbol is None or registrar_symbol.kind not in _CALLABLE_KINDS:
            continue
        callbacks = channels[registrar]
        if len(callbacks) > config.max_callbacks_per_channel:
            result.skipped_uncertain += 1  # whole channel skipped, never truncated
            continue
        dispatchers: set[str] = set()
        for edge in store.incoming_edges(registrar, kinds=("calls",)):
            caller = edge.source
            if caller == registrar:
                continue
            caller_symbol = store.symbol_by_node(caller)
            if caller_symbol is None or caller_symbol.kind not in _CALLABLE_KINDS:
                continue  # file-level call sites are not dispatchers
            dispatchers.add(caller)
        if len(dispatchers) > config.max_dispatchers_per_channel:
            result.skipped_uncertain += 1
            continue
        for caller in sorted(dispatchers):
            for callback in callbacks:
                if caller == callback:
                    continue
                result.emit(
                    store,
                    caller,
                    callback,
                    "calls",
                    _metadata(
                        PASS_CALLBACK_EDGES,
                        "2/5",
                        channel="registered_callback",
                        registrar=registrar,
                    ),
                )
    return result


#: Failed-call callee names that register routes in the dominant web
#: frameworks (Flask/FastAPI/Express/Django/Bottle/...).
_ROUTE_REGISTRARS: frozenset[str] = frozenset({"route", "get", "post", "put", "delete", "patch", "head", "options"})

#: Framework roots a route registrar must co-occur with (the file's import
#: nodes, by name, lowercased).
_FRAMEWORK_ROOTS: frozenset[str] = frozenset(
    {"flask", "fastapi", "express", "django", "bottle", "aiohttp", "starlette", "sanic", "falcon"}
)

_EVENT_EMITTER_PREFIXES: tuple[str, ...] = ("emit", "dispatch", "raise", "trigger", "fire")
_EVENT_HANDLER_PREFIXES: tuple[str, ...] = ("on", "handle")

_FACTORY_PREFIXES: tuple[str, ...] = ("create", "make", "new", "build")

_CONSTRUCTOR_NAMES: frozenset[str] = frozenset({"__init__", "constructor"})

_FILE_PREFIX: str = "file:"


def _bare_name(name: str) -> str:
    return name.rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def _run_module_member_calls(store: EventStore, config: SynthesisConfig) -> _Pass:
    """A failed call resolves through a module the source file imports.

    ``helper()`` in ``main.py`` with ``import pkg.util`` resolved: when
    exactly one symbol named ``helper`` lives in the imported modules, the
    call binds through that module (confidence 7/10). Ambiguity across (or
    within) imported modules never guesses.
    """
    result = _Pass(PASS_MODULE_MEMBER_CALLS)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM unresolved_refs WHERE kind = 'calls' AND status = 'failed' "
            "AND failure_reason = 'no_resolution' ORDER BY first_seen, ref_id"
        ).fetchall()
    refs = [_ref_row(row) for row in rows]
    hits: list[tuple[UnresolvedRefRow, str, str]] = []
    for ref in refs:
        result.candidates += 1
        source_symbol = store.symbol_by_node(ref.source_node_id)
        if source_symbol is None:
            continue
        imported_paths: list[str] = []
        for edge in store.outgoing_edges(ref.source_node_id, kinds=("imports",)):
            if edge.target.startswith(_FILE_PREFIX):
                imported_paths.append(edge.target[len(_FILE_PREFIX) :])
        if not imported_paths:
            continue
        matches: dict[str, str] = {}  # node_id -> module path
        for module_path in sorted(set(imported_paths)):
            for symbol in store.symbols_in_path(module_path):
                if symbol.kind == "import":
                    continue
                if symbol.name == ref.name_tail or symbol.name.endswith(f".{ref.name_tail}"):
                    matches[symbol.node_id] = module_path
        if len(matches) > 1:
            result.skipped_uncertain += 1
            continue
        if not matches:
            continue
        node_id, module_path = next(iter(matches.items()))
        hits.append((ref, node_id, module_path))
    for ref, target, module_path in hits:
        if not _edge_exists(store, ref.source_node_id, target, "calls"):
            result.emit(
                store,
                ref.source_node_id,
                target,
                "calls",
                _metadata(PASS_MODULE_MEMBER_CALLS, "7/10", ref_id=ref.ref_id, module=module_path),
            )
        else:
            result.emit_existing()

    def _resolve_hits(store: EventStore) -> None:
        for ref, _target, _module in hits:
            current = store.unresolved_ref(ref.ref_id)
            if current is None or current.status == "resolved":
                continue
            if current.status == "failed":
                store.retry_failed_ref(ref.ref_id)
            store.record_resolution_attempt(ref.ref_id, outcome="resolved")

    result.after_insert = _resolve_hits
    return result


def _run_reexport_propagation(store: EventStore, config: SynthesisConfig) -> _Pass:
    """``import pkg`` where ``pkg/__init__`` re-exports symbols: the importer
    references every re-exported terminal (deep re-export chains, 3/5).

    A module whose re-export fanout exceeds ``max_reexport_fanout`` is
    skipped whole — never truncated.
    """
    result = _Pass(PASS_REEXPORT_PROPAGATION)
    with store.read() as conn:
        import_nodes = conn.execute("SELECT node_id FROM symbols WHERE kind = 'import' ORDER BY node_id").fetchall()
    for row in import_nodes:
        start = str(row["node_id"])
        outgoing = store.outgoing_edges(start, kinds=("imports",))
        if len(outgoing) != 1:
            continue
        first = outgoing[0].target
        if not first.startswith(_FILE_PREFIX):
            continue  # only module imports propagate re-exports
        result.candidates += 1
        module_path = first[len(_FILE_PREFIX) :]
        reexporters = [symbol for symbol in store.symbols_in_path(module_path) if symbol.kind == "import"]
        terminals: dict[str, list[str]] = {}  # terminal node_id -> via chain
        for reexporter in reexporters:
            visited = {start, reexporter.node_id}
            current = reexporter.node_id
            via = [reexporter.node_id]
            for _depth in range(config.reexport_max_depth):
                hops = store.outgoing_edges(current, kinds=("imports",))
                if len(hops) != 1:
                    break
                nxt = hops[0].target
                if nxt in visited or nxt == start:
                    break
                nxt_symbol = store.symbol_by_node(nxt)
                if nxt_symbol is not None and nxt_symbol.kind == "import":
                    visited.add(nxt)
                    via.append(nxt)
                    current = nxt
                    continue
                if not nxt.startswith(_FILE_PREFIX):
                    terminals.setdefault(nxt, via)
                break
        if len(terminals) > config.max_reexport_fanout:
            result.skipped_uncertain += 1
            continue
        for terminal in sorted(terminals):
            result.emit(
                store,
                start,
                terminal,
                "references",
                _metadata(PASS_REEXPORT_PROPAGATION, "3/5", via=terminals[terminal], module=module_path),
            )
    return result


def _run_dependency_injection(store: EventStore, config: SynthesisConfig) -> _Pass:
    """A class depends on the types its constructor is typed with (6/10).

    Consumes ``type_of`` edges (parse-resolved or ``type_hint_edges``-minted)
    on constructor methods (``__init__`` / ``constructor``) and lifts them to
    the containing class.
    """
    result = _Pass(PASS_DEPENDENCY_INJECTION)
    with store.read() as conn:
        classes = conn.execute("SELECT node_id FROM symbols WHERE kind = 'class' ORDER BY node_id").fetchall()
    for row in classes:
        class_id = str(row["node_id"])
        constructors = [
            edge.target
            for edge in store.outgoing_edges(class_id, kinds=("contains",))
            if (symbol := store.symbol_by_node(edge.target)) is not None
            and symbol.kind == "method"
            and _bare_name(symbol.name) in _CONSTRUCTOR_NAMES
        ]
        if not constructors:
            continue
        deps: set[str] = set()
        for ctor in constructors:
            for edge in store.outgoing_edges(ctor, kinds=("type_of",)):
                target_symbol = store.symbol_by_node(edge.target)
                if target_symbol is not None and target_symbol.kind in _TYPE_KINDS and edge.target != class_id:
                    deps.add(edge.target)
        if not deps:
            continue
        result.candidates += 1
        if len(deps) > config.max_injected_deps:
            result.skipped_uncertain += 1  # whole class skipped, never truncated
            continue
        for dep in sorted(deps):
            result.emit(
                store,
                class_id,
                dep,
                "references",
                _metadata(PASS_DEPENDENCY_INJECTION, "6/10", rule="constructor_injection"),
            )
    return result


def _run_interface_implementation(store: EventStore, config: SynthesisConfig) -> _Pass:
    """A class implementing every method of an interface references it (3/5).

    Full bare-name coverage only — partial matches never mint. Interfaces
    larger than ``max_interface_methods`` are skipped whole.
    """
    result = _Pass(PASS_INTERFACE_IMPLEMENTATION)
    with store.read() as conn:
        interfaces = conn.execute("SELECT node_id FROM symbols WHERE kind = 'interface' ORDER BY node_id").fetchall()
        classes = conn.execute("SELECT node_id FROM symbols WHERE kind = 'class' ORDER BY node_id").fetchall()
    class_methods: dict[str, frozenset[str]] = {}
    for row in classes:
        class_id = str(row["node_id"])
        names: set[str] = set()
        for edge in store.outgoing_edges(class_id, kinds=("contains",)):
            symbol = store.symbol_by_node(edge.target)
            if symbol is not None and symbol.kind == "method":
                names.add(_bare_name(symbol.name))
        class_methods[class_id] = frozenset(names)
    for row in interfaces:
        interface_id = str(row["node_id"])
        required: set[str] = set()
        for edge in store.outgoing_edges(interface_id, kinds=("contains",)):
            symbol = store.symbol_by_node(edge.target)
            if symbol is not None and symbol.kind == "method":
                required.add(_bare_name(symbol.name))
        if not required:
            continue
        result.candidates += 1
        if len(required) > config.max_interface_methods:
            result.skipped_uncertain += 1
            continue
        for class_id in sorted(class_methods):
            if required <= class_methods[class_id]:
                result.emit(
                    store,
                    class_id,
                    interface_id,
                    "references",
                    _metadata(
                        PASS_INTERFACE_IMPLEMENTATION,
                        "3/5",
                        shared_methods=len(required),
                    ),
                )
    return result


def _run_framework_routes(store: EventStore, config: SynthesisConfig) -> _Pass:
    """Failed calls to route registrars in framework-importing files (3/5).

    ``@app.route`` / ``app.get(...)`` parse as calls to ``route``/``get``
    that fail as ``no_resolution`` (the registrar lives in the framework).
    When the file imports a known framework, the handler function references
    the framework's import node — the route registration is in the graph.
    The ref itself is left untouched (the edge kind is ``references``, not
    the resolver's ``calls`` answer).
    """
    result = _Pass(PASS_FRAMEWORK_ROUTES)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM unresolved_refs WHERE kind = 'calls' AND status = 'failed' "
            "AND failure_reason = 'no_resolution' ORDER BY first_seen, ref_id"
        ).fetchall()
    refs = [_ref_row(row) for row in rows]
    for ref in refs:
        if ref.target_name not in _ROUTE_REGISTRARS:
            continue
        source_symbol = store.symbol_by_node(ref.source_node_id)
        if source_symbol is None or source_symbol.kind not in _CALLABLE_KINDS:
            continue
        result.candidates += 1
        frameworks = [
            symbol
            for symbol in store.symbols_in_path(source_symbol.path)
            if symbol.kind == "import" and symbol.name.lower() in _FRAMEWORK_ROOTS
        ]
        if not frameworks:
            continue
        for framework in frameworks:
            result.emit(
                store,
                ref.source_node_id,
                framework.node_id,
                "references",
                _metadata(
                    PASS_FRAMEWORK_ROUTES,
                    "3/5",
                    decorator=ref.target_name,
                    framework=framework.name.lower(),
                    ref_id=ref.ref_id,
                ),
            )
    return result


def _split_event(name: str) -> tuple[str, str] | None:
    """(role, event) for a conventionally named symbol, else None."""
    for role, prefixes in (("emitter", _EVENT_EMITTER_PREFIXES), ("handler", _EVENT_HANDLER_PREFIXES)):
        for prefix in prefixes:
            if name.startswith(f"{prefix}_") and len(name) > len(prefix) + 1:
                return role, name[len(prefix) + 1 :].lower()
            if name.startswith(prefix) and len(name) > len(prefix) and name[len(prefix)].isupper():
                return role, name[len(prefix) :].lower()
    return None


def _run_event_handler_wiring(store: EventStore, config: SynthesisConfig) -> _Pass:
    """Pair ``emit_x``/``dispatch_x`` emitters with ``on_x``/``handle_x``
    handlers over the shared event suffix (2/5, naming-convention channel).

    Caps are per event and skip the whole event on trip — codegraph's
    ``EVENT_FANOUT_CAP`` discipline.
    """
    result = _Pass(PASS_EVENT_HANDLER_WIRING)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT node_id, name, kind FROM symbols WHERE kind IN ('function', 'method') ORDER BY node_id"
        ).fetchall()
    emitters: dict[str, list[str]] = {}
    handlers: dict[str, list[str]] = {}
    for row in rows:
        split = _split_event(str(row["name"]))
        if split is None:
            continue
        role, event = split
        (emitters if role == "emitter" else handlers).setdefault(event, []).append(str(row["node_id"]))
    for event in sorted(emitters):
        if event not in handlers:
            continue
        result.candidates += 1
        event_emitters = emitters[event]
        event_handlers = handlers[event]
        if len(event_handlers) > config.max_handlers_per_event or len(event_emitters) > config.max_emitters_per_event:
            result.skipped_uncertain += 1  # whole event skipped, never truncated
            continue
        for emitter in sorted(event_emitters):
            for handler in sorted(event_handlers):
                if emitter == handler:
                    continue
                result.emit(
                    store,
                    emitter,
                    handler,
                    "references",
                    _metadata(PASS_EVENT_HANDLER_WIRING, "2/5", event=event, channel="naming_convention"),
                )
    return result


def _subject_candidates(basename: str) -> tuple[str, str] | None:
    """(subject basename, rule) for a test file name, else None."""
    for rule, pattern in (
        ("python_test_prefix", r"^test_(.+)\.py$"),
        ("go_test_suffix", r"^(.+)_test\.go$"),
        ("js_test_infix", r"^(.+)\.(?:test|spec)\.(ts|tsx|js|jsx)$"),
    ):
        match = re.match(pattern, basename)
        if match is not None:
            stem = match.group(1)
            if not stem:
                return None
            extension = pattern.endswith("py$") and "py" or (pattern.endswith("go$") and "go" or match.group(2))
            return f"{stem}.{extension}", rule
    return None


def _run_test_subject_edges(store: EventStore, config: SynthesisConfig) -> _Pass:
    """``test_foo.py`` / ``foo_test.go`` / ``foo.test.ts`` reference their
    subject file when exactly one exists (7/10); ambiguity never guesses."""
    result = _Pass(PASS_TEST_SUBJECT_EDGES)
    with store.read() as conn:
        files = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
    paths = [str(row["path"]) for row in files]
    by_basename: dict[str, list[str]] = {}
    for path in paths:
        by_basename.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    for path in paths:
        basename = path.rsplit("/", 1)[-1]
        candidate = _subject_candidates(basename)
        if candidate is None:
            continue
        subject_basename, rule = candidate
        subjects = [
            candidate_path for candidate_path in by_basename.get(subject_basename, []) if candidate_path != path
        ]
        if not subjects:
            continue
        result.candidates += 1
        if len(subjects) > 1:
            result.skipped_uncertain += 1
            continue
        result.emit(
            store,
            f"{_FILE_PREFIX}{path}",
            f"{_FILE_PREFIX}{subjects[0]}",
            "references",
            _metadata(PASS_TEST_SUBJECT_EDGES, "7/10", rule=rule),
        )
    return result


def _factory_type_name(name: str) -> str | None:
    """The produced type name of a factory function name, else None."""
    bare = _bare_name(name)
    for prefix in _FACTORY_PREFIXES:
        if bare.startswith(f"{prefix}_") and len(bare) > len(prefix) + 1:
            return bare[len(prefix) + 1 :]
        if bare.startswith(prefix) and len(bare) > len(prefix) and bare[len(prefix)].isupper():
            return bare[len(prefix) :]
    return None


def _run_factory_functions(store: EventStore, config: SynthesisConfig) -> _Pass:
    """``create_widget``/``makeWidget`` reference the unique type they build
    (3/5). The type must be globally unique — ambiguity never guesses."""
    result = _Pass(PASS_FACTORY_FUNCTIONS)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT node_id, name FROM symbols WHERE kind IN ('function', 'method') ORDER BY node_id"
        ).fetchall()
    for row in rows:
        type_name = _factory_type_name(str(row["name"]))
        if type_name is None:
            continue
        result.candidates += 1
        names = {type_name, type_name[:1].upper() + type_name[1:]}
        candidates: dict[str, str] = {}  # node_id -> matched symbol name
        for candidate_name in sorted(names):
            for symbol in store.symbols_named(candidate_name):
                if symbol.kind in _TYPE_KINDS:
                    candidates[symbol.node_id] = candidate_name
        if len(candidates) > 1:
            result.skipped_uncertain += 1
            continue
        if not candidates:
            continue
        (target,) = candidates
        if target == str(row["node_id"]):
            continue
        result.emit(
            store,
            str(row["node_id"]),
            target,
            "references",
            _metadata(PASS_FACTORY_FUNCTIONS, "3/5", type=candidates[target], factory=True),
        )
    return result


_PASS_RUNNERS = {
    PASS_METHOD_CLASS_CONTAINMENT: _run_method_class_containment,
    PASS_REEXPORT_CHAINS: _run_reexport_chains,
    PASS_TYPE_HINT_EDGES: _run_type_hint_edges,
    PASS_MODULE_MEMBER_CALLS: _run_module_member_calls,
    PASS_REEXPORT_PROPAGATION: _run_reexport_propagation,
    PASS_DEPENDENCY_INJECTION: _run_dependency_injection,
    PASS_INTERFACE_IMPLEMENTATION: _run_interface_implementation,
    PASS_FRAMEWORK_ROUTES: _run_framework_routes,
    PASS_EVENT_HANDLER_WIRING: _run_event_handler_wiring,
    PASS_TEST_SUBJECT_EDGES: _run_test_subject_edges,
    PASS_FACTORY_FUNCTIONS: _run_factory_functions,
    PASS_CALLBACK_EDGES: _run_callback_edges,
}


class Synthesizer:
    """Runs the enabled synthesis passes, in the fixed order, once.

    W13 operator kill switches (``KNOW_YOUR_CONTEXT_DISABLE_SYNTH_*`` /
    ``KNOW_YOUR_CONTEXT_DISABLE_SYNTHESIS``) are consulted at every ``run``:
    a disabled pass is skipped and reported with zero counts (the report shape
    never changes), and the skip is announced via :attr:`disabled_by_env` —
    never silent. The operator env overrides even an explicit
    ``SynthesisConfig.enabled_passes``.
    """

    def __init__(self, config: SynthesisConfig | None = None) -> None:
        self._config = config if config is not None else SynthesisConfig()
        self._disabled_by_env: frozenset[str] = frozenset()

    @property
    def config(self) -> SynthesisConfig:
        return self._config

    @property
    def disabled_by_env(self) -> frozenset[str]:
        """Passes the operator disabled via env at the most recent ``run``."""
        return self._disabled_by_env

    def run(self, store: EventStore) -> SynthesisReport:
        self._disabled_by_env = load_switches().synthesis_passes_disabled()
        reports: list[PassReport] = []
        for name in SYNTH_PASS_ORDER:
            if name in self._disabled_by_env or not self._config.is_enabled(name):
                reports.append(PassReport(name, 0, 0, 0, 0, 0))
                continue
            result = _PASS_RUNNERS[name](store, self._config)
            result.insert(store)  # one transaction per pass + post-insert hook
            reports.append(result.report())
        return SynthesisReport(
            passes=tuple(reports),
            emitted=sum(entry.emitted for entry in reports),
            inserted=sum(entry.inserted for entry in reports),
        )


def run_synthesis(store: EventStore, *, config: SynthesisConfig | None = None) -> SynthesisReport:
    """One-shot entry point: all enabled passes, fixed order."""
    return Synthesizer(config).run(store)


def resolve_and_synthesize(
    store: EventStore,
    *,
    limit: int | None = None,
    resolver_config: ResolverConfig | None = None,
    name_match_config: NameMatchConfig | None = None,
    synthesis_config: SynthesisConfig | None = None,
) -> PipelineReport:
    """The wired pipeline: primary cascade → name-match tier → synthesis.

    Synthesis always runs *after* primary resolution (and the name-match
    tier), as a separate pass writing separately-stamped edges — never
    interleaved with, and never disguised as, parse output.
    """
    resolver_report = ReferenceResolver(resolver_config).resolve_pending(store, limit=limit)
    # W13 kill switch: KNOW_YOUR_CONTEXT_DISABLE_NAME_MATCH skips the tier
    # (report is a zeroed neutral element — the shape never changes).
    if load_switches().name_match_disabled():
        name_match_report = NameMatchReport(processed=0, resolved=0, unmatched=0, edges_inserted=0, outcomes=())
    else:
        name_match_report = NameMatchResolver(name_match_config).resolve_unmatched(store)
    synthesis_report = Synthesizer(synthesis_config).run(store)
    return PipelineReport(
        resolver=resolver_report,
        name_match=name_match_report,
        synthesis=synthesis_report,
    )
