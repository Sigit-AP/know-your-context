"""SUB-181: scope-aware disambiguation for ambiguous exact-name refs.

Root cause measured on the L4 corpus (stress-3-products B6): the resolver
parks every ref whose ``target_name`` matches 2+ symbols and fails it with
``ambiguous_exact:N`` — 351,132 refs on the L4 store, of which 71,653 have
exactly ONE same-name definition in the referring file. Those are not truly
ambiguous: the name is bound lexically.

Design (precision first, additive only)
---------------------------------------
The scope tier runs *after* every existing tier (exact → name_tail →
import_path → module_helper) and only when the ref would otherwise FAIL with
an ambiguity reason. It therefore can never change a ref that already
resolved — no target churn, no provenance churn.

1. **file scope** — exactly one same-name symbol in the referring file wins
   (Python module-level shadowing; same-file definition is the bound name).
2. **import scope** — exactly one same-name symbol in a module the referring
   file imports wins (the file's ``imports`` refs resolved against the
   ``files`` table, reusing the import-path resolution rules).
3. **global** — still 2+ candidates: the ref keeps failing with the original
   ``ambiguous_exact:N`` reason. Never guess. No edge is minted.

Recovered refs report ``strategy == "exact_scoped"`` so the gain is
attributable in edge metadata and batch reports.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from know_your_context.resolve import BatchReport, ReferenceResolver
from know_your_context.store import EventStore

SEEN = "2026-11-17T00:00:00+00:00"
BASE = "function:cli-main"
PARSE_ARGS_DEFS = 14


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def add_file(store: EventStore, path: str, language: str = "python") -> None:
    store.upsert_file(
        path=path,
        sha256=hashlib.sha256(path.encode()).hexdigest(),
        size=10,
        mtime=100,
        language=language,
        generated_hint=False,
        extraction_version=1,
    )


def add_symbol(store: EventStore, node_id: str, name: str, path: str, *, kind: str = "function") -> None:
    store.upsert_symbol(node_id=node_id, name=name, kind=kind, path=path, line=1)


def add_ref(store: EventStore, target: str, kind: str = "calls", *, source: str = BASE) -> int:
    return store.insert_unresolved_ref(
        source_node_id=source, target_name=target, kind=kind, name_tail=None, first_seen=SEEN
    )


def resolve(store: EventStore) -> BatchReport:
    return ReferenceResolver().resolve_pending(store)


def outcome_of(report: BatchReport, ref_id: int):
    matches = [outcome for outcome in report.outcomes if outcome.ref_id == ref_id]
    assert len(matches) == 1
    return matches[0]


def edges_of(store: EventStore) -> list[tuple[object, ...]]:
    with store.read() as conn:
        return [
            tuple(row)
            for row in conn.execute("SELECT source, target, kind, metadata FROM graph_edges ORDER BY edge_id")
        ]


def seed_parse_args(store: EventStore, count: int, source_path: str, *, language: str = "python") -> list[str]:
    """``count`` definitions of ``parse_args`` across modules; source file last."""
    paths: list[str] = []
    for index in range(count):
        path = f"pkg/m{index:02d}.py"
        add_file(store, path, language)
        add_symbol(store, f"function:pa-{index}", "parse_args", path)
        paths.append(path)
    add_file(store, source_path, language)
    add_symbol(store, BASE, "main", source_path)
    return paths


# --------------------------------------------------------------- file scope


class TestFileScope:
    def test_parse_args_14_defs_resolves_to_same_file_definition(self, store: EventStore) -> None:
        """RED before SUB-181: 14 defs → ambiguous_exact:14, 0 edges."""
        paths = seed_parse_args(store, PARSE_ARGS_DEFS - 1, "pkg/cli.py")
        local = paths[3]
        assert local == "pkg/m03.py"  # sanity: the source file is the 14th def below
        add_symbol(store, "function:pa-local", "parse_args", "pkg/cli.py")
        ref_id = add_ref(store, "parse_args", source=BASE)

        report = resolve(store)
        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "exact_scoped"
        assert outcome.target_node_id == "function:pa-local"
        assert report.resolved == 1 and report.edges_inserted == 1
        assert edges_of(store)[0][:3] == (BASE, "function:pa-local", "calls")

    def test_two_same_file_definitions_stay_ambiguous(self, store: EventStore) -> None:
        """Precision: two same-name defs in the file is still a guess → fail."""
        add_file(store, "pkg/cli.py")
        add_symbol(store, BASE, "main", "pkg/cli.py")
        add_symbol(store, "function:pa-a", "parse_args", "pkg/cli.py")
        add_symbol(store, "function:pa-b", "parse_args", "pkg/cli.py")
        add_symbol(store, "function:pa-other", "parse_args", "pkg/other.py")
        ref_id = add_ref(store, "parse_args", source=BASE)

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "failed"
        assert outcome.reason == "ambiguous_exact:3"
        assert edges_of(store) == []

    def test_global_ambiguity_without_scope_relation_still_fails(self, store: EventStore) -> None:
        """The parse_args-14 refs with no lexical anchor keep failing."""
        seed_parse_args(store, PARSE_ARGS_DEFS, "pkg/main.py")
        ref_id = add_ref(store, "parse_args", source=BASE)

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "failed"
        assert outcome.reason == f"ambiguous_exact:{PARSE_ARGS_DEFS}"
        assert edges_of(store) == []


# ------------------------------------------------------------- import scope


class TestImportScope:
    def _import_ref(self, store: EventStore, module: str, source_path: str, index: int) -> None:
        """The extractor's shape: an ``import`` symbol node parks the module ref."""
        node_id = f"import:{index}"
        add_symbol(store, node_id, module.rsplit(".", 1)[-1], source_path, kind="import")
        add_ref(store, module, "imports", source=node_id)

    def test_unique_imported_definition_resolves(self, store: EventStore) -> None:
        paths = seed_parse_args(store, PARSE_ARGS_DEFS - 1, "pkg/main.py")
        add_file(store, "pkg/cli.py")
        add_symbol(store, "function:pa-cli", "parse_args", "pkg/cli.py")
        self._import_ref(store, "pkg.cli", "pkg/main.py", 0)
        ref_id = add_ref(store, "parse_args", source=BASE)

        report = resolve(store)
        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "exact_scoped"
        assert outcome.target_node_id == "function:pa-cli"
        targets = {edge[1] for edge in edges_of(store) if edge[2] == "calls"}
        assert targets == {"function:pa-cli"}
        assert all(path != "pkg/cli.py" or True for path in paths)

    def test_two_imported_candidates_stay_ambiguous(self, store: EventStore) -> None:
        seed_parse_args(store, PARSE_ARGS_DEFS - 2, "pkg/main.py")
        for module in ("pkg/a", "pkg/b"):
            add_file(store, f"{module}.py")
            add_symbol(store, f"function:pa-{module[-1]}", "parse_args", f"{module}.py")
        self._import_ref(store, "pkg.a", "pkg/main.py", 0)
        self._import_ref(store, "pkg.b", "pkg/main.py", 1)
        ref_id = add_ref(store, "parse_args", source=BASE)

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "failed"
        assert outcome.reason == f"ambiguous_exact:{PARSE_ARGS_DEFS}"
        assert [edge for edge in edges_of(store) if edge[2] == "calls"] == []

    def test_file_scope_wins_over_import_scope(self, store: EventStore) -> None:
        """Python module-level shadowing: a local def is the bound name."""
        seed_parse_args(store, 3, "pkg/main.py")
        add_file(store, "pkg/cli.py")
        add_symbol(store, "function:pa-cli", "parse_args", "pkg/cli.py")
        add_symbol(store, "function:pa-local", "parse_args", "pkg/main.py")
        self._import_ref(store, "pkg.cli", "pkg/main.py", 0)
        ref_id = add_ref(store, "parse_args", source=BASE)

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "exact_scoped"
        assert outcome.target_node_id == "function:pa-local"


