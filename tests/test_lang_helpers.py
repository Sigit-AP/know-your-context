"""Tests for per-language module-resolution helpers (wave W12, SUB-121).

``resolve/lang_helpers.py`` adapts the *pattern* of codegraph's per-language
import helpers to this project's declarative model: each helper is pure data
+ one pure function mapping ``(target_name, source_path)`` to an ordered
tuple of repo-relative candidate paths. The resolver performs all store I/O;
helpers never touch the store, never guess, and never throw on garbage input.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from know_your_context.resolve import ReferenceResolver, ResolverConfig
from know_your_context.resolve.lang_helpers import (
    HELPERS,
    ModuleCandidate,
    get_helper,
    helper_languages,
)

SEEN = "2026-10-26T00:00:00+00:00"
BASE = "function:base"


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def store(tmp_path: Path) -> Iterator:
    from know_your_context.store import EventStore

    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def add_file(store, path: str, language: str) -> None:
    store.upsert_file(
        path=path,
        sha256=hashlib.sha256(path.encode()).hexdigest(),
        size=10,
        mtime=100,
        language=language,
        generated_hint=False,
        extraction_version=1,
    )


def add_ref(store, source: str, target: str, kind: str = "imports") -> int:
    return store.insert_unresolved_ref(
        source_node_id=source, target_name=target, kind=kind, name_tail=None, first_seen=SEEN
    )


def base_source(store, path: str, language: str) -> None:
    add_file(store, path, language)
    store.upsert_symbol(node_id=BASE, name="main", kind="function", path=path, line=1)


def resolved_targets(store) -> list[str]:
    with store.read() as conn:
        return [
            row[0]
            for row in conn.execute("SELECT target FROM graph_edges WHERE kind = 'imports' ORDER BY target").fetchall()
        ]


# ------------------------------------------------------------- registry data


def test_helper_count_at_least_ten():
    assert len(HELPERS) >= 10


def test_helper_languages_sorted():
    assert helper_languages() == tuple(sorted(HELPERS))


def test_core_languages_have_helpers():
    for language in ("python", "javascript", "typescript", "go", "rust", "java"):
        assert language in HELPERS


@pytest.mark.parametrize("language", sorted(HELPERS))
def test_helper_lookup_roundtrip(language: str):
    h = get_helper(language)
    assert h is not None
    assert h.name == language


def test_get_helper_unknown_returns_none():
    assert get_helper("cobol") is None


# ------------------------------------------------------- pure candidate data
# language -> (target, source_path) -> expected candidate paths in order.

PURE_CASES = {
    "python": [
        (("pkg.mod", "src/main.py"), ("pkg/mod.py", "pkg/mod/__init__.py", "pkg.py", "pkg/__init__.py")),
        ((".sibling", "pkg/sub/main.py"), ("pkg/sub/sibling.py", "pkg/sub/sibling/__init__.py")),
        (("..aunt", "pkg/sub/main.py"), ("pkg/aunt.py", "pkg/aunt/__init__.py")),
    ],
    "javascript": [
        (("./util", "src/app/main.js"), ("src/app/util", "src/app/util.ts", "src/app/util.d.ts")),
        (("../lib", "src/app/main.js"), ("src/lib",)),
    ],
    "typescript": [
        (("./util", "src/main.ts"), ("src/util", "src/util.ts", "src/util.d.ts")),
    ],
    "go": [
        (("github.com/foo/bar", "main.go"), ("github.com/foo/bar", "foo/bar", "bar")),
        (("fmt", "main.go"), ("fmt",)),
    ],
    "rust": [
        (("crate::foo::bar", "src/main.rs"), ("src/foo/bar.rs", "src/foo/bar/mod.rs", "foo/bar.rs", "foo/bar/mod.rs")),
        (("self::util", "src/pkg/mod.rs"), ("src/pkg/util.rs", "src/pkg/util/mod.rs")),
        (("super::shared", "src/pkg/mod.rs"), ("src/shared.rs", "src/shared/mod.rs")),
    ],
    "java": [
        (("com.example.Foo", "Main.java"), ("com/example/Foo.java",)),
    ],
    "c": [
        (("local.h", "src/main.c"), ("src/local.h", "local.h")),
        (("stdio.h", "src/main.c"), ("src/stdio.h", "stdio.h")),
    ],
    "cpp": [
        (("local.hpp", "src/main.cpp"), ("src/local.hpp", "local.hpp")),
    ],
    "csharp": [
        (("Foo.Bar.Baz", "Program.cs"), ("Foo/Bar/Baz.cs",)),
    ],
    "ruby": [
        (("foo/bar", "app/main.rb"), ("foo/bar.rb", "lib/foo/bar.rb")),
    ],
    "php": [
        (("Vendor\\Pkg\\Thing", "public/index.php"), ("Vendor/Pkg/Thing.php", "src/Vendor/Pkg/Thing.php")),
    ],
    "lua": [
        (("a.b", "main.lua"), ("a/b.lua", "a/b/init.lua")),
    ],
    "haskell": [
        (("Data.List", "app/Main.hs"), ("Data/List.hs", "src/Data/List.hs")),
    ],
    "perl": [
        (("Foo::Bar", "script.pl"), ("Foo/Bar.pm", "lib/Foo/Bar.pm")),
    ],
    "scala": [
        (("a.b.C", "Main.scala"), ("a/b/C.scala",)),
    ],
    "kotlin": [
        (("a.b.C", "Main.kt"), ("a/b/C.kt",)),
    ],
    "solidity": [
        (("./Token.sol", "contracts/Greeter.sol"), ("contracts/Token.sol",)),
    ],
    "crystal": [
        (("./util", "src/main.cr"), ("src/util.cr",)),
        (("json", "src/main.cr"), ("json.cr", "src/json.cr")),
    ],
}


@pytest.mark.parametrize("language", sorted(PURE_CASES))
def test_pure_candidates(language: str):
    helper = get_helper(language)
    assert helper is not None
    for (target, source_path), expected_prefix in PURE_CASES[language]:
        got = helper.candidates(target, source_path)
        assert isinstance(got, tuple)
        assert all(isinstance(candidate, ModuleCandidate) for candidate in got)
        got_paths = tuple(candidate.path for candidate in got)
        for index, expected in enumerate(expected_prefix):
            assert got_paths[index] == expected, (
                f"{language} {target!r}: [{index}] {got_paths[index]!r} != {expected!r}"
            )


@pytest.mark.parametrize("language", sorted(HELPERS))
def test_candidates_are_deterministic(language: str):
    helper = get_helper(language)
    assert helper is not None
    first = helper.candidates("a.b.c", "x/y/z.ext")
    second = helper.candidates("a.b.c", "x/y/z.ext")
    assert first == second


@pytest.mark.parametrize("language", sorted(HELPERS))
def test_candidates_never_absolute_or_escaping(language: str):
    helper = get_helper(language)
    assert helper is not None
    for target in ("a.b", "./x", "../y", "a::b", "", ".", "..", "weird$%^"):
        for candidate in helper.candidates(target, "src/main.x"):
            assert not candidate.path.startswith("/"), (language, target, candidate)
            assert ".." not in candidate.path.split("/"), (language, target, candidate)


@pytest.mark.parametrize("language", sorted(HELPERS))
def test_candidates_empty_input_no_throw(language: str):
    h = get_helper(language)
    assert h is not None
    assert isinstance(h.candidates("", "x.py"), tuple)


def test_go_candidates_are_directories():
    helper = get_helper("go")
    assert helper is not None
    assert all(candidate.is_dir for candidate in helper.candidates("github.com/foo/bar", "main.go"))


def test_non_go_candidates_are_files():
    for language in sorted(HELPERS):
        if language == "go":
            continue
        h = get_helper(language)
        assert h is not None
        for candidate in h.candidates("a.b", "x/y.z"):
            assert not candidate.is_dir, language


def test_rust_bare_first_segment_is_external():
    """`use std::...` / `use serde::...` name external crates — no candidates."""
    h = get_helper("rust")
    assert h is not None
    assert h.candidates("std::collections::HashMap", "src/main.rs") == ()


def test_js_bare_specifier_is_external():
    h = get_helper("javascript")
    assert h is not None
    assert h.candidates("lodash", "src/main.js") == ()


# ----------------------------------------------------- resolver integration


def test_go_import_resolves_to_package_directory(store):
    base_source(store, "cmd/main.go", "go")
    add_file(store, "internal/pkg/util/parse.go", "go")
    add_file(store, "internal/pkg/util/fmt.go", "go")
    add_ref(store, BASE, "internal/pkg/util")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    targets = resolved_targets(store)
    assert len(targets) == 1
    assert targets[0] in ("file:internal/pkg/util/fmt.go", "file:internal/pkg/util/parse.go")


def test_go_ambiguous_package_fails_closed(store):
    base_source(store, "cmd/main.go", "go")
    add_file(store, "a/vendor/util/x.go", "go")
    add_file(store, "b/vendor/util/y.go", "go")
    add_ref(store, BASE, "vendor/util")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 0
    assert report.failed == 1
    assert report.outcomes[0].reason == "ambiguous_module:2"


def test_rust_crate_import_resolves(store):
    base_source(store, "src/main.rs", "rust")
    add_file(store, "src/foo/bar.rs", "rust")
    add_ref(store, BASE, "crate::foo::bar")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:src/foo/bar.rs"]


def test_rust_mod_rs_resolves(store):
    base_source(store, "src/main.rs", "rust")
    add_file(store, "src/foo/mod.rs", "rust")
    add_ref(store, BASE, "crate::foo")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:src/foo/mod.rs"]


def test_java_import_resolves_by_suffix(store):
    base_source(store, "app/Main.java", "java")
    add_file(store, "lib/src/com/example/Foo.java", "java")
    add_ref(store, BASE, "com.example.Foo")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:lib/src/com/example/Foo.java"]


def test_ruby_require_resolves_lib_prefix(store):
    base_source(store, "app/main.rb", "ruby")
    add_file(store, "lib/foo/bar.rb", "ruby")
    add_ref(store, BASE, "foo/bar")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:lib/foo/bar.rb"]


def test_php_psr4_import_resolves(store):
    base_source(store, "public/index.php", "php")
    add_file(store, "src/Vendor/Pkg/Thing.php", "php")
    add_ref(store, BASE, "Vendor\\Pkg\\Thing")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:src/Vendor/Pkg/Thing.php"]


def test_lua_dotted_require_resolves(store):
    base_source(store, "main.lua", "lua")
    add_file(store, "a/b/init.lua", "lua")
    add_ref(store, BASE, "a.b")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:a/b/init.lua"]


def test_haskell_module_resolves_src_prefix(store):
    base_source(store, "app/Main.hs", "haskell")
    add_file(store, "src/Data/List.hs", "haskell")
    add_ref(store, BASE, "Data.List")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:src/Data/List.hs"]


def test_perl_module_resolves(store):
    base_source(store, "script.pl", "perl")
    add_file(store, "lib/Foo/Bar.pm", "perl")
    add_ref(store, BASE, "Foo::Bar")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:lib/Foo/Bar.pm"]


def test_solidity_relative_import_resolves(store):
    base_source(store, "contracts/Greeter.sol", "solidity")
    add_file(store, "contracts/Token.sol", "solidity")
    add_ref(store, BASE, "./Token.sol")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:contracts/Token.sol"]


def test_crystal_relative_require_resolves(store):
    base_source(store, "src/main.cr", "crystal")
    add_file(store, "src/util.cr", "crystal")
    add_ref(store, BASE, "./util")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:src/util.cr"]


def test_c_include_resolves_relative_first(store):
    base_source(store, "src/main.c", "c")
    add_file(store, "src/local.h", "c")
    add_ref(store, BASE, "local.h")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:src/local.h"]


def test_scala_import_resolves(store):
    base_source(store, "Main.scala", "scala")
    add_file(store, "src/main/scala/a/b/C.scala", "scala")
    add_ref(store, BASE, "a.b.C")
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:src/main/scala/a/b/C.scala"]


def test_helper_tier_kill_switch(store):
    base_source(store, "app/Main.java", "java")
    add_file(store, "com/example/Foo.java", "java")
    add_ref(store, BASE, "com.example.Foo")
    config = ResolverConfig(helper_tier_enabled=False)
    report = ReferenceResolver(config).resolve_pending(store)
    assert report.resolved == 0
    assert report.failed == 1
    assert report.outcomes[0].reason == "no_resolution"


def test_helper_tier_records_strategy(store):
    base_source(store, "app/Main.java", "java")
    add_file(store, "com/example/Foo.java", "java")
    add_ref(store, BASE, "com.example.Foo")
    report = ReferenceResolver().resolve_pending(store)
    assert report.outcomes[0].strategy == "module_helper"


def test_helper_tier_only_applies_to_import_refs(store):
    base_source(store, "app/Main.java", "java")
    add_file(store, "com/example/Foo.java", "java")
    add_ref(store, BASE, "com.example.Foo", kind="calls")
    report = ReferenceResolver().resolve_pending(store)
    # calls refs never bind a bare module via the helper tier
    assert report.resolved == 0


def test_go_package_dir_like_overmatch_is_safe(store):
    """Regression (found by bench/codebase): SQLite LIKE is case-insensitive
    for ASCII and `_`/`%` in the escaped pattern are literal-only — a
    prefilter row that fails the literal directory check must be skipped,
    never crash."""
    base_source(store, "cmd/main.go", "go")
    add_file(store, "Apps/Utils/x.go", "go")  # matches LIKE '%/apps/utils/%' only case-insensitively
    add_ref(store, BASE, "apps/utils")
    report = ReferenceResolver().resolve_pending(store)
    assert report.outcomes[0].status == "failed"  # no literal dir match
    add_file(store, "apps/utils/y.go", "go")
    ReferenceResolver().resurrect_failed(store)
    report = ReferenceResolver().resolve_pending(store)
    assert report.resolved == 1
    assert resolved_targets(store) == ["file:apps/utils/y.go"]


def test_helper_tier_idempotent_second_run(store):
    base_source(store, "app/Main.java", "java")
    add_file(store, "com/example/Foo.java", "java")
    add_ref(store, BASE, "com.example.Foo")
    first = ReferenceResolver().resolve_pending(store)
    second = ReferenceResolver().resolve_pending(store)
    assert first.resolved == 1
    assert second.processed == 0
    assert len(resolved_targets(store)) == 1
