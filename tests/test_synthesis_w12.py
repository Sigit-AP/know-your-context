"""Tests for the eight wave-W12 synthesis passes (SUB-121).

Each pass closes a graph gap the parse + primary cascade structurally cannot
see, following codegraph's synth-pass discipline: ``synthesis:<pass>``
provenance, confidence in metadata, per-pass kill switch via
``SynthesisConfig.enabled_passes``, idempotence, and ambiguity never guesses.

* ``module_member_calls`` — a failed ``calls`` ref whose tail names exactly
  one symbol inside a module the source file imports resolves through that
  module (confidence 7/10).
* ``reexport_propagation`` — ``import pkg`` where ``pkg/__init__`` re-exports
  symbols links the importer to the re-exported terminals (confidence 3/5).
* ``dependency_injection`` — a class whose constructor (``__init__`` /
  ``constructor``) carries ``type_of`` edges depends on those types
  (confidence 6/10).
* ``interface_implementation`` — a class implementing every method of an
  interface references it (confidence 3/5).
* ``framework_route_registration`` — a function with a failed call to a route
  registrar (``route``/``get``/…) in a file importing a known web framework
  references that framework import node (confidence 3/5).
* ``event_handler_wiring`` — ``emit_x``/``dispatch_x``/``raise_x`` emitters
  and ``on_x``/``handle_x`` handlers sharing the event suffix are linked
  (confidence 2/5, capped per event).
* ``test_subject_edges`` — ``test_foo.py`` / ``foo_test.go`` /
  ``foo.test.ts`` reference their subject file when it exists (7/10).
* ``factory_function_edges`` — ``create_foo``/``make_foo``/``new_foo`` /
  ``build_foo`` reference the unique type ``foo``/``Foo`` (3/5).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from know_your_context.resolve.synthesis import (
    PASS_DEPENDENCY_INJECTION,
    PASS_EVENT_HANDLER_WIRING,
    PASS_FACTORY_FUNCTIONS,
    PASS_FRAMEWORK_ROUTES,
    PASS_INTERFACE_IMPLEMENTATION,
    PASS_MODULE_MEMBER_CALLS,
    PASS_REEXPORT_PROPAGATION,
    PASS_TEST_SUBJECT_EDGES,
    SYNTH_PASS_ORDER,
    SynthesisConfig,
    SynthesisError,
    Synthesizer,
    pass_provenance,
    run_synthesis,
)
from know_your_context.store import EventStore

SEEN = "2026-11-02T00:00:00+00:00"
PARSE = "ingest:0123456789abcdef"

NEW_PASSES = (
    PASS_MODULE_MEMBER_CALLS,
    PASS_REEXPORT_PROPAGATION,
    PASS_DEPENDENCY_INJECTION,
    PASS_INTERFACE_IMPLEMENTATION,
    PASS_FRAMEWORK_ROUTES,
    PASS_EVENT_HANDLER_WIRING,
    PASS_TEST_SUBJECT_EDGES,
    PASS_FACTORY_FUNCTIONS,
)


# ------------------------------------------------------------------ helpers


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def add_file(store: EventStore, path: str, language: str = "python", *, generated: bool = False) -> None:
    store.upsert_file(
        path=path,
        sha256=hashlib.sha256(path.encode()).hexdigest(),
        size=10,
        mtime=100,
        language=language,
        generated_hint=generated,
        extraction_version=1,
    )


def add_symbol(store: EventStore, node_id: str, name: str, path: str, *, kind: str = "function", line: int = 1) -> None:
    store.upsert_symbol(node_id=node_id, name=name, kind=kind, path=path, line=line)


def add_edge(
    store: EventStore, source: str, target: str, kind: str, *, provenance: str = PARSE, metadata: str | None = None
) -> None:
    store.add_graph_edge(source, target, kind, metadata=metadata, provenance=provenance)


def add_failed_ref(
    store: EventStore, source: str, target: str, kind: str = "calls", *, reason: str = "no_resolution"
) -> int:
    ref_id = store.insert_unresolved_ref(
        source_node_id=source, target_name=target, kind=kind, name_tail=None, first_seen=SEEN
    )
    store.record_resolution_attempt(ref_id, outcome="failed", reason=reason)
    return ref_id


def edge_rows(store: EventStore) -> list[tuple[str, str, str, str | None, str]]:
    with store.read() as conn:
        return [
            (str(r[0]), str(r[1]), str(r[2]), None if r[3] is None else str(r[3]), str(r[4]))
            for r in conn.execute("SELECT source, target, kind, metadata, provenance FROM graph_edges ORDER BY edge_id")
        ]


def synth_edges(store: EventStore, pass_name: str) -> list[tuple[str, str, str, str | None, str]]:
    provenance = f"synthesis:{pass_name}"
    return [row for row in edge_rows(store) if row[4] == provenance]


def only(store: EventStore, pass_name: str):
    return run_synthesis(store, config=SynthesisConfig(enabled_passes=frozenset({pass_name})))


def pass_entry(report, pass_name: str):
    return [p for p in report.passes if p.name == pass_name][0]


# --------------------------------------------------------------- pass roster


def test_twelve_passes_total():
    assert len(SYNTH_PASS_ORDER) == 12


def test_new_passes_in_fixed_order():
    order = list(SYNTH_PASS_ORDER)
    assert order.index("method_class_containment") < order.index("reexport_chains")
    assert order.index("type_hint_edges") < order.index("dependency_injection")
    assert order.index("callback_edges") == len(order) - 1  # still last


@pytest.mark.parametrize("pass_name", NEW_PASSES)
def test_new_pass_provenance(pass_name: str):
    assert pass_provenance(pass_name) == f"synthesis:{pass_name}"


@pytest.mark.parametrize("pass_name", NEW_PASSES)
def test_new_pass_kill_switch(store: EventStore, pass_name: str):
    disabled = frozenset(set(SYNTH_PASS_ORDER) - {pass_name})
    report = Synthesizer(SynthesisConfig(enabled_passes=disabled)).run(store)
    entry = [p for p in report.passes if p.name == pass_name][0]
    assert entry.emitted == 0 and entry.inserted == 0 and entry.candidates == 0


def test_config_rejects_bad_new_caps():
    with pytest.raises(SynthesisError):
        SynthesisConfig(max_reexport_fanout=0)
    with pytest.raises(SynthesisError):
        SynthesisConfig(max_injected_deps=0)
    with pytest.raises(SynthesisError):
        SynthesisConfig(max_handlers_per_event=0)
    with pytest.raises(SynthesisError):
        SynthesisConfig(max_emitters_per_event=0)
    with pytest.raises(SynthesisError):
        SynthesisConfig(max_interface_methods=0)


# ------------------------------------------------------- module_member_calls


def _module_member_store(store: EventStore) -> None:
    add_file(store, "main.py")
    add_file(store, "pkg/util.py")
    add_symbol(store, "fn:main", "main", "main.py")
    add_symbol(store, "fn:helper", "helper", "pkg/util.py")
    # main.py imports pkg.util (resolved at primary tier to the file node)
    add_edge(store, "fn:main", "file:pkg/util.py", "imports", provenance="resolver:two-phase:v1")
    # main calls helper — globally ambiguous? no: unresolved here
    add_failed_ref(store, "fn:main", "helper")


def test_module_member_calls_resolves_via_imported_module(store):
    _module_member_store(store)
    report = only(store, PASS_MODULE_MEMBER_CALLS)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_MODULE_MEMBER_CALLS)
    assert row[0] == "fn:main" and row[1] == "fn:helper" and row[2] == "calls"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "7/10"
    assert payload["module"] == "pkg/util.py"


def test_module_member_calls_marks_ref_resolved(store):
    _module_member_store(store)
    only(store, PASS_MODULE_MEMBER_CALLS)
    with store.read() as conn:
        rows = conn.execute("SELECT status FROM unresolved_refs").fetchall()
    assert [r[0] for r in rows] == ["resolved"]


def test_module_member_calls_skips_ambiguous_member(store):
    _module_member_store(store)
    add_symbol(store, "fn:helper2", "helper", "pkg/util.py", line=9)
    report = only(store, PASS_MODULE_MEMBER_CALLS)
    assert report.inserted == 0
    assert pass_entry(report, PASS_MODULE_MEMBER_CALLS).skipped_uncertain == 1


def test_module_member_calls_requires_import_edge(store):
    add_file(store, "main.py")
    add_file(store, "pkg/util.py")
    add_symbol(store, "fn:main", "main", "main.py")
    add_symbol(store, "fn:helper", "helper", "pkg/util.py")
    add_failed_ref(store, "fn:main", "helper")
    report = only(store, PASS_MODULE_MEMBER_CALLS)
    assert report.inserted == 0


def test_module_member_calls_idempotent(store):
    _module_member_store(store)
    only(store, PASS_MODULE_MEMBER_CALLS)
    second = only(store, PASS_MODULE_MEMBER_CALLS)
    assert second.inserted == 0
    assert len(synth_edges(store, PASS_MODULE_MEMBER_CALLS)) == 1


def test_module_member_calls_never_overwrites_parse_edge(store):
    _module_member_store(store)
    add_edge(store, "fn:main", "fn:helper", "calls")  # parse beat us to it
    report = only(store, PASS_MODULE_MEMBER_CALLS)
    assert report.inserted == 0
    assert len([r for r in edge_rows(store) if r[0] == "fn:main" and r[2] == "calls"]) == 1


# ------------------------------------------------------ reexport_propagation


def _reexport_propagation_store(store: EventStore) -> None:
    add_file(store, "app/main.py")
    add_file(store, "pkg/__init__.py")
    add_file(store, "pkg/impl.py")
    add_symbol(store, "fn:main", "main", "app/main.py")
    add_symbol(store, "imp:pkg", "pkg", "app/main.py", kind="import")
    add_symbol(store, "imp:impl", "impl", "pkg/__init__.py", kind="import")
    add_symbol(store, "fn:real", "real", "pkg/impl.py")
    # import pkg resolved to the package __init__ file node
    add_edge(store, "imp:pkg", "file:pkg/__init__.py", "imports", provenance="resolver:two-phase:v1")
    # pkg/__init__ re-exports impl.real (its import node resolved to the symbol)
    add_edge(store, "imp:impl", "fn:real", "imports", provenance="resolver:two-phase:v1")


def test_reexport_propagation_links_importer_to_terminal(store):
    _reexport_propagation_store(store)
    report = only(store, PASS_REEXPORT_PROPAGATION)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_REEXPORT_PROPAGATION)
    assert row[0] == "imp:pkg" and row[1] == "fn:real" and row[2] == "references"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "3/5"
    assert payload["via"] == ["imp:impl"]


def test_reexport_propagation_idempotent(store):
    _reexport_propagation_store(store)
    only(store, PASS_REEXPORT_PROPAGATION)
    second = only(store, PASS_REEXPORT_PROPAGATION)
    assert second.inserted == 0


def test_reexport_propagation_respects_fanout_cap(store):
    add_file(store, "app/main.py")
    add_file(store, "pkg/__init__.py")
    add_symbol(store, "imp:pkg", "pkg", "app/main.py", kind="import")
    add_edge(store, "imp:pkg", "file:pkg/__init__.py", "imports", provenance="resolver:two-phase:v1")
    for index in range(3):
        add_symbol(store, f"imp:i{index}", f"i{index}", "pkg/__init__.py", kind="import", line=index + 1)
        add_symbol(store, f"fn:t{index}", f"t{index}", "pkg/impl.py", line=index + 1)
        add_edge(store, f"imp:i{index}", f"fn:t{index}", "imports", provenance="resolver:two-phase:v1")
    add_file(store, "pkg/impl.py")
    report = run_synthesis(
        store,
        config=SynthesisConfig(enabled_passes=frozenset({PASS_REEXPORT_PROPAGATION}), max_reexport_fanout=2),
    )
    assert report.inserted == 0  # whole module skipped, never truncated
    assert pass_entry(report, PASS_REEXPORT_PROPAGATION).skipped_uncertain == 1


# ------------------------------------------------------ dependency_injection


def _di_store(store: EventStore) -> None:
    add_file(store, "app.py")
    add_symbol(store, "cls:Service", "Service", "app.py", kind="class")
    add_symbol(store, "fn:init", "Service.__init__", "app.py", kind="method", line=2)
    add_symbol(store, "cls:Repo", "Repo", "app.py", kind="class", line=10)
    add_edge(store, "cls:Service", "fn:init", "contains")
    add_edge(store, "fn:init", "cls:Repo", "type_of", provenance="synthesis:type_hint_edges")


def test_dependency_injection_links_class_to_injected_type(store):
    _di_store(store)
    report = only(store, PASS_DEPENDENCY_INJECTION)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_DEPENDENCY_INJECTION)
    assert row[0] == "cls:Service" and row[1] == "cls:Repo" and row[2] == "references"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "6/10"
    assert payload["rule"] == "constructor_injection"


def test_dependency_injection_js_constructor_name(store):
    add_file(store, "app.ts", "typescript")
    add_symbol(store, "cls:Service", "Service", "app.ts", kind="class")
    add_symbol(store, "fn:ctor", "Service.constructor", "app.ts", kind="method", line=2)
    add_symbol(store, "cls:Repo", "Repo", "app.ts", kind="class", line=10)
    add_edge(store, "cls:Service", "fn:ctor", "contains")
    add_edge(store, "fn:ctor", "cls:Repo", "type_of")
    report = only(store, PASS_DEPENDENCY_INJECTION)
    assert report.inserted == 1


def test_dependency_injection_ignores_non_constructor(store):
    add_file(store, "app.py")
    add_symbol(store, "cls:Service", "Service", "app.py", kind="class")
    add_symbol(store, "fn:other", "Service.other", "app.py", kind="method", line=2)
    add_symbol(store, "cls:Repo", "Repo", "app.py", kind="class", line=10)
    add_edge(store, "cls:Service", "fn:other", "contains")
    add_edge(store, "fn:other", "cls:Repo", "type_of")
    report = only(store, PASS_DEPENDENCY_INJECTION)
    assert report.inserted == 0


def test_dependency_injection_cap_skips_whole_class(store):
    add_file(store, "app.py")
    add_symbol(store, "cls:Service", "Service", "app.py", kind="class")
    add_symbol(store, "fn:init", "Service.__init__", "app.py", kind="method", line=2)
    add_edge(store, "cls:Service", "fn:init", "contains")
    for index in range(3):
        add_symbol(store, f"cls:T{index}", f"T{index}", "app.py", kind="class", line=10 + index)
        add_edge(store, "fn:init", f"cls:T{index}", "type_of")
    report = run_synthesis(
        store,
        config=SynthesisConfig(enabled_passes=frozenset({PASS_DEPENDENCY_INJECTION}), max_injected_deps=2),
    )
    assert report.inserted == 0
    assert pass_entry(report, PASS_DEPENDENCY_INJECTION).skipped_uncertain == 1


def test_dependency_injection_idempotent(store):
    _di_store(store)
    only(store, PASS_DEPENDENCY_INJECTION)
    assert only(store, PASS_DEPENDENCY_INJECTION).inserted == 0


# --------------------------------------------------- interface_implementation


def _ii_store(store: EventStore) -> None:
    add_file(store, "shapes.py")
    add_symbol(store, "if:Shape", "Shape", "shapes.py", kind="interface")
    add_symbol(store, "m:area", "Shape.area", "shapes.py", kind="method", line=2)
    add_symbol(store, "m:name", "Shape.name", "shapes.py", kind="method", line=3)
    add_symbol(store, "cls:Circle", "Circle", "shapes.py", kind="class", line=10)
    add_symbol(store, "m:carea", "Circle.area", "shapes.py", kind="method", line=11)
    add_symbol(store, "m:cname", "Circle.name", "shapes.py", kind="method", line=12)
    add_edge(store, "if:Shape", "m:area", "contains")
    add_edge(store, "if:Shape", "m:name", "contains")
    add_edge(store, "cls:Circle", "m:area", "contains")
    add_edge(store, "cls:Circle", "m:cname", "contains")


def test_interface_implementation_full_coverage(store):
    _ii_store(store)
    report = only(store, PASS_INTERFACE_IMPLEMENTATION)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_INTERFACE_IMPLEMENTATION)
    assert row[0] == "cls:Circle" and row[1] == "if:Shape" and row[2] == "references"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "3/5"
    assert payload["shared_methods"] == 2


def test_interface_implementation_partial_coverage_skips(store):
    _ii_store(store)
    add_symbol(store, "cls:Square", "Square", "shapes.py", kind="class", line=20)
    add_symbol(store, "m:sarea", "Square.area", "shapes.py", kind="method", line=21)
    add_edge(store, "cls:Square", "m:sarea", "contains")
    report = only(store, PASS_INTERFACE_IMPLEMENTATION)
    targets = {row[0] for row in synth_edges(store, PASS_INTERFACE_IMPLEMENTATION)}
    assert "cls:Square" not in targets
    assert report.inserted == 1  # Circle still matches


def test_interface_implementation_interface_never_implements(store):
    _ii_store(store)
    only(store, PASS_INTERFACE_IMPLEMENTATION)
    sources = {row[0] for row in synth_edges(store, PASS_INTERFACE_IMPLEMENTATION)}
    assert "if:Shape" not in sources


def test_interface_implementation_method_cap(store):
    add_file(store, "big.py")
    add_symbol(store, "if:Big", "Big", "big.py", kind="interface")
    for index in range(3):
        add_symbol(store, f"m:b{index}", f"Big.m{index}", "big.py", kind="method", line=index + 1)
        add_edge(store, "if:Big", f"m:b{index}", "contains")
    add_symbol(store, "cls:C", "C", "big.py", kind="class", line=50)
    for index in range(3):
        add_symbol(store, f"m:c{index}", f"C.m{index}", "big.py", kind="method", line=51 + index)
        add_edge(store, "cls:C", f"m:c{index}", "contains")
    report = run_synthesis(
        store,
        config=SynthesisConfig(enabled_passes=frozenset({PASS_INTERFACE_IMPLEMENTATION}), max_interface_methods=2),
    )
    assert report.inserted == 0
    assert pass_entry(report, PASS_INTERFACE_IMPLEMENTATION).skipped_uncertain == 1


def test_interface_implementation_idempotent(store):
    _ii_store(store)
    only(store, PASS_INTERFACE_IMPLEMENTATION)
    assert only(store, PASS_INTERFACE_IMPLEMENTATION).inserted == 0


# --------------------------------------------------- framework_route_registration


def _route_store(store: EventStore) -> None:
    add_file(store, "web/app.py")
    add_symbol(store, "imp:flask", "flask", "web/app.py", kind="import")
    add_symbol(store, "fn:index", "index", "web/app.py", line=5)
    add_failed_ref(store, "imp:flask", "flask", kind="imports", reason="external_module")
    add_failed_ref(store, "fn:index", "route")


def test_framework_routes_link_handler_to_framework_import(store):
    _route_store(store)
    report = only(store, PASS_FRAMEWORK_ROUTES)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_FRAMEWORK_ROUTES)
    assert row[0] == "fn:index" and row[1] == "imp:flask" and row[2] == "references"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "3/5"
    assert payload["decorator"] == "route"
    assert payload["framework"] == "flask"


@pytest.mark.parametrize("registrar", ["route", "get", "post", "put", "delete", "patch", "head", "options"])
def test_framework_routes_registrar_names(store, registrar: str):
    add_file(store, "web/app.py")
    add_symbol(store, "imp:flask", "flask", "web/app.py", kind="import")
    add_symbol(store, "fn:h", "handler", "web/app.py", line=5)
    add_failed_ref(store, "fn:h", registrar)
    report = only(store, PASS_FRAMEWORK_ROUTES)
    assert report.inserted == 1, registrar


def test_framework_routes_require_framework_import(store):
    add_file(store, "web/app.py")
    add_symbol(store, "fn:index", "index", "web/app.py", line=5)
    add_failed_ref(store, "fn:index", "route")
    report = only(store, PASS_FRAMEWORK_ROUTES)
    assert report.inserted == 0


def test_framework_routes_express_variant(store):
    add_file(store, "web/app.ts", "typescript")
    add_symbol(store, "imp:express", "express", "web/app.ts", kind="import")
    add_symbol(store, "fn:h", "handler", "web/app.ts", line=5)
    add_failed_ref(store, "fn:h", "get")
    report = only(store, PASS_FRAMEWORK_ROUTES)
    assert report.inserted == 1
    row_meta = synth_edges(store, PASS_FRAMEWORK_ROUTES)[0][3]
    assert row_meta is not None
    payload = json.loads(row_meta)
    assert payload["framework"] == "express"


def test_framework_routes_idempotent(store):
    _route_store(store)
    only(store, PASS_FRAMEWORK_ROUTES)
    assert only(store, PASS_FRAMEWORK_ROUTES).inserted == 0


# ------------------------------------------------------- event_handler_wiring


def _event_store(store: EventStore) -> None:
    add_file(store, "events.py")
    add_symbol(store, "fn:emit", "emit_login", "events.py", line=1)
    add_symbol(store, "fn:on", "on_login", "events.py", line=5)


def test_event_handler_wiring_links_emitter_to_handler(store):
    _event_store(store)
    report = only(store, PASS_EVENT_HANDLER_WIRING)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_EVENT_HANDLER_WIRING)
    assert row[0] == "fn:emit" and row[1] == "fn:on" and row[2] == "references"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "2/5"
    assert payload["event"] == "login"


@pytest.mark.parametrize(
    ("emitter", "handler"),
    [
        ("emit_save", "on_save"),
        ("dispatch_save", "handle_save"),
        ("raise_error", "on_error"),
        ("trigger_sync", "handle_sync"),
        ("fire_load", "on_load"),
    ],
)
def test_event_handler_prefix_pairs(store, emitter: str, handler: str):
    add_file(store, "events.py")
    add_symbol(store, "fn:e", emitter, "events.py", line=1)
    add_symbol(store, "fn:h", handler, "events.py", line=5)
    report = only(store, PASS_EVENT_HANDLER_WIRING)
    assert report.inserted == 1


def test_event_handler_wiring_mismatched_suffix_no_edge(store):
    add_file(store, "events.py")
    add_symbol(store, "fn:e", "emit_login", "events.py", line=1)
    add_symbol(store, "fn:h", "on_logout", "events.py", line=5)
    assert only(store, PASS_EVENT_HANDLER_WIRING).inserted == 0


def test_event_handler_wiring_cap_skips_whole_event(store):
    add_file(store, "events.py")
    add_symbol(store, "fn:e", "emit_x", "events.py", line=1)
    for index in range(3):
        add_symbol(store, f"fn:h{index}", f"on_thing{index}", "events.py", line=5 + index)
    # three handlers for the same event "thing"? no — distinct suffixes; make
    # a real fanout: one event, three handlers
    store2 = store
    add_file(store2, "ev2.py")
    add_symbol(store2, "fn:e2", "emit_go", "ev2.py", line=1)
    for index in range(3):
        add_symbol(store2, f"fn:g{index}", "on_go", "ev2.py", line=10 + index)
    report = run_synthesis(
        store,
        config=SynthesisConfig(enabled_passes=frozenset({PASS_EVENT_HANDLER_WIRING}), max_handlers_per_event=2),
    )
    # "go" event has 3 handlers > cap 2 → whole event skipped; "x" has none
    assert all("on_go" not in (row[1],) for row in synth_edges(store, PASS_EVENT_HANDLER_WIRING))
    assert pass_entry(report, PASS_EVENT_HANDLER_WIRING).skipped_uncertain >= 1


def test_event_handler_wiring_idempotent(store):
    _event_store(store)
    only(store, PASS_EVENT_HANDLER_WIRING)
    assert only(store, PASS_EVENT_HANDLER_WIRING).inserted == 0


# ---------------------------------------------------------- test_subject_edges


@pytest.mark.parametrize(
    ("test_path", "subject_path"),
    [
        ("tests/test_foo.py", "pkg/foo.py"),
        ("pkg/foo_test.go", "pkg/foo.go"),
        ("src/foo.test.ts", "src/foo.ts"),
        ("src/foo.spec.js", "src/foo.js"),
    ],
)
def test_test_subject_edges(store, test_path: str, subject_path: str):
    add_file(store, test_path)
    add_file(store, subject_path)
    report = only(store, PASS_TEST_SUBJECT_EDGES)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_TEST_SUBJECT_EDGES)
    assert row[0] == f"file:{test_path}" and row[1] == f"file:{subject_path}" and row[2] == "references"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "7/10"


def test_test_subject_missing_subject_no_edge(store):
    add_file(store, "tests/test_ghost.py")
    assert only(store, PASS_TEST_SUBJECT_EDGES).inserted == 0


def test_test_subject_ambiguous_subject_skips(store):
    add_file(store, "tests/test_foo.py")
    add_file(store, "a/foo.py")
    add_file(store, "b/foo.py")
    report = only(store, PASS_TEST_SUBJECT_EDGES)
    assert report.inserted == 0
    assert pass_entry(report, PASS_TEST_SUBJECT_EDGES).skipped_uncertain == 1


def test_test_subject_non_test_file_ignored(store):
    add_file(store, "pkg/foo.py")
    add_file(store, "pkg/bar.py")
    assert only(store, PASS_TEST_SUBJECT_EDGES).inserted == 0


def test_test_subject_idempotent(store):
    add_file(store, "tests/test_foo.py")
    add_file(store, "pkg/foo.py")
    only(store, PASS_TEST_SUBJECT_EDGES)
    assert only(store, PASS_TEST_SUBJECT_EDGES).inserted == 0


# ------------------------------------------------------- factory_function_edges


def _factory_store(store: EventStore) -> None:
    add_file(store, "factory.py")
    add_symbol(store, "fn:make", "create_widget", "factory.py")
    add_symbol(store, "cls:Widget", "Widget", "factory.py", kind="class", line=10)


def test_factory_function_links_to_unique_type(store):
    _factory_store(store)
    report = only(store, PASS_FACTORY_FUNCTIONS)
    assert report.inserted == 1
    (row,) = synth_edges(store, PASS_FACTORY_FUNCTIONS)
    assert row[0] == "fn:make" and row[1] == "cls:Widget" and row[2] == "references"
    assert row[3] is not None
    payload = json.loads(row[3])
    assert payload["confidence"] == "3/5"
    assert payload["type"] == "Widget"


@pytest.mark.parametrize("prefix", ["create", "make", "new", "build"])
def test_factory_prefixes(store, prefix: str):
    add_file(store, "f.py")
    add_symbol(store, "fn:f", f"{prefix}_widget", "f.py")
    add_symbol(store, "cls:Widget", "Widget", "f.py", kind="class", line=10)
    assert only(store, PASS_FACTORY_FUNCTIONS).inserted == 1


def test_factory_ambiguous_type_skips(store):
    add_file(store, "a.py")
    add_file(store, "b.py")
    add_symbol(store, "fn:f", "create_widget", "a.py")
    add_symbol(store, "cls:W1", "Widget", "a.py", kind="class", line=10)
    add_symbol(store, "cls:W2", "Widget", "b.py", kind="class", line=10)
    report = only(store, PASS_FACTORY_FUNCTIONS)
    assert report.inserted == 0
    assert pass_entry(report, PASS_FACTORY_FUNCTIONS).skipped_uncertain == 1


def test_factory_missing_type_no_edge(store):
    add_file(store, "f.py")
    add_symbol(store, "fn:f", "create_ghost", "f.py")
    assert only(store, PASS_FACTORY_FUNCTIONS).inserted == 0


def test_factory_camel_case_prefix(store):
    add_file(store, "f.ts", "typescript")
    add_symbol(store, "fn:f", "createWidget", "f.py")
    add_symbol(store, "cls:Widget", "Widget", "f.py", kind="class", line=10)
    assert only(store, PASS_FACTORY_FUNCTIONS).inserted == 1


def test_factory_idempotent(store):
    _factory_store(store)
    only(store, PASS_FACTORY_FUNCTIONS)
    assert only(store, PASS_FACTORY_FUNCTIONS).inserted == 0


# -------------------------------------------------------------- cross-cutting


def test_full_pipeline_all_twelve_passes_deterministic(store):
    _module_member_store(store)
    _di_store(store)
    _ii_store(store)
    first = run_synthesis(store)
    digest1 = edge_rows(store)
    second = run_synthesis(store)
    digest2 = edge_rows(store)
    assert second.inserted == 0
    assert digest1 == digest2
    assert len(first.passes) == 12


def test_synthesis_version_bumped():
    from know_your_context.resolve.synthesis import SYNTHESIS_VERSION

    assert SYNTHESIS_VERSION == 2
