# W15 — Korpus fixture nyata skala besar (SUB-129)

## 7 pertanyaan pakar (terjawab SEBELUM kode)

1. **Masalah**: korpus fixture tipis — 1 file per bahasa (33 total) untuk
   ekstraktor 33 bahasa, 12 pass sintesis, 20 helper resolver. Regresi
   perilaku per-fitur-bahasa bisa lolos karena tak ada fixture yang memaksa
   perbedaan `async`/`generics`/`error handling` per bahasa.
2. **Kekurangan/kendala**: kontrak beku tak disentuh; tidak commit;
   `tests/fixtures` dieksklusi dari linter (fixture boleh invalid untuk
   linter tapi WAJIB valid untuk grammar bahasanya); fixture tak boleh
   dummy/kosong; scope hanya `tests/fixtures/`, `tests/`,
   `docs/recovery/w15-fixtures.md`.
3. **Tujuan terukur**: ≥600 file fixture nyata; ≥200 test (parametrized);
   full suite hijau; determinisme lintas `PYTHONHASHSEED`; ruff+pyright hijau.
4. **Manfaat**: maintainer mendapat jaring regresi per (bahasa × kategori
   fitur); metrik verdict `test_fixtures`/`parametrize` naik secara jujur
   (setiap fixture dipakai ≥1 test).
5. **Pelajaran dari masalah**: ekspektasi tak boleh snapshot buta — harus
   ditulis dari semantik bahasa lalu DIVERIFIKASI terhadap ekstraktor;
   ketidakcocokan diperbaiki di sisi yang salah (fixture atau ekspektasi),
   dan gap grammar dicatat, tidak dipalsukan.
6. **Prinsip desain**: (a) manifest = registri konsumsi — fixture tanpa test
   gagal CI; (b) ekspektasi subset semantik (`(kind, name)` + target import),
   bukan golden bytes; (c) determinisme di-assert per fixture, bukan sampling;
   (d) gap kemampuan grammar dicatat dengan bukti, bukan diakali.
7. **Kill criteria**: jika grammar bundled tak bisa parse konstruk minimal
   yang valid → tulis ulang fixture ke konstruk yang diparse + catat gap di
   dokumen ini (bukan memaksakan ekspektasi palsu). Alternatif akhir:
   tandai bahasa itu tipis dan laporkan angka apa adanya.

## Apa yang dibangun

| Area | Isi | File fixture | Test |
|------|-----|--------------|------|
| `ingest/<lang>/` | 33 bahasa × 15-16 fixture (functions, async, classes, imports, typing/generics, errors, module, edge-nesting, edge-syntax, calls, types + sample lama) | 499 | 4 param sweep (extract/nodes/imports/posisi) + 6 shape |
| `synthesis/<pass>/` | 12 pass: 5 skenario pipeline nyata (interface_implementation, framework_route_registration, event_handler_wiring, test_subject_edges, factory_function_edges) + 7 skenario seed.json (state yang ditinggalkan resolver saat fail-closed) | 27 | 12×3 param + coverage 12-pass |
| `helpers/` | 37 kasus untuk 20 helper resolver (candidates tepat, repo-confined, deterministik; target kasus dibuktikan diekstrak dari file fixture-nya) | 38 | 37×4 + 2 |
| `documents/` | 12 markdown + 4 plaintext + 4 PDF binary text-layer nyata | 21 | per-dok + per-heading + per-halaman PDF |
| `provenance/` | 12 file teks sumber; submit→approve→verify_provenance per file + tamper drill | 13 | 12 param + 2 negatif |
| `debt/` | 10 kasus: 4 detektor × positif+negatif + gabungan + truncasi budget | 1 (+synthesis reuse) | 10×3 + bentuk |
| `edge_kinds/` | 14 core edge kinds: round-trip store, simetri `bridges`, cross-check pipeline_produced vs skenario sintesis | 1 | 14×3 + kontrak |

Total: **604 file fixture**, **5352 test** pada 9 file test baru
(plus helper `_fixture_corpus.py`, `_synthesis_fixtures.py`).

## Gap grammar yang ditemukan (bukti nyata, dicatat — tidak diakali)

Probe hidup terhadap grammar bundled (tree-sitter-language-pack) selama build
menemukan batasan NYATA; fixture ditulis pada konstruk yang ter-parse bersih:

- **haxe**: `return switch …` (switch-sebagai-ekspresi), `~/`, anonymous fn
  ber-return-type, `abstract`, constraint generik `T:Bound` → parse_error.
- **powershell**: warisan class (`class X : System.Exception`) → parse_error;
  `Export-ModuleMember` dengan daftar koma → parse_error.
- **v**: tipe result `!T` dan parameter generic `[T]` → parse_error
  (fixture typing memakai interface; dicatat di sini).
- **vala**: `errordomain` dan `yield` → parse_error (async memakai
  `.begin/.end`).
- **crystal**: definisi `macro` → parse_error; `struct` tak terpetakan
  (member-nya muncul sebagai function top-level).
- **solidity**: `library` tak terpetakan (hanya `contract_declaration`).
- **zig**: `FnProto` di dalam body struct tidak dikunjungi walker.
- **fsharp**: param beranotasi + return annotation non-`int`, atau `let` di
  dalam body → function_declaration_left tak cocok; definisi type tak
  terpetakan sama sekali (registry hanya `named_module` +
  `function_declaration_left`).
- **nim**: seksi `const` tak menghasilkan node; `enum`/`distinct` typeDef tak
  terpetakan; multi-import sebaris hanya menangkap sebagian.
- **php**: `namespace X\Y;` tak menghasilkan node module; group-use dan
  `use function` menangkap target parsial.
- **objc**: `NS_ENUM` macro → parse_error; nama method kehilangan colon.
- **python**: import alias `import x as y` → target ref `x as y` (bukan
  `x`); assignment di dalam fungsi tak diekstrak sebagai variable.
- **resolver helpers**: registry berisi **20** helper (bukan 21 seperti di
  brief) — `javascriptreact`/`typescriptreact` hanya nama helper (bukan
  bahasa ekstraktor); diverifikasi `len(HELPERS) == 20`.

## Bukti gate

- RED nyata per bagian: collection error / FileNotFoundError sebelum fixture
  ditulis → GREEN sesudahnya.
- Mutation check: merusak 1 ekspektasi manifest →
  `test_fixture_expected_nodes_present[ingest/python/01-functions]` FAIL →
  regen → PASS.
- Determinisme: `PYTHONHASHSEED=1` dan `=42` → 5033 passed identik
  (canonical_json adalah fungsi murni bytes sumber).
- ruff check + format: 0 pelanggaran pada 11 file scope.
- pyright: 0 errors / 0 warnings / 0 informations pada scope.
- Full suite: dicatat di board setelah selesai (in-flight saat doc ditulis).

## Yang tidak dilakukan

- Tidak ada perubahan pada `src/` — semua gap grammar di atas adalah temuan
  untuk wave berikutnya, bukan perbaikan diam-diam.
- Tidak ada commit (aturan sub-agent).
