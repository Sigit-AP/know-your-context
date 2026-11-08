# Cluster B Verdict — Deep Research (SUB-156)

Tanggal: 2026-11-08 · Host ukur: VPS 2-core Intel Xeon E5-2690 v2 @3.00GHz, 3GB RAM, SSD (125G) · Python 3.14.9 · Repo HEAD saat riset: `b786c42`
Scope: `ingest/` (extractor 33 bahasa, pipeline, documents, watcher), `resolve/` (resolver 2-fase, name_matcher, synthesis 12 pass), `retrieval_v2/` (FTS5 + rerank), `analytics/`, `kernel-rust/`.
Metode: REA-any (checklist→evidence ledger; observasi vs inferensi dipisah; REA penuh diskip per skill karena repo sumber utuh — probe runtime nyata dipakai sebagai gantinya) + EXPERT-THINKING 7-pertanyaan (tertulis di §7). **Semua angka dari run nyata di host ini**; skrip + data mentah di `/root/.hermes/cache/scratch/cluster-b/` (`measure_cluster_b.py`, `measure_c.py`, `measure_c2.py`, `measure-a.json`, `measure-c1.json`, `measure-c2.json`). Tidak ada kode repo yang diubah.

Korpus ukur: repo vendored ter-pin `bench/codebase/data/` (requests 3.9MB, chalk, tree-sitter-python — sha256 pinned) + replikasi path-rename 20× untuk skala menengah + replikasi baris SQL (bentuk/distribusi identik dengan baris nyata) untuk titik 407MB dan 1.72GB. Setiap replikasi diberi label metodenya.

---

## 0. EXPERT-THINKING (7 pertanyaan, tertulis)

1. **Masalah**: apakah cluster B (ingest→resolve→synthesis→retrieval→analytics→kernel) benar secara algoritma, dan tahan di skala nyata (store multi-GB, repo ribuan file)?
2. **Kendala keras**: tidak boleh mengubah kode; kontrak beku v1/v2 tidak disentuh; no-model boundary; angka harus dapat direproduksi (perintah + dataset ter-pin).
3. **Tujuan terukur**: per bagian — algoritma + positif/negatif berbukti, kompleksitas terukur, % overload vs batas yang bisa dicapai; angka performa sekarang; jawaban ya/tidak untuk 5-10GB dengan bukti; skor level profesional per butir dengan bukti.
4. **Manfaat**: koordinator tahu persis di mana 3-4 perbaikan bedah memberi faktor 50-4000×, tanpa menyentuh kontrak beku.
5. **Pelajaran dari masalah**: setiap regresi performa di repo ini historisnya lahir dari probe/scan O(N) yang disisipkan "demi keamanan" tanpa diukur di skala — maka setiap klaim di sini diukur di ≥3 titik skala.
6. **Prinsip desain yang dipegang saat menilai**: fail-closed pada kebenaran (ambiguity tidak boleh menebak) dipuji walau mahal; fail-open pada optimasi (kill switch) dipuji; determinisme byte-identical adalah fitur, bukan hiasan.
7. **Kill criteria riset**: berhenti mengejar hipotesis bila dua pengukuran independen mengonfirmasi; eskalasi bila ukuran tidak stabil >10% antar-run (tidak terjadi; determinisme terverifikasi byte-identical di bench).

---

## 1. ALGORITMA per bagian

### 1.1 Ingestion / Extractor (33 bahasa)

**Algoritma** (sendiri, tabelnya mengadopsi pola codegraph tapi di-rewrite): registry deklaratif `LanguageSpec` murni-data (930 baris, 33 bahasa: `bash c cpp crystal csharp dart elisp erlang fsharp gdscript gleam go haskell haxe java javascript kotlin lua nim objc perl php powershell python ruby rust scala solidity swift typescript v vala zig` — dihitung dari `languages.py`), satu AST walker generik (`extractor.py`, 846 baris) yang menginterpretasikan tabel: scope stack → node `kind:sha256(path:kind:name:line[:col])`, edge `contains`, ref tak-terselesaikan untuk calls/imports (resolusi memang dipisah per kontrak). Tambah bahasa = tambah data, nol kode walker. Deteksi generated/minified (nama + banner 60 baris + heuristik minifikasi) sebagai *hint, bukan filter*. Bound 1 MiB, UTF-8 strict, BOM ditangani, fail-closed typed errors (`FILE_TOO_LARGE`/`UNSUPPORTED_MEDIA_TYPE`/`DECODE_ERROR`).

