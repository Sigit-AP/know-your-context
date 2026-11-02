"""Experience-driven re-rank for retrieval v2 (wave W14, innovation I1).

Closes the loop between ``knowledge.record_outcome`` and retrieval ranking:
nodes whose recorded outcomes are positive earn a bounded boost, nodes with
negative outcomes a bounded penalty. The design follows the TokenEconomizer
pattern (learn from real experience, fail open) under the frozen retrieval
contract:

* **Fail-open** — no outcome data, an empty ledger or the kill switch
  (``KYS_EXPERIENCE_RERANK=0``) yield output byte-identical to the baseline.
* **Bounded** — the multiplier is clamped to ``[1 - max_boost, 1 +
  max_boost]`` (``max_boost=0.5`` by default, so ``[0.5, 1.5]``) and
  quantized to 12 decimal places (ROUND_HALF_EVEN), applied only to
  positive base scores (fallback-channel scores <= 0 never move).
* **Deterministic** — pure Decimal arithmetic, no clocks, no randomness;
  journal entries carry no timestamps.
* **Amortized** — outcomes are aggregated once into an indexed sidecar
  ledger (``<store>.experience-ledger.db``); a query costs one indexed
  lookup, not an evidence scan. The frozen store schema is never touched.
* **Audited** — every re-rank call appends one hash-chained journal entry
  naming every decision (node, base score, multiplier, final score, reason).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from know_your_context.domain import quantize_score
from know_your_context.retrieval_v2.lookup import ScoredSymbol
from know_your_context.store import EventStore

__all__ = [
    "ENV_EXPERIENCE_RERANK",
    "GENESIS_HASH",
    "ExperienceConfig",
    "ExperienceError",
    "ExperienceLedger",
    "ExperienceReranker",
    "JournalEntry",
    "JournalVerification",
    "OutcomeSignal",
    "RerankDecision",
    "RerankOutcome",
    "SyncReport",
    "compute_multiplier",
    "experience_enabled",
    "rerank_scored",
    "reranker_from_env",
]

#: Kill switch: ``KYS_EXPERIENCE_RERANK=0`` (or false/no/off) disables the
#: re-ranker; absent or a true spelling enables it. Invalid values are a
#: typed error at construction time — ambiguous operator intent is never
#: guessed (same strictness as ``ops.switches.parse_switch_bool``).
ENV_EXPERIENCE_RERANK = "KYS_EXPERIENCE_RERANK"

_TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

#: Genesis ``prev_hash`` of the re-rank journal chain.
GENESIS_HASH = "0" * 64

_SIDECAR_SUFFIX = ".experience-ledger.db"
_JOURNAL_SCHEMA = 1

_QUANTUM = Decimal("0.000000000001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class ExperienceError(RuntimeError):
    """Invalid experience-rerank configuration or state (fail-closed)."""


def _to_decimal(value: Decimal | str | int, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ExperienceError(f"{label} must be a decimal, not a boolean")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception as error:
            raise ExperienceError(f"{label} is not a decimal: {value!r}") from error
    raise ExperienceError(f"{label} must be a Decimal/str/int, got {type(value).__name__} (floats are banned)")


def experience_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Resolve the kill switch. Absent = enabled; invalid = typed error."""
    import os

    environ: Mapping[str, str] = os.environ if env is None else env
    raw = environ.get(ENV_EXPERIENCE_RERANK)
    if raw is None:
        return True
    lowered = raw.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ExperienceError(
        f"env {ENV_EXPERIENCE_RERANK} is not a boolean ({raw!r}); "
        f"use one of {'/'.join(sorted(_TRUE_VALUES))} or {'/'.join(sorted(_FALSE_VALUES))}"
    )


