#!/usr/bin/env python3
"""Power-of-Ten static gates for ``src/know_your_context`` (W16, NASA/JPL).

Gerard Holzmann's Power of Ten rules for safety-critical code, mapped to the
checks that are meaningful and statically provable for this Python codebase:

* **POT1 — bounded loops.** No provably unbounded loop without a reachable
  exit: ``while True``/``while 1`` (any truthy constant) and ``for`` over
  ``itertools.count()``/``repeat(x)``/``cycle(...)`` must contain a ``break``
  belonging to that loop, or a ``return``/``raise`` reachable from its body.
  A ``break`` inside a nested ``for`` and a ``return`` inside a nested
  function do not count.
* **POT2 — function length.** JPL caps a function at one printed page
  (≈60 lines). New code must stay under the ceiling; oversized legacy
  functions are *ratcheted*: each carries a per-case justification recording
  its current length, and it may shrink but never grow. Shrinking back under
  the ceiling retires the entry (a stale entry fails the gate).
* **POT3 — assert density.** JPL expects an average of two runtime
  assertions per function. This codebase's data path prefers typed
  fail-closed exceptions over ``assert`` (asserts vanish under ``-O``), so
  the critical modules are ratcheted at their recorded density: the density
  may improve, never regress. Each critical module carries a per-case
  justification documenting the recorded density and the reason.
* **POT4 — no unbounded recursion.** No function may be reachable from
  itself through the module's static call graph (direct self-calls and
  mutual recursion alike) without a per-case justification arguing the bound
  (e.g. recursion depth is bounded by the depth of a validated tree).
* **POT5 — checked status returns.** A call to a function annotated
  ``-> bool`` (a success/status flag) must not be dropped as a bare
  expression statement: the result must be assigned, returned, asserted or
  branched on. Attribute calls only resolve when the short name maps to
  exactly one bool-status function in the same module.

The tool is a *gate*: exit 0 when every violation is covered by a live
justification and no justification is stale, exit 1 otherwise, exit 2 on
usage/configuration errors. Justifications live in
``tools/power_of_ten_justifications.json``; a stale entry (matching nothing,
or a ratchet baseline that has been beaten) fails the gate so the file can
never silently rot.

Usage: ``python tools/power_of_ten.py [--src DIR] [--justifications FILE]
[--max-function-lines N] [--json]``
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT_SRC = Path(__file__).resolve().parents[1] / "src" / "know_your_context"
DEFAULT_JUSTIFICATIONS = Path(__file__).resolve().parent / "power_of_ten_justifications.json"

JPL_PAGE_LINES = 60
JPL_ASSERTS_PER_FUNCTION = 2.0

# The modules whose correctness the whole system rests on: the event store,
# audit chain, backup/restore, projection, service write path, the daemon and
# its wire/capability boundary, the kill-switch board and the redaction seam.
DEFAULT_CRITICAL_MODULES: tuple[str, ...] = (
    "store.py",
    "audit.py",
    "backup.py",
    "projection.py",
    "service.py",
    "importer.py",
    "events.py",
    "domain.py",
    "contracts.py",
    "mcp_server.py",
    "serve/superpower.py",
    "serve/middleware.py",
    "serve/rpc.py",
    "serve/daemon_config.py",
    "ops/switches.py",
    "security/__init__.py",
)

RULE_DESCRIPTIONS: dict[str, str] = {
    "POT1": "no provably unbounded loop without a reachable exit",
    "POT2": "function length within the page ceiling (ratcheted legacy baseline)",
    "POT3": "critical-module assert density never regresses (JPL target: 2/function)",
    "POT4": "no recursion without a documented boundedness argument",
    "POT5": "bool status returns are never dropped unchecked",
}

_UNBOUNDED_ITERTOOLS = ("count", "repeat", "cycle")


class ConfigError(Exception):
    """The justification file or CLI configuration is invalid (exit 2)."""


@dataclass(frozen=True)
class AuditConfig:
    max_function_lines: int = JPL_PAGE_LINES
    critical_modules: tuple[str, ...] = DEFAULT_CRITICAL_MODULES
    min_assert_density: float = JPL_ASSERTS_PER_FUNCTION


@dataclass(frozen=True)
class Violation:
    """One rule hit. Identity is ``(rule, file, scope, detail)``."""

    rule: str
    file: str
    line: int
    scope: str
    detail: str
    message: str

    def key(self) -> tuple[str, str, str, str]:
        return (self.rule, self.file, self.scope, self.detail)

    def __str__(self) -> str:
        return f"{self.rule} {self.file}:{self.line} {self.scope} {self.detail} — {self.message}"


@dataclass(frozen=True)
class Justification:
    """One documented per-case exception; ratchet fields tighten over time."""

    rule: str
    file: str
    scope: str
    reason: str
    detail: str = ""
    lines: int | None = None
    density: float | None = None
    count: int = 1

    def key(self) -> tuple[str, str, str, str]:
        return (self.rule, self.file, self.scope, self.detail)


@dataclass
class AuditReport:
    violations: list[Violation] = field(default_factory=list)
    covered: list[tuple[Violation, Justification]] = field(default_factory=list)
    uncovered: list[Violation] = field(default_factory=list)
    stale: list[Justification] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.uncovered and not self.stale


# ------------------------------------------------------------------ helpers


def _qualnames(tree: ast.Module) -> dict[ast.AST, str]:
    """Map every FunctionDef/AsyncFunctionDef in the module to its qualname."""
    qualnames: dict[ast.AST, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualnames[child] = f"{prefix}{child.name}"
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return qualnames


def _enclosing_scope(qualnames: dict[ast.AST, str], line: int) -> str:
    """The tightest function qualname containing *line*, else ``<module>``."""
    best = "<module>"
    best_span: int | None = None
    for fn, qualname in qualnames.items():
        start = getattr(fn, "lineno", 0)
        end = getattr(fn, "end_lineno", None) or start
        if start <= line <= end and (best_span is None or (end - start) < best_span):
            best = qualname
            best_span = end - start
    return best


def _calls_of(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Calls belonging to *fn* itself (nested function bodies excluded)."""
    calls: list[ast.Call] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested callable owns its own calls
            if isinstance(child, ast.Call):
                calls.append(child)
            visit(child)

    visit(fn)
    return calls