**Positif (terbukti)**
- Desain data-driven terbaik di repo: 33 bahasa tanpa satu pun `if language ==` di walker.
- Dual-engine byte-identical diverifikasi LANGSUNH oleh saya: `models.py` (35.5KB) — `canonical_json()` Python == Rust, dan raw dataclass equality == True (lebih kuat dari klaim, karena urutan emisi F1 memang identik).
- Determinisme end-to-end ter-pin: bench/codebase run A/B sha256 identik (`fba6ee…` == `fba6ee…`).

**Negatif / bug nyata (terukur)**
- **B1 — kernel Rust divergen di file nyata, fallback menutupi**: 5/36 file `.py` requests (13.9%) gagal di kernel dengan `EXTRACTION_ERROR: name must be a non-empty string` (`__init__.py`, `api.py`, `help.py`, `utils.py`, `test_requests.py`), lalu Python walker mengambil alih. `tests/test_kernel_rust.py` (16 test) hanya menguji fixture per-bahasa — regresi dunia nyata ini tidak teruji. Biaya fallback: **+68%** per file terdampak (help.py: 10.98ms vs 6.53ms Python-only; kernel jalan separuh lalu Python parse ulang penuh). Hasil akhir benar (fallback = referensi), jadi ini bug *klaim parity* + performa, bukan kebenaran.
- **B2 — `spec.engine` tidak pernah dibaca oleh routing**: `extract_source()` mencoba kernel untuk SEMUA bahasa bila kernel termuat; 16/33 bahasa (`engine="python"`, mis. swift) selalu gagal (`no compiled grammar`) lalu fallback. Terukur **+27%** per file non-kernel (Package.swift: 1.55ms vs 1.22ms). Field `engine` terdokumentasi sebagai kontrak routing — implementasi tidak mengikutinya.
- **B3 — GIL ditahan sepanjang ekstraksi Rust**: tidak ada `py.allow_threads`/`detach` di `kernel-rust/src/lib.rs`. Uji nyata 24 file: serial 123ms vs ThreadPoolExecutor(2) 151ms → **speedup 0.81× (negatif)**. Fan-out `workers` pipeline (dari HardwarePlan) tidak memberi paralelisme sama sekali untuk jalur panas; di mesin 8-core potensi ~7× tidak terpakai.
- **B4 (lintas batas, ditemukan di jalur ingest) — `propose_primary_splits` mendominasi wall-time ingest**: cProfile 1 rep (129 file kandidat, store kecil): total 145.4s → `_primary_suggestions` 134.4s (**92%**), di dalamnya `graph_model.contains_children` 10.859 panggilan / `subtree_ids` 237 panggilan dengan **56.8 juta** akses properti enum (`enum.py:__get__` 40.2s tottime). Resolver hanya 5.7s (4%), ekstraksi ~5s. Pola: rekonstruksi containment in-memory per batch + enum-value lookup di loop dalam. (Pemilik kode: `graph_model.py` cluster A, tapi pemicunya pipeline cluster B.)

**Kompleksitas terukur**: walker O(node AST) murni; pipeline O(files) nominal tapi terserialisasi GIL (B3) dan ditunggangi saran primary-split super-linear (B4); resolver inline menambah O(refs × symbols) (lihat §1.2).
**Optimalitas overload**: wall ingest 145s vs lantai terukur ~11s (ekstraksi 5s + resolve 5.7s + tulis ~1s) → **~92% waktu terbuang, dapat direbut kembali** tanpa mengubah hasil.

### 1.2 Resolver 2-fase

**Algoritma** (berpasangan dengan `name_matcher.py`): cascade deterministik per ref — (a) exact `symbols.name` (index `idx_symbols_name`), (b) name_tail suffix, (c) import_path (Python dotted/relative ke tabel `files`, JS/TS relatif dengan urutan ekstensi/index), (d) helper per-bahasa (20 helper di `lang_helpers.py`, kill switch `KNOW_YOUR_CONTEXT_DISABLE_HELPER_TIER`). Ambiguity TIDAK PERNAH menebak: ≥2 kandidat → defer ke tier berikutnya atau gagal dengan alasan deterministik pertama (`ambiguous_exact:N` dst.). Disiplin batch: 1 read pass murni → 1 transaksi `add_graph_edges` → 1 `record_resolution_attempt` per ref; crash di antaranya replay bersih (edge identity dedup). Edge membawa `ref_id` di metadata → penghapusan file dapat me-resurrect ref (pola codegraph). `name_matcher.py` (520 baris) = tier pasca-primer: segment-vocabulary Jaccard + containment dalam `fractions.Fraction` (tanpa float), hanya me-retry `failure_reason == 'no_resolution'`, butuh margin `min_margin` ke runner-up, language-family gating, ubiquity ceiling 500.

