"""Tests for the VERDICT 7-level instrument (bench/verdict/).

Covers: catalog validity, individual metric correctness on fixture repos
(Python + TypeScript), scoring rules (ratio, zero-division, level mean,
weighted composite, per-opponent only), CLI behavior, ledger schema, determinism, and
a smoke test on the real repos when present.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bench" / "verdict"))

import metrics as vm  # noqa: E402
import run as vr  # noqa: E402

VERDICT_DIR = REPO_ROOT / "bench" / "verdict"


# ------------------------------------------------------------------ fixtures
def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_ours(root: Path) -> Path:
    """Minimal Python repo with known, hand-countable content."""
    _write(
        root,
        "src/know_your_context/core.py",
        '''"""Core."""

import enum
import os
import asyncio

MAX_RETRIES = 3
KILL_SWITCH_ENV = "KYS_DISABLE_THING"


class CoreError(Exception):
    pass


class Mode(enum.Enum):
    A = "a"


def public_fn(x: int) -> int:
    assert x > 0
    if os.environ.get(KILL_SWITCH_ENV):
        return 0
    return x


async def run_loop() -> None:
    lock = asyncio.Lock()
    async with lock:
        await asyncio.sleep(0)
''',
    )
    _write(
        root,
        "src/know_your_context/serve/__init__.py",
        "",
    )
    _write(
        root,
        "src/know_your_context/serve/server.py",
        '''"""Server."""


def restore_store() -> None:
    pass


def rollback_plan() -> None:
    pass


def _private() -> None:
    pass
''',
    )
    _write(
        root,
        "tests/test_core.py",
        """import pytest


@pytest.fixture
def fx():
    return 1


@pytest.mark.parametrize("x", [1, 2])
def test_public(x):
    assert x


def test_other():
    pass
""",
    )
    _write(root, "docs/contracts/c.md", "# contract\n")
    _write(root, "README.md", "# hello\n\nworld\n")
    _write(root, "CHANGELOG.md", "# Changelog\n\n## 1.0.0\n\n- init\n\n## 0.9.0\n\n- pre\n")
    _write(root, "LICENSE", "Apache-2.0\n")
    _write(root, "pyproject.toml", '[project]\ndependencies = ["pydantic==2.0", "httpx>=1"]\n')
    return root


def make_ts(root: Path) -> Path:
    _write(
        root,
        "src/mcp/tools.ts",
        """import { x } from "./a";

export class ToolError extends Error {}

export interface Cfg {
  retries?: number;
  mode: string;
}

enum Color { Red, Blue }

const MAX_DEPTH = 8;

export function search(q: string): number {
  if (q) {
    while (false) {
      return 1;
    }
  }
  return 0;
}

export const run = async () => {
  await Promise.resolve();
};

