"""VERDICT 7-level instrument — metric catalog and measurement machinery.

Every metric is measured by THIS code on all three repos, never by hand.
Each :class:`Metric` carries enough information for the ledger to record the
exact CLI command that reproduces its raw value::

    python bench/verdict/run.py metric <metric_id> --repo <repo_name>

Repos
-----
- ``ours``      : this repository (know-your-context), Python.
- ``codegraph`` : reference opponent, TypeScript (default /root/src/codegraph).
- ``graphify``  : reference opponent, Python (default /root/src/graphify).

Roots are overridable via ``KYS_VERDICT_ROOT_OURS`` / ``_CODEGRAPH`` /
``_GRAPHIFY`` so tests can point the instrument at fixture repos.

Levels (operator-defined)
-------------------------
1. profesional                 — engineering professionalism surface
2. codebase                    — structural code inventory
3. arsitektur                  — architecture surface
4. flow engineering            — operational flow machinery
5. kompleksitas codebase       — code complexity + depth
6. kompleksitas arsitektur     — architecture complexity
7. kompleksitas flow engineering — flow complexity

Scoring rules (pure, deterministic)
-----------------------------------
- per metric: ``relative = 100 * ours / opponent``; if opponent == 0:
  ``100.0`` when ours == 0 (parity), else ``None`` (unbounded — reported, but
  excluded from the mean so a zero in an opponent can never fabricate a
  number).
- per level: mean of the defined metric relatives; ``unbounded`` count is
  reported alongside.
- composite: weighted mean of the 7 level relatives (weights declared in
  :data:`LEVELS`; equal by default).
- combined verdict: mean of the composite vs codegraph and vs graphify.
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

EXCLUDE_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


# --------------------------------------------------------------------- repos
@dataclass(frozen=True)
class RepoSpec:
    name: str
    root: Path
    lang: str  # "python" | "typescript"
    src_globs: tuple[str, ...]
    test_globs: tuple[str, ...]
    src_roots: tuple[str, ...]  # paths relative to root that hold top-level layers


def repo_specs() -> dict[str, RepoSpec]:
    def root(env: str, default: Path) -> Path:
        raw = os.environ.get(env)
        return Path(raw).resolve() if raw else default

    return {
        "ours": RepoSpec(
            "ours",
            root("KYS_VERDICT_ROOT_OURS", REPO_ROOT),
            "python",
            ("src/**/*.py",),
            ("tests/**/*.py",),
            ("src/know_your_context",),
        ),
        "codegraph": RepoSpec(
            "codegraph",
            root("KYS_VERDICT_ROOT_CODEGRAPH", Path("/root/src/codegraph")),
            "typescript",
            ("src/**/*.ts",),
            ("__tests__/**/*.ts",),
            ("src",),
        ),
        "graphify": RepoSpec(
            "graphify",
            root("KYS_VERDICT_ROOT_GRAPHIFY", Path("/root/src/graphify")),
            "python",
            ("graphify/**/*.py",),
            ("tests/**/*.py",),
            ("graphify",),
        ),
    }


def _collect(root: Path, globs: tuple[str, ...]) -> list[Path]:
    seen: set[Path] = set()
    for pattern in globs:
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            seen.add(p)
    return sorted(seen)


class RepoData:
    """Lazily-loaded, cached view of one repo. Parsing happens at most once
    per file no matter how many metrics read it."""

    def __init__(self, spec: RepoSpec):
        self.spec = spec
        self.src_files = _collect(spec.root, spec.src_globs)
        self.test_files = _collect(spec.root, spec.test_globs)
        self.doc_files = _collect(spec.root, ("*.md", "docs/**/*.md"))
        self._text: dict[Path, str] = {}
        self._ast: dict[Path, ast.Module | None] = {}

    def text(self, path: Path) -> str:
        if path not in self._text:
            try:
                self._text[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._text[path] = ""
        return self._text[path]

    def ast_of(self, path: Path) -> ast.Module | None:
        if path not in self._ast:
            try:
                self._ast[path] = ast.parse(self.text(path))
            except SyntaxError, ValueError:
                self._ast[path] = None
        return self._ast[path]

    def py_asts(self, files: list[Path]):
        for p in files:
            tree = self.ast_of(p)
            if tree is not None:
                yield p, tree


# ----------------------------------------------------------------- primitives
def _nonblank(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _rx(rd: RepoData, files: list[Path], pattern: str, flags: int = 0) -> int:
    rx = re.compile(pattern, flags)
    return sum(len(rx.findall(rd.text(p))) for p in files)


def _rx_distinct(rd: RepoData, files: list[Path], pattern: str, flags: int = 0) -> int:
    rx = re.compile(pattern, flags)
    found: set[str] = set()
    for p in files:
        for m in rx.findall(rd.text(p)):
            found.add(m if isinstance(m, str) else m[0])
    return len(found)


def _base_names(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
        elif isinstance(base, ast.Subscript):
            sub = ast.ClassDef(name="", bases=[base.value], keywords=[], body=[], decorator_list=[])  # type: ignore[call-arg]
            names.extend(_base_names(sub))
    return names


def _py_max_nesting(tree: ast.AST) -> int:
    nested = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.With,
        ast.AsyncWith,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
    )
    nested += (ast.Match,) if hasattr(ast, "Match") else ()

    def visit(node: ast.AST, depth: int) -> int:
        best = depth
        for child in ast.iter_child_nodes(node):
            d = depth + 1 if isinstance(child, nested) else depth
            best = max(best, visit(child, d))
        return best

    return visit(tree, 0)


def _brace_max_nesting(text: str) -> int:
    """Max `{` nesting depth with comments and strings stripped (TS/JS)."""
    depth = best = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
        elif two == "/*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif c in "'\"`":
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == c:
                    i += 1
                    break
                i += 1
        elif c == "{":
            depth += 1
            best = max(best, depth)
            i += 1
        elif c == "}":
            depth = max(0, depth - 1)
            i += 1
        else:
            i += 1
    return best


def _func_defs(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


# =================================================================== LEVEL 1
def m_source_loc(rd: RepoData) -> float:
    return float(sum(_nonblank(rd.text(p)) for p in rd.src_files))


def m_test_loc(rd: RepoData) -> float:
    return float(sum(_nonblank(rd.text(p)) for p in rd.test_files))


def m_test_files(rd: RepoData) -> float:
    return float(len(rd.test_files))


def m_test_cases(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        return float(_rx(rd, rd.test_files, r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE))
    return float(_rx(rd, rd.test_files, r"\b(?:it|test)\s*(?:\.\w+)*\s*\("))


def m_doc_files(rd: RepoData) -> float:
    return float(len(rd.doc_files))


def m_doc_lines(rd: RepoData) -> float:
    return float(sum(_nonblank(rd.text(p)) for p in rd.doc_files))


def m_changelog_sections(rd: RepoData) -> float:
    total = 0
    for p in sorted(rd.spec.root.glob("CHANGELOG*")):
        if p.is_file():
            total += len(re.findall(r"^##\s+", rd.text(p), re.MULTILINE))
    return float(total)


def m_comment_lines(rd: RepoData) -> float:
    pattern = r"^\s*#" if rd.spec.lang == "python" else r"^\s*//"
    return float(_rx(rd, rd.src_files, pattern, re.MULTILINE))


def m_license_files(rd: RepoData) -> float:
    return float(sum(1 for p in rd.spec.root.glob("LICENSE*") if p.is_file()))


def m_pinned_deps(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for name in ("pyproject.toml", "requirements.txt"):
            p = rd.spec.root / name
            if p.is_file():
                count += len(re.findall(r"[A-Za-z0-9_.\-]+==", rd.text(p)))
        count += sum(
            len(re.findall(r"[A-Za-z0-9_.\-]+==", rd.text(p))) for p in sorted(rd.spec.root.glob("requirements*.txt"))
        )
        return float(count)
    pkg = rd.spec.root / "package.json"
    if not pkg.is_file():
        return 0.0
    try:
        doc = json.loads(rd.text(pkg))
    except json.JSONDecodeError:
        return 0.0
    count = 0
    for section in ("dependencies", "devDependencies"):
        deps = doc.get(section) or {}
        count += sum(1 for v in deps.values() if isinstance(v, str) and re.match(r"^\d", v))
    return float(count)


# =================================================================== LEVEL 2
def m_packages(rd: RepoData) -> float:
    return float(len({p.parent for p in rd.src_files}))


def m_public_functions(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            count += sum(1 for f in _func_defs(tree) if not f.name.startswith("_"))
        return float(count)
    return float(
        _rx(rd, rd.src_files, r"\bexport\s+(?:async\s+)?function\s+\w+")
        + _rx(rd, rd.src_files, r"\bexport\s+const\s+\w+\s*=\s*(?:async\s*)?\(")
    )


def m_classes(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = sum(len([n for n in ast.walk(t) if isinstance(n, ast.ClassDef)]) for _, t in rd.py_asts(rd.src_files))
        return float(count)
    return float(_rx(rd, rd.src_files, r"\bclass\s+\w+"))


def m_typed_errors(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    b.endswith(("Error", "Exception")) for b in _base_names(node)
                ):
                    count += 1
        return float(count)
    return float(_rx(rd, rd.src_files, r"\bclass\s+\w+\s+extends\s+\w*Error\b"))


def m_enums(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any("Enum" in b for b in _base_names(node)):
                    count += 1
        return float(count)
    return float(_rx(rd, rd.src_files, r"\benum\s+\w+"))


def m_interfaces(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if "Protocol" in _base_names(node):
                    count += 1
                elif any(
                    (isinstance(d, ast.Name) and d.id == "dataclass")
                    or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                    or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass")
                    or (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "dataclass")
                    for d in node.decorator_list
                ):
                    count += 1
        return float(count)
    return float(_rx(rd, rd.src_files, r"\binterface\s+\w+"))


def m_assertions(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            count += len([n for n in ast.walk(tree) if isinstance(n, ast.Assert)])
        return float(count + _rx(rd, rd.src_files, r"\binvariant\s*\("))
    return float(_rx(rd, rd.src_files, r"\bassert\b|\binvariant\s*\("))


def m_named_constants(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            for node in tree.body:
                targets: list[ast.expr] = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", t.id):
                        count += 1
        return float(count)
    return float(_rx(rd, rd.src_files, r"\bconst\s+[A-Z][A-Z0-9_]{2,}\s*="))


def m_typed_signatures(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            count += sum(1 for f in _func_defs(tree) if f.returns is not None)
        return float(count)
    return float(_rx(rd, rd.src_files, r"\)\s*:\s*[A-Za-z]"))


def m_decorators(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    count += len(node.decorator_list)
        return float(count)
    return float(_rx(rd, rd.src_files, r"^\s*@\w+", re.MULTILINE))


# =================================================================== LEVEL 3
def _layer_of(spec: RepoSpec, path: Path) -> str | None:
    for sr in spec.src_roots:
        base = spec.root / sr
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        return rel.parts[0] if len(rel.parts) > 1 else "(root)"
    return None


def m_layers(rd: RepoData) -> float:
    layers = {_layer_of(rd.spec, p) for p in rd.src_files}
    layers.discard(None)
    layers.discard("(root)")
    return float(len(layers))


def m_cli_commands(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        return float(_rx(rd, rd.src_files, r"\badd_parser\s*\(") + _rx(rd, rd.src_files, r"\badd_command\s*\("))
    cli_files = [p for p in rd.src_files if "cli" in p.name.lower() or "bin" in p.parent.name.lower()]
    return float(_rx(rd, cli_files, r"\.command\s*\(") + _rx(rd, cli_files, r"\bcase\s+[\"'][a-z][a-z0-9-]*[\"']\s*:"))


def m_http_routes(rd: RepoData) -> float:
    return float(
        _rx(rd, rd.src_files, r"[\"'](/v1/|/api/)[^\"']*[\"']")
        + _rx(rd, rd.src_files, r"@\w+\.(?:get|post|put|delete|route)\s*\(")
        + _rx(rd, rd.src_files, r"\.(?:get|post|put|delete)\s*\(\s*[\"']/")
    )


def m_mcp_tools(rd: RepoData) -> float:
    mcp_files = [p for p in rd.src_files if "mcp" in p.name.lower() or "tools" in p.name.lower()]
    count = (
        _rx(rd, mcp_files, r"\btypes\.Tool\s*\(")
        + _rx(rd, mcp_files, r"\bTool\s*\(\s*\{")
        + _rx(rd, mcp_files, r"[\s{,]name:\s*[\"'][a-z][a-z0-9_]+[\"']")
    )
    if rd.spec.lang == "python":
        # tools declared as keys of a module-level *_TOOL* mapping
        # (e.g. _TOOL_TO_OPERATION: {"knowledge_query": "knowledge.query", ...})
        for _, tree in rd.py_asts(mcp_files):
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and "TOOL" in t.id for t in targets):
                        count += sum(
                            1 for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
                        )
    return float(count)


def m_db_tables(rd: RepoData) -> float:
    sql_files = _collect(rd.spec.root, ("**/*.sql",))
    return float(
        _rx(rd, rd.src_files, r"\bCREATE\s+TABLE\b", re.IGNORECASE)
        + _rx(rd, sql_files, r"\bCREATE\s+TABLE\b", re.IGNORECASE)
    )


def m_migrations(rd: RepoData) -> float:
    mig_files = [p for p in _collect(rd.spec.root, ("**/*",)) if p.is_file() and "migrations" in p.parts]
    return float(len(mig_files) + _rx(rd, rd.src_files, r"\bALTER\s+TABLE\b", re.IGNORECASE))


_ENV_RX_PY = r"(?:os\.environ(?:\.get)?\(?\[?|os\.getenv\(\s*)[\"']([A-Z][A-Z0-9_]*)[\"']"
_ENV_RX_TS = r"\bprocess\.env\.([A-Z][A-Z0-9_]*)"
_ENV_NAME_RX = re.compile(r"[A-Z][A-Z0-9_]{2,}")
_KILL_RX = re.compile(r"(?:DISABLE|_OFF$|^NO_|ENABLE|_KILL)")


def _py_env_names_ast(rd: RepoData) -> set[str]:
    """AST-based env-var detection for Python: direct string literals plus
    module-level constant indirection (``X = "KYS_FOO"`` then
    ``os.environ.get(X)``)."""
    names: set[str] = set()
    files = rd.src_files + rd.test_files
    for _, tree in rd.py_asts(files):
        consts: dict[str, str] = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and _ENV_NAME_RX.fullmatch(node.value.value)
            ):
                consts[node.targets[0].id] = node.value.value
        for node in ast.walk(tree):
            arg: ast.expr | None = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
                if node.func.attr in ("get", "getenv"):
                    arg = node.args[0]
            elif isinstance(node, ast.Subscript):
                arg = node.slice
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if _ENV_NAME_RX.fullmatch(arg.value):
                    names.add(arg.value)
            elif isinstance(arg, ast.Name) and arg.id in consts:
                names.add(consts[arg.id])
    return names


def _env_names(rd: RepoData) -> set[str]:
    if rd.spec.lang == "python":
        return _py_env_names_ast(rd)
    rx = re.compile(_ENV_RX_TS)
    found: set[str] = set()
    for p in rd.src_files + rd.test_files:
        found.update(rx.findall(rd.text(p)))
    return found


def _env_sites(rd: RepoData) -> list[str]:
    """All env-var read *sites* (with repetition) as names."""
    if rd.spec.lang == "python":
        rx = re.compile(_ENV_RX_PY)
        direct: list[str] = []
        for p in rd.src_files + rd.test_files:
            direct.extend(rx.findall(rd.text(p)))
        # constant-indirect sites: count each resolved name once per file that
        # references the constant in an environ call (approximation documented).
        indirect: list[str] = []
        resolved = _py_env_names_ast(rd)
        direct_set = set(direct)
        indirect.extend(sorted(resolved - direct_set))
        return direct + indirect
    rx = re.compile(_ENV_RX_TS)
    out: list[str] = []
    for p in rd.src_files + rd.test_files:
        out.extend(rx.findall(rd.text(p)))
    return out


def m_env_vars(rd: RepoData) -> float:
    return float(len(_env_names(rd)))


def m_kill_switch_sites(rd: RepoData) -> float:
    return float(sum(1 for name in _env_sites(rd) if _KILL_RX.search(name)))


def m_config_fields(rd: RepoData) -> float:
    cfg_files = [p for p in rd.src_files if "config" in p.name.lower()]
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(cfg_files):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    count += sum(1 for s in node.body if isinstance(s, (ast.AnnAssign, ast.Assign)))
        return float(count)
    return float(_rx(rd, cfg_files, r"^\s+\w+\??\s*:\s*[\w\[\]|]+", re.MULTILINE))


def m_contract_docs(rd: RepoData) -> float:
    count = 0
    for p in rd.doc_files:
        try:
            rel = p.relative_to(rd.spec.root)
        except ValueError:
            rel = p
        if "contract" in p.name.lower() or any("contract" in part for part in rel.parts):
            count += 1
    return float(count)


# =================================================================== LEVEL 4
def m_async_functions(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = sum(
            len([n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef)]) for _, t in rd.py_asts(rd.src_files)
        )
        return float(count)
    return float(_rx(rd, rd.src_files, r"\basync\s+(?:function\s+)?\w+\s*\("))


def m_await_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\bawait\s"))


def m_lock_primitives(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\b(?:Lock|RWLock|RwLock|Mutex|Semaphore|Condition)\s*\("))


def m_queue_primitives(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\b(?:Queue|deque|SimpleQueue)\s*\(|\bmaxsize\b"))


def m_timeout_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\btimeout\b", re.IGNORECASE))


def m_retry_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\bretr(?:y|ies|ied)\b|\bbackoff\b", re.IGNORECASE))


def m_shutdown_handlers(rd: RepoData) -> float:
    return float(
        _rx(rd, rd.src_files, r"\batexit\b|\bsignal\.signal\s*\(|\bdef\s+shutdown|\bSIGINT\b|\bSIGTERM\b|\bonClose\b")
    )


def m_worker_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\bThread\s*\(|\bProcess\s*\(|\bWorker\s*\(|\bspawn\b|ThreadPool|ProcessPool"))


def m_batch_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\bbatch\w*\b", re.IGNORECASE))


def m_journal_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\baudit\w*\b|\bjournal\w*\b|\blog_event\b", re.IGNORECASE))


# =================================================================== LEVEL 5
def m_total_functions(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        return float(sum(len(_func_defs(t)) for _, t in rd.py_asts(rd.src_files)))
    return float(_rx(rd, rd.src_files, r"\bfunction\b") + _rx(rd, rd.src_files, r"=>"))


def m_branch_statements(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\b(?:if|elif|for|while|except|catch|switch|case)\b"))


def m_max_nesting(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        best = 0
        for _, tree in rd.py_asts(rd.src_files):
            best = max(best, _py_max_nesting(tree))
        return float(best)
    return float(max((_brace_max_nesting(rd.text(p)) for p in rd.src_files), default=0))


def m_imports(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        count = 0
        for _, tree in rd.py_asts(rd.src_files):
            count += len([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))])
        return float(count)
    return float(_rx(rd, rd.src_files, r"^\s*import\s", re.MULTILINE) + _rx(rd, rd.src_files, r"\brequire\s*\("))


def m_error_code_literals(rd: RepoData) -> float:
    rx = re.compile(r"[\"']([A-Z][A-Z0-9_]{3,})[\"']")
    found: set[str] = set()
    for p in rd.src_files:
        found.update(rx.findall(rd.text(p)))
    return float(len(found))


def m_property_tests(rd: RepoData) -> float:
    return float(_rx(rd, rd.test_files, r"@given\s*\(|\bfc\.property\s*\(|\bfast-check\b"))


def m_parametrized_tests(rd: RepoData) -> float:
    return float(_rx(rd, rd.test_files, r"\bparametrize\b|\.each\s*\("))


def m_test_fixtures(rd: RepoData) -> float:
    return float(
        _rx(rd, rd.test_files, r"@pytest\.fixture|\bbeforeEach\b|\bbeforeAll\b|^\s*describe\s*\(", re.MULTILINE)
    )


def m_loc_per_function(rd: RepoData) -> float:
    funcs = m_total_functions(rd)
    return round(m_source_loc(rd) / funcs, 4) if funcs else 0.0


def m_distinct_import_roots(rd: RepoData) -> float:
    roots: set[str] = set()
    if rd.spec.lang == "python":
        for _, tree in rd.py_asts(rd.src_files):
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    roots.add(node.module.split(".")[0])
    else:
        rx = re.compile(r"\bfrom\s+[\"']([^\"'./][^\"']*)[\"']")
        for p in rd.src_files:
            roots.update(m.group(1).split("/")[0] for m in rx.finditer(rd.text(p)))
    roots.discard("")
    return float(len(roots))


# =================================================================== LEVEL 6
def m_components(rd: RepoData) -> float:
    counts: dict[str, int] = {}
    for p in rd.src_files:
        layer = _layer_of(rd.spec, p)
        if layer and layer != "(root)":
            counts[layer] = counts.get(layer, 0) + 1
    return float(sum(1 for c in counts.values() if c >= 2))


def m_inter_layer_edges(rd: RepoData) -> float:
    layers: set[str] = set()
    for p in rd.src_files:
        layer = _layer_of(rd.spec, p)
        if layer and layer != "(root)":
            layers.add(layer)
    edges: set[tuple[str, str]] = set()
    if rd.spec.lang == "python":
        pkg_root = rd.spec.src_roots[0].rsplit("/", 1)[-1]
        for p, tree in rd.py_asts(rd.src_files):
            src_layer = _layer_of(rd.spec, p)
            if not src_layer:
                continue
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module if node.level == 0 else f"{pkg_root}.{node.module}"
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        mod = a.name
                        if mod.startswith(f"{pkg_root}."):
                            target = mod.split(".")[1]
                            if target in layers and target != src_layer:
                                edges.add((src_layer, target))
                    continue
                if mod and mod.startswith(f"{pkg_root}."):
                    target = mod.split(".")[1]
                    if target in layers and target != src_layer:
                        edges.add((src_layer, target))
    else:
        rx = re.compile(r"\bfrom\s+[\"'](\.{1,2}/[^\"']+)[\"']")
        for p in rd.src_files:
            src_layer = _layer_of(rd.spec, p)
            if not src_layer:
                continue
            for m in rx.finditer(rd.text(p)):
                target = m.group(1)
                if target.startswith("../"):
                    first = target[3:].split("/")[0]
                    if first in layers and first != src_layer:
                        edges.add((src_layer, first))
    return float(len(edges))


def m_surfaces(rd: RepoData) -> float:
    facets = 0
    names = {p.name.lower() for p in rd.src_files}
    dirs = {part for p in rd.src_files for part in p.parts}
    if any(n.startswith("cli") for n in names) or (rd.spec.root / "bin").is_dir():
        facets += 1
    if any("http" in n for n in names) or _rx(rd, rd.src_files, r"[\"'](/v1/|/api/)"):
        facets += 1
    if any("mcp" in n for n in names):
        facets += 1
    if "ui" in dirs or any("webview" in n for n in names):
        facets += 1
    if any("export" in n for n in names):
        facets += 1
    return float(facets)


def m_schema_versions(rd: RepoData) -> float:
    rx = re.compile(r"\bSCHEMA_?VERSION\b\s*[:=]\s*[\"']?(\d+)", re.IGNORECASE)
    found: set[str] = set()
    for p in rd.src_files:
        found.update(rx.findall(rd.text(p)))
    return float(len(found))


def m_middleware(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\b\w*[Mm]iddleware\b"))


def m_adapters(rd: RepoData) -> float:
    return float(sum(1 for p in rd.src_files if "adapter" in p.name.lower() or "bridge" in p.name.lower()))


def m_event_types(rd: RepoData) -> float:
    """Distinct dotted lowercase literals (``word.word``) across source —
    operation/event-type vocabulary proxy (e.g. ``knowledge.query``)."""
    return float(_rx_distinct(rd, rd.src_files, r"[\"']([a-z][a-z0-9_]*\.[a-z0-9_.]+)[\"']"))


def m_registrations(rd: RepoData) -> float:
    return float(
        _rx(
            rd,
            rd.src_files,
            r"\bregister_\w+\s*\(|\badd_parser\s*\(|\badd_command\s*\(|\.command\s*\(|\badd_route\s*\(",
        )
    )


def m_config_keys(rd: RepoData) -> float:
    return float(len(_env_names(rd)) + m_config_fields(rd))


def m_kill_switch_breadth(rd: RepoData) -> float:
    return float(sum(1 for name in _env_names(rd) if _KILL_RX.search(name)))


# =================================================================== LEVEL 7
_CONCURRENCY_PRIMITIVES = (
    "Lock",
    "RLock",
    "Semaphore",
    "BoundedSemaphore",
    "Event",
    "Condition",
    "Queue",
    "deque",
    "Thread",
    "Process",
    "ThreadPoolExecutor",
    "ProcessPoolExecutor",
    "asyncio",
    "threading",
    "multiprocessing",
    "Mutex",
    "RwLock",
    "Promise",
    "Atomics",
    "Worker",
)


def m_concurrency_breadth(rd: RepoData) -> float:
    rx = re.compile(r"\b(" + "|".join(_CONCURRENCY_PRIMITIVES) + r")\b")
    found: set[str] = set()
    for p in rd.src_files:
        found.update(rx.findall(rd.text(p)))
    return float(len(found))


def m_pipeline_stages(rd: RepoData) -> float:
    pipe_files = [p for p in rd.src_files if "pipeline" in p.name.lower()]
    return float(
        len(pipe_files)
        + _rx(rd, rd.src_files, r"\bclass\s+\w*Stage\b|\b(?:def|function)\s+\w*stage\w*\s*\(|\bstage_\w+")
    )


def m_recovery_paths(rd: RepoData) -> float:
    return float(
        _rx(
            rd,
            rd.src_files,
            r"\b(?:def|function)\s+\w*(?:restore|rollback|recover|backup|relearn|rebuild|repair)\w*\s*\(",
            re.IGNORECASE,
        )
    )


def m_typed_raise_sites(rd: RepoData) -> float:
    if rd.spec.lang == "python":
        return float(_rx_distinct(rd, rd.src_files, r"\braise\s+(\w+)"))
    return float(_rx_distinct(rd, rd.src_files, r"\bthrow\s+new\s+(\w+)"))


def m_throttle_sites(rd: RepoData) -> float:
    return float(
        _rx(
            rd,
            rd.src_files,
            r"\bthrottle\w*\b|\btoken_?bucket\b|\bquota\w*\b|\brate_?limit\w*\b|\badmission\b",
            re.IGNORECASE,
        )
    )


def m_deadline_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\bdeadline\w*\b", re.IGNORECASE))


def m_watermark_lease_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\bwatermark\w*\b|\blease\w*\b", re.IGNORECASE))


def m_flow_tests(rd: RepoData) -> float:
    rx = re.compile(
        r"(?:def\s+(test_\w*(?:storm|concurren|simulat|deadline|throttle|fairness|burst)\w*)\s*\()"
        r"|(?:\b(?:it|test)\s*\(\s*[\"'][^\"']*(?:storm|concurren|deadline|throttle|fairness|burst))",
        re.IGNORECASE,
    )
    count = 0
    for p in rd.test_files:
        count += len(rx.findall(rd.text(p)))
    return float(count)


def m_watchdog_sites(rd: RepoData) -> float:
    return float(_rx(rd, rd.src_files, r"\bwatchdog\w*\b|\bliveness\b|\bhealth\b", re.IGNORECASE))


def m_drain_sites(rd: RepoData) -> float:
    return float(
        _rx(rd, rd.src_files, r"\b(?:def|function)\s+\w*(?:drain|shutdown|stop)\w*\s*\(|\.drain\s*\(", re.IGNORECASE)
    )


# ------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class Level:
    id: str
    name: str
    weight: float
    description: str


@dataclass(frozen=True)
class Metric:
    id: str
    level: str
    description: str
    measure: object = field(compare=False)  # Callable[[RepoData], float]


LEVELS: tuple[Level, ...] = (
    Level("l1", "profesional", 1.0, "engineering professionalism surface (LOC, tests, docs, changelog, license, pins)"),
    Level("l2", "codebase", 1.0, "structural code inventory (modules, functions, classes, typed errors, enums)"),
    Level(
        "l3",
        "arsitektur",
        1.0,
        "architecture surface (layers, CLI/HTTP/MCP, schema, migrations, config, kill switches)",
    ),
    Level(
        "l4",
        "flow engineering",
        1.0,
        "operational flow machinery (async, locks, queues, timeouts, retries, shutdown)",
    ),
    Level(
        "l5",
        "kompleksitas codebase",
        1.0,
        "code complexity + depth (functions, branches, nesting, imports, test rigor)",
    ),
    Level(
        "l6",
        "kompleksitas arsitektur",
        1.0,
        "architecture complexity (components, edges, surfaces, middleware, events)",
    ),
    Level(
        "l7",
        "kompleksitas flow engineering",
        1.0,
        "flow complexity (concurrency breadth, pipelines, recovery, throttling)",
    ),
)

METRICS: tuple[Metric, ...] = (
    # L1 — profesional
    Metric("l1.source_loc", "l1", "non-blank source lines of code", m_source_loc),
    Metric("l1.test_loc", "l1", "non-blank test lines of code", m_test_loc),
    Metric("l1.test_files", "l1", "number of test files", m_test_files),
    Metric("l1.test_cases", "l1", "test case count (def test_ / it()|test())", m_test_cases),
    Metric("l1.doc_files", "l1", "markdown doc file count", m_doc_files),
    Metric("l1.doc_lines", "l1", "non-blank doc lines", m_doc_lines),
    Metric("l1.changelog_sections", "l1", "'## ' sections in CHANGELOG*", m_changelog_sections),
    Metric("l1.comment_lines", "l1", "comment lines in source (# or //)", m_comment_lines),
    Metric("l1.license_files", "l1", "LICENSE* file count", m_license_files),
    Metric("l1.pinned_deps", "l1", "exactly-pinned dependencies (== / no ^~)", m_pinned_deps),
    # L2 — codebase
    Metric("l2.packages", "l2", "distinct source packages (dirs with code)", m_packages),
    Metric("l2.public_functions", "l2", "public function count", m_public_functions),
    Metric("l2.classes", "l2", "class definition count", m_classes),
    Metric("l2.typed_errors", "l2", "typed error classes (Exception/Error heirs)", m_typed_errors),
    Metric("l2.enums", "l2", "enum type count", m_enums),
    Metric("l2.interfaces", "l2", "interfaces (TS) / Protocol+dataclass (PY)", m_interfaces),
    Metric("l2.assertions", "l2", "assert/invariant sites in source", m_assertions),
    Metric("l2.named_constants", "l2", "module-level UPPER_SNAKE constants", m_named_constants),
    Metric("l2.typed_signatures", "l2", "functions with return-type annotation", m_typed_signatures),
    Metric("l2.decorators", "l2", "decorator applications", m_decorators),
    # L3 — arsitektur
    Metric("l3.layers", "l3", "top-level architecture layers under src roots", m_layers),
    Metric("l3.cli_commands", "l3", "CLI command registrations", m_cli_commands),
    Metric("l3.http_routes", "l3", "HTTP route registrations (/v1//api paths)", m_http_routes),
    Metric("l3.mcp_tools", "l3", "MCP tool definitions", m_mcp_tools),
    Metric("l3.db_tables", "l3", "CREATE TABLE count (src + .sql)", m_db_tables),
    Metric("l3.migrations", "l3", "migration files + ALTER TABLE sites", m_migrations),
    Metric("l3.env_vars", "l3", "distinct environment variables read", m_env_vars),
    Metric("l3.kill_switch_sites", "l3", "kill-switch env usage sites (DISABLE/ENABLE/NO_/_OFF)", m_kill_switch_sites),
    Metric("l3.config_fields", "l3", "config dataclass/interface fields", m_config_fields),
    Metric("l3.contract_docs", "l3", "contract documentation files", m_contract_docs),
    # L4 — flow engineering
    Metric("l4.async_functions", "l4", "async function count", m_async_functions),
    Metric("l4.await_sites", "l4", "await sites", m_await_sites),
    Metric("l4.lock_primitives", "l4", "lock/semaphore/mutex constructions", m_lock_primitives),
    Metric("l4.queue_primitives", "l4", "queue constructions / maxsize bounds", m_queue_primitives),
    Metric("l4.timeout_sites", "l4", "timeout mentions in source", m_timeout_sites),
    Metric("l4.retry_sites", "l4", "retry/backoff sites", m_retry_sites),
    Metric("l4.shutdown_handlers", "l4", "shutdown/signal handlers", m_shutdown_handlers),
    Metric("l4.worker_sites", "l4", "thread/process/worker/spawn sites", m_worker_sites),
    Metric("l4.batch_sites", "l4", "batch processing sites", m_batch_sites),
    Metric("l4.journal_sites", "l4", "audit/journal/log_event sites", m_journal_sites),
    # L5 — kompleksitas codebase
    Metric("l5.total_functions", "l5", "total function count (PY AST; TS function+=>)", m_total_functions),
    Metric(
        "l5.branch_statements",
        "l5",
        "branch statements (if/for/while/except/catch/case/switch)",
        m_branch_statements,
    ),
    Metric("l5.max_nesting", "l5", "max nesting depth (PY AST; TS brace depth)", m_max_nesting),
    Metric("l5.imports", "l5", "import statements", m_imports),
    Metric(
        "l5.error_code_literals",
        "l5",
        "distinct UPPER_SNAKE string literals (error-code proxy)",
        m_error_code_literals,
    ),
    Metric("l5.property_tests", "l5", "property-based tests (@given / fc.property)", m_property_tests),
    Metric("l5.parametrized_tests", "l5", "parametrized test sites", m_parametrized_tests),
    Metric("l5.test_fixtures", "l5", "test fixtures (pytest.fixture/beforeEach/describe)", m_test_fixtures),
    Metric("l5.loc_per_function", "l5", "source LOC per function", m_loc_per_function),
    Metric("l5.distinct_import_roots", "l5", "distinct imported module roots", m_distinct_import_roots),
    # L6 — kompleksitas arsitektur
    Metric("l6.components", "l6", "top-level components with >=2 files", m_components),
    Metric("l6.inter_layer_edges", "l6", "distinct cross-layer static import edges", m_inter_layer_edges),
    Metric("l6.surfaces", "l6", "external surfaces present (cli/http/mcp/ui/exporter, 0-5)", m_surfaces),
    Metric("l6.schema_versions", "l6", "distinct schema version constants", m_schema_versions),
    Metric("l6.middleware", "l6", "middleware constructs", m_middleware),
    Metric("l6.adapters", "l6", "adapter/bridge modules", m_adapters),
    Metric("l6.event_types", "l6", "distinct dotted event-type literals in event/audit modules", m_event_types),
    Metric("l6.registrations", "l6", "registry/command registrations", m_registrations),
    Metric("l6.config_keys", "l6", "total config keys (env vars + config fields)", m_config_keys),
    Metric("l6.kill_switch_breadth", "l6", "distinct kill-switch env names", m_kill_switch_breadth),
    # L7 — kompleksitas flow engineering
    Metric(
        "l7.concurrency_breadth",
        "l7",
        "distinct concurrency primitives in use (of 20 known)",
        m_concurrency_breadth,
    ),
    Metric("l7.pipeline_stages", "l7", "pipeline modules + stage constructs", m_pipeline_stages),
    Metric("l7.recovery_paths", "l7", "restore/rollback/recover/backup/relearn function defs", m_recovery_paths),
    Metric("l7.typed_raise_sites", "l7", "distinct exception types raised/thrown", m_typed_raise_sites),
    Metric("l7.throttle_sites", "l7", "throttle/quota/rate-limit/admission sites", m_throttle_sites),
    Metric("l7.deadline_sites", "l7", "deadline sites", m_deadline_sites),
    Metric("l7.watermark_lease_sites", "l7", "watermark/lease sites", m_watermark_lease_sites),
    Metric("l7.flow_tests", "l7", "flow stress tests (storm/concurrency/simulation/deadline/throttle)", m_flow_tests),
    Metric("l7.watchdog_sites", "l7", "watchdog/liveness/health sites", m_watchdog_sites),
    Metric("l7.drain_sites", "l7", "drain/shutdown/stop function defs", m_drain_sites),
)

METRIC_BY_ID = {m.id: m for m in METRICS}
LEVEL_BY_ID = {level.id: level for level in LEVELS}


def measure_all() -> dict[str, dict[str, float]]:
    """Measure every metric on every repo. Returns {metric_id: {repo: value}}."""
    out: dict[str, dict[str, float]] = {}
    data = {name: RepoData(spec) for name, spec in repo_specs().items()}
    for metric in METRICS:
        fn = metric.measure
        out[metric.id] = {name: float(fn(rd)) for name, rd in data.items()}  # type: ignore[operator]
    return out


def measure_one(metric_id: str, repo_name: str) -> float:
    metric = METRIC_BY_ID[metric_id]
    spec = repo_specs()[repo_name]
    fn = metric.measure
    return float(fn(RepoData(spec)))  # type: ignore[operator]
