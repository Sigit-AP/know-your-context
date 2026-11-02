# W13 — Docs + Changelog + Docstring (SUB-123)

## Tujuh pertanyaan pakar (dijawab SEBELUM kode, sesuai EXPERT-THINKING.md)

1. **Masalah**: instrumen verdict l1 (profesional) mengukur repo ini LEMAH pada
   permukaan dokumentasi: `changelog_sections` 0 (codegraph 34), `doc_lines`
   11370 vs codegraph 24629, `comment_lines` 1848 vs 17555 — padahal materi
   nyatanya ada (50+ recovery doc, 12 wave, kontrak v1–v4, 17 MCP tool,
   33 bahasa). Masalah NYATA-nya: tidak ada CHANGELOG yang merekonstruksi
   sejarah rilis, tidak ada dokumen arsitektur/verdict/security/API tingkat
   profesional, dan ada celah docstring pada kelas publik di watcher /
   pipeline / serve (mis. `ScopeDenied`, `DeadlineExceeded`, error hierarchy
   `ingest/pipeline.py`) — pengguna baru tidak bisa membaca kontrak, invariant,
   dan failure mode tanpa membaca implementasi.

2. **Kekurangan/kendala**: kontrak beku (14 knowledge ops + contract v1–v4)
   TIDAK disentuh; TIDAK commit; ruff/pyright/format harus tetap bersih;
   konten harus NYATA — setiap entri changelog berasal dari git log (hash
   nyata yang bisa di-`cat-file`), setiap klaim di docs bisa diverifikasi ke
   kode (nama tool, route, bahasa, level verdict dibaca dari sumbernya, bukan
   diketik dari ingatan); docstring tidak boleh placeholder ("TODO", "…").

3. **Tujuan terukur**: (a) `CHANGELOG.md` valid Keep a Changelog dengan
   `## [0.1.0]`, `## [0.2.0]`, `## [0.3.0]`, `## [Unreleased]` + kategori
   Added/Changed/Fixed/Security — `changelog_sections` 0 → ≥4;
   (b) 4 dokumen baru (`architecture.md`, `verdict.md`, `security.md`,
   `api.md`) dengan link internal hidup — `doc_files` +4, `doc_lines` naik
   nyata; (c) docstring coverage 100% (modul + kelas publik + fungsi publik)
   untuk scope watcher/pipeline/resolver/serve; (d) ≥20 test baru hijau;
   (e) ruff check+format 0, pyright 0/0/0 pada file yang disentuh.

4. **Manfaat**: evaluator/manusia membaca `CHANGELOG.md` dan langsung melihat
   sejarah rekayasa (W1–W13) tanpa menggali git; pengguna API membaca
   `docs/api.md` dan menemukan seluruh permukaan (CLI/MCP/HTTP) dengan failure
   mode; auditor membaca `docs/security.md` dan melihat model keamanan yang
   benar-benar diimplementasi (capability kyc1, loopback-only, no-AI guard).

5. **Pelajaran dari masalah**: metrik l1 lemah BUKAN karena materi tidak ada,
   melainkan karena materi tidak dipublikasi di permukaan standar (CHANGELOG
   root, docs/ top-level, docstring). Pola kegagalan yang harus dicegah:
   padding — menulis kata tanpa fakta. Karena itu test mem-verifikasi fakta
   (hash commit ada di git, nama tool == `EXPECTED_TOOLS`, route ==
   `KNOWLEDGE_OPERATIONS`/`OPS_OPERATIONS`, level == `LEVELS` di
   `bench/verdict/metrics.py`), sehingga dokumen tidak bisa melayang dari
   kode tanpa test merah.

6. **Prinsip desain**:
   - *Fakta dari sumbernya*: test membaca `cli.py`, `mcp_server.py`,
     `http_api.py`, `bench/verdict/metrics.py` secara AST/import dan
     membandingkan dengan isi docs — docs yang basi = test merah.
   - *Keep a Changelog ketat*: hanya 6 kategori standar, tanggal ISO,
     versi descending, link ref per versi; wave W1–W13 muncul sebagai tag
     di dalam entri nyata + tabel wave, bukan section semu.
   - *Docstring = kontrak*: setiap docstring baru menjawab "kapan ini
     muncul / apa yang dijamin / apa yang harus dilakukan pemanggil".
   - *Tidak ada gate yang dilonggarkan*: file baru harus lolos ruff/format/
     pyright yang sama seperti kode lain.

