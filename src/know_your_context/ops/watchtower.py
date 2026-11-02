"""Multi-store status aggregation: one fleet report, one exit code (W12).

:func:`probe_store` opens a single store read-mostly and collects the
operator's at-a-glance facts — reachability, health score, chain and
migration state, watermarks, event counts, stale journals, file size,
projection presence. A probe **never creates** a missing store file and
**never raises**: any failure is contained in the returned
:class:`StoreStatus` (``reachable=False`` or an ``error`` string).

:func:`aggregate_status` probes a fleet and returns a deterministic
:class:`WatchtowerReport` sorted by store name; duplicate names or paths are
a typed :class:`WatchtowerError`. :func:`exit_code` is ``1`` when any store
is unreachable, errored, or fails its chain/migration checks — so a fleet
can gate cron and CI with one command. :func:`render_markdown` renders the
fleet as a stable table for runbooks and chat ops.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from know_your_context.observability import available_migration_versions
from know_your_context.ops import metrics as ops_metrics
from know_your_context.store import EventStore

__all__ = [
    "StoreStatus",
    "StoreTarget",
    "WatchtowerError",
    "WatchtowerReport",
    "aggregate_status",
    "exit_code",
    "probe_store",
    "render_markdown",
]

PROBE_BUSY_TIMEOUT_MS = 1000


class WatchtowerError(RuntimeError):
    """A watchtower contract violation (typed, fail-closed)."""


@dataclass(frozen=True)
class StoreTarget:
    """One fleet member: a name, its store file, optionally its projection."""

    name: str
    store_path: str
    projection_root: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise WatchtowerError("store target name must not be blank")
        if not self.store_path.strip():
            raise WatchtowerError(f"store target {self.name!r} has a blank store path")


@dataclass(frozen=True)
class StoreStatus:
    """The contained outcome of probing one store (success or failure)."""

    name: str
    store_path: str
    reachable: bool
    error: str | None = None
    health_score: int | None = None
    health_max_score: int | None = None
    chain_ok: bool | None = None
    migrations_ok: bool | None = None
    knowledge_high_watermark: int | None = None
    event_count: int | None = None
    stale_journals: int | None = None
    size_bytes: int | None = None
    projection_present: bool | None = None
    projection_watermark_matches: bool | None = None

    @property
    def degraded(self) -> bool:
        """True when the store answers but something is off."""
        if not self.reachable or self.error is not None:
            return True
        if self.chain_ok is False or self.migrations_ok is False:
            return True
        if self.stale_journals:
            return True
        return bool(self.projection_present is True and self.projection_watermark_matches is False)


@dataclass(frozen=True)
class WatchtowerReport:
    """The deterministic fleet verdict."""

    stores: tuple[StoreStatus, ...]

    @property
    def total_stores(self) -> int:
        return len(self.stores)

    @property
    def reachable_count(self) -> int:
        return sum(1 for status in self.stores if status.reachable)

    @property
    def unreachable_names(self) -> tuple[str, ...]:
        return tuple(status.name for status in self.stores if not status.reachable)

    @property
    def degraded_names(self) -> tuple[str, ...]:
        return tuple(status.name for status in self.stores if status.degraded)

    @property
    def total_events(self) -> int:
        return sum(status.event_count or 0 for status in self.stores)

    @property
    def ok(self) -> bool:
        for status in self.stores:
            if not status.reachable or status.error is not None:
                return False
            if status.chain_ok is False or status.migrations_ok is False:
                return False
        return True


def exit_code(report: WatchtowerReport) -> int:
    """The process exit code: 1 when any store is down, errored or corrupt."""
    return 0 if report.ok else 1


# ------------------------------------------------------------------ probing


def probe_store(target: StoreTarget, *, stale_after_seconds: float = 300.0) -> StoreStatus:
    """Probe one store; never creates the file, never raises."""
    path = Path(target.store_path)
    if not path.is_file():
        return StoreStatus(
            name=target.name,
            store_path=target.store_path,
            reachable=False,
            error="store file does not exist",
        )
    store: EventStore | None = None
    try:
        store = EventStore(path, busy_timeout_ms=PROBE_BUSY_TIMEOUT_MS)
    except Exception as error:  # noqa: BLE001 - a probe reports, never raises
        return StoreStatus(
            name=target.name,
            store_path=target.store_path,
            reachable=False,
            error=f"{type(error).__name__}: {error}",
        )
    try:
        collected = ops_metrics.collect_ops_metrics(
            store,
            projection_root=target.projection_root,
            stale_after_seconds=stale_after_seconds,
        )
        health = collected["health"]
        counts = collected["counts"]
        projection = collected["projection"]
        try:
            chain_ok: bool | None = bool(store.verify_chain().ok)
        except Exception:  # noqa: BLE001
            chain_ok = False
        try:
            migrations_ok: bool | None = store.applied_migrations() == available_migration_versions(None)
        except Exception:  # noqa: BLE001
            migrations_ok = False
        size_bytes: int | None = None
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = None
        return StoreStatus(
            name=target.name,
            store_path=target.store_path,
            reachable=True,
            health_score=int(health["score"]),
            health_max_score=int(health["max_score"]),
            chain_ok=chain_ok,
            migrations_ok=migrations_ok,
            knowledge_high_watermark=int(counts["knowledge_high_watermark"]),
            event_count=int(counts["events_total"]),
            stale_journals=int(counts["journals_stale"]),
            size_bytes=size_bytes,
            projection_present=(None if projection is None else bool(projection.get("present"))),
            projection_watermark_matches=(
                None
                if projection is None or not projection.get("present")
                else bool(projection.get("watermark_matches_store"))
            ),
        )
    except Exception as error:  # noqa: BLE001 - a probe reports, never raises
        return StoreStatus(
            name=target.name,
            store_path=target.store_path,
            reachable=True,
            error=f"{type(error).__name__}: {error}",
        )
    finally:
        if store is not None:
            store.close()


def aggregate_status(
    targets: tuple[StoreTarget, ...] | list[StoreTarget],
    *,
    stale_after_seconds: float = 300.0,
) -> WatchtowerReport:
    """Probe the fleet and return the deterministic, name-sorted report."""
    names: set[str] = set()
    paths: set[str] = set()
    for target in targets:
        if target.name in names:
            raise WatchtowerError(f"duplicate store target name {target.name!r}")
        resolved = os.path.realpath(target.store_path)
        if resolved in paths:
            raise WatchtowerError(f"duplicate store path {target.store_path!r}")
        names.add(target.name)
        paths.add(resolved)
    ordered = sorted(targets, key=lambda target: target.name)
    return WatchtowerReport(
        stores=tuple(probe_store(target, stale_after_seconds=stale_after_seconds) for target in ordered)
    )


# ------------------------------------------------------------------ rendering


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def render_markdown(report: WatchtowerReport) -> str:
    """Render the fleet report as a stable markdown table."""
    lines: list[str] = ["# Watchtower Report", ""]
    status = "ok" if report.ok else "degraded"
    lines.append(
        f"Fleet: **{status}** — {report.reachable_count}/{report.total_stores} reachable, "
        f"{report.total_events} events total."
    )
    lines.append("")
    if not report.stores:
        lines.append("No stores configured.")
        lines.append("")
        return "\n".join(lines)
    lines.append("| name | reachable | health | chain | migrations | watermark | events | stale | bytes | projection |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for store in report.stores:
        health = (
            f"{store.health_score}/{store.health_max_score}"
            if store.health_score is not None and store.health_max_score is not None
            else "-"
        )
        projection = (
            "-"
            if store.projection_present is None
            else (
                "ok" if store.projection_watermark_matches else ("absent" if not store.projection_present else "stale")
            )
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    store.name,
                    _cell(store.reachable),
                    health,
                    _cell(store.chain_ok),
                    _cell(store.migrations_ok),
                    _cell(store.knowledge_high_watermark),
                    _cell(store.event_count),
                    _cell(store.stale_journals),
                    _cell(store.size_bytes),
                    projection,
                )
            )
            + " |"
        )
    errored = [store for store in report.stores if store.error]
    if errored:
        lines.append("")
        lines.append("## Errors")
        lines.append("")
        for store in errored:
            lines.append(f"- **{store.name}**: {store.error}")
    lines.append("")
    return "\n".join(lines)
