# W15 — I3 differential fuzzing + query depth (SUB-130)

Dua fitur kedalaman nyata, keduanya deterministik, typed-error, dan terbukti
oleh eksekusi (bukan deskripsi).

## Jawaban 7 pertanyaan pakar

1. **Masalah.** (a) Paritas Rust↔Python hanya terbukti pada fixture berhingga
   pilihan manusia; ruang input di antaranya belum terjelajah — divergensi
   engine adalah bug paritas yang lolos. (b) Kedalaman query vs codegraph
   lemah: tidak ada closure transitif callers/callees di luar traversal
   depth-terbatas retrieval_v2, tidak ada dependency path, impact analysis,
   co-change history, maupun boundary enforcement di permukaan operasional.
2. **Kendala.** Kontrak beku (14 knowledge ops) tak disentuh; tidak ada commit;
   tidak ada randomness tak ber-seed; kedua engine harus dibandingkan
   byte-identik pada output kanonik (kontrak F1: walker Python dipanggil
   langsung, bukan lewat routing kernel).
3. **Tujuan terukur.** ≥200 test baru hijau; ≥1000 kasus/bahasa × 8 bahasa
   sweep paritas (nol divergensi atau bug ditemukan+fix); ruff+pyright bersih
   pada file yang disentuh; CLI + webview wiring bekerja end-to-end.
4. **Manfaat.** Builder: regresi paritas tertangkap sebelum audit; operator:
   pertanyaan "apa yang rusak jika X berubah" dan "file apa yang berubah
   bersama" terjawab deterministik dari store, tanpa model.
5. **Pelajaran.** Paritas pada contoh pilihan ≠ paritas; fuzz tanpa shrinker
   menghasilkan repro tak terbaca → shrinker ddmin deterministik dengan trace.
6. **Prinsip.** (a) seeded & derandomized (Random("kys-fuzzgen:lang:seed") —
   Mersenne Twister stabil lintas proses/PYTHONHASHSEED); (b) divergensi =
   artefak repro; (c) grammar coverage divalidasi terhadap registry produksi;
   (d) fail closed: simbol tak dikenal/ambigu, knob di luar batas, policy
   malformed → typed error sebelum traversal.
7. **Kill criteria.** Generator 3 bahasa butuh >2 iterasi → batasi ke python
   (rencana §I3). TIDAK tercapai: 8 emitter bahasa jalan pada iterasi pertama.

## Fitur 1 — I3 differential fuzzing

* `src/know_your_context/ingest/fuzzgen.py` — generator seeded per bahasa
  (python, javascript, typescript, go, java, c, rust, ruby — semua bahasa
  dual-engine `engine="rust"`). Driver menghasilkan *plan* (imports,
  konstanta, variabel, fungsi saling memanggil, kelas+method, impl-block
  Rust) lalu emitter per bahasa menuliskan teks. Anomali ber-seed: file
  kosong, comment-only, baris terpotong (parse-error parity), blank-heavy,
  identifier unicode (bila grammar mengizinkan).
* `src/know_your_context/ingest/diffuzz.py` — runner diferensial
  (outcome = `("ok", canonical_json)` atau `("error", TipeError)`; dua
  engine gagal dengan tipe sama = agreement) + shrinker ddmin deterministik
  atas baris (granularitas n//2 → 1, kiri-ke-kanan, restart saat diterima,
  budget 400 attempt, trace tercatat).

### Bukti

* **24 000 kasus (3000/bahasa × 8 bahasa), 0 divergensi**, 43,8 s
  (`compare_case` per kasus; mesin: .venv aktif, kernel release termuat).
  Sweep yang sama terkodifikasi sebagai `test_thousand_case_sweep_per_language`
  (default 1000/bahasa; `KYS_FUZZ_SWEEP` menaikkan).
* `test_seeded_corpus_byte_parity_across_engines` — 8 bahasa × 25 seed
  terparametri (200 kasus), byte-identik.
