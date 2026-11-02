"""Per-language module-resolution helpers (wave W12, SUB-121).

Adapts the *pattern* of codegraph's per-language import helpers
(RESEARCH-INGESTION §2e, the 36-helper table) to this project's fail-closed
resolver cascade — rewritten, not ported. Each helper is a frozen dataclass
with one pure function: ``candidates(target_name, source_path)`` returns an
ordered tuple of repo-relative candidate paths (files, or package directories
for Go). Helpers never touch the store, never guess at ambiguity, and never
throw on garbage input; all store I/O and all ambiguity verdicts stay in
:mod:`know_your_context.resolve.resolver`.

Design rules (all pinned by tests):

* candidates are deterministic — same input, same tuple, any hash seed;
* candidates never escape the repo (no absolute paths, no ``..`` segments);
* external references yield **zero** candidates (bare JS specifiers, Rust
  ``use std::…`` — an external crate is not a local path, and inventing one
  would fabricate edges);
* Go imports name *packages* (directories), so its candidates carry
  ``is_dir=True`` and the resolver anchors to the first file inside.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

__all__ = [
    "HELPERS",
    "ModuleCandidate",
    "LanguageHelper",
    "get_helper",
    "helper_languages",
]


@dataclass(frozen=True)
class ModuleCandidate:
    """One repo-relative candidate: a file path, or a package directory."""

    path: str
    is_dir: bool = False


@dataclass(frozen=True)
class LanguageHelper:
    """The import→path mapping rules for one language (pure data + purity)."""

    name: str
    separators: tuple[str, ...] = (".",)  # target separators to split on
    extensions: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ("",)  # conventional roots tried in order
    index_names: tuple[str, ...] = ()  # package-anchor files (mod.rs, __init__.py)
    relative_first: bool = False  # relative targets anchor at the source dir
    dir_packages: bool = False  # go: imports name directories, not files

    def candidates(self, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
        """The ordered candidate paths for *target_name* seen from *source_path*.

        Deterministic, total (never raises), repo-confined. An empty tuple
        means "external or unrecognised — leave the ref parked".
        """
        if not target_name or not target_name.strip():
            return ()
        handler = _SPECIAL.get(self.name)
        if handler is not None:
            return handler(self, target_name, source_path)
        return _generic(self, target_name, source_path)


def _clean(path: str) -> str | None:
    """Normalize a candidate; reject anything absolute or escaping."""
    normalized = posixpath.normpath(path)
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        return None
    if normalized == ".":
        return None
    return normalized


def _dedupe(paths: list[tuple[str, bool]]) -> tuple[ModuleCandidate, ...]:
    seen: set[str] = set()
    out: list[ModuleCandidate] = []
    for path, is_dir in paths:
        if path not in seen:
            seen.add(path)
            out.append(ModuleCandidate(path, is_dir))
    return tuple(out)


def _generic(helper: LanguageHelper, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
    """Dotted/slashed module target → path under each conventional prefix."""
    relative = target_name.startswith("./") or target_name.startswith("../")
    base = posixpath.dirname(source_path) if relative else ""
    target = target_name
    if helper.relative_first and not relative:
        # quoted includes (C) try the source directory before the bare suffix
        pass
    for separator in helper.separators:
        if separator in target and not (separator == "/" and relative):
            target = target.replace(separator, "/") if separator != "/" else target
    if relative:
        joined = posixpath.normpath(posixpath.join(base, target))
        roots = [joined]
    else:
        roots = [posixpath.join(prefix, target) if prefix else target for prefix in helper.prefixes]
    out: list[tuple[str, bool]] = []
    for root in roots:
        if helper.dir_packages:
            out.append((root, True))
            continue
        if helper.extensions and not any(root.endswith(extension) for extension in helper.extensions):
            for extension in helper.extensions:
                out.append((root + extension, False))
            for index_name in helper.index_names:
                out.append((f"{root}/{index_name}", False))
        else:
            out.append((root, False))
    cleaned: list[tuple[str, bool]] = []
    for path, is_dir in out:
        normalized = _clean(path)
        if normalized is not None:
            cleaned.append((normalized, is_dir))
    return _dedupe(cleaned)


# ------------------------------------------------------------- special forms


def _python(helper: LanguageHelper, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
    dots = len(target_name) - len(target_name.lstrip("."))
    rest = target_name.lstrip(".")
    if dots:
        base = posixpath.dirname(source_path)
        for _ in range(dots - 1):
            base = posixpath.dirname(base)
        prefix = "" if base == "." else base
    else:
        prefix = ""
    if not rest:
        return ()
    module = rest.replace(".", "/")
    roots = [posixpath.join(prefix, module) if prefix else module]
    out: list[tuple[str, bool]] = []
    for root in roots:
        out.append((root + ".py", False))
        out.append((f"{root}/__init__.py", False))
        # `from pkg.mod import func` — func may be a sibling module
        parent = posixpath.dirname(module)
        tail = posixpath.basename(module)
        if parent:
            shorter = posixpath.join(prefix, parent) if prefix else parent
            out.append((shorter + ".py", False))
            out.append((f"{shorter}/__init__.py", False))
        elif tail and not dots:
            out.append((tail + ".py", False))
            out.append((f"{tail}/__init__.py", False))
    cleaned = []
    for path, is_dir in out:
        normalized = _clean(path)
        if normalized is not None:
            cleaned.append((normalized, is_dir))
    return _dedupe(cleaned)


_JS_EXTENSIONS = (".ts", ".d.ts", ".tsx", ".js", ".jsx")
_JS_INDEX_NAMES = ("index.ts", "index.d.ts", "index.tsx", "index.js", "index.jsx")


def _js_family(helper: LanguageHelper, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
    if not (target_name.startswith("./") or target_name.startswith("../")):
        return ()  # bare specifier: external package, honestly unresolved
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), target_name))
    out: list[tuple[str, bool]] = [(joined, False)]
    out.extend((joined + extension, False) for extension in _JS_EXTENSIONS)
    out.extend((f"{joined}/{index_name}", False) for index_name in _JS_INDEX_NAMES)
    normalized = _clean(joined)
    if normalized is None:
        return ()
    return _dedupe(out)


def _rust(helper: LanguageHelper, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
    if target_name.startswith("crate::"):
        module = target_name[len("crate::") :].replace("::", "/")
        roots = [f"src/{module}", module]
    elif target_name.startswith("self::"):
        module = target_name[len("self::") :].replace("::", "/")
        roots = [posixpath.join(posixpath.dirname(source_path), module)]
    elif target_name.startswith("super::"):
        module = target_name[len("super::") :].replace("::", "/")
        parent = posixpath.dirname(posixpath.dirname(source_path))
        roots = [posixpath.join(parent, module) if parent != "." else module]
    else:
        return ()  # external crate — honestly unresolved
    out: list[tuple[str, bool]] = []
    for root in roots:
        out.append((root + ".rs", False))
        out.append((f"{root}/mod.rs", False))
    cleaned = []
    for path, is_dir in out:
        normalized = _clean(path)
        if normalized is not None:
            cleaned.append((normalized, is_dir))
    return _dedupe(cleaned)


def _go(helper: LanguageHelper, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
    # An import path names a package directory; try the full path, then
    # progressively shorter tails (host-less and basename layouts).
    segments = [segment for segment in target_name.split("/") if segment and segment not in (".", "..")]
    if not segments:
        return ()
    out: list[tuple[str, bool]] = []
    full = _clean("/".join(segments))
    if full is None:
        return ()
    out.append((full, True))
    if len(segments) > 1:
        out.append(("/".join(segments[1:]), True))
    if len(segments) > 2:
        out.append((segments[-1], True))
    return _dedupe(out)


def _c_family(helper: LanguageHelper, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
    # Quoted includes search the including file's directory first; angle
    # includes (already stripped at extraction) fall to the bare suffix.
    base = posixpath.dirname(source_path)
    joined = posixpath.join(base, target_name) if base else target_name
    out: list[tuple[str, bool]] = [(joined, False)]
    if joined != target_name:
        out.append((target_name, False))
    cleaned = []
    for path, is_dir in out:
        normalized = _clean(path)
        if normalized is not None:
            cleaned.append((normalized, is_dir))
    return _dedupe(cleaned)


def _relative_with_extension(helper: LanguageHelper, target_name: str, source_path: str) -> tuple[ModuleCandidate, ...]:
    """solidity/crystal: ``./x`` joins the source dir; bare names try prefixes."""
    if target_name.startswith("./") or target_name.startswith("../"):
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), target_name))
        out: list[tuple[str, bool]] = []
        if any(joined.endswith(extension) for extension in helper.extensions):
            out.append((joined, False))
        else:
            for extension in helper.extensions:
                out.append((joined + extension, False))
        if not helper.extensions:
            out.append((joined, False))
    else:
        out = []
        for prefix in helper.prefixes:
            root = posixpath.join(prefix, target_name) if prefix else target_name
            for extension in helper.extensions:
                candidate = root if root.endswith(extension) else root + extension
                out.append((candidate, False))
        if not helper.extensions:
            out.append((target_name, False))
    cleaned = []
    for path, is_dir in out:
        normalized = _clean(path)
        if normalized is not None:
            cleaned.append((normalized, is_dir))
    return _dedupe(cleaned)


_SPECIAL = {
    "python": _python,
    "javascript": _js_family,
    "javascriptreact": _js_family,
    "typescript": _js_family,
    "typescriptreact": _js_family,
    "rust": _rust,
    "go": _go,
    "c": _c_family,
    "cpp": _c_family,
    "solidity": _relative_with_extension,
    "crystal": _relative_with_extension,
}


# ------------------------------------------------------------- the registry


def _helper(
    name: str,
    *,
    separators: tuple[str, ...] = (".",),
    extensions: tuple[str, ...] = (),
    prefixes: tuple[str, ...] = ("",),
    index_names: tuple[str, ...] = (),
    dir_packages: bool = False,
) -> LanguageHelper:
    return LanguageHelper(
        name=name,
        separators=separators,
        extensions=extensions,
        prefixes=prefixes,
        index_names=index_names,
        dir_packages=dir_packages,
    )


_HELPERS_LIST = (
    _helper("python", extensions=(".py",), index_names=("__init__.py",)),
    _helper("javascript"),
    _helper("javascriptreact"),
    _helper("typescript"),
    _helper("typescriptreact"),
    _helper("go", separators=("/",), dir_packages=True),
    _helper("rust", separators=("::",)),
    _helper("java", extensions=(".java",)),
    _helper("c"),
    _helper("cpp"),
    _helper("csharp", extensions=(".cs",)),
    _helper("ruby", separators=("/",), extensions=(".rb",), prefixes=("", "lib")),
    _helper("php", separators=("\\",), extensions=(".php",), prefixes=("", "src")),
    _helper("lua", extensions=(".lua",), index_names=("init.lua",)),
    _helper("haskell", extensions=(".hs",), prefixes=("", "src")),
    _helper("perl", separators=("::",), extensions=(".pm",), prefixes=("", "lib")),
    _helper("scala", extensions=(".scala",)),
    _helper("kotlin", extensions=(".kt",)),
    _helper("solidity", extensions=(".sol",)),
    _helper("crystal", extensions=(".cr",), prefixes=("", "src")),
)

HELPERS: dict[str, LanguageHelper] = {helper.name: helper for helper in _HELPERS_LIST}


def helper_languages() -> tuple[str, ...]:
    """Every language with a registered helper, sorted (deterministic)."""
    return tuple(sorted(HELPERS))


def get_helper(language: str) -> LanguageHelper | None:
    """The helper for *language*, or ``None`` when none is registered."""
    return HELPERS.get(language)
