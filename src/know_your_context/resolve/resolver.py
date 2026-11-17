"""Two-phase reference resolver (wave W2, MASTER-PLAN pillar 3).

Strategy cascade per pending ref — codegraph's import-resolver / name-matcher
pair reduced to three deterministic tiers (RESEARCH-INGESTION §2e):

a. **exact** — ``symbols.name == target_name`` (seeks ``idx_symbols_name``).
b. **name_tail** — symbols whose name equals the tail or ends with
   ``.<tail>`` / ``::<tail>`` (qualified-name suffix).
c. **import_path** — module-based languages only: Python dotted/relative
   imports resolved against the ``files`` table (``.py`` then
   ``/__init__.py``), JS/TS relative specifiers resolved against the source
   directory with the extension/index-name search order. Bare JS/TS
   specifiers fail as ``external_module``.
d. **module_helper** — per-language candidate paths (W12).
e. **exact_scoped** (SUB-181) — only for refs that would otherwise fail with
   an ambiguity reason: 2+ symbols share ``target_name``, so pick by lexical
   scope. *File scope* first — exactly one same-name symbol in the referring
   file is the bound definition (module-level shadowing). *Import scope*
   second — exactly one same-name symbol in a module the referring file
   imports (its ``imports`` refs resolved with the tier-(c) rules). Still 2+
   candidates after both scopes → the ref keeps failing with the original
   ``ambiguous_exact:N`` reason. The tier runs *after* (a)–(d) so it can only
   convert failures into edges, never retarget a ref that already resolved.

Ambiguity never guesses: a tier with 2+ candidates defers to the next tier
(import-path can disambiguate via the module path, e.g. ``pkg.mod.func``);
if no later tier resolves, the ref fails with the *first* ambiguity reason.
All failure reasons are deterministic strings (see ``_FAILURE_REASONS``).

Batch discipline: one read pass over the pending queue (oldest first, capped
fail-closed by ``limit``/``ResolverConfig.batch_limit``), then one
``add_graph_edges`` transaction, then one ``record_resolution_attempt`` per
ref. A crash between the two write phases replays cleanly — edge identity
dedups and refs stay pending. Every emitted edge carries the ref identity in
``metadata`` (``ref_id``/``ref_name``/``strategy``) so a later file removal
can resurrect the ref (codegraph ``resurrectRefFromDroppedEdge``).
"""

from __future__ import annotations

import posixpath
import sqlite3
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from know_your_context.domain import canonical_json
from know_your_context.graph_model import file_node_id, is_file_node_id
from know_your_context.ops.switches import load_switches
from know_your_context.resolve.lang_helpers import LanguageHelper, get_helper
from know_your_context.store import EventStore, FileRow, SymbolRow, UnresolvedRefRow

__all__ = [
    "PROVENANCE",
    "REF_KIND_TO_EDGE_KIND",
    "RESOLVER_VERSION",
    "BatchReport",
    "ReferenceResolver",
    "RefOutcome",
    "ResolverConfig",
    "ResolverError",
    "resolve_pending",
]

RESOLVER_VERSION: int = 1

#: Provenance stamp on every resolver-minted edge.
PROVENANCE: str = f"resolver:two-phase:v{RESOLVER_VERSION}"

#: Ref kinds the resolver accepts, mapped to the edge kind they mint
#: (``function_ref`` is codegraph's call-site alias for a ``calls`` edge).
#: W4 addendum (temuan SUB-90): W3 document ingestion parks markdown/
#: plaintext link refs as ``references`` — mapped to the frozen core edge
#: kind ``references`` (graph_model.CoreEdgeKind.REFERENCES).
REF_KIND_TO_EDGE_KIND: dict[str, str] = {
    "calls": "calls",
    "imports": "imports",
    "function_ref": "calls",
    "references": "references",
}

#: Extension → language fallback when the source file has no ``files`` row
#: (or its language is ``unknown``).
_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "typescriptreact",
}


class ResolverError(ValueError):
    """Invalid resolver configuration or batch argument (fail-closed)."""


