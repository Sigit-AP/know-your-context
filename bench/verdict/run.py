#!/usr/bin/env python3
"""VERDICT 7-level — machine-measured verdict vs codegraph and graphify.

Runs every metric in ``bench/verdict/metrics.py`` on all three repos (nothing
is hand-written), computes per-level relative percentages
(``ours / opponent * 100``) and a weighted composite across the 7 levels —
per-opponent only; the two references are never merged into one figure.
Raw values AND the exact measuring command per metric are
recorded in a JSON ledger.

Usage:
    uv run python bench/verdict/run.py run                    # full run, writes results/
    uv run python bench/verdict/run.py check                  # static validation only
    uv run python bench/verdict/run.py print                  # markdown from last ledger
    uv run python bench/verdict/run.py metric <id> --repo <ours|codegraph|graphify>

Exit codes: 0 = ok; 1 = check failed; 2 = usage error.
Numbers are reported AS MEASURED — the instrument never rounds a verdict up,
caps an opponent at zero-advantage, or hides an unbounded ratio (opponent 0,
ours > 0 is reported as null + counted as ``unbounded``, never as a number).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metrics import (  # noqa: E402
    LEVELS,
    METRIC_BY_ID,
    METRICS,
    RepoData,
    measure_all,
    repo_specs,
)

SUITE = "verdict-7-level"
VERSION = "1.0.0"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
OPPONENTS = ("codegraph", "graphify")


# ------------------------------------------------------------------ scoring
def relative(ours: float, opp: float) -> float | None:
    """100 * ours / opp. None = unbounded (opp 0, ours > 0). Parity at 0/0."""
    if opp == 0:
        return 100.0 if ours == 0 else None
    return round(100.0 * ours / opp, 4)


def compute_scores(values: dict[str, dict[str, float]]) -> dict:
    """Pure scoring over raw values. Deterministic; no I/O."""
    levels: dict[str, dict] = {}
    for level in LEVELS:
        metrics = [m for m in METRICS if m.level == level.id]
        per_metric: dict[str, dict] = {}
        for opp in OPPONENTS:
            rels: list[float] = []
            unbounded = 0
            for m in metrics:
                v = values[m.id]
                r = relative(v["ours"], v[opp])
                per_metric.setdefault(m.id, {})[opp] = r
                if r is None:
                    unbounded += 1
                else:
                    rels.append(r)
            mean = round(sum(rels) / len(rels), 4) if rels else None
            levels.setdefault(level.id, {"weight": level.weight, "metrics": per_metric, "relative_percent_vs": {}})[
                "relative_percent_vs"
            ][opp] = mean
            levels[level.id].setdefault("unbounded_vs", {})[opp] = unbounded
    composite: dict[str, float | None] = {}
    for opp in OPPONENTS:
        num = 0.0
        den = 0.0
        for level in LEVELS:
            rel = levels[level.id]["relative_percent_vs"][opp]
            if rel is not None:
                num += level.weight * rel
                den += level.weight
        composite[opp] = round(num / den, 4) if den else None
    return {
        "levels": levels,
        "composite_vs": composite,
        "target_percent": 400,
        # Per-opponent only, by operator order: the two references are never
        # merged into one combined figure — each comparison stands alone.
        "target_met": {opp: (v is not None and v >= 400) for opp, v in composite.items()},
    }


# ------------------------------------------------------------------ validate
def validate() -> list[str]:
    problems: list[str] = []
    if len(LEVELS) != 7:
        problems.append(f"expected 7 levels, got {len(LEVELS)}")
    for level in LEVELS:
        ms = [m for m in METRICS if m.level == level.id]
        if not (8 <= len(ms) <= 12):
            problems.append(f"{level.id}: {len(ms)} metrics outside [8, 12]")
        if level.weight <= 0:
            problems.append(f"{level.id}: weight {level.weight} not positive")
    ids = [m.id for m in METRICS]
    if len(ids) != len(set(ids)):
        problems.append("duplicate metric ids")
    for m in METRICS:
        if not m.id.startswith(m.level + "."):
            problems.append(f"{m.id}: id not prefixed by level {m.level}")
        if not m.description:
            problems.append(f"{m.id}: empty description")
    for name, spec in repo_specs().items():
        if not spec.root.is_dir():
            problems.append(f"repo root missing for {name}: {spec.root}")
    return problems


# ------------------------------------------------------------------ ledger
def _metric_command(metric_id: str, repo: str) -> str:
    return f"python bench/verdict/run.py metric {metric_id} --repo {repo}"


def build_ledger(values: dict[str, dict[str, float]], stamp: str) -> dict:
    scores = compute_scores(values)
    level_entries = []
    for level in LEVELS:
        metric_entries = []
        for m in METRICS:
            if m.level != level.id:
                continue
            v = values[m.id]
            metric_entries.append(
                {
                    "id": m.id,
                    "description": m.description,
                    "values": {r: v[r] for r in ("ours", *OPPONENTS)},
                    "relative_percent_vs": scores["levels"][level.id]["metrics"][m.id],
                    "command": {r: _metric_command(m.id, r) for r in ("ours", *OPPONENTS)},
                }
            )
        level_entries.append(
            {
                "id": level.id,
                "name": level.name,
                "weight": level.weight,
                "description": level.description,
                "relative_percent_vs": scores["levels"][level.id]["relative_percent_vs"],
                "unbounded_vs": scores["levels"][level.id]["unbounded_vs"],
                "metrics": metric_entries,
            }
        )
    return {
        "suite": SUITE,
        "version": VERSION,
        "generated_at": stamp,
        "repos": {name: str(spec.root) for name, spec in repo_specs().items()},
        "formula": {
            "metric_relative": "100 * ours / opponent; opponent==0 -> null if ours>0 (unbounded), 100 if ours==0",
            "level": "mean of defined metric relatives; unbounded reported separately, never fabricated",
            "composite": "weighted mean of 7 level relatives; per-opponent only, never merged",
        },
        "levels": level_entries,
        "composite_vs": scores["composite_vs"],
        "target_percent": scores["target_percent"],
        "target_met": scores["target_met"],
    }


def render_markdown(ledger: dict) -> str:
    lines = [
        "# VERDICT 7-level — latest",
        "",
        f"- ledger: `{ledger['generated_at']}` (suite {ledger['suite']} v{ledger['version']})",
        (
            f"- repos: ours={ledger['repos']['ours']} · codegraph={ledger['repos']['codegraph']}"
            f" · graphify={ledger['repos']['graphify']}"
        ),
        "",
        "| Level | weight | vs codegraph % | unbounded | vs graphify % | unbounded |",
        "|---|---|---|---|---|---|",
    ]
    for lv in ledger["levels"]:
        lines.append(
            f"| {lv['id']} {lv['name']} | {lv['weight']} | "
            f"{lv['relative_percent_vs']['codegraph']} | {lv['unbounded_vs']['codegraph']} | "
            f"{lv['relative_percent_vs']['graphify']} | {lv['unbounded_vs']['graphify']} |"
        )
    lines += [
        "",
        f"**Composite vs codegraph: {ledger['composite_vs']['codegraph']}%**",
        f"**Composite vs graphify: {ledger['composite_vs']['graphify']}%**",
        f"(target {ledger['target_percent']}% per opponent: "
        + ", ".join(f"{k}={'MET' if v else 'NOT MET'}" for k, v in ledger["target_met"].items())
        + ")",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ commands
def cmd_check() -> int:
    problems = validate()
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    repos = ", ".join(str(s.root) for s in repo_specs().values())
    print(f"ok: {len(LEVELS)} levels, {len(METRICS)} metrics, repos: {repos}")
    return 0


def cmd_run() -> int:
    problems = validate()
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    values = measure_all()
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ledger = build_ledger(values, stamp)
    RESULTS_DIR.mkdir(exist_ok=True)
    ledger_path = RESULTS_DIR / f"ledger-{stamp}.json"
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (RESULTS_DIR / "verdict-latest.md").write_text(render_markdown(ledger), encoding="utf-8")

    for lv in ledger["levels"]:
        print(
            f"{lv['id']} {lv['name']}: vs codegraph {lv['relative_percent_vs']['codegraph']}% "
            f"(unbounded {lv['unbounded_vs']['codegraph']}), vs graphify {lv['relative_percent_vs']['graphify']}% "
            f"(unbounded {lv['unbounded_vs']['graphify']})"
        )
    print(f"composite vs codegraph: {ledger['composite_vs']['codegraph']}%")
    print(f"composite vs graphify: {ledger['composite_vs']['graphify']}%")
    print(f"target: {ledger['target_percent']}% per opponent, met={ledger['target_met']}")
    print(f"ledger: {ledger_path}")
    return 0


def cmd_print() -> int:
    ledgers = sorted(RESULTS_DIR.glob("ledger-*.json")) if RESULTS_DIR.is_dir() else []
    if not ledgers:
        print("no ledger found; run first", file=sys.stderr)
        return 1
    print(render_markdown(json.loads(ledgers[-1].read_text(encoding="utf-8"))))
    return 0


def cmd_metric(argv: list[str]) -> int:
    if len(argv) < 3 or "--repo" not in argv:
        print("usage: run.py metric <metric_id> --repo <ours|codegraph|graphify>", file=sys.stderr)
        return 2
    metric_id = argv[2]
    repo = argv[argv.index("--repo") + 1]
    if metric_id not in METRIC_BY_ID:
        print(f"unknown metric {metric_id!r}", file=sys.stderr)
        return 2
    specs = repo_specs()
    if repo not in specs:
        print(f"unknown repo {repo!r}", file=sys.stderr)
        return 2
    metric = METRIC_BY_ID[metric_id]
    fn = metric.measure
    print(fn(RepoData(specs[repo])))  # type: ignore[operator]
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "run":
        return cmd_run()
    if cmd == "check":
        return cmd_check()
    if cmd == "print":
        return cmd_print()
    if cmd == "metric":
        return cmd_metric(argv)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