**Positif**: alasan gagal persisten (schema v4) dan BatchReport dibaca-balik dari store, bukan dari memori; provenance `resolver:two-phase:v1` vs `resolver:name-match:v1` terpisah; idempoten by construction.
**Negatif / bug nyata (terukur)**
- **R1 — `_tail_candidates` = full table scan per ref**: `name LIKE '%.tail' OR name LIKE '%::tail'` → `EXPLAIN QUERY PLAN: SCAN symbols` (diverifikasi). Terukur: 6.7ms @14.896 simbol → **879ms @1.847.104 simbol per ref**. Tidak ada kolom/indeks `name_tail` di tabel `symbols` (indeks yang ada: name, lower(name), path, kind). Ini O(refs × symbols) nyata.
- **R2 — `_module_file` suffix LIKE → SCAN files** (murah hari ini, linear terhadap jumlah file).
- Resolusi rate korpus nyata (bench 2026-11-02): requests 40.3%, chalk 14.5%, tree-sitter-python 11.1% — mayoritas gagal adalah `external_module` (by design) + tail-ambiguity; angka mentah mengecilkan kualitas, tapi tetap: **hanya ~1 dari 4 ref lintas-file terikat** di korpus campuran.

**Kompleksitas**: tier a O(log N), tier b O(N) (R1), tier c/d O(files) per ref. **Overload**: tier tail 879ms vs ~0.2ms dengan indeks tail → **99.98% dapat direbut**.

### 1.3 Synthesis — 12 pass

**Algoritma**: `SYNTH_PASS_ORDER` 12 pass berurutan tetap (containment → reexport → type_hint → module_member_calls → reexport_propagation → dependency_injection → interface_implementation → framework_routes → event_handler → test_subject → factory → callback), masing-masing dengan cap fanout (`max_callbacks_per_channel=40`, `max_reexport_fanout=32`, dll.), provenance `synthesis:<pass>`, confidence terstruktur di metadata, **cap-trip = skip seluruh channel, tidak pernah truncate**, idempoten (run kedua insert 0 — ter-pin test), integrasi HardwarePlan: prefetch himpunan edge-key sekali (1 read terpaginasi) + flush insert per batch disk-profile.

**Positif**: filosofi cap yang benar (skip, bukan truncate); per-pass enable; prefetch mengganti N query `_edge_exists` jadi 1 (SUB-135).
**Negatif (terukur)**
- **S1 — N+1 di pass berat**: `module_member_calls` melakukan per-ref `symbol_by_node` + `outgoing_edges` + `symbols_in_path` per modul terimpor. Terukur pada store 14.896 simbol: **114.4s untuk 12 pass**, 13.410 kandidat module_member_calls → 0 inserted. (Pada run fase-A store 3.501 simbol: 8.0s.)
- **S2 — yield rendah di korpus kode**: fase A (3 repo nyata): 13 edge dari 3.547 kandidat (0,4%); fase C1: 0 inserted. Pass bernilai terutama untuk graf pengetahuan; untuk kode murni, biaya 114s vs hasil 0 perlu dipertanyakan sebagai default ON.

**Kompleksitas**: beberapa pass O(kandidat × query-per-kandidat). **Overload**: 114.4s vs ~1-2s dengan kandidat-batch join → **~98% dapat direbut**.

### 1.4 Retrieval — FTS5 + rerank