def _validate_limit(value: int | None, label: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ResolverError(f"{label} must be None or an int >= 1, got {value!r}")


@dataclass(frozen=True)
class ResolverConfig:
    """Tuning knobs for :class:`ReferenceResolver` (all deterministic)."""

    batch_limit: int | None = None
    js_extensions: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx")
    js_index_names: tuple[str, ...] = ("index.ts", "index.tsx", "index.js", "index.jsx")
    python_suffixes: tuple[str, ...] = (".py", "/__init__.py")
    module_languages: frozenset[str] = frozenset(
        {"python", "javascript", "typescript", "javascriptreact", "typescriptreact"}
    )
    #: W12 (SUB-121): the per-language helper tier (lang_helpers.py) runs for
    #: ``imports`` refs after the import-path tier. ``False`` disables it
    #: wholesale (kill switch; the pre-W12 behaviour).
    helper_tier_enabled: bool = True

    def __post_init__(self) -> None:
        _validate_limit(self.batch_limit, "batch_limit")
        for label, sequence in (
            ("js_extensions", self.js_extensions),
            ("js_index_names", self.js_index_names),
            ("python_suffixes", self.python_suffixes),
        ):
            if not sequence:
                raise ResolverError(f"{label} must be a non-empty sequence")
            if not all(isinstance(item, str) and item for item in sequence):
                raise ResolverError(f"{label} entries must be non-empty strings")
        if not self.module_languages:
            raise ResolverError("module_languages must be non-empty")
        if not all(isinstance(language, str) and language for language in self.module_languages):
            raise ResolverError("module_languages entries must be non-empty strings")
        if not isinstance(self.helper_tier_enabled, bool):
            raise ResolverError("helper_tier_enabled must be a bool")


@dataclass(frozen=True)
class RefOutcome:
    """The deterministic verdict for one processed ref."""

    ref_id: int
    status: Literal["resolved", "failed"]
    strategy: Literal["exact", "name_tail", "import_path", "module_helper", "exact_scoped"] | None
    target_node_id: str | None
    reason: str | None


@dataclass(frozen=True)
class BatchReport:
    """Per-batch resolution counts; ``outcomes`` is sorted by ``ref_id``."""

    processed: int
    resolved: int
    failed: int
    pending_remaining: int
    edges_inserted: int
    outcomes: tuple[RefOutcome, ...]


@dataclass(frozen=True)
class _Decision:
    strategy: Literal["exact", "name_tail", "import_path", "module_helper", "exact_scoped"] | None
    target_node_id: str | None
    reason: str | None


def _hit(
    strategy: Literal["exact", "name_tail", "import_path", "module_helper", "exact_scoped"],
    target_node_id: str,
) -> _Decision:
    return _Decision(strategy, target_node_id, None)


def _fail(reason: str) -> _Decision:
    return _Decision(None, None, reason)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _escape_glob(value: str) -> str:
    """GLOB escaping (no ESCAPE clause exists): bracket the wildcards."""
    return value.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def _ascii_lower(value: str) -> str:
    """ASCII-only case fold — exactly SQLite ``lower()``/``LIKE`` semantics
    (non-ASCII bytes are never folded by either)."""
    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value)


def _file_row(row: sqlite3.Row) -> FileRow:
    return FileRow(
        path=str(row["path"]),
        sha256=str(row["sha256"]),
        size=int(row["size"]),
        mtime=int(row["mtime"]),
        language=str(row["language"]),
        generated_hint=bool(row["generated_hint"]),
        extraction_version=int(row["extraction_version"]),
    )


