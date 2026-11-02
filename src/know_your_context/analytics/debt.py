"""Knowledge debt queue: a budgeted, prioritized work queue for the graph (I4).

Nodes with low scores, flagged contradictions, stale access, or old
unresolved references accumulate silently; without a structured queue the
operator cannot answer "which node is the most expensive to leave broken?".
This module detects debt, scores it deterministically
(``priority = severity x impact`` in quantized Decimal), and emits a budgeted
top-K queue whose every item carries a concrete proposal draft
(``improve``/``merge``/``retire``) for the existing proposal-gated
improvement pipeline. Nothing here writes: the module is a pure function of
its inputs, and ``now`` is always injected.

Signal catalog (fixed order; weights are frozen and sum to 1):

1. ``contradiction_flagged`` (0.5) — endpoint of a ``contradicts`` link with
   no ``supersedes`` arc resolving either endpoint (mirrors the corpus
   health audit's ``contradiction_without_resolution`` semantics).
2. ``low_score`` (0.3) — ``scores.confidence`` below the pinned threshold.
3. ``stale_access`` (0.1) — not used for more than ``stale_days`` days
   (``last_used_at``, falling back to ``updated_at`` then ``created_at``).
4. ``unresolved_ref_old`` (0.1) — source of a ``pending``/``failed``
   unresolved reference older than ``unresolved_ref_days``.

Action mapping (deterministic, first match wins): stale + low score →
``retire``; contradiction → ``merge`` (target = the stronger counterpart,
ties break on the smaller id); otherwise → ``improve``. ``retire``/``merge``
drafts are high-impact (``set_status``), so the gated improvement pipeline
requires operator approval before anything changes; ``improve`` drafts are
low-impact (``add_context_tag debt-review``).

Kill switch: ``KYS_DEBT_QUEUE`` — a false spelling (0/false/no/off) or any
unparseable value fails safe to a disabled, empty, typed queue; the queue
announces its disabled state in the payload (``disabled``/``reason``).

Graceful degradation: an unparseable score or timestamp silences that one
signal for that one node — never an error; with no signals at all the queue
is simply empty.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from know_your_context.domain import canonical_json, quantize_score

__all__ = [
    "DEFAULT_BUDGET",
    "DEFAULT_LOW_SCORE_THRESHOLD",
    "DEFAULT_STALE_DAYS",
    "DEFAULT_UNRESOLVED_REF_DAYS",
    "IMPACT_DEGREE_SATURATION",
    "KILL_SWITCH_ENV",
    "MAX_BUDGET",
    "SCHEMA_VERSION",
    "SIGNAL_CATALOG",
    "SIGNAL_WEIGHTS",
    "DebtBudgetError",
    "DebtConfig",
    "DebtConfigError",
    "DebtError",
    "build_debt_queue",
    "build_debt_report",
    "burndown",
    "kill_switch_disabled",
    "queue_snapshot",
    "render_debt_markdown",
]

SCHEMA_VERSION = 1

SIGNAL_CATALOG: tuple[str, ...] = (
    "contradiction_flagged",
    "low_score",
    "stale_access",
    "unresolved_ref_old",
)

#: Frozen severity weights (12-decimal fixed-point strings; they sum to 1).
SIGNAL_WEIGHTS: dict[str, str] = {
    "contradiction_flagged": "0.500000000000",
    "low_score": "0.300000000000",
    "stale_access": "0.100000000000",
    "unresolved_ref_old": "0.100000000000",
}

DEFAULT_LOW_SCORE_THRESHOLD = "0.250000000000"
DEFAULT_STALE_DAYS = 30
DEFAULT_UNRESOLVED_REF_DAYS = 7
DEFAULT_BUDGET = 10
MAX_BUDGET = 100
#: Impact saturates at this degree: impact = (1 + degree) / (1 + saturation).
IMPACT_DEGREE_SATURATION = 10

KILL_SWITCH_ENV = "KYS_DEBT_QUEUE"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

_SECONDS_PER_DAY = 86400


class DebtError(ValueError):
    """Base class for typed debt-queue failures (fail-closed)."""


class DebtBudgetError(DebtError):
    """The per-cycle budget is outside the fail-closed [1, MAX_BUDGET] range."""


class DebtConfigError(DebtError):
    """A debt configuration value failed validation."""


@dataclass(frozen=True)
class DebtConfig:
    """Pinned detector thresholds and the per-cycle budget."""

    low_score_threshold: str = DEFAULT_LOW_SCORE_THRESHOLD
    stale_days: int = DEFAULT_STALE_DAYS
    unresolved_ref_days: int = DEFAULT_UNRESOLVED_REF_DAYS
    budget: int = DEFAULT_BUDGET
    impact_degree_saturation: int = IMPACT_DEGREE_SATURATION

    def __post_init__(self) -> None:
        if not 1 <= int(self.budget) <= MAX_BUDGET:
            raise DebtBudgetError(f"budget must be in [1, {MAX_BUDGET}], got {self.budget}")
        try:
            threshold = Decimal(str(self.low_score_threshold))
        except InvalidOperation as error:
            raise DebtConfigError(f"low_score_threshold {self.low_score_threshold!r} is not a decimal") from error
        if not Decimal(0) <= threshold <= Decimal(1):
            raise DebtConfigError(f"low_score_threshold must be in [0, 1], got {threshold}")
        if int(self.stale_days) < 0:
            raise DebtConfigError(f"stale_days must be >= 0, got {self.stale_days}")
        if int(self.unresolved_ref_days) < 0:
            raise DebtConfigError(f"unresolved_ref_days must be >= 0, got {self.unresolved_ref_days}")
        if int(self.impact_degree_saturation) < 1:
            raise DebtConfigError(f"impact_degree_saturation must be >= 1, got {self.impact_degree_saturation}")


def kill_switch_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Fail-safe kill switch: only an explicit true spelling keeps the queue on."""
    source = os.environ if env is None else env
    raw = source.get(KILL_SWITCH_ENV)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return False
    # False spellings AND unparseable values both disable: a queue the
    # operator tried to turn off must never come back on by accident.
    return True