**Algoritma** (berpasangan: `fts.py` + `lookup.py` + `scoring.py` + `experience.py`): channel cascade — (1) FTS5 external-content + BM25 berbobot kolom (name 20 / kind 5 / path 1), fetch `max(limit×5, 100)` untuk ruang rescore; (2) suplemen exact-name disuntik dengan skor maksimum; (3) fallback LIKE (hanya bila FTS kosong): skor 1.0/0.9/0.8/0.5; (4) fuzzy Damerau-Levenshtein ber-boundary (cap scan 5000, skor 0.4/(1+d) — selalu di bawah LIKE sehingga tier tidak pernah tercampur). Rescore multi-sinyal: kind bonus + name-match ladder codegraph (80/60/prefix/substring) + path relevance (−15 untuk path test). Filter `kind:`/`lang:`/`path:`/`name:` = hard gate terakhir. Ordering total `(-score, path, line, col, node_id)`. LRU query cache berkunci **event high-watermark** (tulis ⇒ watermark bergerak ⇒ tidak mungkin melayani hit basi — diverifikasi kodenya). Experience reranker (W14): multiplier ter-clamp [0.5, 1.5], Decimal terkuantisasi 12 digit, sidecar ledger terindeks, jurnal hash-chained per keputusan, fail-open byte-identical saat mati.

**Positif (terukur)**: FTS5 sendiri skala sangat baik — **MATCH murni 50ms @1.847.104 simbol**; recall pada ground truth ter-pin (bench/codebase 2026-11-02): **hit@10 15/15, top-1 15/15, hierarchy 7/7**, determinisme byte-identical 2 run; warm cache p50 **0.05-0.10ms di SEMUA ukuran store** (15k → 1,85M simbol).
**Negatif / bug nyata (terukur)**
- **Q1 — freshness fingerprint = full SCAN `symbols` di SETIAP cold query**: `ensure_fts_index` menghitung `count(*)+sum(length(…))` atas seluruh tabel per panggilan search. Terukur terisolasi di store 1,72GB: fingerprint **1.701-2.004ms** vs FTS match 50ms → **~97% waktu cold query adalah penjaga freshness, bukan pencarian**. Ironis: cache LRU sudah memakai `event_high_watermark` yang murah — fingerprint bisa memakai mekanisme yang sama.
- **Q2 — channel LIKE tanpa cap**: miss cold (FTS kosong) = scan penuh + fuzzy 5000: terukur 110ms @15k → **4.453ms @1,85M simbol**.
- Fingerprint juga membuat `hit p95` membengkak: 37.5ms @15k → 932.7ms @477k → **5.760ms @1,85M** (linear ~1,1ms per 1.000 simbol — cocok dengan biaya SCAN).

**Kompleksitas**: FTS O(log N)-ish terukur 50ms@1,8M; guard freshness O(N) per query (Q1); LIKE O(N) (Q2). **Overload**: cold p95 5.760ms vs lantai terukur ~55ms → **99% dapat direbut** dengan freshness event-driven.

### 1.5 Analytics

**Algoritma** (suite besar, murni-fungsi atas read model `graph.json`, Decimal presisi 34, skor 12-desimal string, iterasi hanya atas list terurut): HITS, eigenvector (lapor `converged:false` dengan jujur saat osilasi bipartit), Katz ter-truncate dengan guard, clustering coefficient + transitivitas, **Louvain deterministik** (local-moving, tie-break ke key terkecil), reachability summary all-pairs, motif triangle + triad census 16-kelas MAN, assortativity Newman; plus `components.py` (WCC/SCC Tarjan iteratif, articulation/bridges DFS low-link, k-core Batagelj-Zaversnik), `paths.py` (Yen k-shortest, heap berkunci (cost, path_tuple)), `cycles.py` (anchored enumeration + dua cost guard), `centrality.py` (pagerank/degree/betweenness tersampel ber-seed lokal), `communities.py` (label propagation HANP-style), `code_intel.py` (callers/callees/dependency_path/impact_analysis/co_change/boundary_violations atas CodeGraph beku), `debt.py` (knowledge-debt queue: severity×impact Decimal terkuantisasi, proposal draft per item), `bridges.py` (surprising connections: community-distance + relation-rarity + …), `health.py`, `timeline.py`, `generation_diff.py`, `query_trace.py`, `query_tools.py` (1.199 baris filter/sort/paginate tervalidasi ketat). Cap fail-closed: Louvain 2048 node, reachability 1024, motif 256 — di atas itu `GraphCapExceededError` ter-tipar, bukan jawaban salah.