def _loop_has_exit(loop: ast.While | ast.For) -> bool:
    """True when the loop body carries an exit that terminates *this* loop.

    A ``break`` counts only when no deeper ``for``/``while`` wraps it; a
    ``return``/``raise`` counts anywhere at function level. Nested function,
    lambda and class bodies are not part of the loop.
    """

    found = False

    def visit(node: ast.AST, depth: int) -> None:
        nonlocal found
        if found:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return
        if isinstance(node, (ast.Return, ast.Raise)):
            found = True
            return
        if isinstance(node, ast.Break):
            if depth == 0:
                found = True
            return
        next_depth = depth + 1 if isinstance(node, (ast.While, ast.For, ast.AsyncFor)) else depth
        for child in ast.iter_child_nodes(node):
            visit(child, next_depth)

    for statement in loop.body:
        visit(statement, 0)
    return found


# ------------------------------------------------------------------- rules


def _analyze_loops(tree: ast.Module, filename: str, qualnames: dict[ast.AST, str]) -> list[Violation]:
    itertools_modules: set[str] = set()
    itertools_functions: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "itertools":
                    itertools_modules.add(alias.asname or "itertools")
        elif isinstance(node, ast.ImportFrom) and node.module == "itertools":
            for alias in node.names:
                if alias.name in _UNBOUNDED_ITERTOOLS:
                    itertools_functions[alias.asname or alias.name] = alias.name

    violations: list[Violation] = []
    for node in ast.walk(tree):
        label: str | None = None
        loop: ast.While | ast.For | None = None
        if isinstance(node, ast.While):
            if isinstance(node.test, ast.Constant) and bool(node.test.value):
                label = "while True"
                loop = node
        elif isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
            func = node.iter.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _UNBOUNDED_ITERTOOLS
                and isinstance(func.value, ast.Name)
                and func.value.id in itertools_modules
                and not (func.attr == "repeat" and len(node.iter.args) >= 2)
            ):
                label = f"itertools.{func.attr}"
                loop = node
            elif isinstance(func, ast.Name) and func.id in itertools_functions:
                original = itertools_functions[func.id]
                if not (original == "repeat" and len(node.iter.args) >= 2):
                    label = f"itertools.{original}"
                    loop = node
        if label is None or loop is None or _loop_has_exit(loop):
            continue
        violations.append(
            Violation(
                rule="POT1",
                file=filename,
                line=loop.lineno,
                scope=_enclosing_scope(qualnames, loop.lineno),
                detail=f"loop:L{loop.lineno}",
                message=f"{label} loop has no reachable break/return/raise — every loop must be provably bounded",
            )
        )
    return violations


