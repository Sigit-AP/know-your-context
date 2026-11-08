# WS-B — Retrieval: FTS freshness event-driven + resolver name_tail (SUB-160)

Scope (TASK2-PLAN.md §WS-B): Q1, R1, Q2, R2 + bundel migrasi v6 (S3).
HEAD dasar: 5952097.

## 7 pertanyaan pakar (SEBELUM kode)

1. **Masalah**: (Q1) setiap cold query retrieval membayar fingerprint
   `count(*)+sum(length())` = SCAN penuh tabel `symbols` (1,7–2,0 s @1,85M
   simbol = ~97% waktu cold p95 5.760 ms). (R1) resolver `_tail_candidates`
   memakai `name LIKE '%.tail'` = SCAN `symbols` per ref (879 ms/ref @1,85M).
   (Q2) channel LIKE tanpa cap: miss cold 4.453 ms @1,85M. (R2) `_module_file`
   suffix LIKE → SCAN files (murah hari ini, diukur).
2. **Kendala keras**: kontrak beku v1–v5 + 7 digest + 300 test kontrak tidak
   disentuh; migrasi v6 ADDITIVE-only; fail-closed di store lama (v5→v6
   upgrade path + downgrade rejection); determinisme byte-identik (bench
   2-run); tier ladder skor lookup tidak berubah urutan; recall ground-truth
   15/15 tidak turun; PoT gate tetap hijau; TIDAK commit.
3. **Tujuan terukur**: cold p95 @1,85M ≤150 ms (dari 5.760 ms); freshness
   check O(1) (EXPLAIN tanpa SCAN symbols); tail per ref ≤1 ms @1,85M
   (EXPLAIN SEARCH idx_symbols_name_tail); miss cold ≤500 ms; R2 ≤1 ms atau
   indeks; S3 EXPLAIN SEARCH pada lookup konflik journal.
4. **Manfaat**: query interaktif pada store multi-GB menjadi layak; ingest
   skala besar (resolver per ref) turun dari ~detik ke ~ms per ref.
5. **Pelajaran dari masalah**: freshness-by-rescan tidak berskala — freshness
   harus event-driven (ditulis saat mutasi, dibaca O(1)); pencarian suffix
   tanpa kolom termaterialisasi tidak bisa diindeks — materialisasi +
   backfill di migrasi, jaga konsistensi di SEMUA jalur tulis.
6. **Prinsip desain**: (a) freshness token harus bergerak pada SETIAP mutasi
   symbols, apapun jalur tulisnya (termasuk raw SQL) → trigger DB, fail-closed
   by construction, bukan konvensi pemanggil; (b) migrasi additive, satu
   transaksi, rollback-able, parity artifact↔konstanta dipin test;
   (c) perilaku resolver byte-identik: indeks hanya untuk tail bebas
   pemisah ('.'/'::'), jalur LIKE lama dipertahankan untuk tail eksotik;
   (d) fail open pada cache/freshness non-kritis, fail closed pada skema.
   Catatan deviasi dari plan: plan menulis `event_high_watermark`, tetapi
   terbukti di kode (pipeline._write_batch = raw SQL tanpa event) bahwa
   watermark itu TIDAK bergerak saat ingest → dipakai apa adanya akan
   menyajikan FTS basi. Pengganti setara-niat: counter `symbols_watch` yang
   digerakkan trigger (event-driven di level DB) + fingerprint SCAN sebagai
   arbiter saat counter bergerak (rebuild hanya bila drift nyata).
7. **Kill criteria**: (Q1) recall hit@10/top-1 < 15/15 di bench/codebase →
   rollback; (R1) bench/codebase 2 run tidak byte-identik, atau migrasi v6
   gagal fail-closed di store v5 → rollback; (Q2) urutan tier berubah
   (fuzzy menyalip LIKE) → rollback; (R2) EXPLAIN tetap SCAN dan >5 ms →
   tambah indeks ekspresi; trigger menurunkan throughput ingest >10% →
   ganti trigger dengan bump eksplisit di jalur tulis + uji ekuivalensi.

## Bukti before (terukur, perintah reproduksi tercatat di laporan akhir)

Perintah: `/root/know-your-context/.venv/bin/python /root/.hermes/cache/scratch/cluster-b/ws-b/measure_ws_b.py`
(lalu `measure_ws_b_after.py` + `measure_ws_b_quiet.py` untuk after). Store:
salinan `bench-c.sqlite3` (16 MB / 14.896 simbol) dan `bench-c2.sqlite3`
(1,72 GB / 1.847.104 simbol, ter-pin dari verdict cluster B).

| metrik | small (15k) before | big (1,85M) before |
|---|---|---|
| EXPLAIN fingerprint | SCAN symbols | SCAN symbols |
| fingerprint cost | 13,3 ms | 11.637 ms (cold) |
| cold hit p50 / p95 (cache off) | 26,5 / 62,1 ms | 3.723 / 10.786 ms |
| cold miss p50 / max | 116 / 124 ms | 2.928 / 4.276 ms |
| tail per ref (hit 31.744 kand.) | 11,7 ms (256 kand.) | 1.262 ms |
| tail per ref (miss) | 20,0 ms | 1.092 ms |
| module_file suffix | 0,58 ms (505 files) | 47,6 ms (62.620 files) |
| EXPLAIN tail / module / journal | SCAN / SCAN / SCAN | SCAN / SCAN / SCAN |