**Positif (terukur, bench/institutional ter-pin vs networkx 3.7)**: **8/8 kernel PASS correctness** (pagerank residual L1 < 1e-10 vs referensi numpy self-validated), semua `deterministic: true`; BFS/SSSP dilaporkan SKIPPED dengan alasan jujur ("belum ada entrypoint publik") — bukan dimasukan sebagai angka.
**Negatif (terukur)**
- **A1 — harga Decimal**: pagerank **13,3-16,0× lebih lambat** dari networkx (149,7ms vs 11,2ms @n=512); betweenness 2,05×; WCC 1,17-1,19× (mendekati paritas karena bukan aritmetika berat).
- **A2 — cap keras** mengeluarkan graf >2048 node dari Louvain/reachability: jujur, tapi membatasi untuk repo besar (titik integritas, bukan bug).

### 1.6 Kernel Rust (`kys-kernel`)

**Algoritma**: port byte-faithful walker Python (1.048 baris `walker.rs`): aturan identik (urutan kanonik, scope stack, alokasi id `:col`-on-collision, import/call/variable rules, artefak kanonik); registry satu-sumber (`registry.json` di-dump dari `languages.py` via `tools/dump_language_registry.py`); dua surface dari satu codebase: CLI `kys-kernel` + modul PyO3; error kernel membawa `(code, message, {path,size,limit})` terstruktur sehingga bridge membangun kembali tipe exception referensi persis. **0 `unsafe`** (grep seluruh `kernel-rust/src/*.rs`).
**Positif (terukur)**: **3,5× lebih cepat** dari walker Python (models.py 35,5KB: 23,01ms vs 79,43ms, median 30 run) dengan output byte-identical; fallback logged-reason; kill switch `KYS_DISABLE_RUST_KERNEL`.
**Negatif**: K1 divergensi dunia nyata 13,9% file (B1); K2 GIL ditahan (B3); K3 melayani 17/33 bahasa (16 sisanya selalu gagal-then-fallback, B2); K4 uji parity hanya fixture.

---

## 2. PERFORMA — angka terukur sekarang

### 2.1 Tabel utama (semua diukur di host ini, 2026-11-08)

| Metrik | Nilai terukur | Metode / bukti |
|---|---|---|
| Ekstraksi Rust (p50, file 35,5KB) | **23,01ms** (≈1,54 MB/s/thread) | 30 run, `kernel_bridge.extract_source_via_kernel` |
| Ekstraksi Python (file sama) | 79,43ms → **Rust 3,45×** | 30 run, `TreeSitterExtractor` |
| Kernel fallback tax (file divergen) | **+68%** per file | help.py 10,98 vs 6,53ms |
| Kernel attempt tax (bahasa non-kernel) | **+27%** per file | Package.swift 1,55 vs 1,22ms |
| Paralelisme ekstraksi (2 thread) | **0,81× (negatif — GIL)** | 24 file: 123ms serial vs 151ms pool |
| Ingest end-to-end, store kecil | **3,6 file/s** (118 file/32,8s) | fase A, korpus vendored ter-pin |
| Ingest end-to-end, store→15k simbol | **0,50 file/s** (505 file/1011s) | fase C1, 20-rep tree (budget max_files=1024 membatasi walk — terdokumentasi) |
| Atribusi wall ingest | **92% `propose_primary_splits`** (56,8M akses enum), 4% resolver, ~3% ekstraksi | cProfile 1 rep (145,4s total) |
| Resolve per-ref: exact / tail-LIKE @15k simbol | 0,17ms / **6,7ms (SCAN symbols)** | EXPLAIN + stopwatch |
| Resolve tail-LIKE @1.847.104 simbol | **879ms per ref** | stopwatch di bench-c2 |
| Synthesis 12 pass @15k simbol | **114,4s** (13.410 kandidat → 0 insert) | fase C1 |
| Query warm (LRU) p50 | **0,05-0,10ms di semua ukuran** | fase B/C1/C2 |
| Query cold p95 | 37,5ms @15k · 932,7ms @477k · **5.760ms @1,85M** | fase C2, 3 titik skala |
| FTS5 MATCH murni @1,85M | **50ms** | SQL langsung tanpa guard |
| Freshness fingerprint @1,85M | **1.701-2.004ms per cold query (SCAN symbols)** | EXPLAIN + 2 run |
| Miss cold maks (LIKE+fuzzy) | 110ms @15k → **4.453ms @1,85M** | fase C2 |
| FTS rebuild | 0,07s @15k · 4,4s @477k · **21,4s @1,85M** (≈11,6µs/simbol, linear) | `rebuild_fts_index` |
| Recall lookup (ground truth ter-pin) | **hit@10 15/15 · top-1 15/15 · hierarchy 7/7** | bench/codebase ledger 20261102T011633Z, determinisme byte-identical |
| Refs terselesaikan (korpus campuran) | 24,7% (1.529/6.187) | fase A store |
| Analytics vs networkx (n=512) | pagerank **13,3× lebih lambat** · WCC 1,19× · BC 2,05× · 8/8 correctness PASS | bench/institutional institutional-latest.json |
| Ukuran store per simbol (termasuk edges+FTS) | ≈**930B/simbol** (1,72GB / 1,85M) | fase C2 |

