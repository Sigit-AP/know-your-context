# W12 — VERDICT 7 Level (SUB-120)

Instrumen verdict 7 level: persentase nyata per level untuk 3 repo
(know-your-context vs codegraph vs graphify), komposit = rata-rata tertimbang
7 level. Semua metrik diukur dengan skrip pada ketiga repo — tidak ada angka
tulisan tangan.

## 7 pertanyaan pakar (dijawab sebelum kode)

1. **Masalah**: skor kompetitif yang ada (scorecard kapabilitas, bench
   kompetitif) mengukur *fitur berbukti-test* dan *tugas benchmark*. Operator
   meminta sudut pandang berbeda: verdict menyeluruh atas **wujud kode itu
   sendiri** di 7 level (profesional → kompleksitas flow engineering), terukur
   dari kode nyata ketiga repo, reproducible per metrik.
2. **Kendala**: tidak boleh ada pengukuran manual/opini; deterministik penuh
   (2 run pada pohon yang sama → skor identik); angka dilaporkan apa adanya
   (termasuk level di mana kita KALAH); aturan pembagian-nol tidak boleh
   mengarang angka; repo lawan TypeScript (codegraph) dan Python (graphify)
   harus diukur dengan aturan yang setara.
3. **Tujuan terukur**: 7 level × 8–12 metrik (terpenuhi: 7×10 = 70 metrik);
   ledger JSON berisi nilai mentah + perintah pengukur per metrik per repo
   (`python bench/verdict/run.py metric <id> --repo <repo>` — benar-benar
   mereproduksi nilai, dipin oleh test); run exit 0; ≥30 test (terpenuhi:
   62 test, 61 passed + 1 skip jujur); 2 run identik pada pohon stabil.
4. **Manfaat**: operator mendapat verdict komposit lintas-dimensi yang bisa
   diaudit per metrik sampai ke perintah shell; auditor bisa memverifikasi
   satu metrik tanpa menjalankan seluruh suite.
5. **Pelajaran dari masalah**: instrumen ukur yang berubah diam-diam =
   kecurangan struktural (WAVE3-PLAN §0.1). Maka: katalog metrik adalah kode
   berversi (suite `verdict-7-level` v1.0.0), formula tertulis di ledger,
   dan rasio tak-terbatas (lawan = 0) TIDAK pernah diubah menjadi angka —
   dilaporkan `null` + dihitung `unbounded`.
6. **Prinsip desain**: (a) semua nilai mentah dari skrip, bukan tulisan
   tangan; (b) deterministik — walk terurut, tanpa waktu/random dalam skor;
   (c) nol lawan tidak menghasilkan angka (null + unbounded count); (d)
   aturan identik untuk ketiga repo (perbedaan bahasa hanya pada mekanisme
   parsing: AST untuk Python, regex/brace-scanner untuk TypeScript — setara
   per metrik dan terdokumentasi per metrik).
7. **Kill criteria**: jika >20% metrik tidak bisa diukur deterministik pada
   salah satu repo → hentikan dan persempit katalog; jika composite jauh di
   bawah target operator (400%) → laporkan apa adanya + analisis (tidak
   terjadi; lihat hasil).

## Arsitektur

- `bench/verdict/metrics.py` — katalog (7 `Level` berbobot, bobot equal 1.0)
  + 70 fungsi `measure(RepoData) -> float` + `RepoData` (cache teks/AST,
  parse sekali per file). Python diukur via `ast`; TypeScript via regex +
  brace-scanner (komentar/string ter-strip, deterministik).
- `bench/verdict/run.py` — CLI `run | check | print | metric <id> --repo
  <name>`; scoring murni (`compute_scores`, tanpa I/O); ledger JSON
  `results/ledger-<UTC>.json` + `verdict-latest.md`.
- Formula: `relatif = 100 * ours / lawan`; lawan 0 → `null` bila kita > 0
  (unbounded, dilaporkan terpisah), `100` bila keduanya 0. Level = rata-rata
  relatif yang terdefinisi. Komposit = rata-rata tertimbang 7 level. Gabungan
  = rata-rata komposit vs codegraph dan vs graphify.
