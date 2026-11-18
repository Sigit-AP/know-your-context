# Rencana Penurunan RSS Ingest L4 (SUB-206)

Board:
- [16:30 UTC] SUB-206 | rss ingest research | RISET | struktur pipeline dipetakan per fase (walk/extract/resolve/synthesize/propose/commit); defisit L4 terkonfirmasi 1356.9 MB vs graphify 860.5 MB | bukti: `src/know_your_context/ingest/pipeline.py` + ledger `scratch/stress3/results_final.jsonl`
- [17:01 UTC] SUB-206 | rss ingest research | RISET SELESAI | profil korpus kecil (200 file L4) + probe sqlite di store L4 asli (313 MB): GraphNode 2.03 KiB/instance, GraphEdge 0.64 KiB, sqlite tuning +56 MB hanya untuk 1 query baca; dominan = akumulator proposal Pydantic (~485 MB ekstrapolasi) + sqlite cache/mmap | bukti: `scratch/sub206/profile_phases.py`, `object_sizes.py`, `probe_sqlite.py`
- [17:05 UTC] SUB-206 | rss ingest research | PLAN TERTULIS | `docs/research/rss-ingest-plan.md`: 7 penyebab berperingkat + fix per item (estimasi MB, risiko, test dulu) + kill criteria; TIDAK commit, src/ tak disentuh | bukti: dokumen ini

---

## 1. Konteks dan target

Ledger kanonik L4 (`scratch/stress3/results_final.jsonl`, harness2.py, korpus 5.137 file `.py` sintetis ~88 MB):

| Produk | Ingest wall | Peak RSS | Catatan |
|---|---|---|---|
| **KYC (kita)** | **346.1 s** | **1356.9 MB** | store sqlite 312.6 MB; nodes 186.982 / edges 181.845 / refs_parked 602.907; resolve 159.408 ok / 443.499 failed |
| Graphify | 1729.7 s | 860.5 MB | graph.json 380.5 MB; nodes 163.760 / edges 324.776 |
| Codegraph | 1480 s | 1645 MB | — |

Target SUB-206: **ingest_rss_mb < 860 MB** tanpa memperlambat ingest (kita menang wall 4-5×; anggaran regresi wall ≤ +25% agar tetap jauh di depan).

Semua angka di dokumen ini diberi label: **[terukur]** = diukur langsung di host ini (koefisien per-obyek, RSS fase, probe sqlite, angka ledger); **[estimasi]** = ekstrapolasi dari koefisien terukur ke skala L4 (atau dibatasi batas atas yang terukur). Tidak ada klaim tanpa salah satu label.

## 2. Peta struktur besar yang hidup bersamaan (static read `pipeline.py` + `store.py`)

Fase berjalan berurutan dalam satu proses (tidak ada subproses): A (walk/preflight) → B (extract, stream) → C (commit, interleaved dgn B) → retry-refs → resolve → synthesize (primary splits) → report. Struktur yang **dipegang sampai akhir run** (tumbuh linear):

| Struktur | Lokasi | Isi | Ukuran per item [terukur] |
|---|---|---|---|
| `state.proposals_nodes: list[GraphNode]` | `pipeline.py:523` diisi `_collect_proposal_nodes` (1251) | **SEMUA node semua file** (node file + simbol) sbg Pydantic `GraphNode` (ScoreVector ×3 Decimal, datetime, payload dict, validator id) | **2.03 KiB/node** [terukur] |
| `state.proposals_edges: list[GraphEdge]` | `pipeline.py:524` | SEMUA edge `contains` (file→simbol) sbg Pydantic `GraphEdge` | **0.64 KiB/edge** [terukur] |
| `state.introduced_tails: set[str]` | `pipeline.py:522`, isi 1250 | nama semua simbol (untuk retry failed-ref) | **0.10 KiB/entry** [terukur] |
| `state.reports: list[FileReport]` | `pipeline.py:525` | 1 per file (relpath, sha256, counts) | **0.16 KiB** [terukur] |
| `state.seen: set[str]` | `pipeline.py:521` | semua relpath | kecil |
| `work: dict[int,_WorkItem]` + `terminal` | `_run_phase_a` (833-834) | semua item kerja; token digest utk skipped/failed | **0.32 KiB/_WorkItem** [terukur] |
| `entries` (hasil `_walk`) | `_walk` (969) | semua (abs_path, relpath) — 2 Path/Py per file | kecil |
| sqlite page cache + mmap | `store.py:588-592` (dari `hardware/tuning.py`) | **cap cache 313 MiB + mmap min(db_size, ½RAM)=313 MB** pada store L4 | lihat §4.3 |

