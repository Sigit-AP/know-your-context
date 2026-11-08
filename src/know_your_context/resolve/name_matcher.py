"""Structure-tolerant name matching — the post-primary resolver tier (wave W7).

Adapts the *pattern* of codegraph's ``name-matcher.ts`` (28K LOC,
RESEARCH-COVERAGE-AUDIT §4.3) and its segment-vocabulary search channel
(RESEARCH-QUERY §2.1) to this project's fail-closed resolver cascade —
rewritten, not ported. What survives of the original design:

* identifiers are split into a **segment vocabulary** (camelCase humps,
  ``snake_case`` words, acronym runs, digit boundaries, ``::``/``.``
  separators) so ``parseHTTPResponse`` and ``parse_http_response`` share one
  structure;
* matching is **multi-signal rescoring**: segment overlap (Jaccard +
  query-containment) plus weighted prefix/substring bonuses;
* a **ubiquity ceiling** (codegraph ``AMBIGUOUS_NAME_CEILING = 500``) refuses
  names that match the world, keeping the tier O(bounded) and honest;
* **language-family gating** (codegraph ``gateLanguageMatch``): candidates
  must live in the same language family as the source file.

What is deliberately dropped: fuzzy Levenshtein scoring, receiver-type
inference, per-language scope tables — none of which the schema-v4 store can
ground truthfully.

Fail-closed contract
--------------------
The tier runs *after* the primary cascade (exact/name_tail/import_path) and
only ever retries refs whose persisted failure reason is exactly
``no_resolution``. Ambiguous primary verdicts (``ambiguous_*``) are terminal.
A match requires one candidate scoring at least ``min_score`` with a
``min_margin`` gap to the runner-up; otherwise the ref stays failed,
untouched, so the pass is idempotent by construction. All scores are exact
:class:`fractions.Fraction` values — binary floats never enter the pipeline.

Batch discipline mirrors :class:`~know_your_context.resolve.resolver.ReferenceResolver`:
one pure read phase, one ``add_graph_edges`` transaction, then one
``record_resolution_attempt`` per hit (``retry_failed_ref`` first, because the
store requires attempts to start from ``pending``). A crash between the write
phases replays cleanly: edge identity dedups and the ref is simply retried.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import PurePosixPath
from typing import Literal

from know_your_context.domain import canonical_json
from know_your_context.graph_model import is_file_node_id
from know_your_context.resolve.resolver import REF_KIND_TO_EDGE_KIND
from know_your_context.store import EventStore, SymbolRow, UnresolvedRefRow

__all__ = [
    "NAME_MATCHER_VERSION",
    "PROVENANCE_NAME_MATCH",
    "NameMatch",
    "NameMatchConfig",
    "NameMatchError",
    "NameMatchOutcome",
    "NameMatchReport",
    "NameMatchResolver",
    "match_name",
    "score_name_pair",
    "split_identifier",
]

NAME_MATCHER_VERSION: int = 1

#: Provenance stamp on every name-match-minted edge — distinct from both the
#: parse stamp (``ingest:*``) and the primary resolver stamp, so audit can
#: always tell *which* tier answered.
PROVENANCE_NAME_MATCH: str = f"resolver:name-match:v{NAME_MATCHER_VERSION}"

#: The only primary failure reason this tier retries (fail-closed).
_RETRYABLE_REASON: str = "no_resolution"

#: Extension → language fallback, mirroring the resolver's table (private to
#: it, so duplicated here deliberately — same values, same purpose).
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

_JS_FAMILY: frozenset[str] = frozenset({"javascript", "javascriptreact", "typescript", "typescriptreact"})

#: codegraph LANGUAGE_FAMILY: JS and TS may see each other; everything else
#: matches within its own language only.
_LANGUAGE_FAMILIES: dict[str, frozenset[str]] = {
    "python": frozenset({"python"}),
    "javascript": _JS_FAMILY,
    "javascriptreact": _JS_FAMILY,
    "typescript": _JS_FAMILY,
    "typescriptreact": _JS_FAMILY,
}


class NameMatchError(ValueError):
    """Invalid matcher configuration or input (fail-closed)."""


# ------------------------------------------------------------- segment vocab


def _case_splits(chunk: str) -> Iterable[str]:
    """Split one alphanumeric chunk on case/digit boundaries.

    Boundaries: lower/digit → upper (``parseHTTP``), acronym tail
    (``HTTPResponse`` → ``HTTP`` + ``Response``), and letter ↔ digit.
    """
    start = 0
    length = len(chunk)
    for index in range(1, length):
        previous = chunk[index - 1]
        current = chunk[index]
        following = chunk[index + 1] if index + 1 < length else ""
        boundary = (
            (previous.islower() and current.isupper())
            or (previous.isalpha() != current.isalpha())
            or (previous.isupper() and current.isupper() and following.islower())
        )
        if boundary:
            yield chunk[start:index]
            start = index
    yield chunk[start:]


def split_identifier(name: str) -> tuple[str, ...]:
    """Split an identifier into its lowercase segment vocabulary.

    Separators (``_``, ``.``, ``::``, ``-``, ``/`` …) split first, then each
    chunk splits on camelCase/acronym/digit boundaries. Empty segments are
    dropped. Deterministic and total over all strings.
    """
    if not isinstance(name, str):
        raise NameMatchError(f"name must be a str, got {type(name).__name__}")
    segments: list[str] = []
    chunk: list[str] = []
    chunks: list[str] = []
    for character in name:
        if character.isalnum():
            chunk.append(character)
        elif chunk:
            chunks.append("".join(chunk))
            chunk = []
    if chunk:
        chunks.append("".join(chunk))
    for piece in chunks:
        for segment in _case_splits(piece):
            lowered = segment.lower()
            if lowered:
                segments.append(lowered)
    return tuple(segments)


# ------------------------------------------------------------------- scoring


def _check_fraction(value: object, label: str) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise NameMatchError(f"{label} must be a Fraction/int/str, never a binary float")
    try:
        result = Fraction(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise NameMatchError(f"{label} is not a rational value: {value!r}") from error
    if not (Fraction(0) <= result <= Fraction(1)):
        raise NameMatchError(f"{label} must be within [0, 1], got {result}")
    return result


def _check_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NameMatchError(f"{label} must be an int >= 1, got {value!r}")
    return value


@dataclass(frozen=True)
class NameMatchConfig:
    """Tuning knobs for the matcher (all deterministic, all validated)."""

    min_score: Fraction = Fraction(3, 4)
    min_margin: Fraction = Fraction(1, 20)
    candidate_cap: int = 500  # codegraph AMBIGUOUS_NAME_CEILING
    max_query_segments: int = 8
    prefix_bonus: Fraction = Fraction(1, 10)
    substring_bonus: Fraction = Fraction(1, 20)

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_score", _check_fraction(self.min_score, "min_score"))
        object.__setattr__(self, "min_margin", _check_fraction(self.min_margin, "min_margin"))
        object.__setattr__(self, "prefix_bonus", _check_fraction(self.prefix_bonus, "prefix_bonus"))
        object.__setattr__(self, "substring_bonus", _check_fraction(self.substring_bonus, "substring_bonus"))
        _check_int(self.candidate_cap, "candidate_cap")
        _check_int(self.max_query_segments, "max_query_segments")


def score_name_pair(query: str, candidate: str, *, config: NameMatchConfig | None = None) -> Fraction:
    """Score one (query, candidate) pair in ``[0, 1]`` as an exact fraction.

    Signals (weights fixed by convention, mirrors codegraph's multi-signal
    rescoring in spirit):

    * exact case-insensitive equality → 1;
    * segment overlap: ``(jaccard + 2 * query-containment) / 3`` — a query
      whose segments are all present in the candidate is strong evidence;
    * ``prefix_bonus`` when the candidate starts with the query text, else
      ``substring_bonus`` when the query text occurs inside the candidate.
    """
    config = config if config is not None else NameMatchConfig()
    if not query or not candidate:
        return Fraction(0)
    query_lower = query.lower()
    candidate_lower = candidate.lower()
    if query_lower == candidate_lower:
        return Fraction(1)
    query_segments = frozenset(split_identifier(query))
    candidate_segments = frozenset(split_identifier(candidate))
    if not query_segments or not candidate_segments:
        return Fraction(0)
    overlap = len(query_segments & candidate_segments)
    if overlap == 0:
        return Fraction(0)
    jaccard = Fraction(overlap, len(query_segments | candidate_segments))
    containment = Fraction(overlap, len(query_segments))
    base = (jaccard + 2 * containment) / 3
    if candidate_lower.startswith(query_lower):
        bonus = config.prefix_bonus
    elif query_lower in candidate_lower:
        bonus = config.substring_bonus
    else:
        bonus = Fraction(0)
    return min(Fraction(1), base + bonus)


@dataclass(frozen=True)
class NameMatch:
    """The deterministic winner of one matching round."""

    node_id: str
    name: str
    score: Fraction
    margin: Fraction  # score minus the runner-up's score (full score when alone)


def match_name(
    query: str,
    candidates: Iterable[tuple[str, str]],
    *,
    config: NameMatchConfig | None = None,
) -> NameMatch | None:
    """Pick the unique decisive candidate for ``query`` or return ``None``.

    ``candidates`` is an iterable of ``(node_id, name)``. Fail-closed: no
    candidate above ``min_score`` → ``None``; a runner-up within
    ``min_margin`` of the top → ``None`` (ambiguity never guesses).
    """
    config = config if config is not None else NameMatchConfig()
    scored: dict[str, tuple[str, Fraction]] = {}
    for node_id, name in candidates:
        if node_id in scored:
            continue
        score = score_name_pair(query, name, config=config)
        if score >= config.min_score:
            scored[node_id] = (name, score)
    if not scored:
        return None
    ordered = sorted(scored.items(), key=lambda item: (-item[1][1], item[0]))
    top_id, (top_name, top_score) = ordered[0]
    runner_up = ordered[1][1][1] if len(ordered) > 1 else Fraction(0)
    margin = top_score - runner_up
    if len(ordered) > 1 and margin < config.min_margin:
        return None
    return NameMatch(node_id=top_id, name=top_name, score=top_score, margin=margin)


# ------------------------------------------------------------ store-bound tier


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _symbol_row(row: sqlite3.Row) -> SymbolRow:
    return SymbolRow(
        node_id=str(row["node_id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        path=str(row["path"]),
        line=int(row["line"]),
        col=int(row["col"]),
    )


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


#: ``IN (...)`` chunk size for the batched reads (WS-D) — far below SQLite's
#: host-parameter limit (32766 on every supported build).
_IN_QUERY_CHUNK = 500

_ASCII_LOWER_TABLE = str.maketrans({chr(c): chr(c + 32) for c in range(ord("A"), ord("Z") + 1)})


def _ascii_lower(value: str) -> str:
    """SQLite ``lower()`` semantics exactly: ASCII-only case fold.

    Used to key the batched exact-name pool — both sides of the historical
    ``lower(s.name) = lower(?)`` predicate fold identically under it.
    """
    return value.translate(_ASCII_LOWER_TABLE)


def _chunked_unique(values: Iterable[str]) -> Iterator[list[str]]:
    """First-occurrence-order unique chunks of at most ``_IN_QUERY_CHUNK``."""
    seen: set[str] = set()
    chunk: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        chunk.append(value)
        if len(chunk) >= _IN_QUERY_CHUNK:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


@dataclass(frozen=True)
class NameMatchOutcome:
    """The deterministic verdict for one retried ref."""

    ref_id: int
    status: Literal["resolved", "unmatched"]
    target_node_id: str | None
    score: Fraction | None
    reason: str | None  # report-only for unmatched; the store row is untouched


@dataclass(frozen=True)
class NameMatchReport:
    """Per-pass counts; ``outcomes`` is sorted by ``ref_id``."""

    processed: int
    resolved: int
    unmatched: int
    edges_inserted: int
    outcomes: tuple[NameMatchOutcome, ...]


def _validate_limit(value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NameMatchError(f"limit must be None or an int >= 1, got {value!r}")


def _edge_payloads(decisions: list[tuple[UnresolvedRefRow, NameMatch | None, str | None]]) -> list[dict[str, object]]:
    """One name-match edge payload per decisive hit, in decision order."""
    payloads: list[dict[str, object]] = []
    for ref, match, _reason in decisions:
        if match is None:
            continue
        payloads.append(
            {
                "source": ref.source_node_id,
                "target": match.node_id,
                "kind": REF_KIND_TO_EDGE_KIND[ref.kind],
                "metadata": canonical_json(
                    {
                        "ref_id": ref.ref_id,
                        "ref_name": ref.target_name,
                        "strategy": "name_match",
                        "score": f"{match.score.numerator}/{match.score.denominator}",
                    }
                ),
                "provenance": PROVENANCE_NAME_MATCH,
            }
        )
    return payloads


def _record_outcomes(
    store: EventStore, decisions: list[tuple[UnresolvedRefRow, NameMatch | None, str | None]]
) -> tuple[NameMatchOutcome, ...]:
    """Write the resolution for each decisive hit; outcomes sorted by ref_id."""
    outcomes: list[NameMatchOutcome] = []
    for ref, match, reason in decisions:
        if match is None:
            outcomes.append(
                NameMatchOutcome(
                    ref_id=ref.ref_id,
                    status="unmatched",
                    target_node_id=None,
                    score=None,
                    reason=reason,
                )
            )
            continue
        # The store requires attempts to start from pending.
        store.retry_failed_ref(ref.ref_id)
        store.record_resolution_attempt(ref.ref_id, outcome="resolved")
        outcomes.append(
            NameMatchOutcome(
                ref_id=ref.ref_id,
                status="resolved",
                target_node_id=match.node_id,
                score=match.score,
                reason=None,
            )
        )
    outcomes.sort(key=lambda outcome: outcome.ref_id)
    return tuple(outcomes)


class NameMatchResolver:
    """Retries ``no_resolution`` failures with structure-tolerant matching."""

    def __init__(self, config: NameMatchConfig | None = None) -> None:
        self._config = config if config is not None else NameMatchConfig()

    @property
    def config(self) -> NameMatchConfig:
        return self._config

    def resolve_unmatched(self, store: EventStore, *, limit: int | None = None) -> NameMatchReport:
        """One bounded pass over refs the primary cascade failed ``no_resolution``.

        Refs the matcher cannot resolve stay failed with their original
        reason — the store is only ever mutated on a decisive hit, which is
        what makes the pass idempotent.

        WS-D: reads are batched — one symbols/files join per ref CLASS, one
        exact-name and one LIKE query per DISTINCT lookup/segment per
        language family (was: 4+ queries per ref; measured 61.816 SQL
        executions for 13.426 refs). Decisions are replayed per ref through
        the historical gate/cap/scoring logic, so outcomes (and the write
        order below) are byte-identical to the per-ref queries.
        """
        _validate_limit(limit)
        refs = self._retryable_refs(store, limit)
        decisions = self._decide_all(store, refs)
        edge_payloads = _edge_payloads(decisions)
        edges_inserted = store.add_graph_edges(edge_payloads) if edge_payloads else 0
        outcomes = _record_outcomes(store, decisions)
        resolved = sum(1 for outcome in outcomes if outcome.status == "resolved")
        return NameMatchReport(
            processed=len(outcomes),
            resolved=resolved,
            unmatched=len(outcomes) - resolved,
            edges_inserted=edges_inserted,
            outcomes=outcomes,
        )

    # ---------------------------------------------------------- internals

    def _decide_all(
        self, store: EventStore, refs: list[UnresolvedRefRow]
    ) -> list[tuple[UnresolvedRefRow, NameMatch | None, str | None]]:
        """One ``(ref, match, reason)`` per ref, in ref order.

        Batched reads (WS-D): languages join once per run; candidate pools
        load one query per DISTINCT lookup/segment per language family (was:
        4+ queries per ref; measured 61.816 SQL executions for 13.426 refs).
        Decisions replay the historical gate/cap/scoring logic per ref and
        are cached per ``(kind, target_name, name_tail, family)``, so the
        results are byte-identical to the per-ref queries.
        """
        languages = self._source_languages(store, refs)
        gates = [self._gate(ref, languages.get(ref.source_node_id)) for ref in refs]
        pools: dict[tuple[str, ...], tuple[dict[str, list[SymbolRow]], dict[str, list[SymbolRow]]]] = {}
        grouped: dict[tuple[str, ...], list[UnresolvedRefRow]] = {}
        for ref, (family, _reason) in zip(refs, gates, strict=True):
            if family is not None:
                grouped.setdefault(tuple(sorted(family)), []).append(ref)
        for family_key, members in grouped.items():
            lookups = {ref.name_tail or ref.target_name for ref in members}
            segments: set[str] = set()
            for ref in members:
                segments.update(self._segments(ref))
            pools[family_key] = self._load_candidate_pools(store, frozenset(family_key), lookups, segments)
        decisions: list[tuple[UnresolvedRefRow, NameMatch | None, str | None]] = []
        decision_cache: dict[tuple[str, str, str, tuple[str, ...]], tuple[NameMatch | None, str | None]] = {}
        for ref, (family, reason) in zip(refs, gates, strict=True):
            if family is None:
                decisions.append((ref, None, reason))
                continue
            family_key = tuple(sorted(family))
            cache_key = (ref.kind, ref.target_name, ref.name_tail, family_key)
            if cache_key not in decision_cache:
                exact, segment_pools = pools[family_key]
                decision_cache[cache_key] = self._decide(ref, exact, segment_pools)
            match, reason = decision_cache[cache_key]
            decisions.append((ref, match, reason))
        return decisions

    def _retryable_refs(self, store: EventStore, limit: int | None) -> list[UnresolvedRefRow]:
        sql = "SELECT * FROM unresolved_refs WHERE status = 'failed' AND failure_reason = ? ORDER BY first_seen, ref_id"
        parameters: tuple[object, ...] = (_RETRYABLE_REASON,)
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (_RETRYABLE_REASON, limit)
        with store.read() as conn:
            rows = conn.execute(sql, parameters).fetchall()
        return [_ref_row(row) for row in rows]

    def _source_languages(self, store: EventStore, refs: list[UnresolvedRefRow]) -> dict[str, str | None]:
        """Batched ``_source_language``: two chunked reads for the whole run.

        Replays the historical per-ref rule exactly: the ref's own symbol
        gives the path; a bare ``file:<path>`` source falls back to its file
        row; the file's declared language wins unless ``unknown``, else the
        extension map decides.
        """
        node_ids = [ref.source_node_id for ref in refs]
        symbol_paths: dict[str, str] = {}
        with store.read() as conn:
            for chunk in _chunked_unique(node_ids):
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT node_id, path FROM symbols WHERE node_id IN ({placeholders})",  # nosec B608 -- placeholders are generated '?' marks, values bound
                    chunk,
                ).fetchall()
                for row in rows:
                    symbol_paths[str(row["node_id"])] = str(row["path"])
        candidate_paths: set[str] = set(symbol_paths.values())
        for node_id in node_ids:
            if node_id not in symbol_paths and is_file_node_id(node_id):
                candidate_paths.add(node_id[len("file:") :])
        file_languages: dict[str, str] = {}
        with store.read() as conn:
            for chunk in _chunked_unique(candidate_paths):
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT path, language FROM files WHERE path IN ({placeholders})",  # nosec B608 -- placeholders are generated '?' marks, values bound
                    chunk,
                ).fetchall()
                for row in rows:
                    file_languages[str(row["path"])] = str(row["language"])
        languages: dict[str, str | None] = {}
        for node_id in node_ids:
            path = symbol_paths.get(node_id)
            if path is None and is_file_node_id(node_id):
                candidate = node_id[len("file:") :]
                if candidate in file_languages:
                    path = candidate
            if path is None:
                languages[node_id] = None
                continue
            declared = file_languages.get(path)
            if declared is not None and declared != "unknown":
                languages[node_id] = declared
            else:
                languages[node_id] = _EXTENSION_LANGUAGES.get(PurePosixPath(path).suffix)
        return languages

    def _gate(self, ref: UnresolvedRefRow, language: str | None) -> tuple[frozenset[str] | None, str | None]:
        """``(family, early reason)`` — the pre-candidate gates of a ref."""
        if ref.kind not in REF_KIND_TO_EDGE_KIND:
            return None, "unknown_ref_kind"
        if language is None:
            return None, "unknown_source"
        family = _LANGUAGE_FAMILIES.get(language)
        if family is None:
            return None, "unknown_language"
        return family, None

    def _segments(self, ref: UnresolvedRefRow) -> list[str]:
        """The ref's query segments (deduped, capped) — the historical recipe."""
        segments = list(dict.fromkeys(split_identifier(ref.name_tail or ref.target_name)))
        return segments[: self._config.max_query_segments]

    def _load_candidate_pools(
        self,
        store: EventStore,
        family: frozenset[str],
        lookups: set[str],
        segments: set[str],
    ) -> tuple[dict[str, list[SymbolRow]], dict[str, list[SymbolRow]]]:
        """Per-family pools: exact-name matches + per-segment LIKE matches.

        One query per DISTINCT lookup/segment — the historical SQL verbatim,
        the segment pool capped at ``candidate_cap + 1`` rows (the most any
        per-ref query could ever fetch, since ``len(found) >= 0``), ordered
        identically, so the per-ref replay below observes the same rows.
        """
        languages = sorted(family)
        language_placeholders = ", ".join("?" for _ in languages)
        exact: dict[str, list[SymbolRow]] = {}
        segment_pools: dict[str, list[SymbolRow]] = {}
        with store.read() as conn:
            for chunk in _chunked_unique(sorted(lookups)):
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT s.*, lower(s.name) AS folded FROM symbols s JOIN files f ON f.path = s.path "
                    f"WHERE lower(s.name) IN ({placeholders}) AND f.language IN ({language_placeholders}) "  # nosec B608 -- placeholders are generated '?' marks, values bound
                    "ORDER BY s.path, s.line, s.col, s.node_id",
                    (*chunk, *languages),
                ).fetchall()
                for row in rows:
                    exact.setdefault(str(row["folded"]), []).append(_symbol_row(row))
            for segment in sorted(segments):
                rows = conn.execute(
                    "SELECT s.* FROM symbols s JOIN files f ON f.path = s.path "
                    f"WHERE lower(s.name) LIKE ? ESCAPE '\\' AND f.language IN ({language_placeholders}) "  # nosec B608 -- placeholders are generated '?' marks, values bound
                    "ORDER BY s.path, s.line, s.col, s.node_id LIMIT ?",
                    (f"%{_escape_like(segment.lower())}%", *languages, self._config.candidate_cap + 1),
                ).fetchall()
                segment_pools[segment] = [_symbol_row(row) for row in rows]
        return exact, segment_pools

    def _candidates_from_pools(
        self,
        ref: UnresolvedRefRow,
        exact: dict[str, list[SymbolRow]],
        segment_pools: dict[str, list[SymbolRow]],
    ) -> tuple[list[SymbolRow], bool]:
        """Replay of the historical per-ref accumulation against the pools.

        Identical rule: exact matches first, then per-segment ordered rows
        with the fail-closed cap; ``len(rows) > cap - len(found)`` evaluates
        against the same prefix the old ``LIMIT cap+1-len(found)`` returned
        (the pool holds the first ``cap + 1`` rows in the same order).
        """
        found: dict[str, SymbolRow] = {}
        capped = False
        cap = self._config.candidate_cap
        for symbol in exact.get(_ascii_lower(ref.name_tail or ref.target_name), []):
            found.setdefault(symbol.node_id, symbol)
        for segment in self._segments(ref):
            if len(found) >= cap:
                capped = True
                break
            rows = segment_pools.get(segment, [])[: cap + 1 - len(found)]
            if len(rows) > cap - len(found):
                capped = True
            for symbol in rows:
                if len(found) >= cap:
                    capped = True
                    break
                found.setdefault(symbol.node_id, symbol)
            if capped:
                break
        return sorted(found.values(), key=lambda s: (s.path, s.line, s.col, s.node_id)), capped

    def _decide(
        self,
        ref: UnresolvedRefRow,
        exact: dict[str, list[SymbolRow]],
        segment_pools: dict[str, list[SymbolRow]],
    ) -> tuple[NameMatch | None, str | None]:
        """``(match, report-only reason)`` — pure reads over the pools."""
        candidates, capped = self._candidates_from_pools(ref, exact, segment_pools)
        if capped:
            return None, "candidate_cap_exceeded"
        if not candidates:
            return None, "no_candidate"
        # Score the written form; for qualified targets also score the tail
        # and keep the stronger verdict (both deterministic).
        queries = [ref.target_name]
        if ref.name_tail and ref.name_tail != ref.target_name:
            queries.append(ref.name_tail)
        best: NameMatch | None = None
        for query in queries:
            match = match_name(query, ((c.node_id, c.name) for c in candidates), config=self._config)
            if match is not None and (best is None or match.score > best.score):
                best = match
        if best is None:
            return None, "below_threshold_or_ambiguous"
        return best, None