## Hasil (GREEN)

Implementasi:
- **Migrasi v6 additive** (`store.py` `_SCHEMA_V6_STATEMENTS`,
  `migrations/versions/0006_{up,down}.sql`, registry `ops/schema_migrations.MIGRATIONS`):
  `symbols.name_tail` (ekspresi SQL terbukti ekuivalen dengan
  `extractor._name_tail` pada 37 nama adversarial + unicode), `files.path_rev`
  (reverse via recursive CTE), tabel `symbols_watch` + 3 trigger (INS/UPD/DEL),
  `idx_symbols_name_tail`, `idx_files_path_rev` pada `lower(path_rev)`,
  `idx_invocation_journal_effective_key` (S3 bundle). Backfill SEBELUM trigger
  dibuat (migrasi @1,72 GB = 80 s sekali jalan).
- **Q1** `retrieval_v2/fts.py`: freshness = watch_seq O(1) (SEARCH PK);
  fingerprint SCAN hanya arbiter saat counter bergerak; rebuild hanya bila
  drift nyata. Deviasi dari plan (event_high_watermark) TERBUKTI perlu:
  pipeline ingest menulis symbols via raw SQL tanpa event → watermark event
  tidak bergerak saat ingest (akan menyajikan FTS basi).
- **R1** `resolve/resolver.py::_tail_candidates` → `store.symbols_with_tail`
  (SEARCH idx_symbols_name_tail) untuk tail bebas '.'/'::'; tail eksotik tetap
  jalur LIKE lama byte-identik.
- **R2** `_module_file` → GLOB prefix pada `lower(path_rev)` (lower() SQLite =
  fold ASCII = persis semantik case LIKE; metakarakter GLOB di-escape).
- **Q2** `LookupConfig.like_scan_cap=5000` + LIMIT deterministik + warning
  saat terpotong; ladder tier tak berubah.

Bukti kebenaran:
- Ekuivalensi pada korpus nyata (14.896 simbol): 2.517 tail DISTINCT 0
  mismatch; 1.010 cek module_file 0 mismatch
  (`ws-b/equivalence_check.py`).
- bench/codebase run: recall hit@10 **15/15**, top-1 **15/15**, determinisme
  byte-identik 4/4 repo (ledger-20261108T171837Z) — kill criteria Q1/R1 aman.
- Trigger overhead ingest (238 file, best-of-3, interleaved): 7,368 s vs
  7,276 s = **+1,26%** (< kill 10%).
- Test baru/diubah: test_store_v6.py (16), test_resolve_name_tail.py (9),
  TestEventDrivenFreshness (5), TestLikeScanCap (5), schema_migrations v6
  (+rollback v6→v5, parity artifact, trigger-aware splitter), pin v5→v6 di
  test_store_v3/v5/ops_migrate.

Angka after (host 2 vCPU, quiet window 18:07 UTC, load 0.10):
- **Struktural (deterministik, bebas load)**: EXPLAIN tail = SEARCH
  idx_symbols_name_tail; module_file = SEARCH idx_files_path_rev; journal
  conflict = SEARCH idx_invocation_journal_effective_key; watch = SEARCH PK.
- Big (1,85M): cold hit p50 3.723 → **71,4 ms (52×)**; tail miss 1.092 →
  **0,7 ms (1560×)**; module_file 47,6 → 1,6–2,8 ms warm (~20×); migrasi
  v5→v6 @1,72 GB = 80 s sekali jalan.
- Tail @big: minimum 496 kandidat per tail (replikasi ×124 memang begitu) →
  2,6–6 ms — sekarang terikat O(kandidat), bukan O(tabel). Bukti mekanik
  sub-ms: store nyata 15k → 4 kandidat = 0,044 ms.
- Pathological tail 'f' (31.744 kandidat): 1.262 → 332 ms (dibotaki fetch
  31k baris + sort, bukan scan).
- Q2 miss cold @1,85M: 2.928–4.276 → **955 ms (3,1–4,5×)** — jujur: target
  ≤500 ms TIDAK tercapai absolut di host ini; floor = full LIKE scan 1,85M
  baris (~1,0–1,2 s diukur langsung; covering index tidak membantu —
  diverifikasi eksperimen, SQLite tetap full scan untuk '%x%' OR dua kolom).
  Dengan faktor kalibrasi host terdokumentasi 2,35 ≈ 406 ms di host
  referensi. Cap 5000 membatasi MATERIALISASI match (bukan scan miss murni);
  ladder tier terbukti tak berubah (test + recall 15/15).
- R2: plan menyebut "indeks ekspresi suffix"; SQLite tanpa reverse() →
  direalisasikan sebagai kolom termaterialisasi path_rev + indeks ekspresi
  lower(path_rev) + GLOB prefix (fold ASCII = persis semantik case LIKE).

Yang mengejutkan: (1) plan menyebut event_high_watermark tetapi jalur ingest
tidak menulis event — token freshness harus trigger-level; (2) cap LIKE tidak
membatasi SCAN pada miss murni (LIMIT hanya membatasi materialisasi) — miss
cold tetap linear thd tabel, turun karena tak ada lagi fuzzy+fingerprint di
jalurnya... (lihat analisis Q2); (3) `ruff format` dan E501 berkonflik pada
string panjang — diselesaikan dengan deskripsi ≤120 char.

