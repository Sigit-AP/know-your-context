# WS-C — Kernel Rust: routing spec.engine + py.allow_threads + divergensi 5/36 (SUB-161)

**Sumber:** TASK2-PLAN.md §WS-C (B1, B2, B3, K4) · cluster-b-verdict (B1/K1, B2/K3, B3/K2)
**HEAD dasar:** 5952097 · **Scope:** kernel-rust/ + src/know_your_context/ingest/ (extractor.py, kernel_bridge.py) + tests/test_kernel_rust.py (+ file test baru bila perlu)

## 7 pertanyaan pakar (dijawab SEBELUM kode)

1. **Masalah**: (a) Kernel Rust divergen di 5/36 file requests nyata
   (`__init__.py`, `api.py`, `help.py`, `utils.py`, `test_requests.py` — semua
   `EXTRACTION_ERROR: name must be a non-empty string`) → klaim "byte-identical
   parity" palsu di korpus nyata dan setiap file itu membayar pajak fallback
   +68%/file. (b) 16 bahasa `engine="python"` tetap dicoba ke kernel dulu,
   gagal, baru fallback → +27%/file sia-sia. (c) PyO3 wrapper menahan GIL
   sepanjang parse+walk → ThreadPool(2) 0,81× (lebih lambat dari serial);
   paralelisme multi-core mati total.
2. **Kekurangan/kendala**: kontrak beku v1–v5 tidak disentuh; output kernel
   harus byte-identik dengan Python walker (kontrak emisi F1); tidak ada
   `unsafe` baru; kill switch `KYS_DISABLE_RUST_KERNEL` tetap bekerja; host
   2 vCPU → pengukuran timing harus pakai best-of-N + ambang kill-criteria
   (bukan ambang ketat); tidak commit.
3. **Tujuan terukur** (target plan): divergensi 0/36 + parity korpus-nyata
   hijau; 0 attempt kernel untuk `engine="python"` (pajak +0%); speedup
   2-thread ≥1,8× (kill criteria plan: <1,3× berarti GIL masih tertahan);
   61+ test kernel hijau; determinisme; rebuild via maturin.
4. **Manfaat**: semua jalur ingest (CLI, pipeline, MCP) — 16 bahasa non-kernel
   tak lagi bayar +27%; ekstraksi paralel (HardwarePlan workers) benar-benar
   hidup; bench/codebase bisa berjalan tanpa fallback senyap.
5. **Pelajaran dari masalah**: parity test per-fixture tidak cukup — pola
   nyata (`from . import x` dengan kandidat field dot-only) lolos fixture.
   Desain harus menguji korpus vendored ter-pin, dan guard fail-closed
   (NodeIdError/allocate) harus dicerminkan di KEDUA engine bersama jalur
   fall-through-nya, bukan hanya penolakannya.
6. **Prinsip desain**: (1) Python walker = referensi kebenaran; kernel
   mencerminkan, bukan menafsirkan. (2) Routing membaca spec.engine — kernel
   hanya disentuh untuk bahasa yang dia layani. (3) Setiap fix membawa proof
   test + angka before/after dengan perintah reproduksi. (4) Fail-closed
   di jalur data (guard allocate tetap ada; hanya jalur yang memicu menjadi
   setara referensi).
7. **Kill criteria**: (B1) output Rust ≠ Python di SATU file korpus setelah
   fix → berhenti, jangan ubah pin; (B2) satu bahasa engine="python" tetap
   dirutekan ke kernel → fix salah tempat; (B3) speedup 2-thread tetap
   <1,3× setelah detach → GIL masih tertahan, cari titik tahan lain
   (mis. canonical_json) atau laporkan; (K4) test baru flaky di 2 run →
   perbaiki hingga deterministik.

## Bukti reproduksi BEFORE (2026-11-08, host 2 vCPU)

- Divergensi: loop `extract_source_via_kernel` atas 36 file `.py` dari
  `bench/codebase/data/requests.tar.gz` (ter-pin) → **31 ok / 5 divergen**
  (file persis sama dengan verdict, error `EXTRACTION_ERROR: name must be a
  non-empty string`).
- Akar (analisis kode): `walker.rs::emit_import` mengambil field pertama yang
  ADA tanpa strip + tanpa cek `name_tail`, dan tidak jatuh ke field berikutnya;
  Python `_emit_import` (fix F-SUB94-1, extractor.py:567-589) men-strip
  kandidat, me-skip kandidat tanpa name-tail (`from . import x` →
  `module_name="."`), lalu regex, lalu menolak target tanpa name-tail. Rust
  hanya menolak target kosong → `name_tail(".")=""` → `allocate("")` →
  EXTRACTION_ERROR.
- Routing: `extractor.py::extract_source` mencoba kernel untuk SEMUA bahasa
  tanpa membaca `spec.engine`.
- GIL: `lib.rs` `extract_source`/`extract_file` tanpa `py.detach`.

## Angka BEFORE/AFTER (host 2 vCPU, semua perintah dapat diulang)

