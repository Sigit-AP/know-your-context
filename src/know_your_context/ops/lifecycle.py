"""Store lifecycle tooling: health history, retention policies, safe vacuum.

W12 tools surface. Three capabilities, all fail-closed and auditable:

* **Health history** — :func:`record_health_snapshot` appends one
  hash-chained canonical-JSON line per observation to an append-only JSONL
  ledger next to the store (or at an explicit path). The chain makes silent
  truncation or tampering detectable: :func:`load_health_history` verifies
  every line's hash and every ``prev_hash`` link and raises
  :class:`LifecycleError` on any break. :func:`analyze_health_trend` turns
  the verified history into a deterministic, integer-only trend report
  (direction, drop streaks, flaky components, regressions).

* **Retention policy engine** — declarative :class:`RetentionRule`s
  (``max_age_seconds`` / ``max_entries``) evaluated over generic
  :class:`RetentionItem`s produce a deterministic keep/evict plan.
  Protected items are never evicted; the plan is a pure function of
  (items, rules, now), so it is trivially testable and replayable.
  :func:`prune_health_history` applies the engine to the health ledger
  itself: an atomic rewrite (temp file + fsync + ``os.replace`` + dir fsync)
  that re-chains the retained suffix (re-genesis) and reports both head
  hashes. A dry run computes the identical plan without touching the file.

* **Vacuum/compact** — :func:`vacuum_store` reclaims freelist pages without
  ever endangering the live store: ``VACUUM INTO`` a same-directory staging
  file, verify the staging copy (``quick_check`` + event-count/chain-head
  anchor via :func:`know_your_context.backup.inspect_backup`), then swap it
  in through :func:`know_your_context.backup.restore_store`, which reuses
  the F-1 live-store coordination (close → sidecar purge → verified replace
  → in-place reopen). The chain is verified once more on the reopened handle.
  ``:memory:`` stores are refused; every SQLite failure is wrapped in
  :class:`LifecycleError` — never an untyped ``sqlite3.Error``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from know_your_context.backup import BackupError, inspect_backup, restore_store
from know_your_context.domain import canonical_json
from know_your_context.store import EventStore

__all__ = [
    "DEFAULT_HISTORY_FILENAME",
    "HISTORY_SCHEMA_VERSION",
    "VACUUM_FREELIST_PERMILLE_THRESHOLD",
    "FragmentationReport",
    "HealthSnapshot",
    "HealthTrend",
    "LifecycleError",
    "PruneReport",
    "RetentionDecision",
    "RetentionItem",
    "RetentionRule",
    "VacuumReport",
    "analyze_fragmentation",
    "analyze_health_trend",
    "history_path_for_store",
    "load_health_history",
    "plan_generic_retention",
    "prune_health_history",
    "record_health_snapshot",
    "vacuum_store",
]

DEFAULT_HISTORY_FILENAME = "lifecycle-health.jsonl"
HISTORY_SCHEMA_VERSION = 1
GENESIS_PREV_HASH = "0" * 64
VACUUM_FREELIST_PERMILLE_THRESHOLD = 100  # 10% of pages on the freelist


class LifecycleError(RuntimeError):
    """A lifecycle contract violation (typed, fail-closed)."""


# ------------------------------------------------------------------ snapshots


def _require_hex64(value: str, field_name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise LifecycleError(f"{field_name} must be 64 lowercase hex characters, got {value!r}")


def _parse_iso8601(value: str, *, field_name: str = "recorded_at") -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise LifecycleError(f"{field_name} {value!r} is not an ISO-8601 timestamp: {error}") from error
    if parsed.tzinfo is None:
        raise LifecycleError(f"{field_name} {value!r} must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class HealthSnapshot:
    """One hash-chained health observation of a store."""

    schema_version: int
    recorded_at: str
    score: int
    max_score: int
    ok_components: int
    total_components: int
    components: tuple[tuple[str, bool], ...]
    knowledge_high_watermark: int
    event_count: int
    prev_hash: str
    hash: str

    def __post_init__(self) -> None:
        if self.schema_version != HISTORY_SCHEMA_VERSION:
            raise LifecycleError(f"unsupported snapshot schema version {self.schema_version}")
        _parse_iso8601(self.recorded_at)
        if self.score < 0:
            raise LifecycleError(f"snapshot score must be >= 0, got {self.score}")
        if self.max_score < 0:
            raise LifecycleError(f"snapshot max_score must be >= 0, got {self.max_score}")
        if self.score > self.max_score:
            raise LifecycleError(f"snapshot score {self.score} exceeds max_score {self.max_score}")
        if self.total_components < 0 or self.ok_components < 0:
            raise LifecycleError("component counts must be >= 0")
        if self.ok_components > self.total_components:
            raise LifecycleError(f"ok_components {self.ok_components} exceeds total_components {self.total_components}")
        if self.total_components != len(self.components):
            raise LifecycleError(
                f"total_components {self.total_components} does not match {len(self.components)} component entries"
            )
        if self.ok_components != sum(1 for _, ok in self.components if ok):
            raise LifecycleError("ok_components does not match the component flags")
        names = [name for name, _ in self.components]
        if names != sorted(names):
            raise LifecycleError("components must be sorted by name")
        _require_hex64(self.prev_hash, "prev_hash")
        _require_hex64(self.hash, "hash")

    def _unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "recorded_at": self.recorded_at,
            "score": self.score,
            "max_score": self.max_score,
            "ok_components": self.ok_components,
            "total_components": self.total_components,
            "components": [[name, ok] for name, ok in self.components],
            "knowledge_high_watermark": self.knowledge_high_watermark,
            "event_count": self.event_count,
            "prev_hash": self.prev_hash,
        }

    def to_payload(self) -> dict[str, Any]:
        return {**self._unsigned_payload(), "hash": self.hash}

    @staticmethod
    def compute_hash(unsigned_payload: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_json(unsigned_payload).encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, payload: Any) -> HealthSnapshot:
        """Parse and verify one ledger line (fail-closed on any deviation)."""
        if not isinstance(payload, dict):
            raise LifecycleError(f"snapshot payload must be an object, got {type(payload).__name__}")
        required = {
            "schema_version",
            "recorded_at",
            "score",
            "max_score",
            "ok_components",
            "total_components",
            "components",
            "knowledge_high_watermark",
            "event_count",
            "prev_hash",
            "hash",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise LifecycleError(f"snapshot payload is missing field(s): {', '.join(missing)}")
        try:
            schema_version = int(payload["schema_version"])
            score = int(payload["score"])
            max_score = int(payload["max_score"])
            ok_components = int(payload["ok_components"])
            total_components = int(payload["total_components"])
            watermark = int(payload["knowledge_high_watermark"])
            event_count = int(payload["event_count"])
            recorded_at = str(payload["recorded_at"])
            prev_hash = str(payload["prev_hash"])
            hash_value = str(payload["hash"])
        except (TypeError, ValueError) as error:
            raise LifecycleError(f"snapshot payload field types are invalid: {error}") from error
        raw_components = payload["components"]
        if not isinstance(raw_components, list):
            raise LifecycleError("snapshot components must be a list of [name, ok] pairs")
        components: list[tuple[str, bool]] = []
        for entry in raw_components:
            if (
                not isinstance(entry, list)
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not isinstance(entry[1], bool)
            ):
                raise LifecycleError(f"snapshot component entry {entry!r} is not a [name, ok] pair")
            components.append((entry[0], entry[1]))
        snapshot = cls(
            schema_version=schema_version,
            recorded_at=recorded_at,
            score=score,
            max_score=max_score,
            ok_components=ok_components,
            total_components=total_components,
            components=tuple(components),
            knowledge_high_watermark=watermark,
            event_count=event_count,
            prev_hash=prev_hash,
            hash=hash_value,
        )
        expected = cls.compute_hash(snapshot._unsigned_payload())
        if expected != snapshot.hash:
            raise LifecycleError("snapshot hash does not match its payload (tampered or torn record)")
        return snapshot


# ------------------------------------------------------------------ recording


def history_path_for_store(store: EventStore) -> Path:
    """The default ledger path: alongside the store file."""
    if store.db_path == ":memory:":
        raise LifecycleError("an in-memory store has no default history path; pass history_path explicitly")
    return Path(store.db_path).resolve().parent / DEFAULT_HISTORY_FILENAME


def _last_hash(path: Path) -> str:
    history = load_health_history(path)
    return history[-1].hash if history else GENESIS_PREV_HASH


def record_health_snapshot(
    store: EventStore,
    *,
    history_path: str | Path | None = None,
    projection_root: str | Path | None = None,
    now: datetime | None = None,
) -> HealthSnapshot:
    """Append one verified, hash-chained health observation to the ledger.

    Fail-closed: an existing ledger that does not verify raises
    :class:`LifecycleError` *before* anything is appended — a torn or
    tampered history is never extended. The append is flushed and fsynced.
    """
    from know_your_context.ops import metrics as ops_metrics

    if now is None:
        now = datetime.now(UTC)
    path = Path(history_path) if history_path is not None else history_path_for_store(store)
    prev_hash = _last_hash(path) if path.is_file() else GENESIS_PREV_HASH
    collected = ops_metrics.collect_ops_metrics(store, projection_root=projection_root)
    health = collected["health"]
    components = tuple(sorted((str(name), bool(entry["ok"])) for name, entry in health["components"].items()))
    unsigned = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "recorded_at": now.isoformat(),
        "score": int(health["score"]),
        "max_score": int(health["max_score"]),
        "ok_components": sum(1 for _, ok in components if ok),
        "total_components": len(components),
        "components": [[name, ok] for name, ok in components],
        "knowledge_high_watermark": int(store.knowledge_high_watermark()),
        "event_count": int(store.event_count()),
        "prev_hash": prev_hash,
    }
    snapshot = HealthSnapshot(**unsigned, hash=HealthSnapshot.compute_hash(unsigned))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(canonical_json(snapshot.to_payload()) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return snapshot


def load_health_history(path: str | Path) -> tuple[HealthSnapshot, ...]:
    """Load and fully verify the ledger; an absent file is an empty history."""
    ledger = Path(path)
    if not ledger.is_file():
        return ()
    snapshots: list[HealthSnapshot] = []
    with open(ledger, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise LifecycleError(f"history line {line_number} is not valid JSON: {error}") from error
            snapshot = HealthSnapshot.from_payload(payload)
            expected_prev = snapshots[-1].hash if snapshots else GENESIS_PREV_HASH
            if snapshot.prev_hash != expected_prev:
                raise LifecycleError(
                    f"history line {line_number} breaks the hash chain "
                    f"(prev_hash {snapshot.prev_hash[:12]}… != {expected_prev[:12]}…)"
                )
            snapshots.append(snapshot)
    return tuple(snapshots)


# ------------------------------------------------------------------ trend


@dataclass(frozen=True)
class HealthTrend:
    """Deterministic, integer-only trend summary of a verified history."""

    snapshots: int
    first_score: int | None = None
    last_score: int | None = None
    min_score: int | None = None
    max_score_observed: int | None = None
    direction: str = "insufficient-data"  # improving | stable | degrading | insufficient-data
    current_drop_streak: int = 0
    worst_drop_streak: int = 0
    flaky_components: tuple[tuple[str, int], ...] = ()
    regressions: tuple[str, ...] = ()


def analyze_health_trend(history: tuple[HealthSnapshot, ...] | list[HealthSnapshot]) -> HealthTrend:
    """Summarize direction, streaks and flaky components of the history.

    Integer-only and side-effect free: equal inputs always produce an equal
    report, so it is safe for golden fixtures and diff-based monitoring.
    """
    snapshots = tuple(history)
    if len(snapshots) < 2:
        return HealthTrend(snapshots=len(snapshots))
    scores = [snap.score for snap in snapshots]
    streaks: list[int] = []
    current = 0
    for earlier, later in zip(scores, scores[1:], strict=False):
        if later < earlier:
            current += 1
        else:
            if current:
                streaks.append(current)
            current = 0
    if current:
        streaks.append(current)
    current_drop_streak = 0
    for earlier, later in zip(reversed(scores), reversed(scores[:-1]), strict=False):
        if earlier < later:  # walking backwards: later-in-time dropped below the previous
            current_drop_streak += 1
        else:
            break
    failures: dict[str, int] = {}
    for snap in snapshots:
        for name, ok in snap.components:
            if not ok:
                failures[name] = failures.get(name, 0) + 1
    flaky = tuple(sorted(failures.items(), key=lambda item: (-item[1], item[0])))
    first_ok = {name for name, ok in snapshots[0].components if ok}
    last_bad = {name for name, ok in snapshots[-1].components if not ok}
    regressions = tuple(sorted(first_ok & last_bad))
    if scores[-1] > scores[0]:
        direction = "improving"
    elif scores[-1] < scores[0]:
        direction = "degrading"
    else:
        direction = "stable"
    return HealthTrend(
        snapshots=len(snapshots),
        first_score=scores[0],
        last_score=scores[-1],
        min_score=min(scores),
        max_score_observed=max(scores),
        direction=direction,
        current_drop_streak=current_drop_streak,
        worst_drop_streak=max(streaks, default=0),
        flaky_components=flaky,
        regressions=regressions,
    )


# ------------------------------------------------------------------ retention engine


@dataclass(frozen=True)
class RetentionRule:
    """One declarative retention constraint (age and/or entry count)."""

    name: str
    max_age_seconds: int | None = None
    max_entries: int | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise LifecycleError("retention rule name must not be blank")
        if self.max_age_seconds is None and self.max_entries is None:
            raise LifecycleError(f"retention rule {self.name!r} needs at least one constraint")
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise LifecycleError(f"retention rule {self.name!r} max_age_seconds must be >= 0")
        if self.max_entries is not None and self.max_entries < 0:
            raise LifecycleError(f"retention rule {self.name!r} max_entries must be >= 0")


@dataclass(frozen=True)
class RetentionItem:
    """One timestamped retention candidate."""

    key: str
    created_at: str
    protected: bool = False

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise LifecycleError("retention item key must not be blank")


@dataclass(frozen=True)
class RetentionDecision:
    """The deterministic verdict for one item."""

    key: str
    keep: bool
    rule: str | None
    reason: str  # "protected" | "within-policy" | "over-age" | "over-keep"


def plan_generic_retention(
    items: tuple[RetentionItem, ...] | list[RetentionItem],
    rules: tuple[RetentionRule, ...] | list[RetentionRule],
    *,
    now: datetime,
) -> tuple[RetentionDecision, ...]:
    """Compute the keep/evict plan; a pure function of (items, rules, now).

    Age rules run first (strictly older than ``max_age_seconds`` evicts; an
    item exactly at the boundary survives). Count rules then keep the newest
    ``max_entries`` of the survivors, newest-first by ``(created_at, key)``.
    Protected items are never evicted. The output is sorted by key.
    """
    if now.tzinfo is None:
        raise LifecycleError("retention 'now' must be timezone-aware")
    candidates = tuple(items)
    parsed: dict[str, datetime] = {}
    for item in candidates:
        parsed[item.key] = _parse_iso8601(item.created_at, field_name=f"created_at of {item.key!r}")

    evicted: dict[str, tuple[str, str]] = {}  # key -> (rule, reason)
    for rule in rules:
        if rule.max_age_seconds is None:
            continue
        for item in candidates:
            if item.protected or item.key in evicted:
                continue
            age = (now - parsed[item.key]).total_seconds()
            if age > rule.max_age_seconds:
                evicted[item.key] = (rule.name, "over-age")
    for rule in rules:
        if rule.max_entries is None:
            continue
        survivors = [item for item in candidates if not item.protected and item.key not in evicted]
        ranked = sorted(survivors, key=lambda item: (parsed[item.key], item.key), reverse=True)
        for item in ranked[rule.max_entries :]:
            evicted[item.key] = (rule.name, "over-keep")

    decisions: list[RetentionDecision] = []
    for item in candidates:
        if item.protected:
            decisions.append(RetentionDecision(key=item.key, keep=True, rule=None, reason="protected"))
        elif item.key in evicted:
            rule_name, reason = evicted[item.key]
            decisions.append(RetentionDecision(key=item.key, keep=False, rule=rule_name, reason=reason))
        else:
            decisions.append(RetentionDecision(key=item.key, keep=True, rule=None, reason="within-policy"))
    return tuple(sorted(decisions, key=lambda decision: decision.key))


@dataclass(frozen=True)
class PruneReport:
    """The outcome of one health-ledger prune (or its dry run)."""

    dry_run: bool
    removed: int
    kept: int
    previous_head_hash: str | None
    new_head_hash: str | None
    path: str


def prune_health_history(
    path: str | Path,
    *,
    keep: int,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PruneReport:
    """Prune the health ledger to the newest *keep* snapshots.

    The retained suffix is re-chained (its new first line becomes a genesis
    with ``prev_hash`` of zeros) and written atomically: same-directory temp
    file, fsync, ``os.replace``, directory fsync. The report carries both the
    previous and the new head hash so the re-genesis is itself auditable.
    """
    if keep < 0:
        raise LifecycleError(f"prune keep must be >= 0, got {keep}")
    ledger = Path(path)
    history = load_health_history(ledger)
    if not history:
        return PruneReport(
            dry_run=dry_run, removed=0, kept=0, previous_head_hash=None, new_head_hash=None, path=str(ledger)
        )
    removed = max(0, len(history) - keep)
    retained = history[removed:]
    previous_head = history[-1].hash
    if removed == 0:
        return PruneReport(
            dry_run=dry_run,
            removed=0,
            kept=len(history),
            previous_head_hash=previous_head,
            new_head_hash=previous_head,
            path=str(ledger),
        )
    # Re-chain the retained suffix (re-genesis).
    rewritten: list[HealthSnapshot] = []
    prev_hash = GENESIS_PREV_HASH
    for snapshot in retained:
        unsigned = snapshot._unsigned_payload()
        unsigned["prev_hash"] = prev_hash
        rechained = HealthSnapshot(**unsigned, hash=HealthSnapshot.compute_hash(unsigned))
        rewritten.append(rechained)
        prev_hash = rechained.hash
    new_head = rewritten[-1].hash if rewritten else None
    if not dry_run:
        staging = ledger.with_name(f".{ledger.name}.prune-{os.getpid()}")
        try:
            with open(staging, "w", encoding="utf-8") as handle:
                for snapshot in rewritten:
                    handle.write(canonical_json(snapshot.to_payload()) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, ledger)
            fd = os.open(ledger.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if staging.is_file():
                staging.unlink()
    return PruneReport(
        dry_run=dry_run,
        removed=removed,
        kept=len(retained),
        previous_head_hash=previous_head,
        new_head_hash=new_head,
        path=str(ledger),
    )


# ------------------------------------------------------------------ vacuum


@dataclass(frozen=True)
class FragmentationReport:
    """Read-only page/freelist statistics of the store file."""

    page_count: int
    freelist_count: int
    page_size: int
    file_bytes: int
    wal_bytes: int
    fragmentation_permille: int
    vacuum_recommended: bool


def analyze_fragmentation(store: EventStore) -> FragmentationReport:
    """Snapshot page statistics; ``SELECT``/``PRAGMA`` reads only."""
    with store.read() as conn:
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    file_bytes = 0
    wal_bytes = 0
    if store.db_path != ":memory:":
        db_file = Path(store.db_path)
        if db_file.is_file():
            file_bytes = db_file.stat().st_size
        wal_file = db_file.with_name(db_file.name + "-wal")
        if wal_file.is_file():
            wal_bytes = wal_file.stat().st_size
    permille = (freelist_count * 1000) // page_count if page_count else 0
    return FragmentationReport(
        page_count=page_count,
        freelist_count=freelist_count,
        page_size=page_size,
        file_bytes=file_bytes,
        wal_bytes=wal_bytes,
        fragmentation_permille=permille,
        vacuum_recommended=bool(freelist_count) and permille >= VACUUM_FREELIST_PERMILLE_THRESHOLD,
    )


@dataclass(frozen=True)
class VacuumReport:
    """The verified outcome of one safe compaction."""

    path: str
    before_bytes: int
    after_bytes: int
    pages_before: int
    pages_after: int
    freelist_before: int
    freelist_after: int
    verified: bool
    duration_ms: int


def vacuum_store(store: EventStore, *, verify: bool = True) -> VacuumReport:
    """Compact the store file without endangering the live handle.

    Stages the compacted copy with ``VACUUM INTO`` (same directory, hidden
    pid-stamped name), verifies it (``quick_check`` + chain anchor + event
    count parity), then swaps it in through the F-1 coordinated restore
    discipline and verifies the hash chain on the reopened handle. Any
    failure removes the staging file and raises :class:`LifecycleError`;
    the original store is never left half-swapped.
    """
    if store.db_path == ":memory:":
        raise LifecycleError("cannot vacuum an in-memory store")
    db_path = Path(store.db_path)
    started = time.monotonic_ns()
    before = analyze_fragmentation(store)
    expected_events = store.event_count()
    staging = db_path.with_name(f".{db_path.name}.vacuum-{os.getpid()}")
    if staging.exists():
        if staging.is_dir():
            raise LifecycleError(f"staging path {str(staging)!r} exists as a directory; refusing to vacuum")
        staging.unlink()  # stale staging of a crashed vacuum was never swapped in
    try:
        try:
            with store.read() as conn:
                conn.execute("VACUUM INTO ?", (str(staging),))
        except sqlite3.Error as error:
            raise LifecycleError(f"VACUUM INTO staging failed: {error}") from error
        try:
            info = inspect_backup(staging)
        except BackupError as error:
            raise LifecycleError(f"staged vacuum copy failed inspection: {error}") from error
        if not info["ok"]:
            raise LifecycleError("staged vacuum copy failed quick_check; the store was not touched")
        if verify and int(info["event_count"]) != expected_events:
            raise LifecycleError(
                f"staged vacuum copy holds {info['event_count']} events, expected {expected_events}; refusing the swap"
            )
        try:
            restore_store(staging, db_path, overwrite=True)
        except BackupError as error:
            raise LifecycleError(f"atomic swap of the compacted copy failed: {error}") from error
        verified = True
        if verify:
            verified = bool(store.verify_chain().ok)
            if not verified:
                raise LifecycleError("the compacted store failed its hash-chain verification after the swap")
        after = analyze_fragmentation(store)
        return VacuumReport(
            path=str(db_path),
            before_bytes=before.file_bytes,
            after_bytes=after.file_bytes,
            pages_before=before.page_count,
            pages_after=after.page_count,
            freelist_before=before.freelist_count,
            freelist_after=after.freelist_count,
            verified=verified,
            duration_ms=(time.monotonic_ns() - started) // 1_000_000,
        )
    finally:
        if staging.is_file():
            staging.unlink()
