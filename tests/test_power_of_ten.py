"""Tests for ``tools/power_of_ten.py`` — the NASA/JPL Power-of-Ten static gate (W16).

The tool is loaded by path (tools/ is not a package), exactly like
``tests/test_license_guard.py`` loads ``scripts/license_guard.py``.

Rule surface under test:

* POT1 — no provably unbounded loops (``while True``/``itertools.count`` style
  constructs must carry a reachable ``break``/``return``/``raise``).
* POT2 — functions stay within the line ceiling (JPL: one page ≈ 60 lines);
  oversized legacy functions are ratcheted: baselined per case, may shrink,
  may never grow.
* POT3 — critical modules keep their recorded assert density (ratchet: may
  improve, never regress; the JPL absolute target of 2 asserts/function is
  the aspiration documented per case).
* POT4 — no function may be reachable from itself through the static call
  graph (direct or mutual recursion) without a per-case boundedness
  justification.
* POT5 — calls to functions annotated ``-> bool`` (status returns) must not
  drop the result on the floor as a bare expression statement.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "power_of_ten.py"
JUSTIFICATIONS = REPO_ROOT / "tools" / "power_of_ten_justifications.json"
SRC_ROOT = REPO_ROOT / "src" / "know_your_context"


def _load_tool():
    spec = importlib.util.spec_from_file_location("power_of_ten", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolve cls.__module__ via sys.modules.
    sys.modules["power_of_ten"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pot():
    return _load_tool()


def _violations(pot, source: str, *, filename: str = "mod.py", config=None):
    return pot.analyze_module(filename, source, config or pot.AuditConfig())


# --------------------------------------------------------------------- POT1

POT1_CLEAN = [
    "def f(items):\n    for x in items:\n        pass\n",
    "def f():\n    n = 0\n    while n < 10:\n        n += 1\n",
    "def f():\n    while True:\n        break\n",
    "def f():\n    while True:\n        return 1\n",
    "def f():\n    while True:\n        raise SystemExit\n",
    "def f():\n    while True:\n        try:\n            break\n        finally:\n            pass\n",
    "def f():\n    while True:\n        for x in []:\n            pass\n        break\n",
    "def f():\n    while True:\n        if done():\n            return\n",
    "import itertools\ndef f():\n    for i in itertools.count():\n        if i > 3:\n            break\n",
    "from itertools import count\ndef f():\n    for i in count():\n        return i\n",
    "import itertools\ndef f():\n    for x in itertools.repeat(1, 5):\n        pass\n",
    "def f():\n    while True:\n        try:\n            work()\n        except StopIteration:\n            raise\n",
    # A nested function's return is not an exit of the outer loop, but the
    # outer loop here has its own break.
    "def f():\n    while True:\n        def g():\n            return 1\n        break\n",
]

POT1_VIOLATIONS = [
    ("def f():\n    while True:\n        pass\n", "while"),
    ("def f():\n    while 1:\n        work()\n", "while"),
    # A break inside a nested for does not terminate the while.
    ("def f():\n    while True:\n        for x in items:\n            break\n", "while"),
    # A return inside a nested function does not terminate the while.
    ("def f():\n    while True:\n        def g():\n            return 1\n", "while"),
    ("import itertools\ndef f():\n    for i in itertools.count():\n        work(i)\n", "itertools.count"),
    ("import itertools\ndef f():\n    for x in itertools.repeat(1):\n        work(x)\n", "itertools.repeat"),
    ("import itertools\ndef f():\n    for x in itertools.cycle([1]):\n        work(x)\n", "itertools.cycle"),
    ("from itertools import count as count_\ndef f():\n    for i in count_():\n        work(i)\n", "itertools.count"),
]


class TestPOT1BoundedLoops:
    @pytest.mark.parametrize("source", POT1_CLEAN)
    def test_bounded_constructs_pass(self, pot, source: str) -> None:
        assert _violations(pot, source) == []

    @pytest.mark.parametrize(("source", "needle"), POT1_VIOLATIONS)
    def test_unbounded_constructs_flagged(self, pot, source: str, needle: str) -> None:
        found = _violations(pot, source)
        assert [v.rule for v in found] == ["POT1"]
        assert needle in found[0].message
        assert found[0].scope == "f"
        assert found[0].file == "mod.py"

    def test_module_level_while_true_flagged(self, pot) -> None:
        found = _violations(pot, "while True:\n    work()\n")
        assert len(found) == 1
        assert found[0].scope == "<module>"

    def test_violation_carries_line(self, pot) -> None:
        found = _violations(pot, "x = 1\n\n\ndef f():\n    while True:\n        pass\n")
        assert found[0].line == 5


# --------------------------------------------------------------------- POT2


class TestPOT2FunctionLength:
    def test_short_function_passes(self, pot) -> None:
        assert _violations(pot, "def f():\n    return 1\n") == []

    def test_function_at_ceiling_passes(self, pot) -> None:
        body = "".join(f"    x{i} = {i}\n" for i in range(58))
        source = f"def f():\n{body}    return 0\n"  # def + 59 body lines = 60
        assert _violations(pot, source) == []

    def test_function_over_ceiling_flagged(self, pot) -> None:
        body = "".join(f"    x{i} = {i}\n" for i in range(60))
        source = f"def f():\n{body}\n"  # 61 lines
        found = _violations(pot, source)
        assert [v.rule for v in found] == ["POT2"]
        assert found[0].scope == "f"
        assert "61" in found[0].message

    def test_custom_ceiling_respected(self, pot) -> None:
        config = pot.AuditConfig(max_function_lines=3)
        found = _violations(pot, "def f():\n    a = 1\n    b = 2\n    return a\n", config=config)
        assert [v.rule for v in found] == ["POT2"]

    def test_methods_and_async_counted(self, pot) -> None:
        body = "".join(f"        x{i} = {i}\n" for i in range(70))
        source = f"class C:\n    async def m(self):\n{body}"
        found = _violations(pot, source)
        assert [v.rule for v in found] == ["POT2"]
        assert found[0].scope == "C.m"

    def test_nested_function_counted_with_qualname(self, pot) -> None:
        body = "".join(f"        x{i} = {i}\n" for i in range(70))
        source = f"def outer():\n    def inner():\n{body}"
        found = _violations(pot, source)
        # Both are over the ceiling: the nested body counts toward its own
        # length and toward the enclosing function's span.
        assert [v.scope for v in found] == ["outer", "outer.inner"]


# --------------------------------------------------------------------- POT3


class TestPOT3AssertDensity:
    def test_non_critical_module_exempt(self, pot) -> None:
        source = "def f():\n    return 1\n"
        assert _violations(pot, source, filename="random_module.py") == []

    def test_critical_module_below_target_flagged(self, pot) -> None:
        source = "def f():\n    return 1\n\ndef g():\n    return 2\n"
        found = _violations(pot, source, filename="store.py")
        assert [v.rule for v in found] == ["POT3"]
        assert found[0].scope == "<module>"

    def test_critical_module_at_target_passes(self, pot) -> None:
        source = "def f():\n    assert True\n    assert True\n\ndef g():\n    assert True\n    assert True\n"
        assert _violations(pot, source, filename="store.py") == []

    def test_module_with_no_functions_skipped(self, pot) -> None:
        assert _violations(pot, "X = 1\n", filename="store.py") == []

    def test_density_in_message(self, pot) -> None:
        source = "def f():\n    assert True\n\ndef g():\n    return 1\n\ndef h():\n    return 2\n"
        found = _violations(pot, source, filename="audit.py")
        assert len(found) == 1
        assert "0.33" in found[0].message or "1/3" in found[0].message


# --------------------------------------------------------------------- POT4

POT4_CLEAN = [
    "def f():\n    return 1\n",
    "def f():\n    g()\ndef g():\n    return 1\n",
    # Two same-named methods in different classes are not one recursion.
    "class A:\n    def m(self):\n        return 1\nclass B:\n    def m(self):\n        return 2\n",
    # Calling another object's same-named method is not recursion.
    "class A:\n    def m(self, other):\n        return other.m()\n",
    # super() delegation is not recursion.
    "class A:\n    def m(self):\n        return super().m()\n",
    # A bare call to an imported same-named function is not recursion
    # (regression: service.apply_improvement -> improvement.apply_improvement).
    "from engine import process\nclass A:\n    def process(self):\n        return process(self.store)\n",
]

POT4_VIOLATIONS = [
    "def f():\n    return f()\n",
    "class A:\n    def m(self):\n        return self.m()\n",
    "class A:\n    @classmethod\n    def m(cls):\n        return cls.m()\n",
    "def f():\n    return g()\ndef g():\n    return f()\n",
    "def f():\n    g()\ndef g():\n    h()\ndef h():\n    f()\n",
    # Nested recursive helper.
    "def outer():\n    def inner():\n        return inner()\n    return inner()\n",
]


class TestPOT4Recursion:
    @pytest.mark.parametrize("source", POT4_CLEAN)
    def test_acyclic_passes(self, pot, source: str) -> None:
        assert _violations(pot, source) == []

    @pytest.mark.parametrize("source", POT4_VIOLATIONS)
    def test_cycles_flagged(self, pot, source: str) -> None:
        found = _violations(pot, source)
        assert found, f"expected POT4 violation for {source!r}"
        assert all(v.rule == "POT4" for v in found)

    def test_mutual_cycle_flags_both_sides(self, pot) -> None:
        found = _violations(pot, "def f():\n    return g()\ndef g():\n    return f()\n")
        assert {v.scope for v in found} == {"f", "g"}


# --------------------------------------------------------------------- POT5


class TestPOT5CheckedStatusReturns:
    def test_assigned_result_passes(self, pot) -> None:
        source = "def heal() -> bool:\n    return True\ndef f():\n    healed = heal()\n    assert healed\n"
        assert _violations(pot, source) == []

    def test_bare_call_to_bool_function_flagged(self, pot) -> None:
        source = "def heal() -> bool:\n    return True\ndef f():\n    heal()\n"
        found = _violations(pot, source)
        assert [v.rule for v in found] == ["POT5"]
        assert found[0].scope == "f"
        assert found[0].detail == "heal"

    def test_returned_result_passes(self, pot) -> None:
        source = "def heal() -> bool:\n    return True\ndef f():\n    return heal()\n"
        assert _violations(pot, source) == []

    def test_non_bool_return_ignored(self, pot) -> None:
        source = "def compute() -> int:\n    return 1\ndef f():\n    compute()\n"
        assert _violations(pot, source) == []

    def test_module_level_bare_call_flagged(self, pot) -> None:
        source = "def heal() -> bool:\n    return True\nheal()\n"
        found = _violations(pot, source)
        assert [v.rule for v in found] == ["POT5"]
        assert found[0].scope == "<module>"

    def test_unambiguous_attribute_call_flagged(self, pot) -> None:
        # The module defines exactly one bool-status 'heal', so a bare
        # attribute call with that name is conservatively flagged.
        source = "def heal() -> bool:\n    return True\ndef f(store):\n    store.heal()\n"
        found = _violations(pot, source)
        assert [v.rule for v in found] == ["POT5"]
        assert found[0].detail == "heal"

    def test_ambiguous_attribute_name_not_flagged(self, pot) -> None:
        # Two bool-status functions share the short name: an attribute call
        # cannot be resolved statically and is left alone (documented limit).
        source = (
            "class A:\n    def heal(self) -> bool:\n        return True\n"
            "class B:\n    def heal(self) -> bool:\n        return True\n"
            "def f(store):\n    store.heal()\n"
        )
        assert _violations(pot, source) == []

    def test_same_callee_twice_yields_two_violations(self, pot) -> None:
        source = "def heal() -> bool:\n    return True\ndef f():\n    heal()\n    heal()\n"
        found = _violations(pot, source)
        assert len([v for v in found if v.rule == "POT5"]) == 2


# ------------------------------------------------------- justifications file


class TestJustifications:
    def _entry(self, pot, **overrides):
        base = {"rule": "POT2", "file": "a.py", "scope": "f", "reason": "legacy baseline"}
        base.update(overrides)
        return base

    def test_roundtrip_valid_file(self, pot, tmp_path: Path) -> None:
        path = tmp_path / "j.json"
        path.write_text(json.dumps({"version": 1, "entries": [self._entry(pot, lines=100)]}))
        entries = pot.load_justifications(path)
        assert len(entries) == 1
        assert entries[0].lines == 100

    def test_missing_file_raises(self, pot, tmp_path: Path) -> None:
        with pytest.raises(pot.ConfigError):
            pot.load_justifications(tmp_path / "nope.json")

    def test_malformed_json_raises(self, pot, tmp_path: Path) -> None:
        path = tmp_path / "j.json"
        path.write_text("{not json")
        with pytest.raises(pot.ConfigError):
            pot.load_justifications(path)

    def test_wrong_version_raises(self, pot, tmp_path: Path) -> None:
        path = tmp_path / "j.json"
        path.write_text(json.dumps({"version": 99, "entries": []}))
        with pytest.raises(pot.ConfigError):
            pot.load_justifications(path)

    @pytest.mark.parametrize("missing", ["rule", "file", "scope", "reason"])
    def test_entry_missing_field_raises(self, pot, tmp_path: Path, missing: str) -> None:
        entry = self._entry(pot)
        del entry[missing]
        path = tmp_path / "j.json"
        path.write_text(json.dumps({"version": 1, "entries": [entry]}))
        with pytest.raises(pot.ConfigError):
            pot.load_justifications(path)

    def test_unknown_rule_raises(self, pot, tmp_path: Path) -> None:
        path = tmp_path / "j.json"
        path.write_text(json.dumps({"version": 1, "entries": [self._entry(pot, rule="POT99")]}))
        with pytest.raises(pot.ConfigError):
            pot.load_justifications(path)

    def test_empty_reason_raises(self, pot, tmp_path: Path) -> None:
        path = tmp_path / "j.json"
        path.write_text(json.dumps({"version": 1, "entries": [self._entry(pot, reason="  ")]}))
        with pytest.raises(pot.ConfigError):
            pot.load_justifications(path)

    def test_duplicate_entries_raise(self, pot, tmp_path: Path) -> None:
        entry = self._entry(pot)
        path = tmp_path / "j.json"
        path.write_text(json.dumps({"version": 1, "entries": [entry, entry]}))
        with pytest.raises(pot.ConfigError):
            pot.load_justifications(path)


# ------------------------------------------------------------------- the gate


class TestAuditReport:
    def test_uncovered_violation_fails(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def f():\n    while True:\n        pass\n")
        report = pot.audit(tmp_path, pot.AuditConfig(), ())
        assert not report.ok
        assert len(report.uncovered) == 1
        assert report.stale == []

    def test_justified_violation_passes(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def f():\n    while True:\n        pass\n")
        entry = pot.Justification(rule="POT1", file="m.py", scope="f", detail="loop:L2", reason="supervised loop")
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert report.ok
        assert len(report.covered) == 1

    def test_stale_justification_fails(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def f():\n    return 1\n")
        entry = pot.Justification(rule="POT1", file="m.py", scope="f", detail="loop:L2", reason="gone")
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert not report.ok
        assert report.stale == [entry]

    def test_ratchet_growth_fails(self, pot, tmp_path: Path) -> None:
        body = "".join(f"    x{i} = {i}\n" for i in range(120))
        (tmp_path / "m.py").write_text(f"def f():\n{body}")
        entry = pot.Justification(rule="POT2", file="m.py", scope="f", reason="baseline", lines=100)
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert not report.ok
        assert "grew" in report.uncovered[0].message

    def test_ratchet_at_baseline_passes(self, pot, tmp_path: Path) -> None:
        body = "".join(f"    x{i} = {i}\n" for i in range(99))
        (tmp_path / "m.py").write_text(f"def f():\n{body}")  # exactly 100 lines
        entry = pot.Justification(rule="POT2", file="m.py", scope="f", reason="baseline", lines=100)
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert report.ok

    def test_ratchet_shrunk_below_ceiling_makes_entry_stale(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def f():\n    return 1\n")
        entry = pot.Justification(rule="POT2", file="m.py", scope="f", reason="baseline", lines=100)
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert not report.ok
        assert report.stale == [entry]

    def test_density_ratchet_regression_fails(self, pot, tmp_path: Path) -> None:
        (tmp_path / "store.py").write_text("def f():\n    return 1\n\ndef g():\n    return 2\n")
        entry = pot.Justification(rule="POT3", file="store.py", scope="<module>", reason="typed errors", density=0.5)
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert not report.ok

    def test_density_ratchet_at_baseline_passes(self, pot, tmp_path: Path) -> None:
        (tmp_path / "store.py").write_text("def f():\n    assert True\n\ndef g():\n    return 2\n")
        entry = pot.Justification(rule="POT3", file="store.py", scope="<module>", reason="typed errors", density=0.5)
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert report.ok

    def test_count_covers_repeated_violations(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def heal() -> bool:\n    return True\ndef f():\n    heal()\n    heal()\n")
        entry = pot.Justification(rule="POT5", file="m.py", scope="f", detail="heal", reason="best effort", count=2)
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert report.ok

    def test_count_mismatch_is_stale(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def heal() -> bool:\n    return True\ndef f():\n    heal()\n")
        entry = pot.Justification(rule="POT5", file="m.py", scope="f", detail="heal", reason="best effort", count=2)
        report = pot.audit(tmp_path, pot.AuditConfig(), (entry,))
        assert not report.ok


class TestRebaseline:
    """--rebaseline deliberately re-pins the ratchet entries to the tree."""

    def _seed_tree(self, tmp_path: Path) -> None:
        body = "".join(f"    x{i} = {i}\n" for i in range(79))
        (tmp_path / "m.py").write_text(f"def f():\n{body}")  # 80 lines

    def test_rebaseline_writes_current_measurements(self, pot, tmp_path: Path) -> None:
        self._seed_tree(tmp_path)
        path = tmp_path / "j.json"
        path.write_text(json.dumps({"version": 1, "entries": []}))
        code = pot.main(["--src", str(tmp_path), "--justifications", str(path), "--rebaseline"])
        assert code == 0
        entries = pot.load_justifications(path)
        pot2 = [e for e in entries if e.rule == "POT2"]
        assert len(pot2) == 1 and pot2[0].lines == 80
        # After the re-pin the gate passes.
        assert pot.main(["--src", str(tmp_path), "--justifications", str(path)]) == 0

    def test_rebaseline_reports_raised_baselines(self, pot, tmp_path: Path, capsys) -> None:
        self._seed_tree(tmp_path)
        path = tmp_path / "j.json"
        old = {"rule": "POT2", "file": "m.py", "scope": "f", "lines": 70, "reason": "older baseline"}
        path.write_text(json.dumps({"version": 1, "entries": [old]}))
        code = pot.main(["--src", str(tmp_path), "--justifications", str(path), "--rebaseline"])
        assert code == 0
        assert "RAISED" in capsys.readouterr().out
        entries = pot.load_justifications(path)
        assert next(e for e in entries if e.rule == "POT2").lines == 80

    def test_rebaseline_drops_beaten_ratchets(self, pot, tmp_path: Path, capsys) -> None:
        (tmp_path / "m.py").write_text("def f():\n    return 1\n")
        path = tmp_path / "j.json"
        old = {"rule": "POT2", "file": "m.py", "scope": "f", "lines": 90, "reason": "shrunk away"}
        path.write_text(json.dumps({"version": 1, "entries": [old]}))
        code = pot.main(["--src", str(tmp_path), "--justifications", str(path), "--rebaseline"])
        assert code == 0
        assert "DROPPED" in capsys.readouterr().out
        assert pot.load_justifications(path) == ()

    def test_rebaseline_keeps_other_rules_untouched(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def f():\n    while True:\n        pass\n")
        path = tmp_path / "j.json"
        kept = {"rule": "POT1", "file": "m.py", "scope": "f", "detail": "loop:L2", "reason": "supervised"}
        path.write_text(json.dumps({"version": 1, "entries": [kept]}))
        assert pot.main(["--src", str(tmp_path), "--justifications", str(path), "--rebaseline"]) == 0
        entries = pot.load_justifications(path)
        assert [e.rule for e in entries] == ["POT1"]


class TestCLI:
    def test_exit_zero_on_clean_tree(self, pot, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("def f():\n    return 1\n")
        assert pot.main(["--src", str(tmp_path), "--justifications", str(tmp_path / "none.json")]) == 0

    def test_exit_one_on_violation(self, pot, tmp_path: Path, capsys) -> None:
        (tmp_path / "m.py").write_text("def f():\n    while True:\n        pass\n")
        code = pot.main(["--src", str(tmp_path)])
        assert code == 1
        assert "POT1" in capsys.readouterr().out

    def test_exit_two_on_missing_src(self, pot, tmp_path: Path) -> None:
        assert pot.main(["--src", str(tmp_path / "nope")]) == 2

    def test_json_output_parses(self, pot, tmp_path: Path, capsys) -> None:
        (tmp_path / "m.py").write_text("def f():\n    while True:\n        pass\n")
        code = pot.main(["--src", str(tmp_path), "--json"])
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["uncovered"][0]["rule"] == "POT1"
        assert payload["ok"] is False

    def test_default_paths_point_at_repo(self, pot) -> None:
        assert "src" in str(pot.DEFAULT_SRC)
        assert pot.DEFAULT_JUSTIFICATIONS.name == "power_of_ten_justifications.json"


class TestRepoGate:
    """The CI gate: the real src/ tree must pass with the shipped justifications."""

    def test_real_repo_has_no_unjustified_violations(self, pot) -> None:
        entries = pot.load_justifications(JUSTIFICATIONS)
        report = pot.audit(SRC_ROOT, pot.AuditConfig(), entries)
        assert report.ok, (
            f"{len(report.uncovered)} unjustified: {[str(v) for v in report.uncovered[:10]]}; "
            f"{len(report.stale)} stale: {[str(j) for j in report.stale[:10]]}"
        )

    def test_gate_is_deterministic(self, pot) -> None:
        entries = pot.load_justifications(JUSTIFICATIONS)
        first = pot.audit(SRC_ROOT, pot.AuditConfig(), entries)
        second = pot.audit(SRC_ROOT, pot.AuditConfig(), entries)
        assert [str(v) for v in first.violations] == [str(v) for v in second.violations]

    def test_every_justification_has_real_reason(self, pot) -> None:
        entries = pot.load_justifications(JUSTIFICATIONS)
        assert entries, "justifications file must not be empty"
        for entry in entries:
            assert len(entry.reason.split()) >= 4, f"reason too thin: {entry!r}"