def _q12(value: Decimal) -> str:
    return format(quantize_score(value), "f")


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are treated as UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _normalize_now(now: str | datetime) -> datetime:
    parsed = _parse_ts(now) if isinstance(now, str) else now
    if parsed is None:
        raise DebtConfigError(f"now {now!r} is not an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _config_echo(config: DebtConfig) -> dict[str, Any]:
    return {
        "low_score_threshold": config.low_score_threshold,
        "stale_days": config.stale_days,
        "unresolved_ref_days": config.unresolved_ref_days,
        "budget": config.budget,
        "impact_degree_saturation": config.impact_degree_saturation,
    }


def build_debt_queue(
    nodes: Iterable[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    *,
    now: str | datetime,
    unresolved_refs: Iterable[Mapping[str, Any]] | None = None,
    config: DebtConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Detect knowledge debt and return the budgeted, prioritized queue.

    ``nodes``/``links`` follow the read-model shapes (``id``, ``version``,
    ``domain``, ``scores``, ``last_used_at``/``updated_at``/``created_at``;
    ``source``/``target``/``relation``). ``unresolved_refs`` follows the
    store's ``unresolved_refs`` rows; omitting it disables that one signal.
    The output is canonical (deterministic key order and stable ordering:
    ``-priority, -severity, node_id``).
    """
    config = config if config is not None else DebtConfig()
    now_dt = _normalize_now(now)
    now_iso = now_dt.isoformat()

    if kill_switch_disabled(env):
        return {
            "schema_version": SCHEMA_VERSION,
            "disabled": True,
            "reason": f"kill switch {KILL_SWITCH_ENV} disabled the debt queue",
            "generated_at": now_iso,
            "config": _config_echo(config),
            "signal_counts": {signal: 0 for signal in SIGNAL_CATALOG},
            "candidate_count": 0,
            "budget": config.budget,
            "truncated": False,
            "overflow_count": 0,
            "items": [],
        }

    node_list = sorted((dict(node) for node in nodes), key=lambda node: str(node.get("id", "")))
    node_ids = {str(node.get("id", "")) for node in node_list}
    link_list = sorted(
        (dict(link) for link in links),
        key=lambda link: (str(link.get("source", "")), str(link.get("target", "")), str(link.get("relation", ""))),
    )

    threshold = Decimal(config.low_score_threshold)
    stale_seconds = config.stale_days * _SECONDS_PER_DAY
    ref_seconds = config.unresolved_ref_days * _SECONDS_PER_DAY

    # --- contradiction endpoints (unresolved = no supersedes arc on either side)
    superseded_targets = {
        str(link.get("target"))
        for link in link_list
        if link.get("relation") == "supersedes" and str(link.get("target")) in node_ids
    }
    contradictions: dict[str, set[str]] = {}
    for link in link_list:
        if link.get("relation") != "contradicts":
            continue
        source, target = str(link.get("source", "")), str(link.get("target", ""))
        if source not in node_ids or target not in node_ids or source == target:
            continue
        if source in superseded_targets or target in superseded_targets:
            continue
        contradictions.setdefault(source, set()).add(target)
        contradictions.setdefault(target, set()).add(source)

    # --- old unresolved refs per source node
    ref_counts: dict[str, int] = {}
    if unresolved_refs is not None:
        for ref in unresolved_refs:
            source = str(ref.get("source_node_id", ""))
            if source not in node_ids:
                continue
            if str(ref.get("status", "")) not in ("pending", "failed"):
                continue
            first_seen = _parse_ts(ref.get("first_seen"))
            if first_seen is None:
                continue
            if (now_dt - first_seen).total_seconds() > ref_seconds:
                ref_counts[source] = ref_counts.get(source, 0) + 1

    # --- degree (connectivity impact; self-loops do not count)
    degree: dict[str, int] = {node_id: 0 for node_id in node_ids}
    for link in link_list:
        source, target = str(link.get("source", "")), str(link.get("target", ""))
        if source == target:
            continue
        if source in degree:
            degree[source] += 1
        if target in degree:
            degree[target] += 1

    candidates: list[dict[str, Any]] = []
    signal_counts = {signal: 0 for signal in SIGNAL_CATALOG}
    for node in node_list:
        node_id = str(node.get("id", ""))
        signals: list[str] = []
        detail: dict[str, Any] = {}

        if node_id in contradictions:
            signals.append("contradiction_flagged")
            detail["contradiction_flagged"] = {
                "counterparts": sorted(contradictions[node_id]),
            }

        scores = node.get("scores")
        confidence_raw = scores.get("confidence", "0") if isinstance(scores, Mapping) else "0"
        try:
            confidence = Decimal(str(confidence_raw))
        except InvalidOperation:
            confidence = None
        if confidence is not None and confidence < threshold:
            signals.append("low_score")
            detail["low_score"] = {
                "observed": str(confidence_raw),
                "threshold": config.low_score_threshold,
            }

        reference_ts = None
        for field_name in ("last_used_at", "updated_at", "created_at"):
            reference_ts = _parse_ts(node.get(field_name))
            if reference_ts is not None:
                break
        if reference_ts is not None and (now_dt - reference_ts).total_seconds() > stale_seconds:
            signals.append("stale_access")
            detail["stale_access"] = {
                "reference": reference_ts.isoformat(),
                "stale_days": config.stale_days,
            }

        if node_id in ref_counts:
            signals.append("unresolved_ref_old")
            detail["unresolved_ref_old"] = {"count": ref_counts[node_id]}

        if not signals:
            continue
        for signal in signals:
            signal_counts[signal] += 1

        severity = quantize_score(sum((Decimal(SIGNAL_WEIGHTS[s]) for s in signals), Decimal(0)))
        node_degree = degree.get(node_id, 0)
        impact = quantize_score(Decimal(1 + node_degree) / Decimal(1 + config.impact_degree_saturation))
        priority = quantize_score(severity * impact)

        action, merge_target, diff, impact_level = _action_for(
            signals, node_id, contradictions.get(node_id, set()), nodes=node_list
        )
        candidates.append(
            {
                "node_id": node_id,
                "version": int(node.get("version", 1)),
                "domain": str(node.get("domain", "")),
                "signals": signals,
                "severity": format(severity, "f"),
                "impact": format(impact, "f"),
                "priority": format(priority, "f"),
                "degree": node_degree,
                "action": action,
                "merge_target": merge_target,
                "proposal": {
                    "action": action,
                    "impact": impact_level,
                    "diff": diff,
                    "rationale": _rationale(action, signals, node_id, merge_target),
                },
                "detail": detail,
            }
        )

    candidates.sort(key=lambda item: (-Decimal(item["priority"]), -Decimal(item["severity"]), item["node_id"]))
    budget = config.budget
    items = []
    for rank, candidate in enumerate(candidates[:budget], start=1):
        items.append({"rank": rank, **candidate})

    return {
        "schema_version": SCHEMA_VERSION,
        "disabled": False,
        "generated_at": now_iso,
        "config": _config_echo(config),
        "signal_counts": signal_counts,
        "candidate_count": len(candidates),
        "budget": budget,
        "truncated": len(candidates) > budget,
        "overflow_count": max(0, len(candidates) - budget),
        "items": items,
    }


def _action_for(
    signals: list[str],
    node_id: str,
    counterparts: set[str],
    *,
    nodes: list[dict[str, Any]],
) -> tuple[str, str | None, list[dict[str, Any]], str]:
    """Deterministic action mapping; returns (action, merge_target, diff, impact)."""
    if "stale_access" in signals and "low_score" in signals:
        return (
            "retire",
            None,
            [{"op": "set_status", "key": "", "value": "deprecate"}],
            "high",
        )
    if "contradiction_flagged" in signals:
        target = _strongest_counterpart(node_id, counterparts, nodes)
        return (
            "merge",
            target,
            [{"op": "set_status", "key": "", "value": "superseded"}],
            "high",
        )
    return (
        "improve",
        None,
        [{"op": "add_context_tag", "key": "", "value": "debt-review"}],
        "low",
    )


def _strongest_counterpart(node_id: str, counterparts: set[str], nodes: list[dict[str, Any]]) -> str | None:
    """The merge target: highest-confidence counterpart, ties break on id."""

    def confidence_of(candidate_id: str) -> Decimal:
        for node in nodes:
            if str(node.get("id", "")) == candidate_id:
                scores = node.get("scores")
                raw = scores.get("confidence", "0") if isinstance(scores, Mapping) else "0"
                try:
                    return Decimal(str(raw))
                except InvalidOperation:
                    return Decimal(0)
        return Decimal(0)

    if not counterparts:
        return None
    # Max confidence; the sorted() input makes ties break on the smaller id.
    return max(sorted(counterparts), key=confidence_of)


def _rationale(action: str, signals: list[str], node_id: str, merge_target: str | None) -> str:
    joined = ", ".join(signals)
    if action == "retire":
        return f"knowledge debt ({joined}): node {node_id} is stale and low-score; propose retirement"
    if action == "merge":
        return (
            f"knowledge debt ({joined}): node {node_id} contradicts {merge_target}; "
            "propose superseding it by the stronger counterpart"
        )
    return f"knowledge debt ({joined}): node {node_id} needs review; tag it for the review cycle"


# ---------------------------------------------------------------------- burndown


def queue_snapshot(queue: Mapping[str, Any], *, cycle: int, recorded_at: str) -> dict[str, Any]:
    """A canonical per-cycle snapshot of the queue for the burn-down ledger."""
    items = queue.get("items", [])
    total = sum((Decimal(str(item["priority"])) for item in items), Decimal(0))
    return {
        "schema_version": SCHEMA_VERSION,
        "cycle": int(cycle),
        "recorded_at": str(recorded_at),
        "candidate_count": int(queue.get("candidate_count", 0)),
        "total_priority": _q12(total),
        "signal_counts": dict(queue.get("signal_counts", {})),
    }


def burndown(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Debt burn-down metrics across cycles (deterministic, Decimal-exact)."""
    series = [
        {
            "cycle": int(snap["cycle"]),
            "candidate_count": int(snap["candidate_count"]),
            "total_priority": str(snap["total_priority"]),
        }
        for snap in snapshots
    ]
    deltas: list[dict[str, Any]] = []
    for previous, current in zip(series, series[1:], strict=False):
        delta_priority = quantize_score(Decimal(current["total_priority"]) - Decimal(previous["total_priority"]))
        deltas.append(
            {
                "from_cycle": previous["cycle"],
                "to_cycle": current["cycle"],
                "delta_count": current["candidate_count"] - previous["candidate_count"],
                "delta_priority": format(delta_priority, "f"),
            }
        )
    if len(series) < 2:
        trend = "insufficient_data"
        net_count = 0
        net_priority = Decimal(0)
    else:
        net_count = series[-1]["candidate_count"] - series[0]["candidate_count"]
        net_priority = Decimal(series[-1]["total_priority"]) - Decimal(series[0]["total_priority"])
        trend = "improving" if net_count < 0 else "worsening" if net_count > 0 else "flat"
    burned = sum(-delta["delta_count"] for delta in deltas if delta["delta_count"] < 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "cycles": len(series),
        "series": series,
        "deltas": deltas,
        "burned_total": burned,
        "net_count_delta": net_count,
        "net_priority_delta": format(quantize_score(net_priority), "f"),
        "trend": trend,
    }


# ------------------------------------------------------------------------ report


def build_debt_report(queue: Mapping[str, Any], *, snapshots: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The canonical debt report: the queue plus burn-down metrics."""
    return {
        "schema_version": SCHEMA_VERSION,
        "queue": dict(queue),
        "burndown": burndown(snapshots),
    }


def render_debt_markdown(report: Mapping[str, Any]) -> str:
    """Deterministic markdown rendering of a debt report."""
    queue = report["queue"]
    lines = ["# Knowledge Debt Report", ""]
    if queue.get("disabled"):
        lines.append(f"**disabled**: {queue.get('reason', '')}")
        lines.append("")
    lines.append(f"generated_at: {queue.get('generated_at', '')}")
    lines.append(
        f"candidates: {queue.get('candidate_count', 0)} "
        f"(budget {queue.get('budget', 0)}, overflow {queue.get('overflow_count', 0)})"
    )
    lines.append("")
    lines.append("## Signals")
    lines.append("")
    for signal in SIGNAL_CATALOG:
        lines.append(f"- {signal}: {queue.get('signal_counts', {}).get(signal, 0)}")
    lines.append("")
    lines.append("## Queue")
    lines.append("")
    items = queue.get("items", [])
    if not items:
        lines.append("no debt candidates")
    else:
        lines.append("| rank | node | action | priority | severity | signals |")
        lines.append("|---|---|---|---|---|---|")
        for item in items:
            lines.append(
                "| {rank} | {node} | {action} | {priority} | {severity} | {signals} |".format(
                    rank=item["rank"],
                    node=item["node_id"],
                    action=item["action"],
                    priority=item["priority"],
                    severity=item["severity"],
                    signals=", ".join(item["signals"]),
                )
            )
    lines.append("")
    lines.append("## Burn-down")
    lines.append("")
    burn = report["burndown"]
    lines.append(f"trend: {burn['trend']} (cycles {burn['cycles']})")
    lines.append(f"net count delta: {burn['net_count_delta']}; burned total: {burn['burned_total']}")
    lines.append("")
    return "\n".join(lines)


# Re-exported so callers can fingerprint reports without importing domain.
_CANONICAL = canonical_json