def _analyze_lengths(tree: ast.Module, filename: str, qualnames: dict[ast.AST, str], ceiling: int) -> list[Violation]:
    violations: list[Violation] = []
    for fn, qualname in qualnames.items():
        assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        length = (fn.end_lineno or fn.lineno) - fn.lineno + 1
        if length > ceiling:
            violations.append(
                Violation(
                    rule="POT2",
                    file=filename,
                    line=fn.lineno,
                    scope=qualname,
                    detail="",
                    message=f"function is {length} lines, over the {ceiling}-line page ceiling",
                )
            )
    return violations


def assert_density(tree: ast.Module) -> tuple[int, int, float]:
    """(asserts, functions, density) of one module."""
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    asserts = sum(isinstance(n, ast.Assert) for n in ast.walk(tree))
    density = asserts / len(functions) if functions else 0.0
    return asserts, len(functions), density


def _analyze_assert_density(tree: ast.Module, filename: str, config: AuditConfig) -> list[Violation]:
    if filename not in config.critical_modules:
        return []
    asserts, functions, density = assert_density(tree)
    if functions == 0 or density >= config.min_assert_density:
        return []
    return [
        Violation(
            rule="POT3",
            file=filename,
            line=1,
            scope="<module>",
            detail="",
            message=(
                f"critical module assert density {density:.2f} ({asserts}/{functions}) "
                f"is below the JPL target {config.min_assert_density:.2f}"
            ),
        )
    ]


def _analyze_recursion(tree: ast.Module, filename: str, qualnames: dict[ast.AST, str]) -> list[Violation]:
    """Flag every function reachable from itself via the module call graph."""
    name_set = set(qualnames.values())
    top_level = {name for name in name_set if "." not in name}
    short_map: dict[str, list[str]] = {}
    for name in name_set:
        short_map.setdefault(name.rsplit(".", 1)[-1], []).append(name)

    # Names imported into the module namespace shadow same-named module
    # functions: a bare call to an imported name is not recursion.
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".", 1)[0])

    def resolve_name(name: str) -> str | None:
        if name in imported:
            return None
        if name in top_level:
            return name
        matches = short_map.get(name, [])
        return matches[0] if len(matches) == 1 else None

    edges: dict[str, set[str]] = {}
    for fn, qualname in qualnames.items():
        assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        targets: set[str] = set()
        class_prefix = qualname.rsplit(".", 1)[0] if "." in qualname else None
        for call in _calls_of(fn):
            func = call.func
            if isinstance(func, ast.Name):
                resolved = resolve_name(func.id)
                if resolved is not None:
                    targets.add(resolved)
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in ("self", "cls")
                and class_prefix is not None
            ):
                candidate = f"{class_prefix}.{func.attr}"
                if candidate in name_set:
                    targets.add(candidate)
        edges[qualname] = targets

    cyclic: set[str] = set()
    for start in edges:
        stack = list(edges[start])
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node == start:
                cyclic.add(start)
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(edges.get(node, ()))

    violations: list[Violation] = []
    for qualname in sorted(cyclic):
        fn_line = next(getattr(fn, "lineno", 1) for fn, name in qualnames.items() if name == qualname)
        direct = qualname in edges.get(qualname, set())
        detail = "direct" if direct else "mutual"
        violations.append(
            Violation(
                rule="POT4",
                file=filename,
                line=fn_line,
                scope=qualname,
                detail=detail,
                message=f"function is reachable from itself ({detail}) — recursion needs a documented bound",
            )
        )
    return violations


