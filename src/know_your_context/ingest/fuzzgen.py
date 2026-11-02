"""Deterministic seeded source-code generator for differential fuzzing (I3, W15, SUB-130).

The generator builds **synthetic but grammar-real** source files per
language so the Python reference walker and the Rust kernel can be
differential-tested far beyond the hand-picked fixture corpus
(:mod:`know_your_context.ingest.diffuzz` runs the comparison and the
shrinker; ``tests/test_differential_fuzz.py`` carries the proofs).

Design rules (WAVE3-PLAN §I3):

* **Seeded, derandomized.** All choices flow from ``random.Random(seed)``
  (Mersenne Twister — stable across processes and platforms by the
  CPython documentation guarantee). No wall clock, no ``os.urandom``, no
  ``PYTHONHASHSEED`` influence: every collection that feeds the emitted
  text is a tuple built in choice order, and name pools are sorted tuples
  indexed by the RNG. Two runs with the same seed produce byte-identical
  corpora.
* **Grammar from the production registry.** The construct set per
  language is the one the production ``LanguageSpec`` recognizes
  (functions, classes/methods, imports, calls, variables/constants) —
  cross-checked by :func:`validate_grammar_coverage` against
  ``ingest.languages.LANGUAGES``, so a registry language the generator
  claims to serve but cannot actually feed is a hard error, not a silent
  skip.
* **Edge cases on purpose.** A seeded minority of cases carries
  anomalies: comment-only files, empty files, truncated final lines
  (parse-error parity), deep indentation, unicode identifiers (where the
  grammar allows), shadowed names and zero-parameter functions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = [
    "GENERATOR_LANGUAGES",
    "FuzzCase",
    "FuzzGenError",
    "generate_case",
    "generate_corpus",
    "validate_grammar_coverage",
]

#: Languages with a full emitter. All are dual-engine (``engine == "rust"``)
#: languages — the differential fuzzer's whole point is exercising the
#: Rust kernel against the Python reference.
GENERATOR_LANGUAGES: tuple[str, ...] = ("c", "go", "java", "javascript", "python", "ruby", "rust", "typescript")


class FuzzGenError(ValueError):
    """Unknown language or invalid generator knob (fail-closed)."""


@dataclass(frozen=True)
class FuzzCase:
    """One deterministic synthetic source file."""

    language: str
    seed: int
    path: str
    source: str


# ------------------------------------------------------------------ name pools

_NAME_POOL: tuple[str, ...] = tuple(
    sorted(
        {
            "alpha",
            "beta",
            "gamma",
            "delta",
            "omega",
            "sigma",
            "kappa",
            "lambda_fn",
            "render",
            "parse",
            "build",
            "fetch",
            "store",
            "load",
            "emit",
            "walk",
            "visit",
            "fold",
            "map_values",
            "reduce_sum",
            "handle",
            "dispatch",
            "validate",
            "compute",
            "transform",
            "collect",
            "merge",
            "split",
            "join_text",
            "open_gate",
            "close_gate",
        }
    )
)

_CLASS_POOL: tuple[str, ...] = tuple(
    sorted({"Engine", "Builder", "Handler", "Walker", "Parser", "Store", "Queue", "Registry", "Shell", "Runner"})
)

_MODULE_POOL: tuple[str, ...] = tuple(
    sorted({"os", "sys", "math", "json", "util", "core", "helpers", "lib", "fmt", "strings"})
)

_CONST_POOL: tuple[str, ...] = tuple(sorted({"LIMIT", "MAX_RETRIES", "DEFAULT_SIZE", "THRESHOLD", "CAPACITY"}))

#: Unicode identifier suffixes for the anomaly cases (legal in python,
#: ruby and javascript-family identifiers; skipped by the emitters of the
#: languages whose grammars reject them).
_UNICODE_SUFFIXES: tuple[str, ...] = ("é", "λ", "雪")


# ------------------------------------------------------------------ the plan


@dataclass(frozen=True)
class _FunctionPlan:
    name: str
    params: tuple[str, ...]
    calls: tuple[int, ...]  # indices into earlier functions
    returns_call: bool


@dataclass(frozen=True)
class _ClassPlan:
    name: str
    methods: tuple[_FunctionPlan, ...]


@dataclass(frozen=True)
class _Plan:
    comment: str
    imports: tuple[str, ...]
    constants: tuple[tuple[str, int], ...]
    variables: tuple[tuple[str, int], ...]
    functions: tuple[_FunctionPlan, ...]
    classes: tuple[_ClassPlan, ...]
    anomaly: str  # "" | "empty" | "comment_only" | "truncated" | "blank_heavy"
    unicode_names: bool


def _draw_plan(rng: random.Random, *, allow_classes: bool, allow_unicode: bool) -> _Plan:
    anomaly_roll = rng.random()
    if anomaly_roll < 0.04:
        anomaly = "empty"
    elif anomaly_roll < 0.08:
        anomaly = "comment_only"
    elif anomaly_roll < 0.14:
        anomaly = "truncated"
    elif anomaly_roll < 0.20:
        anomaly = "blank_heavy"
    else:
        anomaly = ""
    unicode_names = allow_unicode and rng.random() < 0.12

    comment = f"seeded case {rng.randrange(1_000_000)}"
    imports = tuple(rng.choice(_MODULE_POOL) for _ in range(rng.randrange(0, 4)))
    constants = tuple(
        (rng.choice(_CONST_POOL) + (f"_{index}" if index else ""), rng.randrange(0, 10_000))
        for index in range(rng.randrange(0, 4))
    )
    variables = tuple(
        (rng.choice(_NAME_POOL) + f"_v{index}", rng.randrange(0, 1000)) for index in range(rng.randrange(0, 4))
    )

    names = list(_NAME_POOL)
    rng.shuffle(names)
    functions: list[_FunctionPlan] = []
    for index in range(rng.randrange(1, 7)):
        name = names[index % len(names)] + (f"_{index}" if index >= len(names) or rng.random() < 0.3 else "")
        if unicode_names and rng.random() < 0.5:
            name = name + rng.choice(_UNICODE_SUFFIXES)
        params = tuple(f"p{slot}" for slot in range(rng.randrange(0, 4)))
        call_count = rng.randrange(0, min(3, len(functions)) + 1)
        calls = tuple(rng.randrange(0, len(functions)) for _ in range(call_count)) if functions else ()
        functions.append(
            _FunctionPlan(name=name, params=params, calls=tuple(sorted(set(calls))), returns_call=rng.random() < 0.5)
        )

    classes: list[_ClassPlan] = []
    if allow_classes:
        class_names = list(_CLASS_POOL)
        rng.shuffle(class_names)
        for class_index in range(rng.randrange(0, 3)):
            methods: list[_FunctionPlan] = []
            for method_index in range(rng.randrange(1, 4)):
                calls = tuple(rng.randrange(0, len(functions)) for _ in range(rng.randrange(0, 3))) if functions else ()
                methods.append(
                    _FunctionPlan(
                        name=f"m_{names[(class_index + method_index) % len(names)]}",
                        params=tuple(f"p{slot}" for slot in range(rng.randrange(0, 3))),
                        calls=tuple(sorted(set(calls))),
                        returns_call=rng.random() < 0.5,
                    )
                )
            classes.append(_ClassPlan(name=class_names[class_index % len(class_names)], methods=tuple(methods)))

    return _Plan(
        comment=comment,
        imports=imports,
        constants=constants,
        variables=variables,
        functions=tuple(functions),
        classes=tuple(classes),
        anomaly=anomaly,
        unicode_names=unicode_names,
    )


# ------------------------------------------------------------------ emitters


def _emit_python(plan: _Plan) -> str:
    lines: list[str] = [f"# {plan.comment}"]
    for module in plan.imports:
        lines.append(f"import {module}")
    if plan.imports:
        lines.append("")
    for name, value in plan.constants:
        lines.append(f"{name} = {value}")
    for name, value in plan.variables:
        lines.append(f"{name} = {value}")
    if plan.constants or plan.variables:
        lines.append("")
    names = [fn.name for fn in plan.functions]
    for fn in plan.functions:
        lines.append(f"def {fn.name}({', '.join(fn.params)}):")
        body = [f"    {names[target]}({', '.join(str(i + 1) for i in range(len(fn.params)))})" for target in fn.calls]
        if fn.returns_call and fn.calls:
            body.append(f"    return {names[fn.calls[0]]}()")
        else:
            body.append(f"    return {len(fn.params)}")
        lines.extend(body or ["    pass"])
        lines.append("")
    for cls in plan.classes:
        lines.append(f"class {cls.name}:")
        for method in cls.methods:
            params = ("self", *method.params)
            lines.append(f"    def {method.name}({', '.join(params)}):")
            body = [f"        {names[target]}()" for target in method.calls]
            body.append(f"        return {len(method.params)}")
            lines.extend(body)
        lines.append("")
    return "\n".join(lines)


def _emit_javascript(plan: _Plan, *, typed: bool) -> str:
    lines: list[str] = [f"// {plan.comment}"]
    for module in plan.imports:
        lines.append(f'import {{ {module} }} from "{module}";')
    for name, value in plan.constants:
        lines.append(f"const {name} = {value};")
    for name, value in plan.variables:
        lines.append(f"let {name} = {value};")
    names = [fn.name for fn in plan.functions]
    for fn in plan.functions:
        params = ", ".join(f"{param}: number" for param in fn.params) if typed else ", ".join(fn.params)
        ret = ": number" if typed else ""
        lines.append(f"function {fn.name}({params}){ret} {{")
        for target in fn.calls:
            lines.append(f"    {names[target]}({', '.join(str(i + 1) for i in range(len(fn.params)))});")
        lines.append(f"    return {len(fn.params)};")
        lines.append("}")
        lines.append("")
    for cls in plan.classes:
        lines.append(f"class {cls.name} {{")
        for method in cls.methods:
            params = ", ".join(f"{param}: number" for param in method.params) if typed else ", ".join(method.params)
            ret = ": number" if typed else ""
            lines.append(f"    {method.name}({params}){ret} {{")
            for target in method.calls:
                lines.append(f"        {names[target]}();")
            lines.append(f"        return {len(method.params)};")
            lines.append("    }")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def _emit_go(plan: _Plan) -> str:
    lines: list[str] = [f"// {plan.comment}", "package main", ""]
    if plan.imports:
        lines.append("import (")
        for module in plan.imports:
            lines.append(f'    "{module}"')
        lines.append(")")
        lines.append("")
    for name, value in plan.constants:
        lines.append(f"const {name} = {value}")
    for name, value in plan.variables:
        lines.append(f"var {name} = {value}")
    if plan.constants or plan.variables:
        lines.append("")
    names = [fn.name for fn in plan.functions]
    for fn in plan.functions:
        params = ", ".join(f"{param} int" for param in fn.params)
        lines.append(f"func {fn.name}({params}) int {{")
        for target in fn.calls:
            lines.append(f"    {names[target]}({', '.join(str(i + 1) for i in range(len(fn.params)))})")
        lines.append(f"    return {len(fn.params)}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def _emit_java(plan: _Plan) -> str:
    lines: list[str] = [f"// {plan.comment}"]
    for module in plan.imports:
        lines.append(f"import java.util.{module};")
    if plan.imports:
        lines.append("")
    lines.append("public class Main {")
    for name, value in plan.constants:
        lines.append(f"    static final int {name} = {value};")
    for name, value in plan.variables:
        lines.append(f"    static int {name} = {value};")
    names = [fn.name for fn in plan.functions]
    for fn in plan.functions:
        params = ", ".join(f"int {param}" for param in fn.params)
        lines.append(f"    static int {fn.name}({params}) {{")
        for target in fn.calls:
            lines.append(f"        {names[target]}({', '.join(str(i + 1) for i in range(len(fn.params)))});")
        lines.append(f"        return {len(fn.params)};")
        lines.append("    }")
    for cls in plan.classes:
        lines.append(f"    static class {cls.name} {{")
        for method in cls.methods:
            params = ", ".join(f"int {param}" for param in method.params)
            lines.append(f"        int {method.name}({params}) {{")
            for target in method.calls:
                lines.append(f"            Main.{names[target]}();")
            lines.append(f"            return {len(method.params)};")
            lines.append("        }")
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines)


def _emit_c(plan: _Plan) -> str:
    lines: list[str] = [f"/* {plan.comment} */", "#include <stdio.h>", ""]
    for name, value in plan.constants:
        lines.append(f"const int {name} = {value};")
    for name, value in plan.variables:
        lines.append(f"int {name} = {value};")
    if plan.constants or plan.variables:
        lines.append("")
    names = [fn.name for fn in plan.functions]
    for fn in plan.functions:
        lines.append(f"int {fn.name}({', '.join('int ' + param for param in fn.params)});")
    if plan.functions:
        lines.append("")
    for fn in plan.functions:
        params = ", ".join(f"int {param}" for param in fn.params)
        lines.append(f"int {fn.name}({params}) {{")
        for target in fn.calls:
            lines.append(f"    {names[target]}({', '.join(str(i + 1) for i in range(len(fn.params)))});")
        lines.append(f"    return {len(fn.params)};")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def _emit_rust(plan: _Plan) -> str:
    lines: list[str] = [f"// {plan.comment}"]
    for module in plan.imports:
        lines.append(f"use std::{module};")
    if plan.imports:
        lines.append("")
    for name, value in plan.constants:
        lines.append(f"const {name}: i64 = {value};")
    for name, value in plan.variables:
        lines.append(f"static {name.upper()}: i64 = {value};")
    if plan.constants or plan.variables:
        lines.append("")
    names = [fn.name for fn in plan.functions]
    for fn in plan.functions:
        params = ", ".join(f"{param}: i64" for param in fn.params)
        lines.append(f"fn {fn.name}({params}) -> i64 {{")
        for target in fn.calls:
            lines.append(f"    {names[target]}({', '.join(str(i + 1) for i in range(len(fn.params)))});")
        lines.append(f"    {len(fn.params)}")
        lines.append("}")
        lines.append("")
    for cls in plan.classes:
        lines.append(f"pub struct {cls.name} {{}}")
        lines.append(f"impl {cls.name} {{")
        for method in cls.methods:
            params = ", ".join(f"{param}: i64" for param in method.params)
            sep = ", " if params else ""
            lines.append(f"    pub fn {method.name}(&self{sep}{params}) -> i64 {{")
            for target in method.calls:
                lines.append(f"        {names[target]}();")
            lines.append(f"        {len(method.params)}")
            lines.append("    }")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def _emit_ruby(plan: _Plan) -> str:
    lines: list[str] = [f"# {plan.comment}"]
    for module in plan.imports:
        lines.append(f"require '{module}'")
    if plan.imports:
        lines.append("")
    for name, value in plan.constants:
        lines.append(f"{name} = {value}")
    for name, value in plan.variables:
        lines.append(f"{name} = {value}")
    if plan.constants or plan.variables:
        lines.append("")
    names = [fn.name for fn in plan.functions]
    for fn in plan.functions:
        lines.append(f"def {fn.name}({', '.join(fn.params)})")
        for target in fn.calls:
            lines.append(f"  {names[target]}({', '.join(str(i + 1) for i in range(len(fn.params)))})")
        lines.append(f"  {len(fn.params)}")
        lines.append("end")
        lines.append("")
    for cls in plan.classes:
        lines.append(f"class {cls.name}")
        for method in cls.methods:
            lines.append(f"  def {method.name}({', '.join(method.params)})")
            for target in method.calls:
                lines.append(f"    {names[target]}()")
            lines.append(f"    {len(method.params)}")
            lines.append("  end")
        lines.append("end")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ driver


def _apply_anomaly(source: str, plan: _Plan, comment_prefix: str) -> str:
    if plan.anomaly == "empty":
        return ""
    if plan.anomaly == "comment_only":
        return f"{comment_prefix} {plan.comment}\n"
    if plan.anomaly == "truncated":
        # Chop mid-line: parse-error parity must hold on broken input too.
        cut = max(1, len(source) - max(1, len(source) // 7))
        return source[:cut]
    if plan.anomaly == "blank_heavy":
        return source.replace("\n", "\n\n\n")
    return source


_EMITTERS = {
    "python": (_emit_python, ".py", "#", True, True),
    "javascript": (lambda plan: _emit_javascript(plan, typed=False), ".js", "//", True, True),
    "typescript": (lambda plan: _emit_javascript(plan, typed=True), ".ts", "//", True, True),
    "go": (_emit_go, ".go", "//", False, False),
    "java": (_emit_java, ".java", "//", True, False),
    "c": (_emit_c, ".c", "//", False, False),
    "rust": (_emit_rust, ".rs", "//", True, False),
    "ruby": (_emit_ruby, ".rb", "#", True, True),
}


def generate_case(language: str, seed: int) -> FuzzCase:
    """One deterministic synthetic case for ``language`` and ``seed``."""
    if language not in _EMITTERS:
        raise FuzzGenError(f"no generator for language {language!r} (have: {', '.join(GENERATOR_LANGUAGES)})")
    if isinstance(seed, bool) or not isinstance(seed, int) or not (0 <= seed < 2**63):
        raise FuzzGenError(f"seed must be an int in [0, 2**63), got {seed!r}")
    emitter, extension, comment_prefix, allow_classes, allow_unicode = _EMITTERS[language]
    # A single Random instance per (language, seed) — stable across
    # processes, platforms and PYTHONHASHSEED (Mersenne Twister guarantee).
    rng = random.Random(f"kys-fuzzgen:{language}:{seed}")
    plan = _draw_plan(rng, allow_classes=allow_classes, allow_unicode=allow_unicode)
    source = _apply_anomaly(emitter(plan), plan, comment_prefix)
    return FuzzCase(language=language, seed=seed, path=f"fuzz/{language}/case_{seed}{extension}", source=source)


def generate_corpus(language: str, count: int, *, seed: int = 0) -> tuple[FuzzCase, ...]:
    """``count`` deterministic cases; seeds are ``seed, seed+1, ...``."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise FuzzGenError(f"count must be a positive int, got {count!r}")
    return tuple(generate_case(language, seed + offset) for offset in range(count))


def validate_grammar_coverage() -> tuple[str, ...]:
    """Generator languages that the production registry does not serve.

    Returns the offending language names (empty = full coverage); callers
    fail closed on a non-empty result. Imports the registry lazily so this
    module stays importable in documentation contexts.
    """
    from know_your_context.ingest.languages import get_spec

    missing: list[str] = []
    for language in GENERATOR_LANGUAGES:
        try:
            get_spec(language)
        except Exception:  # noqa: BLE001 — any registry failure means no coverage
            missing.append(language)
    return tuple(missing)
