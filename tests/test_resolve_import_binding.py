"""SUB-184: import-binding scope + flat-corpus module fallback.

Root cause measured on the L4 corpus (stress-3-products, SUB-178/SUB-185T):
289k parked ``calls`` refs fail with ``ambiguous_exact:N`` because the
extractor stores member calls as bare names (``json.loads`` → ``loads``,
``parser.parse_args`` → ``parse_args``) and the store's only other binding
signal — the referring file's ``kind='import'`` bookkeeping symbols — was
never consulted. SUB-185T's file/import/global scope tiers measured **+0
edges** on L4 because the corpus stores files in a flat ``NNNNN_name.py``
layout that the module-file lookup (exact path → reversed-path prefix) can
never match, leaving the import scope and the import-path tiers inert.

Two additive, precision-first changes (both run only where a ref would
otherwise FAIL — they can never retarget an already-resolved ref):

A. **Flat-corpus module fallback** (``_module_file``): when the exact match
   and the reversed-path-prefix probe both find nothing, probe the reversed
   **basename** prefix. A flat ``NNNNN_pathlib.py`` reverses to
   ``yp.bilhtap_...`` which the basename pattern ``yp.bilhtap*`` matches.
   Two or more basename matches → ``ambiguous`` (fail-safe, same discipline
   as the full-path probe). Strictly additive: a corpus the full probe can
   resolve never reaches the fallback.

B. **Import-binding scope** (new ``import_binding`` verdict between the
   import scope and the global scope): a bare call named ``X`` in file ``F``
   is bound by Python semantics when ``F`` holds exactly one
   ``kind='import'`` symbol named ``X`` and that symbol's ``imports`` ref
   resolves through the tier-(c) rules to exactly one node. Two bindings of
   the same name, an unresolved binding ref, or a module that resolves
   ambiguously → the ref keeps its original failure. This is the explicit
   ``from mod import X``/``import mod`` contract, never a same-name guess.

Recovered refs report ``strategy == "import_binding"`` so the gain is
attributable in edge metadata and batch reports (mirrors ``exact_scoped``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from know_your_context.resolve import BatchReport, ReferenceResolver
from know_your_context.store import EventStore

SEEN = "2026-11-17T00:00:00+00:00"


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


def add_symbol(
    store: EventStore, node_id: str, name: str, path: str, *, kind: str = "function", line: int = 1
) -> None:
    store.upsert_symbol(node_id=node_id, name=name, kind=kind, path=path, line=line)


def add_ref(
    store: EventStore,
    target: str,
    kind: str = "calls",
    *,
    source: str,
    tail: str | None = None,
) -> int:
    return store.insert_unresolved_ref(
        source_node_id=source, target_name=target, kind=kind, name_tail=tail, first_seen=SEEN
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
            for row in conn.execute("SELECT source, target, kind FROM graph_edges ORDER BY edge_id")
        ]


# --------------------------------------------------- flat module fallback


class TestFlatModuleFallback:
    def test_flat_import_resolves_via_basename(self, store: EventStore) -> None:
        """RED: `import pathlib` where the corpus stores only `00420_pathlib.py`."""
        add_file(store, "00420_pathlib.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        add_file(store, "00000_user.py")
        ref_id = add_ref(store, "pathlib", "imports", source="function:user-main")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "import_path"
        assert outcome.target_node_id == "file:00420_pathlib.py"

    def test_two_flat_basenames_stay_ambiguous(self, store: EventStore) -> None:
        """Two `NNNNN_pathlib.py` copies → the fallback must fail safe."""
        add_file(store, "00101_pathlib.py")
        add_file(store, "00420_pathlib.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        add_file(store, "00000_user.py")
        ref_id = add_ref(store, "pathlib", "imports", source="function:user-main")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "failed"
        assert outcome.reason == "ambiguous_module:2"

    def test_real_paths_never_reach_the_fallback(self, store: EventStore) -> None:
        """A corpus the reversed-path probe resolves is untouched by it."""
        add_file(store, "lib/pathlib.py")
        add_file(store, "00420_pathlib.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        add_file(store, "00000_user.py")
        ref_id = add_ref(store, "pathlib", "imports", source="function:user-main")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        # 'lib/pathlib.py' is the real module path; the flat namesake must not win.
        assert outcome.target_node_id == "file:lib/pathlib.py"

    def test_flat_module_fallback_activates_import_scope(self, store: EventStore) -> None:
        """RED: import-scope tier was inert on flat corpora — now it resolves."""
        add_file(store, "00420_pathlib.py")
        add_symbol(store, "class:pathlib-Path", "Path", "00420_pathlib.py", kind="class", line=945)
        add_file(store, "00000_user.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        add_file(store, "00090_os.py")
        add_ref(store, "pathlib", "imports", source="function:user-main")
        add_ref(store, "os", "imports", source="function:user-main")
        # user.py calls Path(...); a same-name def elsewhere keeps exact/global inert.
        add_file(store, "09800_other.py")
        add_symbol(store, "function:other-Path", "Path", "09800_other.py")
        ref_id = add_ref(store, "Path", "calls", source="function:user-main")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        assert outcome.target_node_id == "class:pathlib-Path"


# ------------------------------------------------------------ binding tier


class TestImportBinding:
    def _seed_binding(
        self,
        store: EventStore,
        *,
        second_compress_def: bool = True,
        binding_target: str = "headroom.compress",
        binding_name: str = "compress",
        call_name: str = "compress",
        ref_kind: str = "calls",
    ) -> int:
        """File F binds `compress` from headroom.compress; `other` also defines it.

        With ``second_compress_def`` the import scope sees 2+ candidates, so
        only the binding tier can be decisive.
        """
        add_file(store, "headroom/compress.py")
        add_symbol(store, "function:hr-compress", "compress", "headroom/compress.py")
        add_file(store, "other.py")
        if second_compress_def:
            add_symbol(store, "function:other-compress", "compress", "other.py")
        add_file(store, "00000_user.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        binding_node = "import:user-compress"
        add_symbol(store, binding_node, binding_name, "00000_user.py", kind="import", line=9)
        add_ref(store, binding_target, "imports", source=binding_node, tail="compress")
        add_ref(store, "other", "imports", source="function:user-main")
        return add_ref(store, call_name, ref_kind, source="function:user-main")

    def test_binding_resolves_when_import_scope_sees_two_defs(self, store: EventStore) -> None:
        """RED: both imports define `compress` → scopes ambiguous → binding picks."""
        ref_id = self._seed_binding(store, second_compress_def=True)

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "import_binding"
        assert outcome.target_node_id == "function:hr-compress"

    def test_binding_module_file_links_file_node(self, store: EventStore) -> None:
        """A module-only binding target resolves through tier (c) to the file."""
        add_file(store, "00418_array.py")
        add_file(store, "00000_user.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        binding_node = "import:user-array"
        add_symbol(store, binding_node, "array", "00000_user.py", kind="import", line=9)
        add_ref(store, "array", "imports", source=binding_node, tail="array")
        ref_id = add_ref(store, "array", "calls", source="function:user-main")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        assert outcome.strategy == "import_binding"
        assert outcome.target_node_id == "file:00418_array.py"

    def test_two_same_name_bindings_stay_parked(self, store: EventStore) -> None:
        """Two `from X import Y` for the same Y → genuinely ambiguous."""
        add_file(store, "aaa.py")
        add_symbol(store, "function:aaa-loads", "loads", "aaa.py")
        add_file(store, "bbb.py")
        add_symbol(store, "function:bbb-loads", "loads", "bbb.py")
        add_file(store, "00000_user.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        add_symbol(store, "import:user-loads-a", "loads", "00000_user.py", kind="import")
        add_symbol(store, "import:user-loads-b", "loads", "00000_user.py", kind="import")
        add_ref(store, "aaa", "imports", source="import:user-loads-a")
        add_ref(store, "bbb", "imports", source="import:user-loads-b")
        ref_id = add_ref(store, "loads", "calls", source="function:user-main")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "failed"
        # 2 real defs + 2 import bookkeeping nodes share the name (SUB-185
        # counting); the persisted reason preserves the original count.
        assert outcome.reason == "ambiguous_exact:4"

    def test_unresolvable_binding_stays_parked(self, store: EventStore) -> None:
        """Binding ref to a module that is not in the corpus → no guess."""
        add_file(store, "other.py")
        add_symbol(store, "function:other-compress", "compress", "other.py")
        add_file(store, "unused.py")
        add_symbol(store, "function:unused-compress", "compress", "unused.py")
        add_file(store, "00000_user.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        add_symbol(store, "import:user-compress", "compress", "00000_user.py", kind="import")
        add_ref(store, "ghost.compress", "imports", source="import:user-compress")
        ref_id = add_ref(store, "compress", "calls", source="function:user-main")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "failed"
        assert outcome.reason == "ambiguous_exact:2"

    def test_imports_ref_never_binds_through_scope_tier(self, store: EventStore) -> None:
        """Imports refs stay module bindings — the binding tier owns calls only."""
        add_file(store, "alpha.py")
        add_symbol(store, "function:alpha-ghost", "ghost", "alpha.py")
        add_file(store, "beta.py")
        add_symbol(store, "function:beta-ghost", "ghost", "beta.py")
        add_file(store, "00000_user.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        binding_node = "import:user-ghost"
        add_symbol(store, binding_node, "ghost", "00000_user.py", kind="import")
        add_ref(store, "ghost", "imports", source=binding_node)
        ref_id = add_ref(store, "ghost", "imports", source="function:user-main", tail="ghost")

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "failed"
        assert outcome.reason == "ambiguous_exact:2"

    def test_binding_verdict_is_deterministic_across_replays(self, tmp_path: Path) -> None:
        """Same store, two fresh resolver passes → identical outcomes."""
        stores: list[EventStore] = []
        reports: list[BatchReport] = []
        for _ in range(2):
            instance = EventStore(tmp_path / f"soul-{_:02d}.sqlite3")
            ref_id = self._seed_binding(instance, second_compress_def=True)
            stores.append(instance)
            reports.append(resolve(instance))
        try:
            first = outcome_of(reports[0], ref_id)
            second = outcome_of(reports[1], ref_id)
            assert first.status == second.status == "resolved"
            assert first.target_node_id == second.target_node_id == "function:hr-compress"
            assert first.strategy == second.strategy == "import_binding"
        finally:
            for instance in stores:
                instance.close()

    def test_binding_edge_carries_strategy_metadata(self, store: EventStore) -> None:
        """Edge metadata keeps attribution: strategy == import_binding."""
        self._seed_binding(store, second_compress_def=False)
        # binding import ref + 'other' import ref + the call = 3 refs.
        assert resolve(store).processed == 3
        edges = edges_of(store)
        calls_edges = [edge for edge in edges if edge[2] == "calls"]
        assert len(calls_edges) == 1
        source, target, _kind = calls_edges[0]
        assert source == "function:user-main"
        assert target == "function:hr-compress"


# ------------------------------------------ the L4 poster child: parse_args


class TestParseArgsFourteenDefs:
    """SUB-188 RED: ``parser.parse_args()`` is stored as a bare ``parse_args``
    call with 14 real definitions corpus-wide (L4 measured: 14 rows, methods
    + CLI-script functions). The referring file's own import of ``argparse``
    (flat ``02326_argparse.py`` layout) makes the import scope decisive; two
    same-name definitions across the imported modules keep the ref parked.
    """

    OTHER_DEFS = (
        "00006_pr-health-labels.py",
        "00595_pr-governance.py",
        "00603_build_python_release_smoke.py",
        "00604_release_smoke_all.py",
        "00617_cache_validation_bundle.py",
        "00632_claude_session_branch_compare.py",
        "02279_optparse.py",
        "02721_main.py",
        "03748_parser.py",
        "03999_one.py",
        "04000_two.py",
        "04001_three.py",
        "04002_four.py",
    )

    def _seed(
        self,
        store: EventStore,
        *,
        modules: tuple[str, ...],
        parse_args_in: dict[str, str],
    ) -> int:
        """Corpus with 13 flat ``parse_args`` defs plus ``parse_args_in``'s
        (14 for the single-argparse case, mirroring L4's measured 14)."""
        for index, path in enumerate(self.OTHER_DEFS):
            add_file(store, path)
            add_symbol(store, f"function:{path}:{index}", "parse_args", path)
        add_file(store, "00000_user.py")
        add_symbol(store, "function:user-main", "main", "00000_user.py")
        for module in modules:
            add_file(store, module)
            module_name = module.split("_", 1)[1].removesuffix(".py")
            add_symbol(store, f"import:user-{module}", module_name, "00000_user.py", kind="import")
            add_ref(store, module_name, "imports", source=f"import:user-{module}")
        for module, node_id in parse_args_in.items():
            add_symbol(store, node_id, "parse_args", module, kind="method", line=1244)
        return add_ref(store, "parse_args", "calls", source="function:user-main", tail="parse_args")

    def test_bare_method_call_resolves_via_flat_import_scope(self, store: EventStore) -> None:
        """RED: 14 defs corpus-wide, but the caller imports argparse (flat
        ``02326_argparse.py``) — the only imported file defining
        ``parse_args``. The import scope must win, not ``ambiguous_exact:14``."""
        ref_id = self._seed(
            store,
            modules=("02326_argparse.py",),
            parse_args_in={"02326_argparse.py": "method:argparse-parse_args"},
        )

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "resolved"
        assert outcome.target_node_id == "method:argparse-parse_args"
        assert outcome.strategy == "exact_scoped"

    def test_two_imported_modules_both_define_it_stay_parked(self, store: EventStore) -> None:
        """Precision: argparse AND optparse both define ``parse_args`` and the
        file imports both — genuinely ambiguous, the ref must stay parked."""
        ref_id = self._seed(
            store,
            modules=("02326_argparse.py", "02279_optparse.py"),
            parse_args_in={
                "02326_argparse.py": "method:argparse-parse_args",
                "02279_optparse.py": "method:optparse-parse_args",
            },
        )

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        assert outcome.status == "failed"
        # 13 flat defs + argparse's + optparse's = 15 corpus-wide.
        assert outcome.reason == "ambiguous_exact:15"

    def test_no_import_signal_stays_parked(self, store: EventStore) -> None:
        """Precision: the file imports ``os`` which defines no ``parse_args``
        — no scope is decisive, the defs keep the ref parked."""
        ref_id = self._seed(store, modules=("00420_os.py",), parse_args_in={})

        report = resolve(store)

        outcome = outcome_of(report, ref_id)
        # 13 flat defs only — os defines none, no scope decides.
        assert outcome.status == "failed"
        assert outcome.reason == "ambiguous_exact:13"