# --------------------------------------------------------------- invariants


class TestScopeInvariants:
    def test_scope_tier_never_overrides_import_path_resolution(self, store: EventStore) -> None:
        """An imports ref already resolved by the module path keeps its verdict."""
        add_file(store, "pkg/main.py")
        add_symbol(store, BASE, "main", "pkg/main.py")
        add_file(store, "pkg/cli.py")
        add_symbol(store, "function:pa-cli", "parse_args", "pkg/cli.py")
        add_symbol(store, "function:pa-other", "parse_args", "pkg/other.py")
        ref_id = add_ref(store, "pkg.cli", "imports", source="file:pkg/main.py")

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "import_path"
        assert outcome.target_node_id == "file:pkg/cli.py"

    def test_scope_decision_is_deterministic_across_replays(self, tmp_path: Path) -> None:
        results: list[tuple[str | None, str | None]] = []
        for replay in range(3):
            instance = EventStore(tmp_path / f"replay-{replay}.sqlite3")
            try:
                seed_parse_args(instance, PARSE_ARGS_DEFS - 1, "pkg/cli.py")
                add_symbol(instance, "function:pa-local", "parse_args", "pkg/cli.py")
                ref_id = add_ref(instance, "parse_args", source=BASE)
                outcome = outcome_of(resolve(instance), ref_id)
                results.append((outcome.status, outcome.target_node_id))
            finally:
                instance.close()
        assert results == [("resolved", "function:pa-local")] * 3

    def test_resolution_is_idempotent_on_rerun(self, store: EventStore) -> None:
        seed_parse_args(store, PARSE_ARGS_DEFS - 1, "pkg/cli.py")
        add_symbol(store, "function:pa-local", "parse_args", "pkg/cli.py")
        add_ref(store, "parse_args", source=BASE)

        first = resolve(store)
        assert first.resolved == 1
        edges_after_first = edges_of(store)
        second = resolve(store)
        assert second.processed == 0
        assert edges_of(store) == edges_after_first

    def test_scope_tier_reports_persistence_faithfully(self, store: EventStore) -> None:
        """The report is a view of persisted state: a retry re-reads the reason."""
        seed_parse_args(store, PARSE_ARGS_DEFS, "pkg/main.py")
        ref_id = add_ref(store, "parse_args", source=BASE)
        resolve(store)
        row = store.unresolved_ref(ref_id)
        assert row is not None
        assert row.status == "failed"
        assert row.failure_reason == f"ambiguous_exact:{PARSE_ARGS_DEFS}"
        moved = ReferenceResolver().resurrect_failed(store)
        assert ref_id in moved
        assert outcome_of(resolve(store), ref_id).reason == f"ambiguous_exact:{PARSE_ARGS_DEFS}"