def _analyze_unchecked_status(
    tree: ast.Module,
    filename: str,
    qualnames: dict[ast.AST, str],
    package_bool_names: dict[str, int] | None = None,
) -> list[Violation]:
    """Bare expression-statement calls to ``-> bool`` functions.

    *package_bool_names* (short name -> package-wide definition count) lets
    the tree-level audit resolve attribute calls across modules; a name is
    only actionable when it is unambiguous.
    """
    bool_qualnames = {
        qualname
        for fn, qualname in qualnames.items()
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and fn.returns is not None
        and ast.unparse(fn.returns) == "bool"
    }
    short_names: dict[str, list[str]] = {}
    for qualname in bool_qualnames:
        short_names.setdefault(qualname.rsplit(".", 1)[-1], []).append(qualname)

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        name: str | None = None
        if isinstance(func, ast.Name):
            if func.id in short_names:
                name = func.id
            elif package_bool_names is not None and package_bool_names.get(func.id, 0) == 1:
                name = func.id  # imported or global; unique package-wide
        elif isinstance(func, ast.Attribute):
            if len(short_names.get(func.attr, [])) == 1:
                name = func.attr  # unambiguous inside this module
            elif package_bool_names is not None and package_bool_names.get(func.attr, 0) == 1:
                name = func.attr  # unambiguous across the package
        if name is None:
            continue
        violations.append(
            Violation(
                rule="POT5",
                file=filename,
                line=node.lineno,
                scope=_enclosing_scope(qualnames, node.lineno),
                detail=name,
                message=f"bool status return of {name}() dropped unchecked — check, assign or justify",
            )
        )
    return violations


# -------------------------------------------------------------------- driver


def analyze_module(
    filename: str,
    source: str,
    config: AuditConfig,
    package_bool_names: dict[str, int] | None = None,
) -> list[Violation]:
    """All Power-of-Ten violations in one module, deterministic order."""
    tree = ast.parse(source, filename=filename)
    qualnames = _qualnames(tree)
    violations = [
        *_analyze_loops(tree, filename, qualnames),
        *_analyze_lengths(tree, filename, qualnames, config.max_function_lines),
        *_analyze_assert_density(tree, filename, config),
        *_analyze_recursion(tree, filename, qualnames),
        *_analyze_unchecked_status(tree, filename, qualnames, package_bool_names),
    ]
    violations.sort(key=lambda v: (v.rule, v.file, v.scope, v.detail, v.line))
    return violations


def iter_sources(src_root: Path) -> Iterable[tuple[str, str]]:
    for path in sorted(src_root.rglob("*.py")):
        yield path.relative_to(src_root).as_posix(), path.read_text(encoding="utf-8")


def _violation_length(violation: Violation) -> int:
    for token in violation.message.split():
        if token.isdigit():
            return int(token)
    raise ConfigError(f"cannot parse length from {violation.message!r}")


def _violation_density(violation: Violation) -> float:
    return float(violation.message.split("density ", 1)[1].split(" ", 1)[0])


def _covers(entry: Justification, violation: Violation) -> bool:
    """True when *entry* honestly covers *violation* (ratchet-aware)."""
    if entry.key() != violation.key():
        return False
    if entry.rule == "POT2":
        return entry.lines is not None and entry.lines >= _violation_length(violation)
    if entry.rule == "POT3":
        return entry.density is not None and entry.density <= _violation_density(violation)
    return True