Fase B (extract) sudah **ter-streaming** (SUB-186): window `workers*4`, hasil per file dilepas setelah commit; kernel Rust terbukti bounded (§4.5). Fase resolve sudah **ter-chunk** (SUB-185, `RESOLVE_BATCH=4096`). Fase synthesize membangun **UniversalGraph kedua** (dict 186k node + index contains + list subtree) di atas obyek yang sama — tambahan transien, tapi menumpuk di puncak.

## 3. Metodologi profil (korpus ≤ 200 file, tidak mengganggu benchmark koordinator)

- Korpus: 200 file pertama korpus L4 (disalin ke `scratch/sub206/corpus`, 3.5 MB).
- In-process run `IngestionPipeline.ingest_path` dengan hook fase (monkeypatch, src/ tak disentuh): `_run_phase_a`, `_analyze_one`, `_commit_analyzed`, `resolve_pending`, `_primary_suggestions`, `_assemble_report`; sampel `/proc/self/status VmRSS` + `tracemalloc` per boundary.
- Varian: `default200` (tuning default = kondisi L4), `legacy200` (`StorageTuning.legacy()`), `noresolve200`, `serial200` (`INGEST_DISABLE_HW_TUNING=1`).
- Ukuran per-obyek: tracemalloc 5.000 instans.
- Probe sqlite: buka store L4 asli (313 MB, 181.845 simbol) dgn tuning default vs legacy, 1 query `COUNT(*)`, ukur RSS. (Read-only, < 2 s — bukan benchmark besar.)

## 4. Hasil pengukuran

### 4.1 Ingest 200 file (fase per fase)

| Run | Peak RSS | tm_peak | Catatan |
|---|---|---|---|
| default200 (tuning L4) | **150.7 MB** | 29.1 MB | [terukur] |
| legacy200 (pragmas lama) | **134.0 MB** | 29.1 MB | [terukur] → selisih tuning ≈ −17 MB di DB kecil |
| noresolve200 | **124.4 MB** | 23.9 MB | [terukur] |
| serial200 (workers=1, batch=1) | **137.8 MB** | 29.1 MB | [terukur] |

Fase: baseline proses ~53 MB [terukur] (interpreter + tree-sitter language pack + kernel bridge); walk ≈ 0; extract+commit menaikkan RSS ke ~116-130 MB; **resolve menaikkan ke puncak** (+10-26 MB); synthesize hampir datar di skala kecil (node direferensikan, tidak disalin).

### 4.2 Koefisien linear (per obyek) [terukur]

| Obyek | KiB/instance |
|---|---|
| `GraphNode` (Pydantic) | 2.03 |
| `GraphEdge` (Pydantic) | 0.64 |
| `FileReport` | 0.16 |
| `_WorkItem` | 0.32 |
| entry `set[str]` | 0.10 |

### 4.3 SQLite tuning — probe di store L4 asli (313 MB) [terukur]

| Skenario | RSS setelah 1 query `COUNT(*) FROM symbols` |
|---|---|
| Tuning default (mmap 313 MB + cache 313 MiB) | +**56 MB** (31 → 89 MB) |
| Legacy (no mmap, cache 2 MiB) | +3 MB |

Cache `cache_kib=320,716` dan mmap `min(db_size, ½ RAM, cap)` pada store 313 MB → cap keduanya ≈ 313 MB. Saat ingest menulis DB dari 0 → 313 MB, cache mengisi menuju cap. Kontribusi RSS sqlite saat ingest L4 penuh: **[estimasi] 150-350 MB, dibatasi cap terukur 313+313 MB** (anchor terukur: +56 MB hanya untuk satu scan baca; ingest menulis jauh lebih banyak halaman).

### 4.4 Ekstrapolasi L4 (koefisien terukur × jumlah ledger 186.982 node / 181.845 edge / 5.137 file)

| Komponen | MB @ L4 | Label |
|---|---|---|
| `proposals_nodes` (GraphNode × 186.982) | **~371-380** | [estimasi: koefisien terukur × jumlah ledger] |
| `proposals_edges` (GraphEdge × 181.845) | **~114-116** | [estimasi: koefisien terukur × jumlah ledger] |
| `introduced_tails` (~120-186k unik) | ~12-19 | [estimasi] |
| reports + seen + work + entries | ~4-10 | [estimasi] |
| Resolve (chunk 4096; +26 MB terukur di 200 file, sebagian sqlite) | ~50-150 | [estimasi, ter-chunk] |
| UniversalGraph synthesize (transien, menumpuk di puncak) | ~30-60 | [estimasi] |
| sqlite cache+mmap saat ingest | 150-350 | [estimasi, cap terukur] |
| Baseline proses | ~60 | [terukur ±] |
| **Total model** | **~790-1150** | vs **1356.9 terukur** (sisa = arena allocator yang tidak dikembalikan + cache di ujung atas) |