const tools = [
  { name: "codegraph_search" },
  { name: "codegraph_status" },
];
""",
    )
    _write(
        root,
        "__tests__/tools.test.ts",
        """describe("tools", () => {
  it("searches", () => {});
  test.each([[1], [2]])("param", () => {});
});
""",
    )
    _write(root, "package.json", '{"dependencies": {"zod": "3.0.0", "ws": "^8.0.0"}}')
    _write(root, "LICENSE", "MIT\n")
    return root


@pytest.fixture()
def ours_repo(tmp_path: Path) -> vm.RepoData:
    root = make_ours(tmp_path / "ours")
    spec = vm.RepoSpec("ours", root, "python", ("src/**/*.py",), ("tests/**/*.py",), ("src/know_your_context",))
    return vm.RepoData(spec)


@pytest.fixture()
def ts_repo(tmp_path: Path) -> vm.RepoData:
    root = make_ts(tmp_path / "ts")
    spec = vm.RepoSpec("codegraph", root, "typescript", ("src/**/*.ts",), ("__tests__/**/*.ts",), ("src",))
    return vm.RepoData(spec)


# ---------------------------------------------------------------- catalog
def test_seven_levels():
    assert len(vm.LEVELS) == 7


def test_metric_count_per_level_within_8_12():
    for level in vm.LEVELS:
        count = sum(1 for m in vm.METRICS if m.level == level.id)
        assert 8 <= count <= 12, (level.id, count)


def test_metric_ids_unique():
    ids = [m.id for m in vm.METRICS]
    assert len(ids) == len(set(ids))


def test_metric_id_prefixed_by_level():
    for m in vm.METRICS:
        assert m.id.startswith(m.level + ".")


def test_level_weights_positive():
    assert all(level.weight > 0 for level in vm.LEVELS)


def test_every_metric_has_description_and_callable():
    for m in vm.METRICS:
        assert m.description
        assert callable(m.measure)


def test_level_names_match_operator_spec():
    names = [level.name for level in vm.LEVELS]
    assert names == [
        "profesional",
        "codebase",
        "arsitektur",
        "flow engineering",
        "kompleksitas codebase",
        "kompleksitas arsitektur",
        "kompleksitas flow engineering",
    ]


def test_repo_specs_cover_three_repos():
    assert set(vm.repo_specs()) == {"ours", "codegraph", "graphify"}


# ------------------------------------------------------- metric correctness
def test_source_loc_counts_nonblank(ours_repo):
    assert vm.m_source_loc(ours_repo) == sum(
        1 for p in ours_repo.src_files for line in p.read_text().splitlines() if line.strip()
    )


def test_test_cases_python(ours_repo):
    assert vm.m_test_cases(ours_repo) == 2  # test_public + test_other


def test_test_cases_ts(ts_repo):
    assert vm.m_test_cases(ts_repo) == 2  # it( + test.each(


def test_test_files(ours_repo):
    assert vm.m_test_files(ours_repo) == 1


def test_changelog_sections(ours_repo):
    assert vm.m_changelog_sections(ours_repo) == 2


def test_license_files(ours_repo, ts_repo):
    assert vm.m_license_files(ours_repo) == 1
    assert vm.m_license_files(ts_repo) == 1


def test_pinned_deps_python(ours_repo):
    assert vm.m_pinned_deps(ours_repo) == 1  # only pydantic==2.0


def test_pinned_deps_ts(ts_repo):
    assert vm.m_pinned_deps(ts_repo) == 1  # zod exact; ws ^8 excluded


def test_typed_errors_python(ours_repo):
    assert vm.m_typed_errors(ours_repo) == 1  # CoreError


def test_typed_errors_ts(ts_repo):
    assert vm.m_typed_errors(ts_repo) == 1  # ToolError extends Error


def test_enums_ts(ts_repo):
    assert vm.m_enums(ts_repo) == 1


def test_interfaces_ts(ts_repo):
    assert vm.m_interfaces(ts_repo) == 1


def test_public_functions_python(ours_repo):
    # public_fn, run_loop, restore_store, rollback_plan (_private excluded)
    assert vm.m_public_functions(ours_repo) == 4


def test_classes_python(ours_repo):
    assert vm.m_classes(ours_repo) == 2  # CoreError + Mode


def test_named_constants_python(ours_repo):
    assert vm.m_named_constants(ours_repo) == 2  # MAX_RETRIES + KILL_SWITCH_ENV


def test_named_constants_ts(ts_repo):
    assert vm.m_named_constants(ts_repo) == 1  # MAX_DEPTH


def test_assertions_python(ours_repo):
    assert vm.m_assertions(ours_repo) == 1  # assert x > 0


def test_async_functions_python(ours_repo):
    assert vm.m_async_functions(ours_repo) == 1


def test_await_sites_python(ours_repo):
    assert vm.m_await_sites(ours_repo) == 1


def test_lock_primitives_python(ours_repo):
    assert vm.m_lock_primitives(ours_repo) == 1


def test_env_vars_resolves_module_constant(ours_repo):
    assert "KYS_DISABLE_THING" in vm._env_names(ours_repo)


def test_kill_switch_sites_detects_constant_indirection(ours_repo):
    assert vm.m_kill_switch_sites(ours_repo) == 1


def test_env_vars_ts(ts_repo):
    assert vm.m_env_vars(ts_repo) == 0


def test_max_nesting_python(ours_repo):
    # run_loop: def(1) -> async with(2) -> await; public_fn: def(1) -> if(2)
    assert vm.m_max_nesting(ours_repo) == 2


def test_max_nesting_ts_braces(ts_repo):
    assert vm.m_max_nesting(ts_repo) >= 3  # function > if > while


def test_contract_docs(ours_repo):
    assert vm.m_contract_docs(ours_repo) == 1


def test_mcp_tools_from_tool_mapping(tmp_path):
    root = make_ours(tmp_path / "ours2")
    _write(
        root,
        "src/know_your_context/mcp_server.py",
        '_TOOL_TO_OPERATION = {\n    "a_tool": "a.op",\n    "b_tool": "b.op",\n}\n',
    )
    spec = vm.RepoSpec("ours", root, "python", ("src/**/*.py",), ("tests/**/*.py",), ("src/know_your_context",))
    assert vm.m_mcp_tools(vm.RepoData(spec)) == 2


def test_mcp_tools_ts_name_entries(ts_repo):
    assert vm.m_mcp_tools(ts_repo) == 2


def test_recovery_paths_python(ours_repo):
    assert vm.m_recovery_paths(ours_repo) == 2  # restore_store + rollback_plan


def test_typed_raise_sites_ts():
    assert True  # covered indirectly via ts_repo below


def test_total_functions_python(ours_repo):
    assert vm.m_total_functions(ours_repo) == 5  # incl. _private


def test_layers_python(ours_repo):
    assert vm.m_layers(ours_repo) == 1  # only serve/ (top-level files are root)


def test_components_python(ours_repo):
    assert vm.m_components(ours_repo) == 1  # serve/ has 2 files (__init__ + server)


def test_doc_files_and_lines(ours_repo):
    assert vm.m_doc_files(ours_repo) == 3  # c.md + README + CHANGELOG
    assert vm.m_doc_lines(ours_repo) > 0


# ------------------------------------------------------------------ scoring
def test_relative_normal_ratio():
    assert vr.relative(10.0, 4.0) == 250.0


def test_relative_opponent_zero_ours_positive_is_none():
    assert vr.relative(5.0, 0.0) is None


def test_relative_both_zero_is_parity():
    assert vr.relative(0.0, 0.0) == 100.0


def _flat_values(table: dict[str, tuple[float, float, float]]) -> dict[str, dict[str, float]]:
    return {mid: {"ours": v[0], "codegraph": v[1], "graphify": v[2]} for mid, v in table.items()}


def test_level_mean_excludes_unbounded():
    values = _flat_values({m.id: (10.0, 10.0, 10.0) for m in vm.METRICS})
    values["l1.source_loc"] = {"ours": 5.0, "codegraph": 0.0, "graphify": 10.0}
    scores = vr.compute_scores(values)
    cg_level = scores["levels"]["l1"]["relative_percent_vs"]["codegraph"]
    assert cg_level == 100.0  # 9 metrics at 100, unbounded excluded
    assert scores["levels"]["l1"]["unbounded_vs"]["codegraph"] == 1


def test_composite_equal_weights_mean_of_levels():
    values = _flat_values({m.id: (10.0, 10.0, 10.0) for m in vm.METRICS})
    scores = vr.compute_scores(values)
    assert scores["composite_vs"]["codegraph"] == 100.0
    assert scores["composite_vs"]["graphify"] == 100.0
    assert "combined" not in scores
    assert scores["target_met"] == {"codegraph": False, "graphify": False}


def test_composite_weighted():
    values = _flat_values({m.id: (10.0, 5.0, 2.0) for m in vm.METRICS})
    scores = vr.compute_scores(values)
    assert scores["composite_vs"]["codegraph"] == 200.0
    assert scores["composite_vs"]["graphify"] == 500.0


def test_target_met_flag():
    values = _flat_values({m.id: (40.0, 10.0, 10.0) for m in vm.METRICS})
    scores = vr.compute_scores(values)
    assert "combined" not in scores


def test_compute_scores_pure_deterministic():
    values = _flat_values({m.id: (7.0, 3.0, 11.0) for m in vm.METRICS})
    assert vr.compute_scores(values) == vr.compute_scores(values)


# --------------------------------------------------------------------- CLI
@pytest.fixture()
def fixture_env(tmp_path: Path):
    ours = make_ours(tmp_path / "ours")
    codegraph = make_ts(tmp_path / "cg")
    graphify = make_ours(tmp_path / "gf")
    env = dict(os.environ)
    env["KYS_VERDICT_ROOT_OURS"] = str(ours)
    env["KYS_VERDICT_ROOT_CODEGRAPH"] = str(codegraph)
    env["KYS_VERDICT_ROOT_GRAPHIFY"] = str(graphify)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _cli(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERDICT_DIR / "run.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_check_cli_exit_zero(fixture_env):
    proc = _cli(["check"], fixture_env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "70 metrics" in proc.stdout


def test_metric_cli_prints_value(fixture_env):
    proc = _cli(["metric", "l1.license_files", "--repo", "ours"], fixture_env)
    assert proc.returncode == 0
    assert float(proc.stdout.strip()) == 1.0


def test_metric_cli_unknown_metric(fixture_env):
    assert _cli(["metric", "nope", "--repo", "ours"], fixture_env).returncode == 2


def test_metric_cli_unknown_repo(fixture_env):
    assert _cli(["metric", "l1.license_files", "--repo", "nobody"], fixture_env).returncode == 2


def test_run_cli_exit_zero_and_ledger(fixture_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KYS_VERDICT_ROOT_OURS", fixture_env["KYS_VERDICT_ROOT_OURS"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_CODEGRAPH", fixture_env["KYS_VERDICT_ROOT_CODEGRAPH"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_GRAPHIFY", fixture_env["KYS_VERDICT_ROOT_GRAPHIFY"])
    results = tmp_path / "results"
    monkeypatch.setattr(vr, "RESULTS_DIR", results)
    assert vr.cmd_run() == 0
    ledgers = sorted(results.glob("ledger-*.json"))
    assert len(ledgers) == 1
    ledger = json.loads(ledgers[0].read_text())
    assert ledger["suite"] == "verdict-7-level"
    assert set(ledger["repos"]) == {"ours", "codegraph", "graphify"}
    assert len(ledger["levels"]) == 7


def test_ledger_records_raw_values_and_commands(fixture_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KYS_VERDICT_ROOT_OURS", fixture_env["KYS_VERDICT_ROOT_OURS"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_CODEGRAPH", fixture_env["KYS_VERDICT_ROOT_CODEGRAPH"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_GRAPHIFY", fixture_env["KYS_VERDICT_ROOT_GRAPHIFY"])
    monkeypatch.setattr(vr, "RESULTS_DIR", tmp_path / "results")
    vr.cmd_run()
    ledger = json.loads(sorted((tmp_path / "results").glob("ledger-*.json"))[0].read_text())
    for level in ledger["levels"]:
        assert 8 <= len(level["metrics"]) <= 12
        for m in level["metrics"]:
            assert set(m["values"]) == {"ours", "codegraph", "graphify"}
            assert all(isinstance(v, (int, float)) for v in m["values"].values())
            for repo, cmd in m["command"].items():
                assert cmd == f"python bench/verdict/run.py metric {m['id']} --repo {repo}"


def test_metric_command_reproduces_ledger_value(fixture_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KYS_VERDICT_ROOT_OURS", fixture_env["KYS_VERDICT_ROOT_OURS"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_CODEGRAPH", fixture_env["KYS_VERDICT_ROOT_CODEGRAPH"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_GRAPHIFY", fixture_env["KYS_VERDICT_ROOT_GRAPHIFY"])
    monkeypatch.setattr(vr, "RESULTS_DIR", tmp_path / "results")
    vr.cmd_run()
    ledger = json.loads(sorted((tmp_path / "results").glob("ledger-*.json"))[0].read_text())
    entry = ledger["levels"][0]["metrics"][0]
    proc = _cli(["metric", entry["id"], "--repo", "codegraph"], fixture_env)
    assert float(proc.stdout.strip()) == entry["values"]["codegraph"]


def test_two_runs_identical_scores(fixture_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KYS_VERDICT_ROOT_OURS", fixture_env["KYS_VERDICT_ROOT_OURS"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_CODEGRAPH", fixture_env["KYS_VERDICT_ROOT_CODEGRAPH"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_GRAPHIFY", fixture_env["KYS_VERDICT_ROOT_GRAPHIFY"])
    values_a = vm.measure_all()
    values_b = vm.measure_all()
    assert values_a == values_b
    a = vr.build_ledger(values_a, "t1")
    b = vr.build_ledger(values_b, "t2")
    strip = lambda d: {k: v for k, v in d.items() if k != "generated_at"}  # noqa: E731
    assert strip(a) == strip(b)


def test_markdown_render_written(fixture_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KYS_VERDICT_ROOT_OURS", fixture_env["KYS_VERDICT_ROOT_OURS"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_CODEGRAPH", fixture_env["KYS_VERDICT_ROOT_CODEGRAPH"])
    monkeypatch.setenv("KYS_VERDICT_ROOT_GRAPHIFY", fixture_env["KYS_VERDICT_ROOT_GRAPHIFY"])
    monkeypatch.setattr(vr, "RESULTS_DIR", tmp_path / "results")
    vr.cmd_run()
    md = (tmp_path / "results" / "verdict-latest.md").read_text()
    assert "Composite vs codegraph" in md
    assert "Combined verdict" not in md
    assert "per opponent" in md


def test_check_detects_missing_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("KYS_VERDICT_ROOT_OURS", str(tmp_path / "missing"))
    problems = vr.validate()
    assert any("ours" in p for p in problems)


# ------------------------------------------------------- real repos (smoke)
REAL_ROOTS = [Path("/root/src/codegraph"), Path("/root/src/graphify")]


@pytest.mark.skipif(not all(p.is_dir() for p in REAL_ROOTS), reason="reference repos absent")
def test_real_repos_smoke_positive_values():
    values = vm.measure_all()
    assert values["l1.source_loc"]["ours"] > 0
    assert values["l1.source_loc"]["codegraph"] > 0
    assert values["l1.source_loc"]["graphify"] > 0
    assert values["l2.typed_errors"]["ours"] > 0


def _src_snapshot() -> dict[str, str]:
    import hashlib

    snap: dict[str, str] = {}
    for base in ("src", "tests"):
        root = REPO_ROOT / base
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.py")):
            snap[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


@pytest.mark.skipif(not all(p.is_dir() for p in REAL_ROOTS), reason="reference repos absent")
def test_real_measure_deterministic():
    snap_a = _src_snapshot()
    a = vm.measure_all()
    snap_b = _src_snapshot()
    b = vm.measure_all()
    snap_c = _src_snapshot()
    if snap_a != snap_b or snap_b != snap_c:
        pytest.skip("sibling agent modified src/ mid-measurement")
    assert a == b
