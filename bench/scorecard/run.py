#!/usr/bin/env python3
"""Capability scorecard — machine-measured, not opinion.

Runs every proof test listed in ``capabilities.json`` for our side under real
pytest (exit codes, parsed from JUnit XML), scores capabilities by weight, and
computes relative percentages against each opponent (codegraph, graphify)
whose scores come from documented manual evidence (repo path + line, status
supported/partial/absent — see capabilities.json).

Final score = 0.6 * capability component + 0.4 * competitive-bench component.
The bench component is NEVER hardcoded: it is read from a competitive ledger
($KYS_COMPETITIVE_LEDGER, else newest bench/competitive/results/ledger-*.json).
When no ledger exists the bench weight renormalizes to 0 and the ledger says
so explicitly.

Usage:
    uv run python bench/scorecard/run.py run       # full run, writes results/
    uv run python bench/scorecard/run.py check     # static validation only
    uv run python bench/scorecard/run.py print     # markdown table from last ledger

Exit codes: 0 = ok (all proofs pass on `run`); 1 = a check/proof failed;
2 = usage error.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCORECARD_DIR = Path(__file__).resolve().parent
CAPABILITIES_PATH = SCORECARD_DIR / "capabilities.json"
RESULTS_DIR = SCORECARD_DIR / "results"

STATUS_POINTS = {"supported": 1.0, "partial": 0.5, "absent": 0.0}
EXPECTED_CATEGORIES = (
    "concurrency",
    "integrity",
    "security",
    "operability",
    "contracts/governance",
    "recovery",
    "observability",
    "testing rigor",
    "determinism",
    "extensibility",
)
MIN_CAPABILITIES = 60
MAX_CAPABILITIES = 80
WEIGHT_RANGE = (1, 3)


# ---------------------------------------------------------------- load/validate
def _node_defined(src: Path, parts: list[str]) -> bool:
    """True if the test function exists — top-level (``file::test``) or as a
    class method (``file::Class::test``). AST-based, not text matching."""
    import ast

    tree = ast.parse(src.read_text(encoding="utf-8"))
    if len(parts) == 1:
        return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == parts[0] for n in tree.body)
    cls, name = parts
    return any(
        isinstance(n, ast.ClassDef)
        and n.name == cls
        and any(isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == name for s in n.body)
        for n in tree.body
    )


def load_capabilities(path: Path | None = None) -> dict:
    with open(path or CAPABILITIES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def validate(doc: dict) -> list[str]:
    """Static validation of the capabilities document. Returns problems."""
    problems: list[str] = []
    caps = doc.get("capabilities", [])
    if not (MIN_CAPABILITIES <= len(caps) <= MAX_CAPABILITIES):
        problems.append(f"capability count {len(caps)} outside [{MIN_CAPABILITIES}, {MAX_CAPABILITIES}]")
    ids = [c.get("id") for c in caps]
    if len(ids) != len(set(ids)):
        problems.append("duplicate capability ids")
    cats = {c.get("category") for c in caps}
    for cat in EXPECTED_CATEGORIES:
        if cat not in cats:
            problems.append(f"missing category: {cat}")
    scoring = doc.get("scoring", {})
    if scoring.get("status_points") != STATUS_POINTS:
        problems.append("status_points mapping drifted from the runner")
    comp = scoring.get("composite", {})
    weights = (comp.get("capability_weight"), comp.get("bench_weight"))
    if weights[0] is None or weights[1] is None or abs(sum(weights) - 1.0) > 1e-9:
        problems.append("composite weights missing or do not sum to 1.0")
    for cap in caps:
        cid = cap.get("id", "<missing>")
        w = cap.get("weight")
        if not isinstance(w, int) or isinstance(w, bool) or not (WEIGHT_RANGE[0] <= w <= WEIGHT_RANGE[1]):
            problems.append(f"{cid}: weight {w!r} outside {WEIGHT_RANGE}")
        proofs = cap.get("ours", {}).get("proof_tests", [])
        if not proofs:
            problems.append(f"{cid}: no proof tests")
        for node in proofs:
            parts = node.split("::")
            if len(parts) not in (2, 3) or not parts[0].startswith("tests/"):
                problems.append(f"{cid}: malformed proof node id {node!r}")
            else:
                src = REPO_ROOT / parts[0]
                if not src.is_file():
                    problems.append(f"{cid}: proof file missing: {parts[0]}")
                elif not _node_defined(src, parts[1:]):
                    problems.append(f"{cid}: proof test not defined: {node}")
        opponents = cap.get("opponents", {})
        for name in ("codegraph", "graphify"):
            opp = opponents.get(name)
            if opp is None:
                problems.append(f"{cid}: missing opponent {name}")
                continue
            if opp.get("status") not in STATUS_POINTS:
                problems.append(f"{cid}: {name} status invalid: {opp.get('status')!r}")
            evs = opp.get("evidence") or []
            if not evs:
                problems.append(f"{cid}: {name} evidence empty")
            for e in evs:
                if not e.get("path") or "line" not in e or not e.get("note"):
                    problems.append(f"{cid}: {name} evidence entry incomplete: {e!r}")
    return problems


# ---------------------------------------------------------------- proof runner
def proof_node_ids(doc: dict) -> list[str]:
    nodes: set[str] = set()
    for cap in doc["capabilities"]:
        nodes.update(cap["ours"]["proof_tests"])
    return sorted(nodes)


def _junit_nodeid(classname: str, name: str) -> str:
    """Reconstruct a pytest node id from JUnit classname+name. The classname
    is dotted (``tests.test_serve_gates.TestStormAllVerbs``); the longest
    prefix resolving to an existing .py file is the module, the remainder is
    the class path."""
    parts = classname.split(".")
    file_part = parts
    class_parts: list[str] = []
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i]) + ".py"
        if (REPO_ROOT / candidate).is_file():
            file_part = parts[:i]
            class_parts = parts[i:]
            break
    return "/".join(file_part) + ".py::" + "::".join([*class_parts, name])


def parse_junit(xml_path: Path) -> dict[str, bool]:
    """Map base node id -> passed (True only if every parametrization passed
    and none were skipped)."""
    results: dict[str, bool] = {}
    tree = ET.parse(xml_path)
    for case in tree.getroot().iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        base = _junit_nodeid(classname, name)
        if "[" in name:  # parametrized: group under the base function
            base = _junit_nodeid(classname, name[: name.index("[")])
        ok = case.find("failure") is None and case.find("error") is None and case.find("skipped") is None
        results[base] = results.get(base, True) and ok
    return results


def run_proofs(doc: dict, timeout_s: int = 1200) -> dict:
    """Execute every proof test under real pytest; return per-node outcomes."""
    nodes = proof_node_ids(doc)
    with tempfile.TemporaryDirectory(prefix="scorecard-") as tmp:
        xml_path = Path(tmp) / "junit.xml"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={xml_path}",
            *nodes,
        ]
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        outcomes = parse_junit(xml_path) if xml_path.exists() else {}
    missing = [n for n in nodes if n not in outcomes]
    passed = {n for n, ok in outcomes.items() if ok}
    return {
        "pytest_exit_code": proc.returncode,
        "pytest_tail": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "",
        "proof_total": len(nodes),
        "proof_executed": len(outcomes),
        "proof_passed": len(passed),
        "missing": sorted(missing),
        "failed": sorted(n for n in outcomes if n not in passed),
        "passed": sorted(passed),
    }


# ---------------------------------------------------------------- scoring (pure)
def compute_capability_scores(doc: dict, passed: set[str]) -> dict:
    """Pure scoring. ours: full weight iff ALL proofs of the capability passed.
    opponents: weight * status_points[status]."""
    total_weight = sum(c["weight"] for c in doc["capabilities"])
    ours_points = 0.0
    opp_points = {"codegraph": 0.0, "graphify": 0.0}
    per_category: dict[str, dict] = {}
    for cap in doc["capabilities"]:
        w = cap["weight"]
        cat = cap["category"]
        bucket = per_category.setdefault(cat, {"weight": 0.0, "ours": 0.0, "codegraph": 0.0, "graphify": 0.0})
        bucket["weight"] += w
        ours_ok = all(p in passed for p in cap["ours"]["proof_tests"])
        if ours_ok:
            ours_ok_points = float(w)
        else:
            ours_ok_points = 0.0
        ours_points += ours_ok_points
        bucket["ours"] += ours_ok_points
        for name in opp_points:
            pts = w * STATUS_POINTS[cap["opponents"][name]["status"]]
            opp_points[name] += pts
            bucket[name] += pts

    def pct(p: float) -> float:
        return round(100.0 * p / total_weight, 4) if total_weight else 0.0

    ours_pct = pct(ours_points)
    opp_pct = {n: pct(p) for n, p in opp_points.items()}
    relative = {n: round(100.0 * ours_pct / opp_pct[n], 4) if opp_pct[n] else None for n in opp_pct}
    for bucket in per_category.values():
        bw = bucket["weight"]
        for key in ("ours", "codegraph", "graphify"):
            bucket[key] = round(100.0 * bucket[key] / bw, 4) if bw else 0.0
    return {
        "total_weight": total_weight,
        "points": {"ours": ours_points, **opp_points},
        "percent": {"ours": ours_pct, **opp_pct},
        "relative_percent_vs": relative,
        "per_category_percent": dict(sorted(per_category.items())),
    }


# ---------------------------------------------------------------- bench ledger
def find_bench_ledger() -> Path | None:
    explicit = os.environ.get("KYS_COMPETITIVE_LEDGER")
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    results = REPO_ROOT / "bench" / "competitive" / "results"
    if results.is_dir():
        ledgers = sorted(results.glob("ledger-*.json"))
        if ledgers:
            return ledgers[-1]
    return None


def _extract_bench_relative(payload: dict) -> dict[str, float] | None:
    """Tolerant extraction of per-opponent relative percentages (ours vs each
    opponent, in percent) from a competitive bench ledger. Two shapes:

    1. explicit: a mapping (top-level or under 'scores'/'summary'/'results')
       with keys mentioning codegraph/graphify and numeric values;
    2. per-system outcome counts: ``summary.per_system`` with a
       ``know_your_context`` entry plus opponent entries, each carrying
       OK/FAILED/NOT_SUPPORTED counts -> success rate OK/total per system,
       relative = 100 * ours_rate / opponent_rate.
    """
    candidates = [payload]
    for key in ("scores", "summary", "results"):
        if isinstance(payload.get(key), dict):
            candidates.append(payload[key])
    for cand in candidates:
        for sub_key in ("relative_percent_vs", "relative_percent", "relative"):
            if isinstance(cand.get(sub_key), dict):
                cand = cand[sub_key]
                break
        found: dict[str, float] = {}
        for k, v in cand.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                kl = str(k).lower()
                if "codegraph" in kl and ("relative" in kl or "percent" in kl or cand is not payload):
                    found["codegraph"] = float(v)
                elif "graphify" in kl and ("relative" in kl or "percent" in kl or cand is not payload):
                    found["graphify"] = float(v)
        if found:
            return found
    per_system = payload.get("summary", {}).get("per_system")
    if isinstance(per_system, dict) and "know_your_context" in per_system:

        def rate(system: str) -> float | None:
            row = per_system.get(system)
            if not isinstance(row, dict):
                return None
            ok = row.get("OK", 0)
            total = ok + row.get("FAILED", 0) + row.get("NOT_SUPPORTED", 0)
            return ok / total if total else None

        ours = rate("know_your_context")
        if ours is not None:
            found = {}
            for opp in ("codegraph", "graphify"):
                opp_rate = rate(opp)
                if opp_rate:
                    found[opp] = round(100.0 * ours / opp_rate, 4)
            if found:
                return found
    return None


def compute_final(
    capability: dict, bench_relative: dict[str, float] | None, capability_weight: float, bench_weight: float
) -> dict:
    finals: dict[str, float | None] = {}
    for opp in ("codegraph", "graphify"):
        cap_rel = capability["relative_percent_vs"][opp]
        if cap_rel is None:
            finals[opp] = None
            continue
        if bench_relative and opp in bench_relative:
            finals[opp] = round(capability_weight * cap_rel + bench_weight * bench_relative[opp], 4)
        else:
            finals[opp] = cap_rel  # bench weight renormalized to 0, documented
    known = [v for v in finals.values() if v is not None]
    composite = round(sum(known) / len(known), 4) if known else None
    return {"final_vs": finals, "composite": composite}


# ---------------------------------------------------------------- commands
def cmd_check() -> int:
    doc = load_capabilities()
    problems = validate(doc)
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    nodes = proof_node_ids(doc)
    print(
        f"ok: {len(doc['capabilities'])} capabilities, {len(nodes)} unique proof tests, "
        f"total weight {sum(c['weight'] for c in doc['capabilities'])}"
    )
    return 0


def cmd_run() -> int:
    doc = load_capabilities()
    problems = validate(doc)
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    proofs = run_proofs(doc)
    capability = compute_capability_scores(doc, set(proofs["passed"]))

    bench_path = find_bench_ledger()
    bench_relative = None
    bench_status = "absent"
    if bench_path is not None:
        try:
            bench_relative = _extract_bench_relative(json.loads(bench_path.read_text(encoding="utf-8")))
            bench_status = "loaded" if bench_relative else "unrecognized"
        except OSError, json.JSONDecodeError:
            bench_status = "unreadable"
    comp = doc["scoring"]["composite"]
    final = compute_final(capability, bench_relative, comp["capability_weight"], comp["bench_weight"])

    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ledger = {
        "suite": doc["suite"],
        "version": doc["version"],
        "generated_at": stamp,
        "formula": {
            "capability": doc["scoring"]["percent_formula"],
            "relative": doc["scoring"]["relative_formula"],
            "composite": doc["scoring"]["composite"]["formula"],
            "bench_absent_rule": doc["scoring"]["composite"]["bench_absent_rule"],
        },
        "proofs": {k: v for k, v in proofs.items() if k != "passed"},
        "capability_scores": capability,
        "bench_component": {
            "status": bench_status,
            "source": str(bench_path) if bench_path else None,
            "relative_percent_vs": bench_relative,
            "weights": {
                "capability": comp["capability_weight"],
                "bench": comp["bench_weight"] if bench_status == "loaded" else 0.0,
            },
        },
        "final": final,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    ledger_path = RESULTS_DIR / f"ledger-{stamp}.json"
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (RESULTS_DIR / "scorecard-latest.md").write_text(render_markdown(ledger), encoding="utf-8")

    _print_summary(ledger, ledger_path)
    if proofs["missing"] or proofs["failed"]:
        return 1
    return 0


def _print_summary(ledger: dict, ledger_path: Path) -> None:
    cap = ledger["capability_scores"]
    print(f"proofs: {ledger['proofs']['proof_passed']}/{ledger['proofs']['proof_total']} passed")
    print(
        f"capability score: ours {cap['percent']['ours']}% "
        f"(codegraph {cap['percent']['codegraph']}%, graphify {cap['percent']['graphify']}%)"
    )
    for opp, rel in cap["relative_percent_vs"].items():
        print(f"relative vs {opp}: {rel}%")
    print(f"bench component: {ledger['bench_component']['status']}")
    print(f"final vs codegraph: {ledger['final']['final_vs']['codegraph']}%")
    print(f"final vs graphify: {ledger['final']['final_vs']['graphify']}%")
    print(f"composite: {ledger['final']['composite']}%")
    print(f"ledger: {ledger_path}")


def render_markdown(ledger: dict) -> str:
    cap = ledger["capability_scores"]
    lines = [
        "# Capability scorecard — latest",
        "",
        f"- ledger: `{ledger['generated_at']}` (suite {ledger['suite']} v{ledger['version']})",
        f"- proofs: {ledger['proofs']['proof_passed']}/{ledger['proofs']['proof_total']} pytest proofs green",
        f"- capability score: **ours {cap['percent']['ours']}%** of total weight "
        f"{cap['total_weight']} (codegraph {cap['percent']['codegraph']}%, "
        f"graphify {cap['percent']['graphify']}%)",
        "",
        "| Opponent | capability % | relative % | bench % | final (0.6/0.4) |",
        "|---|---|---|---|---|",
    ]
    for opp in ("codegraph", "graphify"):
        bench = ledger["bench_component"]
        bench_rel = (bench.get("relative_percent_vs") or {}).get(opp)
        lines.append(
            f"| {opp} | {cap['percent'][opp]} | {cap['relative_percent_vs'][opp]} | "
            f"{bench_rel if bench_rel is not None else 'n/a'} | "
            f"{ledger['final']['final_vs'][opp]} |"
        )
    lines += [
        "",
        f"**Composite: {ledger['final']['composite']}%** (bench component: {ledger['bench_component']['status']})",
        "",
        "## Per-category percent of category weight",
        "",
        "| Category | ours | codegraph | graphify |",
        "|---|---|---|---|",
    ]
    for cat, row in cap["per_category_percent"].items():
        lines.append(f"| {cat} | {row['ours']} | {row['codegraph']} | {row['graphify']} |")
    lines.append("")
    return "\n".join(lines)


def cmd_print() -> int:
    ledgers = sorted(RESULTS_DIR.glob("ledger-*.json")) if RESULTS_DIR.is_dir() else []
    if not ledgers:
        print("no ledger found; run first", file=sys.stderr)
        return 1
    ledger = json.loads(ledgers[-1].read_text(encoding="utf-8"))
    print(render_markdown(ledger))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "run":
        return cmd_run()
    if cmd == "check":
        return cmd_check()
    if cmd == "print":
        return cmd_print()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