Pola pertumbuhan: yang **linear di jumlah node/edge** adalah `proposals_nodes` + `proposals_edges` (≈ 485 MB di L4 — item terbesar) dan tails (kecil). Resolve **tidak** linear (chunk 4096) tapi menaikkan sqlite cache. SQLite cache/mmap linear-terhadap-DB tapi **dibatasi cap**.

### 4.5 Ekstraksi (fase B) — bukan masalah

Kernel Rust (`kernel_bridge`) terbukti bounded: file terbesar korpus (101 KB, 181 node) → +8 MB panggilan pertama (load modul) lalu ±0 [terukur]. Window stream workers×4 + lock serial → transien ekstraksi kecil. Graphify mengisolasi native parser di subproses; kita sudah aman karena satu proses + serial lock.

## 5. Perbandingan teknik Graphify (860.5 MB, korpus sama)

Dari static read `/root/src/graphify/extract.py` (9031-9290, 9461-9462) + ledger:

1. **Obyek graf = dict polos**, bukan Pydantic: `_extract_single_file(...) -> dict`, diakumulasi `all_nodes.extend(result.get("nodes"))`. Tidak ada validasi ulang id per node, tidak ada ScoreVector/Decimal/datetime per node. → per-node ~0.3-0.5 KiB vs kita 2.03 KiB.
2. **Ekstraksi paralel via `ProcessPoolExecutor`** (`_extract_parallel`, 8844-8944): native tree-sitter + AST hidup di **subproses worker** (RSS worker dihitung harness via pgid-sum, tapi terisolasi & dibebaskan per task). Kita: kernel Rust in-process dengan lock seri → native memory tetap di proses utama.
3. **Tanpa resolve pass 602.907 refs** — Graphify menyelesaikan imported edges via pass kedua pada dict, tidak memarkir refs jutaan.
4. **Tanpa sqlite** — graph.json di-dump JSON di akhir (`json.dumps(result)`); tidak ada page cache 313 MiB + mmap 313 MB yang menahan halaman DB di RSS.
5. Wall 1729 s vs kita 346 s — kecepatan kita (sqlite + batch + kernel) justru sumber RSS: DB 313 MB di-RSS-kan via mmap/cache.

Kesimpulan: 860 MB Graphify = graf dict polos (~150-200 MB) + satu dump JSON 380 MB + worker transient. Kita tidak perlu meniru semuanya (jangan buang keunggulan wall sqlite), hanya: **menghilangkan Pydantic dari akumulator**, dan **mengendalikan cap sqlite cache/mmap saat ingest**.

## 6. Penyebab dominan berperingkat + rencana fix

### Fix 1 — Akumulator proposal ringan (ganti GraphNode/GraphEdge Pydantic) — [estimasi] −460 s.d. −490 MB di L4
- **Bagian**: `pipeline.py` `_collect_proposal_nodes` (1464-1513) + `_primary_suggestions` (1515-1569); konsumen hanya `propose_primary_splits` yang butuh `node_id/kind/name/path/line/col` (+`domain`, selalu None untuk ingest) dan edge `contains`.
- **Rencana**: akumulasi record tuple/dataclass ringan (~0.2 KiB) sbg ganti `GraphNode`/`GraphEdge`; bangun `UniversalGraph` (atau graf dict ringan) hanya saat `_primary_suggestions` — transien di akhir, bukan dipegang sepanjang run. Preserve skip-semantik: node yang id-nya tak cocok `make_node_id` TETAP dilewati (perilaku saat ini).
- **Estimasi simpanan**: `proposals_nodes` ~371 MB + `proposals_edges` ~114 MB ≈ **−485 MB** (koefisien terukur; ekstrapolasi ke jumlah L4).
- **Risiko**: output `primary_suggestions` / determinisme byte-identik; GraphNode validator saat ini menjamin well-formedness (id cocok, kind terdaftar) — ringan path harus menguji ulang.
- **Test DULU**: (a) determinisme byte-identik `IngestionReport.primary_suggestions` + `canonical_ingestion_dump(include_ids=False)` pada korpus tetap sebelum/sesudah; (b) uji skip node id-tak-cocok; (c) uji banyak edge `contains` count tetap; (d) determinisme lintas `PYTHONHASHSEED`.