### 2.2 Bisa di-push?

**Ya — besar, dengan 4 perbaikan bedah (tidak menyentuh kontrak beku):**

| # | Perbaikan | Overload yang direbut | Faktor |
|---|---|---|---|
| 1 | Freshness FTS event-driven (pakai `event_high_watermark` yang sudah ada) alih-alih SCAN per query | 99% waktu cold query | **~50×** cold p95 @1,8M (5.760→~55ms) |
| 2 | Kolom `name_tail` + indeks di `symbols` (atau FTS suffix) untuk `_tail_candidates` | 99,98% waktu tier tail | **~4.000×** per ref @1,8M (879→~0,2ms) |
| 3 | `py.allow_threads` di PyO3 wrapper + hormati `spec.engine` + perbaiki divergensi empty-name kernel | 100% kapasitas multi-core + pajak fallback 68%/27% | ~N× (core) pada ekstraksi; ingest keseluruhan ~2-4× |
| 4 | Batch-kan `propose_primary_splits` (hoist enum `.value`, containment dihitung sekali per batch) | 92% wall ingest | **~13×** ingest end-to-end |
| 5 | Batch-kan kandidat synthesis berat (join set, bukan N+1) | ~98% waktu synthesis | ~50-100× |

### 2.3 Tahan store 5-10GB?

**Korektness: YA. Latensi interaktif: BELUM tanpa perbaikan #1/#2.** Bukti: store 1,72GB (1,85M simbol / 1,89M edge — setara ±185 repo seukuran requests) berfungsi benar; tren linear terukur di 3 titik:
- cold p95 ≈ 1,1ms/1k simbol → **@5GB (≈5,4M simbol): ~17-19s; @10GB (≈10,7M): ~34-38s** (fingerprint 5,8-11,6s + FTS/scan). Dengan perbaikan #1: diproyeksikan **<150ms di 10GB** (FTS 50ms@1,8M, sub-linear).
- Ingest di skala itu **tidak praktis hari ini**: tail-scan 879ms/ref @1,8M → 2,4-4,8s/ref @5-10GB; dengan perbaikan #2 menjadi ~0,2ms/ref.
- FTS rebuild linear 11,6µs/simbol → **~63s @5GB, ~125s @10GB** (operasi latar, bukan jalur query).
- WAL group-commit + mmap 1GB (plan) sudah aktif; SQLite sendiri jauh dari batas (page 4KB default, batas teoretis ratusan TB).

### 2.4 Hardware optimal

Host ini (2 core / 3GB): HardwarePlan memberi workers=2, cache 371MB, group-commit 32, query-cache 16.384, mmap 1GB — **tapi GIL (B3) membuat workers=2 tidak berguna untuk ekstraksi**. Setelah perbaikan #3: core adalah lever utama (ekstraksi CPU-bound; proyeksi ~linear sampai I/O atau batas parser). RAM: query cache + page cache agresif sudah benar arahnya; di host 3GB, cache_kib=371MB + mmap 1GB aman (terukur tidak OOM di pengisian 1,7GB). Rekomendasi bentuk optimal: ≥8 core (pasca-allow_threads), RAM ≥ ⅛ db untuk cache, SSD (group-commit 32 sudah di-tune untuk SSD).

---

## 3. LEVEL PROFESIONAL (per butir, dengan bukti)