7. **Kill criteria**: berhenti menambah konten bila (a) test sudah tidak bisa
   membedakan konten nyata dari padding (semua assertion hijau hanya karena
   panjang), atau (b) doc_lines naik tapi test fakta (hash, tool, route)
   harus dilonggarkan untuk hijau — itu tanda padding. Alternatif: potong
   dokumen ke subset yang terbukti faktual, biarkan angka l1 apa adanya
   (aturan EXPERT-THINKING: jangan lunakkan angka).

## Rencana build (superpowers: plan → TDD → verify → review)

1. **RED**: `tests/test_docs_changelog.py` (≥20 test): format Keep a
   Changelog, hash commit nyata, link docs hidup, kesejajaran docs↔kode
   (CLI/MCP/HTTP/verdict-level), docstring coverage AST 100% pada scope.
2. **GREEN**: tulis `CHANGELOG.md`, 4 dokumen, docstring hilang.
3. **Verify**: pytest file baru + test tetangga modul yang disentuh
   (watcher/pipeline/serve/resolve), ruff check+format, pyright.
4. **Review**: audit diri — tidak ada TODO/TBD, semua angka punya sumber.

## Hasil (sesudah membangun)

**RED nyata → GREEN nyata**: `tests/test_docs_changelog.py` 32 test —
run pertama 30 failed/2 passed (CHANGELOG + 4 docs belum ada, 18 kelas/fungsi
publik tanpa docstring), setelah build 32 passed (1.32s). Dua bug test
ditemukan saat GREEN dan diperbaiki di test-nya (regex Level multi-line,
kategori wajib hanya untuk heading rilis), satu gap nyata ditemukan:
`serve/watchdog.py` (file sibling W13) punya 3 kelas publik tanpa docstring —
diisi additif.

**Terkirim**: `CHANGELOG.md` (Keep a Changelog: Unreleased + 0.3.0/0.2.0/0.1.0,
kategori Added/Changed/Fixed/Security, 40 hash commit nyata terverifikasi
`git cat-file`, wave W1–W13 sebagai tag entri + tabel wave, link ref per
versi) + `docs/architecture.md` + `docs/verdict.md` + `docs/security.md` +
`docs/api.md` + docstring kontrak/invariant/failure-mode untuk
`source_watcher.ChangeKind`, `ingest/watcher.ChangeKind`, 6 error
`ingest/pipeline`, 6 error + `SpawnResult` di `serve/superpower`,
`run_proxy`, `write_message`/`result_response`/`error_response`,
`serve/__main__.{build_parser,main}`, dan 3 kelas `serve/watchdog`.

**Angka terukur** (instrumen verdict itu sendiri, exit 0):
`l1.changelog_sections` 0 → 5; `l1.doc_files` 124 → 132 (kolaborasi dgn
sibling W13); `l1.doc_lines` 11370 → 12142. `l1.comment_lines` tidak naik
banyak — docstring tidak dihitung sebagai komentar oleh instrumen; tidak ada
padding komentar yang ditambahkan untuk mengejar angka (kill criteria #7
dihormati).

**Gates**: ruff check 0 + format bersih pada 9 file yang disentuh (4 issue
UP047 di `serve/flow_retry.py` = file sibling, tak disentuh); pyright
0/0/0 pada 9 file src yang disentuh; pytest 444 passed (test baru 32 +
tetangga watcher/pipeline/serve 412) exit 0.

**Tidak terbukti / kejutan**: parity 34 section codegraph tidak tercapai (5
vs 34) — satu-satunya cara mencapainya adalah section semu per-wave, yang
ditolak oleh prinsip "konten nyata"; angka dilaporkan apa adanya.