@dataclass(frozen=True)
class ExperienceConfig:
    """Tuning knobs for the re-ranker (all deterministic, fail-closed).

    ``max_boost`` caps the multiplier at ``1 ± max_boost``; it may never
    exceed ``0.5`` so the frozen contract bound ``[0.5, 1.5]`` always holds.
    ``count_saturation`` damps small samples: a node reaches full effect at
    ``count_saturation`` outcomes. ``min_outcomes`` gates noise below one
    usable signal.
    """

    max_boost: Decimal | str | int = Decimal("0.5")
    count_saturation: int = 5
    min_outcomes: int = 1
    journal_enabled: bool = True

    def __post_init__(self) -> None:
        max_boost = _to_decimal(self.max_boost, "max_boost")
        if not (_ZERO < max_boost <= Decimal("0.5")):
            raise ExperienceError(f"max_boost must be in (0, 0.5], got {max_boost}")
        object.__setattr__(self, "max_boost", max_boost)
        for label, value in (("count_saturation", self.count_saturation), ("min_outcomes", self.min_outcomes)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ExperienceError(f"{label} must be an int >= 1, got {value!r}")
        if not isinstance(self.journal_enabled, bool):
            raise ExperienceError(f"journal_enabled must be a bool, got {self.journal_enabled!r}")


@dataclass(frozen=True)
class OutcomeSignal:
    """Aggregated outcome experience for one node (Decimal-exact)."""

    node_id: str
    outcome_count: int
    positive_count: int
    negative_count: int
    outcome_sum: Decimal
    p: Decimal | None = None
    m: Decimal | None = None
    e: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ExperienceError("node_id must be non-empty")
        for label, value in (
            ("outcome_count", self.outcome_count),
            ("positive_count", self.positive_count),
            ("negative_count", self.negative_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ExperienceError(f"{label} must be an int >= 0, got {value!r}")
        if self.positive_count + self.negative_count > self.outcome_count:
            raise ExperienceError("positive_count + negative_count must be <= outcome_count")
        outcome_sum = _to_decimal(self.outcome_sum, "outcome_sum")
        if abs(outcome_sum) > self.outcome_count:
            raise ExperienceError(f"outcome_sum {outcome_sum} inconsistent with count {self.outcome_count}")
        object.__setattr__(self, "outcome_sum", outcome_sum)
        for label in ("p", "m", "e"):
            value = getattr(self, label)
            if value is not None:
                object.__setattr__(self, label, _to_decimal(value, label))

    @property
    def mean_outcome(self) -> Decimal:
        """Exact mean outcome; ``0`` when no outcomes were recorded."""
        if self.outcome_count == 0:
            return _ZERO
        return self.outcome_sum / Decimal(self.outcome_count)


def compute_multiplier(signal: OutcomeSignal | None, config: ExperienceConfig) -> Decimal:
    """The bounded score multiplier for one node.

    ``1 + max_boost * mean_outcome * min(1, count/count_saturation)``,
    clamped to ``[1 - max_boost, 1 + max_boost]`` and quantized to 12dp.
    Missing or sub-threshold signals yield exactly ``1.000000000000`` —
    the fail-open identity.
    """
    if signal is None or signal.outcome_count < config.min_outcomes or signal.outcome_count == 0:
        return quantize_score(_ONE)
    max_boost = _to_decimal(config.max_boost, "max_boost")  # narrows the declared union for typing
    damping = min(_ONE, Decimal(signal.outcome_count) / Decimal(config.count_saturation))
    raw = _ONE + max_boost * signal.mean_outcome * damping
    clamped = min(max(raw, _ONE - max_boost), _ONE + max_boost)
    return quantize_score(clamped)


@dataclass(frozen=True)
class RerankDecision:
    """The audited decision for one hit: scores are 12dp strings."""

    node_id: str
    base_score: str
    multiplier: str
    final_score: str
    outcome_count: int
    reason: str  # "no-signal" | "boosted" | "penalized" | "neutral" | "non-positive-base"


@dataclass(frozen=True)
class RerankOutcome:
    """Re-ranked hits plus one decision per hit (aligned by position)."""

    hits: tuple[ScoredSymbol, ...]
    decisions: tuple[RerankDecision, ...]


def rerank_scored(
    hits: Sequence[ScoredSymbol],
    signals: Mapping[str, OutcomeSignal],
    config: ExperienceConfig,
) -> RerankOutcome:
    """Apply experience multipliers and re-sort with the canonical order.

    Pure function: same inputs always give the same output. Hits without a
    signal keep their score bit-for-bit; the output order is the frozen
    total order ``(-score, path, line, col, node_id)`` applied to the new
    scores, so a no-op pass returns the input order exactly.
    """
    identity = quantize_score(_ONE)
    scored: list[tuple[ScoredSymbol, RerankDecision]] = []
    for hit in hits:
        signal = signals.get(hit.node_id)
        multiplier = compute_multiplier(signal, config)
        base_decimal = Decimal(str(hit.score))
        base_text = repr(float(base_decimal))
        if signal is None or multiplier == identity:
            reason = "no-signal" if signal is None else "neutral"
            final_decimal = base_decimal
            final_text = base_text
        elif hit.score <= 0.0:
            reason = "non-positive-base"
            final_decimal = base_decimal
            final_text = base_text
            multiplier = identity
        else:
            final_decimal = quantize_score(base_decimal * multiplier)
            final_text = format(final_decimal, "f")
            reason = "boosted" if multiplier > identity else "penalized"
        reranked = ScoredSymbol(
            node_id=hit.node_id,
            name=hit.name,
            kind=hit.kind,
            path=hit.path,
            line=hit.line,
            col=hit.col,
            score=float(final_decimal),
            source=hit.source,
        )
        decision = RerankDecision(
            node_id=hit.node_id,
            base_score=base_text,
            multiplier=format(multiplier, "f"),
            final_score=final_text,
            outcome_count=signal.outcome_count if signal is not None else 0,
            reason=reason,
        )
        scored.append((reranked, decision))
    scored.sort(key=lambda pair: (-pair[0].score, pair[0].path, pair[0].line, pair[0].col, pair[0].node_id))
    return RerankOutcome(
        hits=tuple(pair[0] for pair in scored),
        decisions=tuple(pair[1] for pair in scored),
    )


# --------------------------------------------------------------------- ledger


@dataclass(frozen=True)
class SyncReport:
    """What one ``sync_from_store`` pass did (watermark = evidence event_seq)."""

    evidence_scanned: int
    signals_updated: int
    watermark_before: int
    watermark_after: int


@dataclass(frozen=True)
class JournalEntry:
    """One hash-chained re-rank journal row."""

    seq: int
    prev_hash: str
    entry_hash: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class JournalVerification:
    """Result of re-deriving the journal hash chain."""

    ok: bool
    checked: int
    first_bad_seq: int | None = None


_LEDGER_DDL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS signal ("
    "    node_id TEXT PRIMARY KEY,"
    "    outcome_count INTEGER NOT NULL CHECK (outcome_count >= 0),"
    "    positive_count INTEGER NOT NULL CHECK (positive_count >= 0),"
    "    negative_count INTEGER NOT NULL CHECK (negative_count >= 0),"
    "    outcome_sum TEXT NOT NULL,"
    "    p TEXT,"
    "    m TEXT,"
    "    e TEXT"
    ")",
    "CREATE TABLE IF NOT EXISTS sync_state ("
    "    id INTEGER PRIMARY KEY CHECK (id = 1),"
    "    watermark INTEGER NOT NULL CHECK (watermark >= 0)"
    ")",
    "CREATE TABLE IF NOT EXISTS journal ("
    "    seq INTEGER PRIMARY KEY,"
    "    prev_hash TEXT NOT NULL,"
    "    entry_hash TEXT NOT NULL,"
    "    payload_json TEXT NOT NULL"
    ")",
)


class ExperienceLedger:
    """Sidecar outcome ledger: indexed signals + hash-chained journal.

    Lives next to the store (``<store>.experience-ledger.db``); the frozen
    store schema is never touched. Reads from the store are read-only.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA synchronous = FULL")
            for statement in _LEDGER_DDL:
                self._conn.execute(statement)
            self._conn.execute("INSERT OR IGNORE INTO sync_state (id, watermark) VALUES (1, 0)")
            self._conn.commit()
        except sqlite3.Error as error:
            raise ExperienceError(f"cannot open experience ledger at {self._path!r}: {error}") from error

    @classmethod
    def for_store(cls, store: EventStore) -> ExperienceLedger:
        """Derive the sidecar path from a store (``:memory:`` -> ``:memory:``)."""
        if store.db_path == ":memory:":
            return cls(":memory:")
        return cls(f"{store.db_path}{_SIDECAR_SUFFIX}")

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ExperienceLedger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ sync

    def watermark(self) -> int:
        row = self._conn.execute("SELECT watermark FROM sync_state WHERE id = 1").fetchone()
        return int(row["watermark"]) if row is not None else 0

    def sync_from_store(self, store: EventStore) -> SyncReport:
        """Incrementally aggregate new evidence into indexed signals.

        Only evidence rows with ``event_seq`` above the watermark are read
        (indexed seek); each touched node's aggregate is then recomputed in
        full, so sync is idempotent and self-healing. P/M/E come from the
        node's latest revision. All reads are read-only.
        """
        before = self.watermark()
        with store.read() as conn:
            rows = conn.execute(
                "SELECT node_id, event_seq FROM evidence WHERE event_seq > ? ORDER BY event_seq",
                (before,),
            ).fetchall()
        if not rows:
            return SyncReport(evidence_scanned=0, signals_updated=0, watermark_before=before, watermark_after=before)
        node_ids = sorted({str(row["node_id"]) for row in rows})
        after = max(int(row["event_seq"]) for row in rows)
        with store.read() as conn:
            for node_id in node_ids:
                evidence_rows = conn.execute(
                    "SELECT payload_json FROM evidence WHERE node_id = ? ORDER BY event_seq",
                    (node_id,),
                ).fetchall()
                outcome_sum = Decimal("0")
                positive = 0
                negative = 0
                for evidence_row in evidence_rows:
                    payload = json.loads(str(evidence_row["payload_json"]))
                    outcome = _to_decimal(str(payload["outcome"]), "evidence outcome")
                    outcome_sum += outcome
                    if outcome > 0:
                        positive += 1
                    elif outcome < 0:
                        negative += 1
                revision = conn.execute(
                    "SELECT payload_json FROM node_revision WHERE node_id = ? ORDER BY version DESC LIMIT 1",
                    (node_id,),
                ).fetchone()
                p = m = e = None
                if revision is not None:
                    scores = json.loads(str(revision["payload_json"])).get("scores", {})
                    p = scores.get("p")
                    m = scores.get("m")
                    e = scores.get("e")
                self._conn.execute(
                    "INSERT OR REPLACE INTO signal "
                    "(node_id, outcome_count, positive_count, negative_count, outcome_sum, p, m, e) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node_id,
                        len(evidence_rows),
                        positive,
                        negative,
                        format(quantize_score(outcome_sum), "f"),
                        p,
                        m,
                        e,
                    ),
                )
        self._conn.execute("UPDATE sync_state SET watermark = ? WHERE id = 1", (after,))
        self._conn.commit()
        return SyncReport(
            evidence_scanned=len(rows),
            signals_updated=len(node_ids),
            watermark_before=before,
            watermark_after=after,
        )

    # ------------------------------------------------------------ signals

    def signal(self, node_id: str) -> OutcomeSignal | None:
        row = self._conn.execute("SELECT * FROM signal WHERE node_id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_signal(row)

    def signals_for(self, node_ids: Sequence[str]) -> dict[str, OutcomeSignal]:
        """Batch lookup — one indexed ``IN`` query, no evidence scan."""
        unique = sorted({str(node_id) for node_id in node_ids})
        if not unique:
            return {}
        placeholders = ", ".join("?" for _ in unique)
        rows = self._conn.execute(
            f"SELECT * FROM signal WHERE node_id IN ({placeholders})",  # nosec B608 -- placeholders only
            unique,
        ).fetchall()
        return {str(row["node_id"]): self._row_to_signal(row) for row in rows}

    @staticmethod
    def _row_to_signal(row: sqlite3.Row) -> OutcomeSignal:
        return OutcomeSignal(
            node_id=str(row["node_id"]),
            outcome_count=int(row["outcome_count"]),
            positive_count=int(row["positive_count"]),
            negative_count=int(row["negative_count"]),
            outcome_sum=Decimal(str(row["outcome_sum"])),
            p=Decimal(str(row["p"])) if row["p"] is not None else None,
            m=Decimal(str(row["m"])) if row["m"] is not None else None,
            e=Decimal(str(row["e"])) if row["e"] is not None else None,
        )

    # ------------------------------------------------------------ journal

    def append_journal(self, payload: Mapping[str, Any]) -> str:
        """Append one hash-chained journal entry; returns its entry hash."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        row = self._conn.execute("SELECT MAX(seq) AS top FROM journal").fetchone()
        seq = int(row["top"] or 0) + 1
        prev_hash = GENESIS_HASH
        if seq > 1:
            prev = self._conn.execute("SELECT entry_hash FROM journal WHERE seq = ?", (seq - 1,)).fetchone()
            assert prev is not None  # contiguous sequence by construction
            prev_hash = str(prev["entry_hash"])
        entry_hash = hashlib.sha256(f"{prev_hash}\n{canonical}".encode()).hexdigest()
        self._conn.execute(
            "INSERT INTO journal (seq, prev_hash, entry_hash, payload_json) VALUES (?, ?, ?, ?)",
            (seq, prev_hash, entry_hash, canonical),
        )
        self._conn.commit()
        return entry_hash

    def journal_entries(self) -> tuple[JournalEntry, ...]:
        rows = self._conn.execute(
            "SELECT seq, prev_hash, entry_hash, payload_json FROM journal ORDER BY seq"
        ).fetchall()
        return tuple(
            JournalEntry(
                seq=int(row["seq"]),
                prev_hash=str(row["prev_hash"]),
                entry_hash=str(row["entry_hash"]),
                payload=json.loads(str(row["payload_json"])),
            )
            for row in rows
        )

    def verify_journal(self) -> JournalVerification:
        """Re-derive the chain; the first tampered row is reported by seq."""
        prev_hash = GENESIS_HASH
        checked = 0
        for entry in self.journal_entries():
            checked += 1
            if entry.prev_hash != prev_hash:
                return JournalVerification(ok=False, checked=checked, first_bad_seq=entry.seq)
            canonical = json.dumps(entry.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            expected = hashlib.sha256(f"{prev_hash}\n{canonical}".encode()).hexdigest()
            if entry.entry_hash != expected:
                return JournalVerification(ok=False, checked=checked, first_bad_seq=entry.seq)
            prev_hash = entry.entry_hash
        return JournalVerification(ok=True, checked=checked)


# ------------------------------------------------------------------- reranker


class ExperienceReranker:
    """Binds a ledger and config; re-ranks hits and audits every decision."""

    def __init__(
        self,
        ledger: ExperienceLedger,
        config: ExperienceConfig | None = None,
        *,
        enabled: bool | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._ledger = ledger
        self._config = config if config is not None else ExperienceConfig()
        self._enabled = experience_enabled(env) if enabled is None else bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def config(self) -> ExperienceConfig:
        return self._config

    @property
    def ledger(self) -> ExperienceLedger:
        return self._ledger

    def rerank(self, hits: Sequence[ScoredSymbol], *, query: str = "") -> RerankOutcome:
        """Re-rank ``hits``; disabled -> input returned untouched, no journal."""
        if not self._enabled:
            return RerankOutcome(hits=tuple(hits), decisions=())
        signals = self._ledger.signals_for([hit.node_id for hit in hits])
        outcome = rerank_scored(hits, signals, self._config)
        if self._config.journal_enabled:
            self._ledger.append_journal(
                {
                    "schema": _JOURNAL_SCHEMA,
                    "query": query,
                    "watermark": self._ledger.watermark(),
                    "config": {
                        "max_boost": format(self._config.max_boost, "f"),
                        "count_saturation": self._config.count_saturation,
                        "min_outcomes": self._config.min_outcomes,
                    },
                    "decisions": [
                        {
                            "node_id": decision.node_id,
                            "base_score": decision.base_score,
                            "multiplier": decision.multiplier,
                            "final_score": decision.final_score,
                            "outcome_count": decision.outcome_count,
                            "reason": decision.reason,
                        }
                        for decision in outcome.decisions
                    ],
                }
            )
        return outcome


def reranker_from_env(
    ledger: ExperienceLedger,
    config: ExperienceConfig | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> ExperienceReranker | None:
    """Fail-open wiring: ``None`` when the kill switch disables the feature."""
    if not experience_enabled(env):
        return None
    return ExperienceReranker(ledger, config, enabled=True)
