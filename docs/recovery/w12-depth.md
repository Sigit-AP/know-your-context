# W12 — Depth push: languages 19→33, synthesis 4→12, resolver helpers 0→21 (SUB-121)

Tanggal: 2026-11-02. Repo: `/root/know-your-context` @ `main`. TIDAK di-commit
(aturan operator). Kontrak beku (graph_model CoreNodeKind/CoreEdgeKind,
contracts v1/v2) TIDAK disentuh; semua edge baru memakai kind beku
(`calls`/`imports`/`references`/`type_of`).

## 1. Bahasa: 19 → 33 (+14, target 30+ TERLAMPAUI)

Audit lanjutan atas tree-sitter-language-pack 1.22.1 (372 grammar, metode
W7: `get_parser` + parse sampel nyata + inspeksi field programatik — bukan
README). 16 kandidat W12 di-probe; **14 DITERIMA, 2 DITOLAK dengan bukti**.

### Diterima (semua `engine="python"` — fail-closed default)

Tak ada crates.io grammar yang dibuktikan parse-identik dengan revisi bundled
untuk satupun bahasa W12, dan wave ini tidak menambah crate kernel; semua
bahasa baru dilayani walker referensi lewat jalur fallback bridge (preseden
dart/swift W7, dipin `test_python_fallback_language_routes_to_reference`).

