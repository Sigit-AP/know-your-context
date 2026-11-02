"""W13 (SUB-123): CHANGELOG format, docs/ integrity, docstring coverage.

These tests pin three professional-surface properties:

1. ``CHANGELOG.md`` follows Keep a Changelog: standard categories only, ISO
   dates, descending versions, per-version link references — and every wave
   tag (W1-W13) plus every cited commit hash is real (verified against git).
2. The four top-level docs (``architecture.md``, ``verdict.md``,
   ``security.md``, ``api.md``) exist, carry substance, contain no
   placeholders, and stay aligned with the code: CLI commands, MCP tools,
   HTTP operations and verdict levels are re-derived from the source modules
   and must all be documented — a stale doc fails the build.
3. Docstring coverage on the W13 scope (watcher, update pipeline, ingestion
   pipeline, resolver, serve) is 100% for modules, public classes and
   public top-level functions, measured with ``ast``.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
DOCS = REPO_ROOT / "docs"
SRC = REPO_ROOT / "src" / "know_your_context"

DOC_FILES = (
    DOCS / "architecture.md",
    DOCS / "verdict.md",
    DOCS / "security.md",
    DOCS / "api.md",
)

DOCSTRING_SCOPE: tuple[Path, ...] = (
    SRC / "source_watcher.py",
    SRC / "update_pipeline.py",
    SRC / "ingest" / "watcher.py",
    SRC / "ingest" / "pipeline.py",
    *sorted((SRC / "resolve").glob("*.py")),
    *sorted((SRC / "serve").glob("*.py")),
)

STANDARD_CATEGORIES = {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"}
PLACEHOLDER_RX = re.compile(r"\b(TODO|FIXME|XXX|TBD)\b|lorem ipsum", re.IGNORECASE)
HEADING_RX = re.compile(r"^## (\[([^\]]+)\]|.+?)(?:\s+-\s+(\d{4})-(\d{2})-(\d{2}))?\s*$", re.MULTILINE)


def _changelog_text() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


def _release_headings() -> list[re.Match[str]]:
    return [m for m in HEADING_RX.finditer(_changelog_text()) if m.group(2)]


# --------------------------------------------------------------------- changelog


def test_changelog_exists():
    assert CHANGELOG.is_file(), "CHANGELOG.md missing at repo root"


def test_changelog_title_is_first_line():
    first = _changelog_text().splitlines()[0]
    assert first.strip() == "# Changelog"


def test_changelog_declares_keepachangelog_and_semver():
    text = _changelog_text()
    assert "keepachangelog.com" in text
    assert "semver.org" in text


def test_changelog_has_unreleased_section():
    assert re.search(r"^## \[Unreleased\]", _changelog_text(), re.MULTILINE)


def test_changelog_has_three_releases():
    names = [m.group(2) for m in _release_headings() if m.group(2) != "Unreleased"]
    assert names == ["0.3.0", "0.2.0", "0.1.0"], f"unexpected release headings/order: {names}"


def test_changelog_release_dates_are_iso():
    for m in _release_headings():
        if m.group(2) == "Unreleased":
            continue
        assert m.group(3), f"release {m.group(2)} has no ISO date"
        year, month, day = int(m.group(3)), int(m.group(4)), int(m.group(5))
        assert year == 2026 and 1 <= month <= 12 and 1 <= day <= 31


def test_changelog_versions_descending():
    versions = [tuple(int(p) for p in m.group(2).split(".")) for m in _release_headings() if m.group(2) != "Unreleased"]
    assert versions == sorted(versions, reverse=True)


def test_changelog_only_standard_categories():
    cats = set(re.findall(r"^### (.+?)\s*$", _changelog_text(), re.MULTILINE))
    assert cats, "no ### categories at all"
    assert cats <= STANDARD_CATEGORIES, f"non-standard categories: {cats - STANDARD_CATEGORIES}"


def test_changelog_each_release_has_at_least_one_category():
    text = _changelog_text()
    heads = list(re.finditer(r"^## .+$", text, re.MULTILINE))
    for i, head in enumerate(heads):
        if "[" not in head.group(0):  # only release/unreleased headings need categories
            continue
        body = text[head.end() : heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        assert re.search(r"^### ", body, re.MULTILINE), f"section {head.group(0)!r} has no category"


def test_changelog_entries_are_bullets():
    in_category = False
    for line in _changelog_text().splitlines():
        if line.startswith("### "):
            in_category = True
            continue
        if line.startswith("#"):
            in_category = False
            continue
        if in_category and line.strip() and not line.startswith(("-", " ")):
            raise AssertionError(f"non-bullet line inside a category: {line!r}")


def test_changelog_no_placeholders():
    assert not PLACEHOLDER_RX.search(_changelog_text())


def test_changelog_references_all_waves_w1_to_w13():
    text = _changelog_text()
    for n in range(1, 14):
        assert re.search(rf"\bW{n}\b", text), f"wave W{n} not referenced"


def test_changelog_commit_hashes_exist_in_git():
    hashes = set(re.findall(r"`([0-9a-f]{7,40})`", _changelog_text()))
    assert len(hashes) >= 10, "changelog should cite real commits"
    for h in hashes:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{h}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert proc.returncode == 0, f"changelog cites unknown commit {h}"


def test_changelog_security_category_mentions_pypdf_cve():
    text = _changelog_text()
    assert re.search(r"^### Security", text, re.MULTILINE)
    assert "CVE-2026-119203" in text


def test_changelog_version_link_references():
    text = _changelog_text()
    for version in ("0.3.0", "0.2.0", "0.1.0"):
        assert re.search(rf"^\[{re.escape(version)}\]: \S+", text, re.MULTILINE), f"missing link ref for {version}"


# -------------------------------------------------------------------------- docs


def test_docs_files_exist():
    for path in DOC_FILES:
        assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"


def test_docs_have_substance():
    for path in DOC_FILES:
        nonblank = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        assert nonblank >= 80, f"{path.name} too thin ({nonblank} non-blank lines)"


def test_docs_no_placeholders():
    for path in DOC_FILES:
        assert not PLACEHOLDER_RX.search(path.read_text(encoding="utf-8")), f"placeholder in {path.name}"


def test_docs_balanced_code_fences():
    for path in DOC_FILES:
        fences = re.findall(r"^```", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert len(fences) % 2 == 0, f"unbalanced code fence in {path.name}"


def test_docs_internal_links_resolve():
    link_rx = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for path in DOC_FILES:
        for target in link_rx.findall(path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#") or target.startswith("mailto:"):
                continue
            target_path = target.split("#")[0]
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            assert resolved.is_file() or resolved.is_dir(), f"{path.name}: dead link {target}"


def test_api_documents_every_cli_command():
    cli_src = (SRC / "cli.py").read_text(encoding="utf-8")
    named = set(re.findall(r'add_parser\(\s*"([a-z0-9-]+)"', cli_src))
    http_src = (SRC / "http_api.py").read_text(encoding="utf-8")
    for block in ("KNOWLEDGE_OPERATIONS", "OPS_OPERATIONS"):
        section = http_src.split(f"{block}: tuple[str, ...] = (", 1)[1].split(")", 1)[0]
        named |= {op.replace("_", "-") for op in re.findall(r'"([a-z_]+)"', section)}
    api = (DOCS / "api.md").read_text(encoding="utf-8")
    missing = {name for name in named if f"`{name}`" not in api}
    assert not missing, f"api.md misses CLI commands: {sorted(missing)}"


def test_api_documents_every_mcp_tool():
    mcp_src = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    block = mcp_src.split("_TOOL_TO_OPERATION: dict[str, str] = {", 1)[1].split("}", 1)[0]
    tools = set(re.findall(r'"(knowledge_[a-z_]+)":', block))
    assert len(tools) == 17, f"expected 17 frozen MCP tools, found {len(tools)}"
    api = (DOCS / "api.md").read_text(encoding="utf-8")
    missing = {tool for tool in tools if f"`{tool}`" not in api}
    assert not missing, f"api.md misses MCP tools: {sorted(missing)}"


def test_api_documents_http_routes():
    api = (DOCS / "api.md").read_text(encoding="utf-8")
    for route in (
        "/v1/health",
        "/v1/knowledge/ingest/file",
        "/v1/knowledge/ingest/directory",
        "/v1/knowledge/extraction",
        "/v1/knowledge/{operation}",
        "/v1/ops/{operation}",
    ):
        assert route in api, f"api.md misses HTTP route {route}"


def test_verdict_doc_covers_seven_levels():
    metrics_src = (REPO_ROOT / "bench" / "verdict" / "metrics.py").read_text(encoding="utf-8")
    levels = re.findall(r'Level\(\s*"(l\d)",\s*"([^"]+)"', metrics_src)
    assert len(levels) == 7, f"expected 7 levels in metrics.py, found {len(levels)}"
    doc = (DOCS / "verdict.md").read_text(encoding="utf-8")
    for level_id, level_name in levels:
        assert level_id in doc, f"verdict.md misses level {level_id}"
        assert level_name in doc, f"verdict.md misses level name {level_name!r}"


def test_security_doc_covers_capability_model():
    doc = (DOCS / "security.md").read_text(encoding="utf-8")
    for token in ("kyc1.", "capability", "0600", "loopback", "KNOW_YOUR_CONTEXT_CAPABILITY_SECRET"):
        assert token in doc, f"security.md misses {token!r}"


def test_architecture_doc_names_core_components():
    doc = (DOCS / "architecture.md").read_text(encoding="utf-8")
    for component in (
        "ingest",
        "resolve",
        "store",
        "retrieval_v2",
        "serve",
        "analytics",
        "exporters",
        "security",
        "projection",
    ):
        assert component in doc, f"architecture.md misses component {component!r}"


# ------------------------------------------------------------------ docstrings


def _public_defs(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
    functions = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
    ]
    return tree, classes, functions


def test_scope_files_exist():
    assert len(DOCSTRING_SCOPE) >= 12
    for path in DOCSTRING_SCOPE:
        assert path.is_file(), f"scope file missing: {path}"


def test_module_docstrings_present_in_scope():
    missing = [str(p) for p in DOCSTRING_SCOPE if not ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")))]
    assert not missing, f"modules without docstring: {missing}"


def test_public_class_docstrings_complete_in_scope():
    missing = []
    for path in DOCSTRING_SCOPE:
        _, classes, _ = _public_defs(path)
        for cls in classes:
            if not ast.get_docstring(cls):
                missing.append(f"{path.name}:{cls.name}")
    assert not missing, f"public classes without docstring: {missing}"


def test_public_function_docstrings_complete_in_scope():
    missing = []
    for path in DOCSTRING_SCOPE:
        _, _, functions = _public_defs(path)
        for fn in functions:
            if not ast.get_docstring(fn):
                missing.append(f"{path.name}:{fn.name}")
    assert not missing, f"public functions without docstring: {missing}"


def test_serve_error_docstrings_explain_failure_modes():
    """The serve error taxonomy must say *when* each error is raised."""
    tree = ast.parse((SRC / "serve" / "superpower.py").read_text(encoding="utf-8"))
    docs = {n.name: ast.get_docstring(n) or "" for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    for name in (
        "ScopeDenied",
        "UnknownVerb",
        "ActorRequired",
        "DaemonUnavailable",
        "ShuttingDown",
        "DeadlineExceeded",
        "SpawnResult",
    ):
        assert name in docs, f"{name} not found in serve/superpower.py"
        assert len(docs[name]) >= 60, f"{name} docstring too thin to explain its failure mode"


def test_ingest_pipeline_error_docstrings_explain_caller_contract():
    tree = ast.parse((SRC / "ingest" / "pipeline.py").read_text(encoding="utf-8"))
    docs = {n.name: ast.get_docstring(n) or "" for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    for name in (
        "PathNotFoundError",
        "PathNotReadableError",
        "PathOutsideRootError",
        "UnsupportedMediaTypeError",
        "FileTooLargeError",
        "IngestionBudgetError",
    ):
        assert name in docs, f"{name} not found in ingest/pipeline.py"
        assert len(docs[name]) >= 60, f"{name} docstring too thin to explain the caller contract"
