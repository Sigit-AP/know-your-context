"""W12 language breadth tests (SUB-121): 19 -> 32 languages.

Pins the 13 wave-12 languages admitted by the tree-sitter-language-pack
1.22.1 audit (docs/recovery/w12-depth.md): objc, zig, nim, crystal,
solidity, erlang, fsharp, powershell, gdscript, v, haxe, gleam, vala
— plus elisp. All are ``engine="python"`` (fail-closed default): no
crates.io grammar is proven parse-identical to the bundled revisions, and
no kernel crate was added in this wave.

Rejected candidates are pinned by evidence tests at the bottom (groovy,
clojure) so a future grammar upgrade can revisit them deliberately.
"""

from __future__ import annotations

import pytest
from tree_sitter_language_pack import get_parser

from know_your_context.ingest.languages import (
    EXTENSION_MAP,
    LANGUAGES,
    get_spec,
    language_for_path,
    supported_languages,
)

W12_LANGUAGES = (
    "crystal",
    "elisp",
    "erlang",
    "fsharp",
    "gdscript",
    "gleam",
    "haxe",
    "nim",
    "objc",
    "powershell",
    "solidity",
    "v",
    "vala",
    "zig",
)

#: W12 grammars whose call node carries no callee field (verified against
#: the bundled grammars; docs/recovery/w12-depth.md §3). They must declare
#: empty ``call_types`` — honest data over dead strategies.
W12_NO_CALL_FIELD = frozenset({"zig", "nim", "fsharp", "gdscript", "elisp", "powershell"})

W12_EXTENSIONS = {
    ".cr": "crystal",
    ".el": "elisp",
    ".erl": "erlang",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".gd": "gdscript",
    ".gleam": "gleam",
    ".hrl": "erlang",
    ".hx": "haxe",
    ".m": "objc",
    ".mm": "objc",
    ".nim": "nim",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".sol": "solidity",
    ".v": "v",
    ".vala": "vala",
    ".zig": "zig",
}

ALL_W12 = pytest.mark.parametrize("language", W12_LANGUAGES)


def test_w12_language_count():
    """The wave's own arithmetic: 19 pre-wave + 14 admitted = 33? No — pin
    the real total so the count is never wishful."""
    assert len(W12_LANGUAGES) == 14
    assert len(supported_languages()) == 19 + 14


def test_w12_languages_are_new():
    pre_existing = {
        "bash",
        "c",
        "cpp",
        "csharp",
        "dart",
        "go",
        "haskell",
        "java",
        "javascript",
        "kotlin",
        "lua",
        "perl",
        "php",
        "python",
        "ruby",
        "rust",
        "scala",
        "swift",
        "typescript",
    }
    assert not (set(W12_LANGUAGES) & pre_existing)


@ALL_W12
def test_w12_language_registered(language: str):
    assert language in LANGUAGES
    assert get_spec(language).name == language


@ALL_W12
def test_w12_engine_is_python_fail_closed(language: str):
    """No kernel crate was audited parse-identical for these grammars, so
    every W12 language takes the reference walker (fail-closed default)."""
    assert get_spec(language).engine == "python"


@ALL_W12
def test_w12_parser_loads(language: str):
    assert get_parser(get_spec(language).parser) is not None


@ALL_W12
def test_w12_declares_a_function_like_kind(language: str):
    kinds = {kind for _, kind in get_spec(language).declaration_kinds}
    assert kinds & {"function", "method"}, f"{language} declares no function-like kind"


@ALL_W12
def test_w12_import_rules_nonempty(language: str):
    assert get_spec(language).import_rules, f"{language} has no import rules"


@ALL_W12
def test_w12_call_gate_two_way(language: str):
    spec = get_spec(language)
    if language in W12_NO_CALL_FIELD:
        assert spec.call_types == (), f"{language}: no callee field; call_types must be empty"
    else:
        assert spec.call_types, f"{language} must extract calls"


@pytest.mark.parametrize(("extension", "language"), sorted(W12_EXTENSIONS.items()))
def test_w12_extension_map(extension: str, language: str):
    assert language_for_path(f"x{extension}") == language
    assert EXTENSION_MAP[extension] == language


def test_w12_extensions_do_not_rebind_existing():
    """No pre-wave extension may change language (path stability contract)."""
    pre_wave = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".sh": "bash",
        ".kt": "kotlin",
        ".swift": "swift",
        ".scala": "scala",
        ".lua": "lua",
        ".dart": "dart",
        ".hs": "haskell",
        ".pl": "perl",
    }
    for extension, language in pre_wave.items():
        assert EXTENSION_MAP[extension] == language


# ------------------------------------------------------ rejection evidence


def test_groovy_grammar_has_no_declaration_nodes():
    """REJECTED (W12 audit): the bundled groovy grammar reduces every
    statement to ``command``/``unit`` nodes — ``class Greeter {`` parses as
    ``command > unit(identifier 'class')``; no declaration/import node types
    exist to map honestly."""
    tree = get_parser("groovy").parse(b"class Greeter {\n  def greet(who) { who }\n}\n")
    seen: set[str] = set()

    def collect(node):
        seen.add(node.type)
        for child in node.children:
            collect(child)

    collect(tree.root_node)
    assert "class_declaration" not in seen
    assert "method_declaration" not in seen
    assert "import_declaration" not in seen


def test_clojure_defn_is_a_bare_symbol():
    """REJECTED (W12 audit, same class as W7's elixir rejection): ``defn``
    is a plain ``sym_lit`` inside ``list_lit``; the declarative walker
    cannot match symbol text for declarations, so every list would risk
    becoming a phantom node."""
    tree = get_parser("clojure").parse(b"(defn greet [who] (helper who))")
    root_children = [child.type for child in tree.root_node.children]
    assert "function_definition" not in root_children
    assert root_children == ["list_lit"]