| bahasa | deklarasi | import | call | catatan jujur |
|---|---|---|---|---|
| objc | class_interface/class_implementation→class, protocol_declaration→interface, function_definition, method_declaration/definition→method, struct/enum/typedef→type, preproc_def→constant | preproc_include field `path` (#import = #include) | call_expression.function, message_expression.method | interface+implementation = 2 node class nyata (bukan duplikat) |
| zig | FnProto→function (field `function`) | VarDecl regex `@import\("…"` — VarDecl biasa (`const MAX`) tak lolos regex → tanpa fabrikasi | — | call = SuffixExpr+FnCallArguments tanpa field callee; struct-via-VarDecl tak terdeklarasi (tak bisa dibedakan deklaratif) |
| nim | routine→function (proc/func/method/template), typeDef→type, constant→constant | importStmt regex `^import` | — | nama di child `symbol` polos via identifier_types; invocation = primary+primarySuffix tanpa node call |
| crystal | class_def→class, module_def→module, method_def→function (flip method di scope class) | `require` regex string | call.method | `def self.run` → method lewat name field |
| solidity | contract→class, interface→interface, struct/enum→type, function_definition→function (flip method), state_variable→variable | import_directive field `source` | call_expression.function | — |
| erlang | module_attribute→module, fun_decl→function | import_attribute field `module` | call.expr | nama fun_decl 2 hop: clause→name→atom; remote call (m:f) teratribusi ke nama fungsi saja |
| fsharp | named_module→module, function_declaration_left→function | import_decl regex `open` | — | value_declaration_left (`let X = 42`) sengaja TIDAK dideklarasi (kelas penolakan ocaml/r W7); nama type terkubur 2 level tanpa field → type absen jujur |
| powershell | function_statement→function, class_statement→class, class_method_definition→method | command regex `^(using namespace\|module\|assembly\|Import-Module)` | — | command = pipeline tanpa field callee; import-regex anchored sehingga command biasa tak terfabricasi |
| gdscript | class_name_statement→class, function_definition→function; const/variable_statement→constant/variable (VariableRule: scope file saja) | extends_statement regex (base type = dependensi utama file; kontrak tak punya kind inherits) | — | call node membawa callee sbg identifier tanpa field |
| v | module_clause→module, struct→type, interface→interface, enum→type, function_declaration→function, const_spec→constant | import_declaration field `path` | call_expression | fn dengan receiver tetap "function" (receiver field tak bisa jadi syarat deklaratif) |
| haxe | class→class, interface→interface, enum→type, function_declaration→function (flip method) | import_statement regex | call_expression.object | package_statement sengaja diskip (field `name` ganda → hanya segmen pertama — akan salah-nama) |
| gleam | function→function, type_name→type (hanya type_name ber-field name) | `import` field `module` | function_call.function | anotasi tipe memakai node `type`, jadi type_name hanya muncul di definisi (terverifikasi) |
| vala | class→class, interface→interface, struct/enum→type, function_definition→function (flip method) | `using` regex | function_call.identifier | — |
| elisp | function_definition→function | list regex `^\(require '…` | — | defvar/defconst berbagi `special_form` dengan `let` → tak dideklarasi; limitasi jujur: list non-require menelan anaknya (defun di dalam progn tak terlihat) |

Ekstensi baru (18): .m/.mm .zig .nim .cr .sol .erl/.hrl .fs/.fsx .ps1/.psm1
.gd .v .hx .gleam .vala .el. `.v` dipetakan ke V (kolisi dgn Verilog
didokumentasikan; Verilog bisa ditambah wave berikut dgn disambiguasi isi).

### Ditolak (bukti test permanen di tests/test_ingest_languages_w12.py)

| kandidat | bukti |
|---|---|
| groovy | grammar bundled mereduksi semua statement ke `command`/`unit`; `class Greeter {` parse sbg `command > unit(identifier 'class')` — tak ada node deklarasi/import utk dipetakan |
| clojure | `defn` adalah `sym_lit` polos di dalam `list_lit` (kelas penolakan elixir W7): walker deklaratif tak bisa mencocokkan teks simbol utk deklarasi |

### Gate yang berubah (dengan alasan)

- `test_declaration_node_types_are_grammar_identifiers`: regex diperlebar ke
  `[A-Za-z][A-Za-z0-9_]*` — grammar zig/nim memang CamelCase (`FnProto`,
  `typeDef`). Gate anti-typo kuat tetap ada:
  `test_declaration_node_types_exist_in_grammar` memvalidasi thd grammar asli.
- `NO_CALL_FIELD_GRAMMARS` += {zig, nim, fsharp, gdscript, elisp, powershell}
  (gate dua arah: call_types kosong WAJIB utk mereka, non-empty utk sisanya).

## 2. Synthesis passes: 4 → 12 (+8, target 8-12 TERPENUHI)

Semua pass baru: provenance `synthesis:<pass>`, confidence di metadata,
kill switch via `SynthesisConfig.enabled_passes`, idempoten (dedup identitas
`_edge_exists` provenance-agnostik), cap fail-closed (skip utuh, tak pernah
terpotong), deterministik (urutan SQL ORDER BY + sort kanonik payload).
SYNTHESIS_VERSION 1→2. Urutan tetap: `type_hint_edges` mendahului
`dependency_injection` (yang mengonsumsi edge `type_of`-nya);
`callback_edges` tetap terakhir.

| pass | aturan | confidence |
|---|---|---|
| module_member_calls | ref calls gagal `no_resolution`; tail cocok TEPAT 1 simbol di modul yang di-import file sumber → calls edge + ref resolved | 7/10 |
| reexport_propagation | `import pkg` → file __init__/index; terminal re-export di dalamnya (walk import→import depth-cap, cycle-safe) → importer references tiap terminal; cap `max_reexport_fanout=32` | 3/5 |
| dependency_injection | method `__init__`/`constructor` milik class dgn edge `type_of` → class references tipe itu; cap `max_injected_deps=16` | 6/10 |
| interface_implementation | nama method class ⊇ nama method interface (cakupan PENUH), interface ≤ `max_interface_methods=32` | 3/5 |
| framework_route_registration | ref calls gagal ke {route,get,post,put,delete,patch,head,options} dari function/method di file yg meng-import {flask,fastapi,express,django,bottle,aiohttp,starlette,sanic,falcon} → handler references import node framework; ref dibiarkan gagal (edge references ≠ jawaban calls) | 3/5 |
| event_handler_wiring | emit/dispatch/raise/trigger/fire_X ↔ on/handle_X (snake+camel) berbagi suffix event; cap per event 16 handler/8 emitter — trip = event diskip utuh (pola EVENT_FANOUT_CAP) | 2/5 |
| test_subject_edges | test_foo.py / foo_test.go / foo.test|spec.ts|js → file:subjek references bila basename subjek unik; ambigu → skip | 7/10 |
| factory_function_edges | create/make/new/build_X (snake+camel) → tipe X unik global (class/interface/type) | 3/5 |

## 3. Resolver helpers: 0 → 21 (target 10+ TERLAMPAUI)

`resolve/lang_helpers.py` BARU — pola tabel 36-helper codegraph ditulis
ulang: tiap helper = dataclass beku + fungsi murni
`candidates(target_name, source_path) -> tuple[ModuleCandidate, ...]`.
Helper tak pernah menyentuh store, tak pernah melempar, deterministik,
ter-confine repo (tanpa path absolut/`..`), dan referensi eksternal
menghasilkan NOL kandidat (specifier bare JS, `use std::…` Rust — eksternal
bukan path lokal; memfabricasi = bohong).

21 helper: python, javascript, javascriptreact, typescript,
typescriptreact, go (direktori paket, `is_dir`), rust (crate::/self::/super::),
java, c, cpp (include relatif-dulu), csharp, ruby (lib/), php (PSR-4, src/),
lua (init.lua), haskell (src/), perl (::→lib/), scala, kotlin, solidity,
crystal.

Wiring: tier baru **(d) module_helper** di `ReferenceResolver._resolve_ref`,
hanya utk ref `imports`, sesudah tier import-path, kill switch
`ResolverConfig(helper_tier_enabled=False)` (default True = perilaku baru
yang diinginkan; False = perilaku pra-W12 persis). Strategi tercatat di
outcome/metadata edge (`strategy: module_helper`). Go: kecocokan direktori
paket — 1 direktori distingtif menang (anchor = file terurut-pertama),
>1 → `ambiguous_module:N` gagal tertutup. `resolver.py` hanya diubah
ADDITIF (tier baru + 1 flag config + label Literal); tier (a)(b)(c) tak
berubah.

## 4. Angka nyata

- Bahasa: **33** (12 W2 + 7 W7 + 14 W12). Passes: **12** (4 + 8).
  Helpers: **21**.
- Test baru: `test_ingest_languages_w12.py` (107), `test_lang_helpers.py`
  (125), `test_synthesis_w12.py` (78), `test_depth_w12_properties.py`
  (5 property) = **315 test dedikatif**, + ratusan kasus parametrisasi baru
  di test_ingest_languages.py / test_ingest_extractor.py /
  test_kernel_rust_registry.py dari 14 bahasa baru. Target 300+ terlampaui.
- registry.json: 28019 byte, 33 bahasa, `--check` OK, dump deterministik.
- Fixture: 14 file `tests/fixtures/ingest/<lang>/sample.*`, semua
  parse-bersih (`parse_error == False`, dipin test).
- Bench codebase: refresh baseline SAH (preseden SUB-95/SUB-102) — delta
  tree-sitter-python terbukti 100% dari tier helper: probe dua-jalan
  menunjukkan helper_tier=True 1561 edges vs False 1557 = baseline lama
  persis; +4 edges = 4 edge `strategy: module_helper` (#include C yang kini
  terselesaikan), refs_resolved 292→296, refs_failed 2386→2382, nodes tak
  berubah, determinisme byte-identik tetap OK.
- Bug nyata ditemukan bench saat integrasi: `_package_dir` memakai hasil
  prefilter LIKE tanpa verifikasi literal — SQLite LIKE case-insensitive
  utk ASCII, sehingga `path.index(marker)` bisa melempar ValueError pada
  overmatch. Fix: cek literal authoritative (dipin
  `test_go_package_dir_like_overmatch_is_safe`).

## 5. Residual terdokumentasi

- Semua bahasa W12 python-engine: tak ada parity dual-engine (crate tak
  diaudit). Jalur fallback ter-pin.
- zig/nim/fsharp/gdscript/elisp/powershell tanpa call refs (grammar tanpa
  field callee — gate dua arah).
- elisp: list non-require menelan subtree (import-probe swallow).
- `.v` → V; Verilog butuh disambiguasi isi (wave berikut).
- framework_route_registration tak me-resolve ref (edge references jujur,
  ref tetap failed — tak menyamar sebagai jawaban resolver).