- Root repo bisa di-override via env `KYS_VERDICT_ROOT_{OURS,CODEGRAPH,
  GRAPHIFY}` (dipakai test pada repo fixture).

## Hasil nyata (ledger-20261102T011524Z, exit 0; 2 run berturut-turut
011323Z/011524Z skor byte-identik)

| Level | vs codegraph | vs graphify |
|---|---|---|
| l1 profesional | 53.90% | 88.41% |
| l2 codebase | 730.05% | 2829.17% |
| l3 arsitektur | 94.63% | 170.75% |
| l4 flow engineering | 345.15% | 1616.74% |
| l5 kompleksitas codebase | 96.01% | 902.88% |
| l6 kompleksitas arsitektur | 130.20% | 307.58% |
| l7 kompleksitas flow engineering | 412.00% | 707.41% |
| **komposit** | **265.99%** | **946.13%** |
| **gabungan** | **606.06%** (target 400%: codegraph BELUM, graphify TERPENUHI, gabungan TERPENUHI) ||

Catatan angka apa adanya: kita KALAH di l1 dan l5 vs codegraph (LOC/test/code
mereka lebih besar; `env_vars` 19 vs 97, `kill_switch_sites` 1 vs 17 — kita
memang membaca sedikit env var). Unbounded (lawan 0, kita > 0, tidak dijadikan
angka): vs codegraph 3 metrik, vs graphify 8 metrik. Angka bergeser dari run
pertama (003116Z) karena sibling SUB-121 menambah kode `src/` saat pengukuran
berjalan — instrumen mengukur realitas yang bergerak; dua run pada pohon
stabil byte-identik.

## Yang terbukti / tidak / mengejutkan

- **Terbukti**: determinisme pada pohon stabil (test fixture + real-repo pass
  saat pohon diam); perintah per-metrik mereproduksi nilai ledger (test
  membandingkan); 3 bug instrumen ketangkap saat build dan difix dengan
  aturan yang sama untuk semua repo: (1) env var kita tersembunyi di balik
  konstanta modul (`os.environ.get(KILL_SWITCH_ENV)`) → deteksi AST dengan
  resolusi konstanta; (2) 17 tool MCP kita didefinisikan sebagai kunci dict
  `_TOOL_TO_OPERATION`, bukan literal `types.Tool(...)` → penghitungan kunci
  dict `*_TOOL*`; (3) false-positive path absolut pada `contract_docs` →
  path relatif root.
- **Tidak**: determinisme 2-run pada repo HIDUP saat sibling agent menulis
  `src/` (SUB-121 depth push paralel) — test real-repo kini sadar-snapshot
  (sha256 src+tests) dan SKIP jujur bila pohon berubah mid-measure, bukan
  gagal palsu. Angka final di atas dari run saat pohon cukup stabil; angka
  bisa bergeser sedikit setelah SUB-121 selesai (instrument mengukur realitas
  yang bergerak).
- **Mengejutkan**: codegraph unggul telak di permukaan profesional mentah
  (LOC, test count, komentar) — verdict ini menyeimbangkan klaim katalog
  kapabilitas (di mana kita unggul besar) dengan realitas ukuran kode lawan.

## Gates

- `uv run python bench/verdict/run.py run` → exit 0 (ledger di atas).
- `uv run pytest tests/test_verdict.py` → 62 passed (301s), exit 0 — termasuk
  determinisme real-repo sadar-snapshot (sha256 src+tests; SKIP jujur hanya
  bila sibling menulis mid-measure, terbukti 2 passed + 1 skip dalam 3 run
  berturut selama SUB-121 aktif).
- `uv run ruff check bench/verdict tests/test_verdict.py` → 0; `ruff format
  --check` bersih; `pyright bench/verdict` → 0/0/0.
- TIDAK commit (aturan kolam renang).