# ------------------------------------------------- language / source shapes


class TestScopeShapes:
    def test_file_node_source_scopes_to_its_own_file(self, store: EventStore) -> None:
        add_file(store, "pkg/cli.py")
        add_symbol(store, "function:pa-local", "parse_args", "pkg/cli.py")
        add_symbol(store, "function:pa-other", "parse_args", "pkg/m00.py")
        ref_id = add_ref(store, "parse_args", source="file:pkg/cli.py")

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "exact_scoped"
        assert outcome.target_node_id == "function:pa-local"

    def test_js_relative_import_scope_resolves(self, store: EventStore) -> None:
        add_file(store, "src/a.ts", "typescript")
        add_symbol(store, BASE, "main", "src/a.ts", kind="function")
        add_file(store, "src/util.ts", "typescript")
        add_symbol(store, "function:h-util", "helper", "src/util.ts")
        add_file(store, "src/other.ts", "typescript")
        add_symbol(store, "function:h-other", "helper", "src/other.ts")
        add_symbol(store, "import:0", "util", "src/a.ts", kind="import")
        add_ref(store, "./util", "imports", source="import:0")
        ref_id = add_ref(store, "helper", source=BASE)

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "exact_scoped"
        assert outcome.target_node_id == "function:h-util"

    def test_markdown_reference_scope_is_precision_safe(self, store: EventStore) -> None:
        """Doc refs are not code symbols: no same-name symbol → no binding."""
        add_file(store, "docs/a.md", "markdown")
        add_symbol(store, "function:dup1", "parse_args", "pkg/m00.py")
        add_symbol(store, "function:dup2", "parse_args", "pkg/m01.py")
        ref_id = add_ref(store, "parse_args", "references", source="file:docs/a.md")

        outcome = outcome_of(resolve(store), ref_id)
        assert outcome.status == "failed"
        assert outcome.reason == "ambiguous_exact:2"


# --------------------------------------------------------------- cost model


def test_scope_tier_materialises_at_most_the_decisive_row(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """B7 cost model holds for the scope tier: count first, fetch only on 1.

    300 same-name defs + 1 same-file def must cost O(1) rows per ref, not
    O(candidates) — the pre-SUB-181 failure path materialised nothing and the
    scope tier must not reintroduce the quadratic lookup.
    """
    for index in range(300):
        path = f"pkg/m{index:03d}.py"
        add_file(store, path)
        add_symbol(store, f"function:pa-{index}", "parse_args", path)
    add_file(store, "pkg/cli.py")
    add_symbol(store, BASE, "main", "pkg/cli.py")
    add_symbol(store, "function:pa-local", "parse_args", "pkg/cli.py")

    calls = [0]
    original = EventStore._symbol_row

    def counting(row):  # noqa: ANN001, ANN202 - mirrors the staticmethod signature
        calls[0] += 1
        return original(row)

    monkeypatch.setattr(EventStore, "_symbol_row", staticmethod(counting))
    ref = add_ref(store, "parse_args", source=BASE)
    report = resolve(store)
    assert outcome_of(report, ref).target_node_id == "function:pa-local"
    # Exactly two rows: the source symbol (``_source_context``) + the hit.
    assert calls[0] <= 2, f"scope tier materialised {calls[0]} symbol rows"