def audit(src_root: Path, config: AuditConfig, justifications: Sequence[Justification]) -> AuditReport:
    src_root = Path(src_root)
    if not src_root.is_dir():
        raise ConfigError(f"src root {src_root} does not exist")
    report = AuditReport()
    modules = list(iter_sources(src_root))
    # Package-wide index of bool-status function short names, so POT5 can
    # resolve cross-module attribute calls when the name is unambiguous.
    package_bool_names: dict[str, int] = {}
    for _, source in modules:
        tree = ast.parse(source)
        for fn, qualname in _qualnames(tree).items():
            if (
                isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and fn.returns is not None
                and ast.unparse(fn.returns) == "bool"
            ):
                short = qualname.rsplit(".", 1)[-1]
                package_bool_names[short] = package_bool_names.get(short, 0) + 1
    for filename, source in modules:
        report.violations.extend(analyze_module(filename, source, config, package_bool_names))

    for violation in report.violations:
        match = next(
            (entry for entry in justifications if _covers(entry, violation)),
            None,
        )
        if match is None:
            # A key-matching justification whose ratchet baseline was broken
            # turns into an explicit "grew"/"regressed" message.
            same_key = next((entry for entry in justifications if entry.key() == violation.key()), None)
            if same_key is not None and violation.rule == "POT2":
                violation = replace(
                    violation,
                    message=f"{violation.message} — grew beyond the recorded baseline of {same_key.lines} lines",
                )
            elif same_key is not None and violation.rule == "POT3":
                violation = replace(
                    violation,
                    message=f"{violation.message} — regressed below the recorded baseline of {same_key.density:.2f}",
                )
            report.uncovered.append(violation)
        else:
            report.covered.append((violation, match))

    for entry in justifications:
        matched = sum(1 for _, covered in report.covered if covered == entry)
        if matched != entry.count:
            # Either it matched nothing, or its recorded count disagrees with
            # the real number of violations — both mean the justification
            # file no longer tells the truth about the code.
            report.stale.append(entry)
    return report


# --------------------------------------------------------------- config file


def load_justifications(path: Path) -> tuple[Justification, ...]:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"justifications file {path} does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"justifications file {path} is not valid JSON: {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ConfigError(f'justifications file {path} must be a JSON object with "version": 1')
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ConfigError(f'justifications file {path} needs an "entries" list')

    entries: list[Justification] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise ConfigError(f"entry #{index} is not an object")
        for required in ("rule", "file", "scope", "reason"):
            if required not in raw:
                raise ConfigError(f"entry #{index} misses required field {required!r}")
        rule = str(raw["rule"])
        if rule not in RULE_DESCRIPTIONS:
            raise ConfigError(f"entry #{index} has unknown rule {rule!r}")
        reason = str(raw["reason"])
        if not reason.strip():
            raise ConfigError(f"entry #{index} has an empty reason")
        count = raw.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ConfigError(f"entry #{index} count must be a positive int")
        lines = raw.get("lines")
        density = raw.get("density")
        if rule == "POT2" and (not isinstance(lines, int) or isinstance(lines, bool) or lines < 1):
            raise ConfigError(f'POT2 entry #{index} must record the ratchet baseline as a positive int "lines"')
        if rule == "POT3" and (not isinstance(density, (int, float)) or isinstance(density, bool) or density < 0):
            raise ConfigError(f'POT3 entry #{index} must record the ratchet baseline as a non-negative "density"')
        entry = Justification(
            rule=rule,
            file=str(raw["file"]),
            scope=str(raw["scope"]),
            detail=str(raw.get("detail", "")),
            reason=reason,
            lines=lines if isinstance(lines, int) and not isinstance(lines, bool) else None,
            density=float(density) if isinstance(density, (int, float)) and not isinstance(density, bool) else None,
            count=count,
        )
        if entry.key() in seen:
            raise ConfigError(f"duplicate justification for {entry.key()}")
        seen.add(entry.key())
        entries.append(entry)
    return tuple(entries)


# ------------------------------------------------------------------ rebaseline

_RATCHET_REASON_POT2 = (
    "W16 ratchet: legacy function above the 60-line page ceiling; "
    "recorded at its measured length, may shrink but never grow."
)
_RATCHET_REASON_POT3 = (
    "Fail-closed typed exceptions and pydantic validation replace asserts on this data path "
    "(asserts vanish under -O); ratcheted at the measured density, may improve, never regress."
)


def rebaseline(src_root: Path, config: AuditConfig, justifications_path: Path) -> int:
    """Deliberately re-pin the POT2/POT3 ratchet entries to the current tree.

    POT1/POT4/POT5 entries are never touched — they carry per-case reasoning a
    regeneration cannot invent. Every baseline raise is printed (RAISED) so a
    quiet growth cannot hide inside a re-pin; beaten ratchets are DROPPED.
    Existing reasons survive a re-pin.
    """
    try:
        existing = load_justifications(justifications_path)
    except ConfigError:
        existing = ()
    kept = [entry for entry in existing if entry.rule not in ("POT2", "POT3")]
    old_by_key = {entry.key(): entry for entry in existing if entry.rule in ("POT2", "POT3")}

    report = audit(src_root, config, ())
    regenerated: list[Justification] = []
    for violation in report.violations:
        if violation.rule not in ("POT2", "POT3"):
            continue
        old = old_by_key.pop(violation.key(), None)
        default_reason = _RATCHET_REASON_POT2 if violation.rule == "POT2" else _RATCHET_REASON_POT3
        reason = old.reason if old is not None else default_reason
        if violation.rule == "POT2":
            lines = _violation_length(violation)
            if old is not None and old.lines is not None and lines > old.lines:
                print(f"RAISED {violation.file} {violation.scope}: {old.lines} -> {lines} lines")
            regenerated.append(
                Justification(rule="POT2", file=violation.file, scope=violation.scope, reason=reason, lines=lines)
            )
        else:
            density = _violation_density(violation)
            if old is not None and old.density is not None and density > old.density + 1e-9:
                print(f"IMPROVED {violation.file}: assert density {old.density:.2f} -> {density:.2f}")
            regenerated.append(
                Justification(rule="POT3", file=violation.file, scope=violation.scope, reason=reason, density=density)
            )
    for entry in sorted(old_by_key.values(), key=lambda e: e.key()):
        print(f"DROPPED {entry.rule} {entry.file} {entry.scope} — baseline beaten or target gone")

    entries = [*kept, *regenerated]
    payload = {
        "version": 1,
        "policy": (
            "NASA/JPL Power-of-Ten mapping for this codebase; see tools/power_of_ten.py docstring "
            "and docs/recovery/w16-modelbased.md. Ratchet entries record the measured baseline: "
            "the code may improve, never regress. A stale entry fails the gate."
        ),
        "entries": [
            {
                "rule": entry.rule,
                "file": entry.file,
                "scope": entry.scope,
                **({"detail": entry.detail} if entry.detail else {}),
                "reason": entry.reason,
                **({"lines": entry.lines} if entry.lines is not None else {}),
                **({"density": entry.density} if entry.density is not None else {}),
                **({"count": entry.count} if entry.count != 1 else {}),
            }
            for entry in entries
        ],
    }
    justifications_path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"rebaseline: {len(regenerated)} ratchet entries re-pinned, {len(kept)} reasoned entries kept")
    return 0