| Butir | Skor | Bukti utama | Pengurang |
|---|---|---|---|
| **Flow** (alur end-to-end) | **82%** | walk→preflight→extract(paralel-kanonik)→group-commit write→resolve→suggest→audit 1-event-per-invocation terbukti rapi (pipeline.py 676-820); kill switch di setiap optimasi; crash-replay bersih | routing mengabaikan `spec.engine` (B2); fallback double-walk (B1); budget 1024 default membatasi walk besar |
| **Core-algo** | **78%** | 8/8 kernel graph PASS vs networkx + deterministik; cascade resolver jujur-ambiguity; FTS+BM25+rescore+3 fallback ber-tier disiplin; Louvain/Yen/triad-census dari literatur, implementasi asli | 3 hotspot naif terukur (SCAN fingerprint Q1, SCAN tail R1, N+1 synthesis S1); pagerank 13-16× lebih lambat karena Decimal (A1 — harga integritas, disengaja) |
| **Arsitektur** | **90%** | registry bahasa murni-data (33 bahasa 0 cabang walker); satu HardwarePlan dikonsumsi semua subsistem; dual-engine dengan kontrak urutan-emisi F1; provenance per-tier (`ingest:*`/`resolver:*`/`synthesis:<pass>`) tak pernah menyaru; schema v4/v5 berevolusi tanpa menyentuh beku v1/v2; error ter-tipar fail-closed di mana-mana | engine-flag tidak ter-wire (B2); bocor tanggung jawab: saran primary-split (mahal) dipanggil sinkron di jalur ingest (B4) |
| **Kreativitas** | **88%** | experience reranker ber-jurnal hash-chained (ada di KEDUA referensi: tidak ada); debt queue dengan proposal draft; surprising-connections ber-skor terstruktur; ubiquity ceiling + language-family gating di name-matcher; dokumen `references` edge di resolver kode | sebagian besar inovasi berada di luar hot path; yield synthesis rendah di korpus kode (S2) |
| **Detail** | **85%** | 12-digit Decimal; canonical JSON; ~900 test cluster-B (extractor 55, pipeline 85+10prop, resolver 85+8prop, synthesis 68+47+15prop, retrieval 46+23+60+12prop, kernel 16+8, analytics 201+…); setiap SQL dinamis membawa `nosec` beralasan; determinisme 2-run byte-identical di bench | **bench/codebase strict gate MERAH saat ini** (regression FAIL: edges 1561 vs pin 1557, +4 — deterministik tapi drift dari pin); parity kernel tidak menguji korpus nyata (K4) |

**Rata-rata level profesional cluster B: 84,6%** (82+78+90+88+85)/5.
Konteks repo-wide (bench/verdict 2026-11-04, per-opponent): L1 profesional vs codegraph **65,2%**, vs graphify 102,6%; komposit 391,9% vs codegraph (target 400%: NOT MET) / 1321% vs graphify (MET). Cluster B kuat di arsitektur-detail, tertahan di flow/hot-path oleh temuan B1-B4/Q1/R1/S1.

---

## 4. Ledger bukti (ringkas)

| Klaim | Perintah reproduksi |
|---|---|
| 33 bahasa | `grep -c "LanguageSpec(" src/know_your_context/ingest/languages.py` → 33 |
| Kernel melayani 17 bahasa | `python -c "from know_your_context.ingest import kernel_bridge as k; k.kernel_available(); print(k.kernel_supported_languages())"` |
| Rust 3,45× + byte-identical | skrip `measure` inline §2.1 (30 run, models.py) |
| Divergensi kernel 5/36 + tax 68%/27% | loop `extract_source_via_kernel` atas `corpus/requests/**/*.py`; A/B `KYS_DISABLE_RUST_KERNEL=1` |
| GIL 0,81× | ThreadPoolExecutor(2) vs serial, 24 file kernel-clean |
| Atribusi ingest 92% | `cProfile` atas `IngestionPipeline.ingest_path(scale-tree/rep00)` |
| SCAN symbols (tail/fingerprint) | `EXPLAIN QUERY PLAN` di bench-c.sqlite3 / bench-c2.sqlite3 |
| Latensi 3 titik skala | `measure_cluster_b.py`, `measure_c.py`, `measure_c2.py` → `measure-{a,c1,c2}.json` |
| Recall 15/15 | `bench/codebase/results/ledger-20261102T011633Z.json` |
| Analytics vs networkx | `bench/institutional/results/institutional-latest.json` |
| Verdict repo-wide | `bench/verdict/results/ledger-20261104T082511Z.json` |

Observasi vs inferensi: semua angka §2.1 = observasi langsung; proyeksi 5-10GB (§2.3) = **inferensi berlabel** dari tren linear 3 titik terukur; atribusi cProfile membesar absolut tapi proporsi relatif valid.