* `test_injected_divergence_detected_and_shrunk` — kernel terinstrumentasi
  (drop node terakhir) → terdeteksi, shrinker mengecilkan repro dengan trace,
  hasil tetap divergen; `test_shrink_is_deterministic` — dua shrink identik;
  `test_shrink_reaches_minimal_core` — 1-minimal terhadap marker tunggal.
* `test_corpus_generation_seed_stable` + pinned corpus hash
  (`test_corpus_digest_pinned`) — stabilitas lintas proses.
* Nol bug nyata ditemukan pada sweep ini → tidak ada regression-test hasil
  shrink; hook-nya sudah permanen (Divergence.repro()).

## Fitur 2 — Query depth (code intelligence)

`src/know_your_context/analytics/code_intel.py` — murni, store-agnostic,
snapshot `CodeGraph` terkanonisasi (urutan input tak relevan; edge malformed
/unknown-endpoint/self-loop didrop+di-hitung):

* `callers_of` / `callees_of` — closure transitif reverse/forward atas
  calls/imports/references; entri terurut `(depth, path, line, col, node_id)`;
  guard `max_results_reached`.
* `dependency_path(a,b)` — jalur terpendek; tie-break leksikografis
  (heap `(cost, path)`) → tak tergantung urutan input; tanpa jalur = hasil
  valid (`path_count: 0`), bukan error; budget ekspansi dengan guard.
* `impact_analysis(node)` — closure reverse termasuk rantai `contains`
  (file pemilik selalu terdampak) + histogram depth + via-kind + daftar file.
* `co_change` — pasangan file yang berubah bersama; support + confidence
  arah sebagai desimal eksak; deterministik penuh.
* `boundary_violations` — layer dari prefix path (longest-prefix, tie → nama
  terurut); edge lintas layer yang tidak diizinkan eksplisit = pelanggaran;
  node tanpa layer di-skip+dihitung, tak pernah dihakimi diam-diam.

Typed errors: `CodeIntelError` ⊃ `UnknownSymbolError` (→404 webview),
`AmbiguousSymbolError` (kandidat tercantum), `InvalidPolicyError` (→400).

### Wiring

* **CLI** `codeintel callers-of|callees-of|dependency-path|impact|co-change|boundary-violations`
  (`ops/codeintel_cli.py`; ops-only, di luar kontrak beku; envelope error
  standar; exit 0/1/2). Adapter read-only: `load_code_graph` (symbols +
  graph_edges), `change_sets_from_source_versions` (diff manifest
  source_version berurutan per source — satu-satunya histori perubahan
  per-file yang jujur terekam di store).
* **Webview** — route `GET /api/codeintel?op=...` (AUTHENTICATED), payload
  `WebviewApp.codeintel_payload`, tab "Code Intel" di shell
  (index.html + app.js, renderer per op).

### Bukti E2E (store nyata, ingest nyata)

`src/{core,api}.py` (helper←service←handle) → ingest 2 file →
`callers-of helper` = service(d1), handle(d2); `dependency-path handle helper`
= cost 2 lewat service; `boundary-violations --layers core:core.py;api:api.py`
= 1 pelanggaran api→core. Semua exit 0, canonical JSON.

## Verdict angka apa adanya

* Test baru: **395** (87 code_intel + 32 CLI + 18 webview + 50 fuzzgen +
  208 differential fuzz — 8×25 parametri + 8 proof) — target ≥200 terlampaui.
* Sweep: 24 000 kasus, 0 divergensi; CI default 8 000 kasus (~16 s).
* ruff check + `ruff format --check` + pyright: bersih pada semua file baru/
  yang disentuh. Catatan: `serve/webview.py` memuat lint-error sementara
  milik WIP SUB-131 (route ledger/config mereka) — bukan baris SUB-130;
  gate akhir menunggu mereka selesai.

## Risiko / catatan integrasi

* `co_change` hanya sekuat histori source_version: store yang tidak pernah
  relearn menghasilkan himpunan kosong (jujur, bukan dibuat-buat).
* `_parse_layers`/`_parse_flows` dipakai bersama CLI dan webview (satu
  sumber validasi policy).
* Determinisme corpus dipaku oleh hash — mengubah generator = mengubah hash
  pinned; itu disengaja (perubahan grammar harus eksplisit).