# ------------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NASA/JPL Power-of-Ten static gate for src/know_your_context")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="source tree to audit")
    parser.add_argument("--justifications", type=Path, default=DEFAULT_JUSTIFICATIONS, help="justification JSON")
    parser.add_argument("--max-function-lines", type=int, default=JPL_PAGE_LINES, help="POT2 page ceiling")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument(
        "--rebaseline",
        action="store_true",
        help="deliberately re-pin the POT2/POT3 ratchet baselines to the current tree (prints every RAISED baseline)",
    )
    args = parser.parse_args(argv)

    if not args.src.is_dir():
        print(f"error: src root {args.src} does not exist", file=sys.stderr)
        return 2
    config = AuditConfig(max_function_lines=args.max_function_lines)
    if args.rebaseline:
        return rebaseline(args.src, config, args.justifications)
    try:
        entries = load_justifications(args.justifications) if args.justifications.is_file() else ()
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    report = audit(args.src, config, entries)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "violations": len(report.violations),
                    "uncovered": [v.__dict__ for v in report.uncovered],
                    "stale": [list(j.key()) for j in report.stale],
                    "covered": len(report.covered),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"power-of-ten: {len(report.violations)} violation(s), {len(report.covered)} justified")
        for violation in report.uncovered:
            print(f"UNJUSTIFIED {violation}")
        for entry in report.stale:
            print(f"STALE {entry.rule} {entry.file} {entry.scope} {entry.detail} — {entry.reason}")
        if report.ok:
            print("power-of-ten: PASS")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