### Fix 2 — Kendalikan pragma sqlite saat ingest (page cache + mmap) — [estimasi] −150 s.d. −350 MB di L4
- **Bagian**: `store.py:588-592` (mmap/cache dari `hardware/tuning.py`), knob `storage.cache_max_kib` / `storage.mmap_max_bytes`, atau `StorageTuning` eksplisit untuk koneksi ingest.
- **Rencana**: (a) cache dikap ~16-64 MiB (bukan 313 MiB) selama ingest; (b) mmap 0 atau ≤64 MB saat ingest (query tetap boleh pakai tuning penuh). Catatan penting: env `INGEST_DISABLE_HW_TUNING=1` yang ada saat ini **hanya** mengubah workers/batch di `pipeline._run` — TIDAK menyentuh pragma store; kill switch pragma terpisah (`KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING`). Fix harus menutup celah itu: satu saklar untuk mode RSS-rendah ingest.
- **Estimasi simpanan**: bounded oleh cap; anchor terukur +56 MB utk 1 scan → saat ingest penulis berkalih halaman: **150-350 MB** [estimasi].
- **Risiko**: wall-clock (cache adalah akselerator baca/tulis). Kita menang wall 4-5×, anggaran regresi ≤ +25% (masih 2-3× lebih cepat dari Graphify). Wajib diukur head-to-head wall sebelum/ sesudah.
- **Test DULU**: (a) `canonical_ingestion_dump` + bytes store identik antara tuning penuh vs terkendali (pragma terdokumentasi "change access mechanics, never results" — uji ini mengikat janji itu); (b) determinisme resolve counts; (c) micro-bench wall di korpus 200 file (regresi < +25%).

### Fix 3 — Synthesize tanpa UniversalGraph kedua — [estimasi] −30 s.d. −60 MB transien
- **Bagian**: `_primary_suggestions` (1515-1569) membangun `UniversalGraph` 186k node + `propose_primary_splits` (subtree_ids list + member_nodes tuples + 371 proposal × 500 member).
- **Rencana**: jalankan algoritma split di atas graf dict ringan dari record Fix 1 (contains-children map saja — cukup utk subtree walk + packing).
- **Estimasi simpanan**: 30-60 MB di puncak (setelah resolve; puncak adalah yang diukur harness).
- **Risiko**: determinisme packing `_size_split_proposals` — wajib byte-identik.
- **Test DULU**: sama dengan Fix 1 (a) — satu korpus tetap, diff byte output.

### Fix 4 — Resolve: pertahankan chunk, cek kandidat-set per ref — [estimasi] kecil
- Sudah ter-chunk (`RESOLVE_BATCH=4096`, SUB-185). Sisa: query kandidat (`symbols_with_tail`/`symbols_named`) fetchall per ref — di korpus sintetis tail populer (fn_0_0) bisa ratusan baris; aman tapi bisa dibatasi `LIMIT`. Simpanan kecil (<50 MB [estimasi]); prioritas rendah. Risiko: mengubah determinisme resolve — jangan tanpa test byte-identik resolve counts.

### Fix 5 — Minor (tidak direncanakan, dicatat)
- `introduced_tails` ~12-19 MB [estimasi] — dibutuhkan retry failed-ref; biarkan.
- `reports` + `--json` output (2.2 MB di L4) — dapat di-streaming tapi tidak signifikan.
- Baseline 53-60 MB (interpreter + tree-sitter language pack + kernel bridge) — biaya tetap, bukan sasaran.

## 7. Urutan eksekusi yang disarankan

1. Fix 1 (test dulu) → ukur ulang 200-file + ekstrapolasi — target RSS Python ~50% turun.
2. Fix 3 (menyatu dengan Fix 1, satu refactor).
3. Fix 2 (paling mudah diukur, gain terbesar kedua) — ukur wall sebelum/sesudah.
4. Fix 4 hanya jika setelah 1-3 masih > 860 dan resolve terbukti kontributor.

## 8. Kill criteria (diukur pada harness kanonik L4 — `harness2.py` / `results_final.jsonl`)

1. **`ingest_rss_mb` < 860** (parity Graphify) — primary; hard gate.
2. **Wall ingest ≤ 346 s × 1.25** (tanpa regresi material; kita tetap 3-4× lebih cepat dari Graphify 1729 s).
3. **Byte-identik**: `canonical_ingestion_dump(include_ids=False)` dan `primary_suggestions` pada korpus ter-pin identik sebelum/sesudah tiap fix (determinisme contract v3).
4. **Resolve counts identik** (L4: 159.408 resolved / 443.499 failed / 0 pending) pada korpus sama.
5. **Suite test** ingest/resolve/determinisme hijau penuh.
6. Jika setelah semua fix RSS masih > 860: buktikan batas host (baseline + model §4.4 + profil fase) dan laporkan angka apa adanya — jangan menurunkan target diam-diam.

---
*Disusun SUB-206, 2026-11-18. Hanya `docs/research/` yang diubah; TIDAK commit; `src/` tidak disentuh.*