def _symbol_row(row: sqlite3.Row) -> SymbolRow:
    return SymbolRow(
        node_id=str(row["node_id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        path=str(row["path"]),
        line=int(row["line"]),
        col=int(row["col"]),
    )


@dataclass
class _PassCache:
    """Per-pass memo for read-only lookups (B7, SUB-173).

    The resolve pass never mutates the ``symbols``/``files`` tables (edges are
    written after the read phase), so within one ``resolve_pending`` call every
    lookup is a pure function of the store. A batch shares many refs per source
    file and many repeated names (``len``/``append``/``json``), so memoising
    the *counts* and the source context removes repeated round-trips without
    changing a single decision. A new cache is built per call: nothing survives
    across passes, so an incremental re-ingest never resolves against stale
    counts.

    Ids are opaque strings and tails arbitrary text, hence the dict keys.
    """

    source_context: dict[str, tuple[str, str] | None]
    name_counts: dict[str, int]
    tail_counts: dict[str, int]
    module_files: dict[str, tuple[str, FileRow | int | None]]
    symbols_in_file: dict[tuple[str, str], tuple[str, SymbolRow | int | None]]
    #: SUB-181: scope tier memos — (name, sorted paths) → same-name lookup
    #: verdict, source path → the module paths it imports, source path → its
    #: raw import target names.
    same_name_in_paths: dict[tuple[str, tuple[str, ...]], tuple[str, SymbolRow | int | None]]
    imported_paths: dict[str, frozenset[str]]
    import_targets: dict[str, tuple[str, ...]]

    @classmethod
    def fresh(cls) -> _PassCache:
        return cls(
            source_context={},
            name_counts={},
            tail_counts={},
            module_files={},
            symbols_in_file={},
            same_name_in_paths={},
            imported_paths={},
            import_targets={},
        )


class ReferenceResolver:
    """Turns pending ``unresolved_refs`` into ``calls``/``imports`` edges."""

    def __init__(self, config: ResolverConfig | None = None) -> None:
        self._config = config if config is not None else ResolverConfig()
        # W13 operator kill switch: KNOW_YOUR_CONTEXT_DISABLE_HELPER_TIER
        # overrides even an explicitly enabled config (operator wins).
        self._helper_tier_active = self._config.helper_tier_enabled and not load_switches().helper_tier_disabled()

    @property
    def config(self) -> ResolverConfig:
        return self._config

    @property
    def helper_tier_active(self) -> bool:
        """Whether the module-helper tier will run (config AND kill switch)."""
        return self._helper_tier_active

    # ------------------------------------------------------------ public API

    def resolve_pending(self, store: EventStore, *, limit: int | None = None) -> BatchReport:
        """Resolve one batch of pending refs (oldest first), capped fail-closed.

        Read phase is pure; the write phase is one ``add_graph_edges``
        transaction followed by one ``record_resolution_attempt`` per ref.
        """
        _validate_limit(limit, "limit")
        effective_limit = limit if limit is not None else self._config.batch_limit
        refs = store.pending_unresolved_refs(limit=effective_limit)
        cache = _PassCache.fresh()
        decisions: list[tuple[UnresolvedRefRow, _Decision]] = []
        edge_payloads: list[dict[str, object]] = []
        for ref in refs:
            decision = self._resolve_ref(store, ref, cache=cache)
            decisions.append((ref, decision))
            if decision.reason is None:
                assert decision.target_node_id is not None and decision.strategy is not None
                edge_payloads.append(
                    {
                        "source": ref.source_node_id,
                        "target": decision.target_node_id,
                        "kind": REF_KIND_TO_EDGE_KIND[ref.kind],
                        "metadata": canonical_json(
                            {"ref_id": ref.ref_id, "ref_name": ref.target_name, "strategy": decision.strategy}
                        ),
                        "provenance": PROVENANCE,
                    }
                )
        edges_inserted = store.add_graph_edges(edge_payloads) if edge_payloads else 0
        # B7 (SUB-173): one transaction for the whole batch. Per-ref commits
        # meant one fsync per parked reference (38,371 of them at 1,000 files =
        # 14.6 s of the 65 s ingest) for writes the pass had already decided.
        recorded_rows = store.record_resolution_attempts(
            [
                (ref.ref_id, "resolved" if decision.reason is None else "failed", decision.reason)
                for ref, decision in decisions
            ]
        )
        return self._batch_report(store, decisions, recorded_rows, edges_inserted)

    @staticmethod
    def _batch_report(
        store: EventStore,
        decisions: list[tuple[UnresolvedRefRow, _Decision]],
        recorded_rows: list[UnresolvedRefRow],
        edges_inserted: int,
    ) -> BatchReport:
        """Assemble the BatchReport as a view of persisted state (F4)."""
        outcomes: list[RefOutcome] = []
        for (ref, decision), recorded in zip(decisions, recorded_rows, strict=True):
            # F4 (schema v4): the deterministic failure reason is persisted on
            # the row, and the reported outcome reads it back from the store —
            # the BatchReport is a view of persisted state, never just memory.
            outcomes.append(
                RefOutcome(
                    ref_id=ref.ref_id,
                    status="resolved" if decision.reason is None else "failed",
                    strategy=decision.strategy,
                    target_node_id=decision.target_node_id,
                    reason=recorded.failure_reason,
                )
            )
        outcomes.sort(key=lambda outcome: outcome.ref_id)
        resolved = sum(1 for outcome in outcomes if outcome.status == "resolved")
        return BatchReport(
            processed=len(outcomes),
            resolved=resolved,
            failed=len(outcomes) - resolved,
            pending_remaining=store.pending_unresolved_count(),
            edges_inserted=edges_inserted,
            outcomes=tuple(outcomes),
        )

    def resurrect_failed(
        self, store: EventStore, *, name_tail: str | None = None, limit: int | None = None
    ) -> tuple[int, ...]:
        """Move failed refs back to pending (codegraph #1240 incremental retry).

        Pass ``name_tail`` to retry only the refs a newly landed/changed file
        could satisfy; ``attempts`` is preserved by the store.
        """
        _validate_limit(limit, "limit")
        failed = store.failed_unresolved_refs(name_tail=name_tail)
        if limit is not None:
            failed = failed[:limit]
        moved: list[int] = []
        for ref in failed:
            store.retry_failed_ref(ref.ref_id)
            moved.append(ref.ref_id)
        return tuple(moved)

    # ------------------------------------------------------- strategy cascade

    def _resolve_ref(self, store: EventStore, ref: UnresolvedRefRow, *, cache: _PassCache | None = None) -> _Decision:
        pass_cache = cache if cache is not None else _PassCache.fresh()
        edge_kind = REF_KIND_TO_EDGE_KIND.get(ref.kind)
        if edge_kind is None:
            return _fail("unknown_ref_kind")
        context = self._source_context(store, ref.source_node_id, cache=pass_cache)
        if context is None:
            return _fail("unknown_source")
        source_path, language = context
        deferred_reason: str | None = None
        # (a) exact symbol match. B7 (SUB-173): ask the index how many rows a
        # name has before fetching any — materialising every same-named row
        # only to read ``len()`` made the resolve pass quadratic in corpus size
        # (every module defines ``__init__``/``add``/``summary``/``json``).
        exact_count = pass_cache.name_counts.get(ref.target_name)
        if exact_count is None:
            exact_count = store.symbols_named_count(ref.target_name)
            pass_cache.name_counts[ref.target_name] = exact_count
        if exact_count == 1:
            exact = store.symbols_named(ref.target_name)
            return _hit("exact", exact[0].node_id)
        if exact_count > 1:
            deferred_reason = f"ambiguous_exact:{exact_count}"
        # (b) name_tail match (skip when the tail adds no information)
        tail = ref.name_tail
        if tail and tail != ref.target_name:
            tail_count = pass_cache.tail_counts.get(tail)
            if tail_count is None:
                tail_count = self._tail_candidate_count(store, tail)
                pass_cache.tail_counts[tail] = tail_count
            if tail_count == 1:
                candidates = self._tail_candidates(store, tail)
                return _hit("name_tail", candidates[0].node_id)
            if tail_count > 1 and deferred_reason is None:
                deferred_reason = f"ambiguous_tail:{tail_count}"
        # (c) import-path resolution (module-based languages)
        if language in self._config.module_languages and self._import_path_applies(ref.kind, ref.target_name, language):
            if language == "python":
                decision = self._resolve_python_import(store, source_path, ref.target_name, edge_kind, cache=pass_cache)
            else:
                decision = self._resolve_js_import(store, source_path, ref.target_name, cache=pass_cache)
            if decision is not None:
                return decision
        # (d) per-language helper tier (W12): imports refs only, after (c).
        if edge_kind == "imports" and self._helper_tier_active:
            helper = get_helper(language)
            if helper is not None:
                decision = self._resolve_module_helper(store, helper, source_path, ref.target_name)
                if decision is not None:
                    return decision
        if deferred_reason is not None:
            # (e) SUB-181 scope tier: the name is defined 2+ times, so try the
            # lexical scopes before declaring ambiguity. Runs last, so it can
            # only rescue a ref that would have failed — never retarget one.
            scoped = self._resolve_scoped(store, ref, source_path, language, cache=pass_cache)
            if scoped is not None:
                return scoped
            return _fail(deferred_reason)
        return _fail("no_resolution")

    def _source_context(
        self, store: EventStore, source_node_id: str, *, cache: _PassCache | None = None
    ) -> tuple[str, str] | None:
        """(source file path, language) or None when the source node is unknown."""
        if cache is not None and source_node_id in cache.source_context:
            return cache.source_context[source_node_id]
        path: str | None = None
        symbol = store.symbol_by_node(source_node_id)
        if symbol is not None:
            path = symbol.path
        elif is_file_node_id(source_node_id):
            candidate = source_node_id[len("file:") :]
            if store.get_file(candidate) is not None:
                path = candidate
        if path is None:
            if cache is not None:
                cache.source_context[source_node_id] = None
            return None
        file_row = store.get_file(path)
        language = (
            file_row.language
            if file_row is not None and file_row.language != "unknown"
            else _EXTENSION_LANGUAGES.get(PurePosixPath(path).suffix, "unknown")
        )
        if cache is not None:
            cache.source_context[source_node_id] = (path, language)
        return path, language

    def _import_path_applies(self, ref_kind: str, target_name: str, language: str) -> bool:
        if ref_kind == "imports":
            return True
        if language == "python":
            return target_name.startswith(".") or "." in target_name
        return target_name.startswith(".") or "/" in target_name

    def _tail_candidate_count(self, store: EventStore, tail: str) -> int:
        """How many symbols the tail strategy would match — without the rows.

        Mirrors :meth:`_tail_candidates`'s predicate exactly (the v6 indexed
        ``name_tail`` path for separator-free tails, the legacy exact/LIKE union
        otherwise) so a count and a fetch can never disagree. B7: the resolver
        only needs the count to choose a strategy; the rows are fetched solely
        when there is exactly one.
        """
        if "." not in tail and "::" not in tail:
            return store.symbols_with_tail_count(tail)
        like = _escape_like(tail)
        with store.read() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM symbols WHERE name = ? OR name LIKE ? ESCAPE '\\' "
                "OR name LIKE ? ESCAPE '\\'",
                (tail, f"%.{like}", f"%::{like}"),
            ).fetchone()
        return 0 if row is None else int(row["n"])

    def _tail_candidates(self, store: EventStore, tail: str) -> list[SymbolRow]:
        # v6 index path: a separator-free tail equals the materialized
        # ``name_tail`` exactly when the legacy exact/LIKE union would match —
        # ``name_tail(name) = segment after the last '.'/'::'``, so
        # ``name_tail = tail`` ⟺ ``name = tail OR name ends '.tail' OR name
        # ends '::tail'`` for any tail without '.'/'::'. Tails containing a
        # separator (e.g. markdown link tails like "README.md") keep the
        # legacy scan byte-for-byte.
        if "." not in tail and "::" not in tail:
            return store.symbols_with_tail(tail)
        found: dict[str, SymbolRow] = {symbol.node_id: symbol for symbol in store.symbols_named(tail)}
        like = _escape_like(tail)
        with store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE name LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' "
                "ORDER BY path, line, col, node_id",
                (f"%.{like}", f"%::{like}"),
            ).fetchall()
        for row in rows:
            symbol = _symbol_row(row)
            found.setdefault(symbol.node_id, symbol)
        return sorted(found.values(), key=lambda symbol: (symbol.path, symbol.line, symbol.col, symbol.node_id))

    # -------------------------------------------------------- module lookups

    def _module_file(
        self, store: EventStore, rel: str, *, cache: _PassCache | None = None
    ) -> tuple[str, FileRow | int | None]:
        """Exact-then-suffix file match: ``("hit", row)`` / ``("ambiguous", n)`` / ``("miss", None)``.

        Memoised per pass (B7): every ref of a file imports from the same few
        modules, so the same module path was resolved thousands of times.
        """
        if cache is not None and rel in cache.module_files:
            return cache.module_files[rel]
        result = self._module_file_uncached(store, rel)
        if cache is not None:
            cache.module_files[rel] = result
        return result

    def _module_file_uncached(self, store: EventStore, rel: str) -> tuple[str, FileRow | int | None]:
        """The uncached exact-then-suffix module-file search."""
        exact = store.get_file(rel)
        if exact is not None:
            return ("hit", exact)
        # v6 index path (R2): ``path LIKE '%/rel'`` ⟺ ``reverse(path)`` starts
        # with ``reverse('/' + rel)``; the reversed prefix is a GLOB prefix
        # against ``lower(path_rev)`` (SQLite lower() folds ASCII only — LIKE's
        # exact case semantics), which seeks ``idx_files_path_rev``.
        pattern = _escape_glob(_ascii_lower(("/" + rel)[::-1])) + "*"
        with store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM files WHERE lower(path_rev) GLOB ? ORDER BY path",
                (pattern,),
            ).fetchall()
        if not rows:
            return ("miss", None)
        if len(rows) > 1:
            return ("ambiguous", len(rows))
        return ("hit", _file_row(rows[0]))

    def _symbol_in_file(
        self, store: EventStore, path: str, tail: str, *, cache: _PassCache | None = None
    ) -> tuple[str, SymbolRow | int | None]:
        key = (path, tail)
        if cache is not None and key in cache.symbols_in_file:
            return cache.symbols_in_file[key]
        result = self._symbol_in_file_uncached(store, path, tail)
        if cache is not None:
            cache.symbols_in_file[key] = result
        return result

    @staticmethod
    def _symbol_in_file_uncached(store: EventStore, path: str, tail: str) -> tuple[str, SymbolRow | int | None]:
        matches = [
            symbol
            for symbol in store.symbols_in_path(path)
            if symbol.name == tail or symbol.name.endswith(f".{tail}") or symbol.name.endswith(f"::{tail}")
        ]
        if len(matches) == 1:
            return ("hit", matches[0])
        if not matches:
            return ("miss", None)
        return ("ambiguous", len(matches))

    # ------------------------------------------------- scope tier (SUB-181)

    #: Bound-variable ceiling per ``IN (...)`` probe (SQLite caps the count;
    #: a file importing a thousand modules must not trip it).
    _SCOPE_PATH_CHUNK: int = 400

    def _resolve_scoped(
        self, store: EventStore, ref: UnresolvedRefRow, source_path: str, language: str, *, cache: _PassCache
    ) -> _Decision | None:
        """File scope, then import scope; ``None`` keeps the ambiguity verdict.

        Both scopes ask one question — "is there exactly ONE same-name symbol
        in this scope?" — with a count-first lookup, so a name with 300
        definitions still materialises at most one candidate row (the B7
        cost model). Two or more candidates in a scope is *not* a guess: the
        next scope runs, and if none is decisive the ref keeps failing with
        its original ambiguity reason.
        """
        status, payload = self._same_name_in_paths(store, ref.target_name, (source_path,), cache=cache)
        if status == "hit":
            assert isinstance(payload, SymbolRow)
            return _hit("exact_scoped", payload.node_id)
        imported = self._imported_paths(store, source_path, language, cache=cache)
        if imported:
            status, payload = self._same_name_in_paths(store, ref.target_name, tuple(sorted(imported)), cache=cache)
            if status == "hit":
                assert isinstance(payload, SymbolRow)
                return _hit("exact_scoped", payload.node_id)
        return None

    def _same_name_in_paths(
        self, store: EventStore, name: str, paths: tuple[str, ...], *, cache: _PassCache | None = None
    ) -> tuple[str, SymbolRow | int | None]:
        """``("hit", row)`` only when the path set holds exactly one *name*.

        ``("ambiguous", n)`` / ``("miss", None)`` otherwise — the same
        verdict triple the module lookups use.
        """
        ordered = tuple(sorted({path for path in paths if path}))
        if not ordered:
            return ("miss", None)
        key = (name, ordered)
        if cache is not None and key in cache.same_name_in_paths:
            return cache.same_name_in_paths[key]
        result = self._same_name_in_paths_uncached(store, name, ordered)
        if cache is not None:
            cache.same_name_in_paths[key] = result
        return result

    @classmethod
    def _same_name_in_paths_uncached(
        cls, store: EventStore, name: str, ordered: tuple[str, ...]
    ) -> tuple[str, SymbolRow | int | None]:
        total = 0
        first: SymbolRow | None = None
        for start in range(0, len(ordered), cls._SCOPE_PATH_CHUNK):
            chunk = ordered[start : start + cls._SCOPE_PATH_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            with store.read() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM symbols WHERE name = ? AND path IN ({placeholders})",
                    (name, *chunk),
                ).fetchone()
                total += 0 if row is None else int(row["n"])
                if total > 1:
                    return ("ambiguous", total)
                if total == 1 and first is None:
                    # Fetch only the single decisive row (never the candidate set).
                    hit = conn.execute(
                        f"SELECT * FROM symbols WHERE name = ? AND path IN ({placeholders}) "
                        "ORDER BY path, line, col, node_id LIMIT 1",
                        (name, *chunk),
                    ).fetchone()
                    if hit is not None:
                        first = _symbol_row(hit)
        if total == 0 or first is None:
            return ("miss", None)
        return ("hit", first)

    def _import_targets(self, store: EventStore, source_path: str, *, cache: _PassCache) -> tuple[str, ...]:
        """Module specifiers named by the referring file's ``imports`` refs.

        Read from ``unresolved_refs`` (rows persist after resolution), so the
        scope verdict is a pure function of the store — batch order and earlier
        passes cannot change it.
        """
        cached = cache.import_targets.get(source_path)
        if cached is not None:
            return cached
        with store.read() as conn:
            rows = conn.execute(
                "SELECT DISTINCT target_name FROM unresolved_refs "
                "WHERE kind = 'imports' AND (source_node_id = ? OR source_node_id IN "
                "(SELECT node_id FROM symbols WHERE path = ?)) ORDER BY target_name",
                (f"file:{source_path}", source_path),
            ).fetchall()
        targets = tuple(str(row["target_name"]) for row in rows)
        cache.import_targets[source_path] = targets
        return targets

    def _imported_paths(
        self, store: EventStore, source_path: str, language: str, *, cache: _PassCache
    ) -> frozenset[str]:
        """The store files the referring file imports (tier-(c) rules, memoised)."""
        cached = cache.imported_paths.get(source_path)
        if cached is not None:
            return cached
        paths: set[str] = set()
        if language in self._config.module_languages:
            for target in self._import_targets(store, source_path, cache=cache):
                if language == "python":
                    decision = self._resolve_python_import(store, source_path, target, "imports", cache=cache)
                else:
                    decision = self._resolve_js_import(store, source_path, target, cache=cache)
                if decision is None or decision.reason is not None or decision.target_node_id is None:
                    continue
                path = self._node_path(store, decision.target_node_id)
                if path is not None:
                    paths.add(path)
        result = frozenset(paths)
        cache.imported_paths[source_path] = result
        return result

    @staticmethod
    def _node_path(store: EventStore, node_id: str) -> str | None:
        """The file path a node id lives in (``file:`` nodes or symbols)."""
        if is_file_node_id(node_id):
            return node_id[len("file:") :]
        symbol = store.symbol_by_node(node_id)
        return None if symbol is None else symbol.path

    @staticmethod
    def _relative_import_prefix(source_path: str, dots: int) -> str:
        """The package prefix a leading-dots import is relative to."""
        if not dots:
            return ""
        base = PurePosixPath(source_path).parent
        for _ in range(dots - 1):
            base = base.parent
        return "" if str(base) in ("", ".") else str(base)

    def _resolve_python_import(
        self,
        store: EventStore,
        source_path: str,
        target_name: str,
        edge_kind: str,
        *,
        cache: _PassCache | None = None,
    ) -> _Decision | None:
        dots = len(target_name) - len(target_name.lstrip("."))
        rest = target_name[dots:]
        prefix = self._relative_import_prefix(source_path, dots)
        segments = [segment for segment in rest.split(".") if segment] if rest else []
        # Deterministic split order: longest module + symbol tail first, then
        # the whole target as a module, then (relative only) the package
        # __init__ with the last segment as a symbol (`from . import x`).
        splits: list[tuple[list[str], str | None]] = []
        if len(segments) >= 2:
            splits.append((segments[:-1], segments[-1]))
        if segments:
            splits.append((segments, None))
        if dots:
            splits.append(([], segments[-1] if segments else None))
        fallback: FileRow | None = None
        for module_segments, symbol_tail in splits:
            module_rel = "/".join(([prefix] if prefix else []) + module_segments)
            if not module_rel:
                continue  # root-level relative import has no anchor package
            module_row: FileRow | None = None
            for candidate in (f"{module_rel}{suffix}" for suffix in self._config.python_suffixes):
                status, payload = self._module_file(store, candidate, cache=cache)
                if status == "ambiguous":
                    return _fail(f"ambiguous_module:{payload}")
                if status == "hit":
                    assert isinstance(payload, FileRow)
                    module_row = payload
                    break
            if module_row is None:
                continue
            if symbol_tail is None:
                if edge_kind == "imports":
                    return _hit("import_path", file_node_id(module_row.path))
                continue  # a calls edge never binds a bare module
            symbol_status, symbol_payload = self._symbol_in_file(store, module_row.path, symbol_tail, cache=cache)
            if symbol_status == "hit":
                assert isinstance(symbol_payload, SymbolRow)
                return _hit("import_path", symbol_payload.node_id)
            if symbol_status == "ambiguous":
                return _fail(f"ambiguous_module_symbol:{symbol_payload}")
            if edge_kind != "imports":
                return _fail(f"symbol_not_in_module:{symbol_tail}")
            if fallback is None:
                fallback = module_row  # `from pkg.mod import missing` still binds the module
        if fallback is not None:
            return _hit("import_path", file_node_id(fallback.path))
        return None

    # -------------------------------------------------------- helper tier

    def _resolve_module_helper(
        self, store: EventStore, helper: LanguageHelper, source_path: str, target_name: str
    ) -> _Decision | None:
        """W12 tier: per-language candidate paths, resolved by the store.

        Same discipline as the import-path tier: exact-then-suffix file
        match, ambiguity never guesses (deterministic fail), first hit wins
        in the helper's candidate order.
        """
        for candidate in helper.candidates(target_name, source_path):
            if candidate.is_dir:
                status, payload = self._package_dir(store, candidate.path)
                if status == "ambiguous":
                    return _fail(f"ambiguous_module:{payload}")
                if status == "hit":
                    assert isinstance(payload, str)
                    return _hit("module_helper", file_node_id(payload))
                continue
            status, payload = self._module_file(store, candidate.path)
            if status == "ambiguous":
                return _fail(f"ambiguous_module:{payload}")
            if status == "hit":
                assert isinstance(payload, FileRow)
                return _hit("module_helper", file_node_id(payload.path))
        return None

    def _package_dir(self, store: EventStore, rel: str) -> tuple[str, str | int | None]:
        """Go-style package directory match: one distinct directory wins.

        ``("hit", first_file_path)`` / ``("ambiguous", n_dirs)`` /
        ``("miss", None)`` — multiple files in one directory are normal; the
        anchor is the lexicographically first file (deterministic).
        """
        escaped = _escape_like(rel)
        with store.read() as conn:
            rows = conn.execute(
                "SELECT path FROM files WHERE path LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' ORDER BY path",
                (f"{escaped}/%", f"%/{escaped}/%"),
            ).fetchall()
        if not rows:
            return ("miss", None)
        directories: set[str] = set()
        ordered_paths: list[str] = []
        for row in rows:
            path = str(row["path"])
            # LIKE is a prefilter (its wildcards `_`/`%` may overmatch rel);
            # the literal check is authoritative.
            if path.startswith(f"{rel}/"):
                directories.add(rel)
                ordered_paths.append(path)
                continue
            marker = f"/{rel}/"
            if marker in path:
                directories.add(path[: path.index(marker) + 1 + len(rel)])
                ordered_paths.append(path)
        if not ordered_paths:
            return ("miss", None)
        if len(directories) > 1:
            return ("ambiguous", len(directories))
        return ("hit", ordered_paths[0])

    def _resolve_js_import(
        self, store: EventStore, source_path: str, target_name: str, *, cache: _PassCache | None = None
    ) -> _Decision | None:
        if not (target_name.startswith("./") or target_name.startswith("../")):
            return _fail("external_module")
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), target_name))
        candidates: list[str] = []
        for candidate in (
            [joined]
            + [f"{joined}{extension}" for extension in self._config.js_extensions]
            + [f"{joined}/{index_name}" for index_name in self._config.js_index_names]
        ):
            if candidate not in candidates:
                candidates.append(candidate)
        for candidate in candidates:
            status, payload = self._module_file(store, candidate, cache=cache)
            if status == "ambiguous":
                return _fail(f"ambiguous_module:{payload}")
            if status == "hit":
                assert isinstance(payload, FileRow)
                return _hit("import_path", file_node_id(payload.path))
        return None


def resolve_pending(
    store: EventStore, *, limit: int | None = None, config: ResolverConfig | None = None
) -> BatchReport:
    """One-shot convenience entry point for the pipeline (SUB-89)."""
    return ReferenceResolver(config).resolve_pending(store, limit=limit)