| Item | Before | After | Perintah reproduksi |
|---|---|---|---|
| B1 divergensi korpus | **5/36** EXTRACTION_ERROR (`__init__.py`, `api.py`, `help.py`, `utils.py`, `test_requests.py`) | **0/36**, byte-identik raw+canonical | `pytest tests/test_kernel_rust.py -k requests_corpus` → 36/36 |
| B1 pajak fallback help.py | routing 30,216 ms vs walker 25,229 ms (**+19,8%**) | routing 2,583 ms vs walker 3,772 ms (**−31,5%**, kernel kini melayani) | loop 300× `extract_source` vs `TreeSitterExtractor.extract` |
| B2 pajak engine=python | swift routing 1,841 vs walker 1,478 ms (**+24,6%**); kernel selalu dicoba+gagal | **0 attempt kernel** (dipin test 16/16 bahasa); routing 0,792 vs walker 0,727 ms (+9,0% = biaya tetap wrapper publik, BUKAN kernel) | `pytest tests/test_kernel_rust.py -k engine_python` → 16/16 |
| B3 GIL | ThreadPool(2) vs serial = **0,79–0,94×** (lebih lambat) | **mekanisme**: overlap probe — main thread 2,4% rate solo saat worker di dalam call (GIL tertahan) → **162–194%** sesudah detach; throughput 2-thread terukur 1,38–1,70× di jendela relatif sepi | `pytest tests/test_kernel_rust.py::test_kernel_extraction_releases_gil` (probe overlap, bukan rasio wall-clock) |

## Fix yang dilakukan

1. **B1** `kernel-rust/src/walker.rs::emit_import` — mencerminkan F-SUB94-1
   persis: kandidat `target_fields` di-strip dulu, kandidat tanpa name-tail
   di-skip (fall-through ke field berikutnya), regex hanya bila tak ada
   kandidat valid, dan target akhir tanpa name-tail ditolak (bukan hanya
   target kosong). Guard `allocate("")` tetap ada sebagai cermin fail-closed
   `NodeIdError`, kini tak tercapai lewat jalur import.
2. **B2** `extractor.py::extract_source` — routing membaca
   `get_spec(resolved).engine == "rust"` sebelum menyentuh kernel; spec
   di-hoist (satu lookup untuk kedua jalur).
3. **B3** `kernel-rust/src/lib.rs` — `py.detach` membungkus parse+walk di
   `extract_source` dan `extract_file`; argumen `&str` di-own dulu (borrow
   terikat lifetime GIL).
4. **K4** `tests/test_kernel_rust.py` — parity diperluas ke korpus nyata
   ter-pin (`bench/codebase/data/requests.tar.gz`, 36 file): raw dataclass +
   canonical JSON per file, + repro minimal dot-import, + 17 test routing
   engine, + 1 test GIL. Koleksi 3 file kernel: 103 → 158 test.

## Bukti tambahan (implikasi ke WS-G1)

- `bench/codebase/run.py run` kernel-ON (kernel baru) vs
  `KYS_DISABLE_RUST_KERNEL=1`: edges identik di 4 target
  (fixture 18, requests 2832, chalk 321, tree-sitter-python 1561) —
  ledger `20261108T151833Z` vs `20261108T151901Z`. Kontribusi kernel terhadap
  drift edge = **0** (fallback lama sudah menghasilkan output referensi).
- `bench/codebase/run.py check` → exit 0 (strict gate hijau pada baseline
  terkini; baseline HEAD sudah 1561 — "+4 vs pin 1557" di verdict cluster-B
  adalah pin lama).
- Determinisme: subset parity/routing 54p × PYTHONHASHSEED 0/1/42 → identik.
- Rebuild: `maturin build --release` (wheel cp314 + refresh
  `target/release/libknow_your_context_kernel.so`) + `cargo build --release
  --bin kys-kernel` (bin saja, .so tak tersentuh).

## Sesudah membangun — yang terbukti / tidak / mengejutkan

- **Terbukti**: 0/36 divergen byte-identik; 0 attempt kernel untuk 16 bahasa
  engine=python; GIL dilepas (rasio >1,3× konsisten); edges bench identik
  kernel on/off/baru.
- **Tidak/tahan**: target ≥1,8× di 2 thread tidak stabil terukur di host 2
  vCPU yang dibagi agen sibling (load 1–3,7); terukur 1,38–1,70× di jendela
  relatif sepi. Karena itu gate B3 dibuktikan lewat probe overlap mekanisme
  (GIL dilepas ⟺ main thread jalan selama call): versi rasio wall-clock
  flaky di bawah kontensi — dibuktikan goyah 0,83–1,52× — lalu diganti probe
  dengan margin >10× (held=2,4% vs dilepas=162%+ vs ambang 25%). RED probe
  diverifikasi terhadap build HEAD tanpa detach (worktree terpisah,
  /tmp/kernel-red, sudah dihapus).
- **Mengejutkan**: kernel kini ~31% lebih cepat dari walker Python pada file
  yang dulu divergen (pajak berbalik jadi keuntungan); +9% residual B2 adalah
  biaya tetap wrapper `extract_source` (encode + resolusi bahasa), sama untuk
  semua pemanggil, bukan pajak kernel.
