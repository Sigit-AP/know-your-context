# TASK2 — Audit Independen Wave 1 (SUB-164)

**Auditor:** SUB-164 · **Tanggal:** 2026-11-08 · **HEAD audit:** `e46fc89` (berisi wave 1 = commit `634d568` + lint fix)
**Metode:** setiap klaim angka SUB-159/160/161/162 dijalankan ulang dengan perintah/skrip dari recovery docs mereka (atau rekonstruksi setara), pada host yang sama (2 vCPU / 3 GiB), store ter-pin yang sama (bench-c 16 MB, bench-c2 1,72 GB / 1.847.104 simbol). Before-code = worktree `5952097` di `/root/.hermes/cache/scratch/sub164-audit/head-5952097`. Verdict profesional cluster A/B dihitung ulang memakai rubrik 5 sumbu yang sama dengan verdict aslinya.
**Batasan:** serve/superpower.py, security/, ops/doctor.py, webview handler TIDAK disentuh (in-flight SUB-163, WS-E/F). Tidak ada commit. Artefak audit: `/root/.hermes/cache/scratch/sub164-audit/`.

---

## 1. Tabel klaim vs reproduksi

### SUB-159 (WS-A store: S1, S2, S3)

| Klaim | Hasil reproduksi | Status |
|---|---|---|
| S1: EXPLAIN insert path `SCAN event_log` → `SEARCH system_state` | `bench/store_scaling.py explain` pada store 375 MB: `SEARCH system_state USING INDEX sqlite_autoindex_system_state_1 (key=?)` | **TERBUKTI** |
| S1: throughput serial @165k **+30–47%** | Lane A fresh 0→160k, dua lengan berurutan: before 346,6 → after 389,4 ops/s (last window) = **+12,3%**; first window +17,3%; avg +0,5%. Biaya COUNT(*) diukur langsung: **1,5 ms → 0,012 ms @160k** (mekanisme terbukti); proof test trace-callback nol COUNT hijau | **PARSIAL — mekanisme ya, magnitudo tidak ter-reproduksi** (pengukuran SUB-159 pada store berisi graph penuh, 2 ronde; degradasi first→last ~52% bertahan di KEDUA lengan → didominasi pertumbuhan B-tree/cache, bukan S1) |
| S2: verify_chain streaming, RSS 422–433 MiB @160k | Store 170k event (lebih berat): **RSS puncak 424,7 MiB**, wall 8,3–9,0 s (lebih baik dari klaim 13,6 s), `ok=True` | **TERBUKTI** |
| S2: materialisasi Python 660 → 15,6 MiB (tracemalloc) | Dipin test `test_verify_chain_streams_the_event_log` (budget 64 MiB) — hijau | **TERBUKTI (via proof test)** |
| S3: EXPLAIN journal conflict SCAN → SEARCH idx | `SEARCH invocation_journal USING INDEX idx_invocation_journal_effective_key` | **TERBUKTI** |
| 3 proof test `test_store_scaling_guards.py` hijau | 3/3 hijau (bagian dari 104p scope run) | **TERBUKTI** |

### SUB-160 (WS-B retrieval: Q1, R1, Q2, R2, migrasi v6)

| Klaim | Hasil reproduksi | Status |
|---|---|---|
| Ekuivalensi korpus nyata: 2.517 tail + 1.010 module_file, 0 mismatch | `equivalence_check.py`: **2517 tails, 0 mismatch; 1010 module, 0 mismatch** | **TERBUKTI** |
| EXPLAIN 4 query baru = SEARCH (bukan SCAN) | tail → `SEARCH idx_symbols_name_tail`; module → `SEARCH idx_files_path_rev`; journal → SEARCH; watch_seq → SEARCH PK. Schema v6, 1.847.104 simbol | **TERBUKTI** |
| Cold hit p50 @1,85M: 3.723 → **71,4 ms (52×)** | Repro quiet: **66,65 ms (55,9×)** | **TERBUKTI** |
| Tail miss @1,85M: 1.092 → **0,7 ms (1560×)** | Repro: **0,68 ms (1606×)** | **TERBUKTI** |
| Tail hit patologis (31.744 kand.): 1.262 → 332 ms | Repro: **292,7 ms** | **TERBUKTI** |
| module_file: 47,6 → 1,6–2,8 ms warm | Repro 15× warm: **1,28–1,51 ms** (single-shot cold 170 ms vs aslinya 61,7 ms — orde sama, varians host) | **TERBUKTI** |
| Q2 miss cold 2.928 → 955 ms, **jujur di bawah target ≤500 ms** | Repro: **896,6 ms** — klaim jujur (target tidak tercapai) ter-reproduksi apa adanya | **TERBUKTI (termasuk kejujuran miss target)** |
| Migrasi v5→v6 @1,72 GB = 80 s sekali jalan | Repro pada salinan bench-c2: **37,3 s** (host lebih sepi), schema (6,) | **TERBUKTI** |
| Trigger overhead ingest **+1,26%** (< kill 10%) | Repro best-of-3 interleaved: 4,861 s vs 5,158 s = **−5,76%** (tak terukur bedanya) | **TERBUKTI (arah klaim: overhead negligible)** |
| bench/codebase: recall 15/15 hit@10, 15/15 top-1, determinisme byte-identik | **Run ulang penuh @e46fc89**: hit@10 15/15, top-1 15/15, hierarchy 7/7, determinisme OK 4/4 target, gate correctness+regression **PASS exit 0** (ledger-20261108T192508Z) | **TERBUKTI** |

### SUB-161 (WS-C kernel Rust: B1, B2, B3, K4)

| Klaim | Hasil reproduksi | Status |
|---|---|---|
| B1: divergensi 5/36 → **0/36 byte-identik** | Loop independen atas `bench/codebase/data/requests.tar.gz` (36 .py): raw equality **DAN** canonical JSON = **36/36 identik, 0 divergen** | **TERBUKTI** |
| B2: 0 attempt kernel untuk 16 bahasa engine=python | Dipin test routing (`-k engine_python`): bagian dari 53p hijau | **TERBUKTI (via pin test)**; angka ms +24,6%→+9% tidak diukur ulang |
| B3: GIL dilepas (probe overlap 2,4% → 162–194%) | `test_kernel_extraction_releases_gil` hijau | **TERBUKTI (via proof test)** |
| K4: parity korpus ter-pin, 3 file kernel 103 → **158 test** | Collect: **158 test**; `test_kernel_rust.py` 144p hijau | **TERBUKTI** |
| Determinisme 3 seed | PYTHONHASHSEED 0/1/42 × 144p: hijau identik (klaim 54p subset — repro lebih kuat) | **TERBUKTI** |
| Kernel on/off edges identik (18/2832/321/1561) | Ledger 151833Z (on) vs 151901Z (off): edges identik 4/4 target | **TERBUKTI (dari ledger tersimpan)** |
| Bench/codebase gate hijau, drift +4 = pin usang (G1) | Gate PASS direproduksi di run baru (di atas) | **TERBUKTI** |
| Pajak fallback berbalik: kernel 31% lebih cepat di file yang dulu divergen | Tidak diukur ulang (mikro-benchmark); mekanisme (divergensi 0) terbukti | **TIDAK DIVERIFIKASI (minor)** |

### SUB-162 (WS-D synthesis + primary-splits: B4, S1B)

| Klaim | Hasil reproduksi | Status |
|---|---|---|
| B4: `_primary_suggestions` 88,5–90,5% wall → **1,92%** | cProfile replika: before **90,1%** (74,36s/82,49s) → after **1,7%** (0,12s/6,95s) | **TERBUKTI** |
| B4: wall ingest 1× korpus 111,97 → **12,18 s (9,2×)**, target ≤15 s | Metode mereka (cProfile): 82,49 → 6,95 s = **11,9×**. **Tanpa instrumen (jujur): 34,19 → 5,63 s = 6,1×** | **TERBUKTI dengan catatan metodologi**: baseline 111,97 s adalah angka cProfile-inflated (101,4 s dari 111,97 s adalah waktu terprofil pada 56,8M akses enum); rasio wall nyata = 6,1×. Target ≤15 s terlampaui di kedua metode |
| Proposal 8=8 byte-identik (kill criterion) | **Diverifikasi independen**: ingest kode before (worktree 5952097) vs after (e46fc89) → 8 suggestion **byte-identik** | **TERBUKTI** |
| S1B: name_match 199,94 → **3,663 s (54,6×)** | After di snapshot 14.896 simbol: **2,689 s** (lebih baik). Before tidak diulang (snapshot kini v6; before-code menolak downgrade — bukti fail-closed bekerja) | **TERBUKTI (sisi after)** |
| S1B: 12 pass 3,71 → **1,581 s** | Repro: **1,355 s** | **TERBUKTI** |
| `resolve_and_synthesize` 144,17 → ~5,3 s (27×) | Repro: 2,689 + 1,355 = **4,04 s** | **TERBUKTI** |
| Diff harness 6/6 skenario ALL IDENTICAL | `diff-report.json` tersimpan ALL IDENTICAL; diganti verifikasi independen byte-identik (di atas) karena harness HEAD-vs-HEAD kini vakum | **TERBUKTI** |
| PoT scope bersih, ruff/format 0 | ruff check + format --check pada scope wave 1: bersih; kontrak v3/v5 99p hijau | **TERBUKTI** |

---

## 2. Klaim yang tidak terbukti / catatan auditor

1. **S1 magnitudo +30–47% TIDAK ter-reproduksi** — terukur +12,3% (last window, 1 ronde). Mekanisme (COUNT(*) O(N) → anchor O(1)) terbukti 3 cara (EXPLAIN, trace test, ukur langsung 1,5 ms→0,012 ms), tetapi degradasi serial @160k **bertahan ~52% di kedua lengan** → penyebab dominan degradasi lane A bukan S1 melainkan pertumbuhan B-tree/tekanan cache. Klaim throughput sebaiknya dibaca "eliminasi O(N) per insert" (terbukti), bukan janji throughput +30–47%.
2. **Angka 9,2× ingest memakai baseline cProfile-inflated** (111,97 s terprofil vs wall nyata 34,19 s di host sepi). Rasio jujur tanpa instrumen = **6,1×**; rasio terprofil = 11,9×. Kesimpulan (target ≤15 s, dominasi _primary_suggestions hilang) tidak berubah, tetapi angka headline 9,2× adalah artefak metode ukur.
3. **Mikro-benchmark WS-C** (+19,8%→−31,5% pajak fallback; +24,6%→+9% routing) tidak diukur ulang — mekanismenya ter-pin test (0/36 divergen, 0 attempt kernel), angka ms persisnya tidak diverifikasi.
4. **Before-run name_match (199,94 s)** tidak direproduksi (snapshot telah bermigrasi v6; before-code fail-closed menolak — sendirinya bukti positif migrasi). Sisi after ter-reproduksi lebih baik dari klaim.
5. Tidak ada klaim yang terbukti **salah arah** (angka after selalu ≈ atau lebih baik dari klaim di host yang lebih sepi); yang lemah hanya magnitudo S1 dan basis instrumen 9,2×.

## 3. Verdict profesional ulang (rubrik 5 sumbu asli, HEAD e46fc89)

### Cluster A — STORE: 88,6 → **92,6**

| Sumbu | Lama | Baru | Dasar |
|---|---|---|---|
| Flow engineering | 88 | 93 | S1/S2 tertutup dengan proof test + streaming terukur; −7: degradasi serial first→last @160k (~52%) bertahan (B-tree/cache, inheren) |
| Core algoritma | 90 | 96 | COUNT(*) O(N)/insert → anchor O(1), terbukti EXPLAIN + trace + ukur langsung; verify_chain streaming |
| Arsitektur | 92 | 93 | Migrasi v6 additive + rollback + trigger watch fail-closed by construction |
| Kreativitas | 85 | 85 | Tidak berubah |
| Attention to detail | 90 | 96 | fetchall tanpa paginasi hilang; batch 4096 dipin tracemalloc; kill-criteria RSS didekomposisi jujur |

### Cluster A — SERVE FLOW: 89,6 → **89,6 (TIDAK NAIK — koreksi ekspektasi)**

Wave 1 tidak menyentuh serve flow. **F1 (FrameReader off-by-one) terkonfirmasi MASIH ADA** di `rpc.py:144-152` @e46fc89. Temuan: F1 tidak pernah dialokasikan ke workstream mana pun di TASK2-PLAN (celah plan); D1 (writer tak disupervisi — terkonfirmasi masih 4 SupervisedThread tanpa writer) dan D3 milik **WS-E, in-flight SUB-163, belum ter-commit** → di luar HEAD audit. Superpower (91,6) juga tak berubah di HEAD.

### Cluster B: 84,6 → **90,6**

| Sumbu | Lama | Baru | Dasar |
|---|---|---|---|
| Flow | 82 | 90 | Routing membaca spec.engine (0 attempt dipin); tidak ada fallback double-walk; ingest korpus 34,2→5,6 s di host sama |
| Core algoritma | 78 | 91 | Tiga hotspot naif tereliminasi terukur: fingerprint O(1) watch_seq (52× repro), tail index (1606× repro), synthesis/name_match dibatch (54× repro); sisanya: pagerank Decimal (by design) |
| Arsitektur | 90 | 92 | engine-flag kini ter-wire; containment index dipelihara saat insert (append-only, tak bisa drift by construction) |
| Kreativitas | 88 | 88 | Tidak berubah |
| Detail | 85 | 92 | bench/codebase strict gate **HIJAU direproduksi** (regression PASS, determinisme byte-identik 4/4, recall 15/15+15/15+7/7); parity kernel kini di korpus nyata 36/36 + 158 test; migrasi v6 bawa test rollback |

**Catatan jujur:** target plan "≥95" tidak tercapai di ketiga bagian pada HEAD ini (92,6 / 89,6 / 90,6). Store tertahan degradasi serial inheren + kreativitas; serve flow tertahan F1/D1/D3 yang memang belum dikerjakan; cluster B tertahan kreativitas + sisa by-design. Kenaikan nyata dan berbukti: +4,0 (store) dan +6,0 (cluster B).

## 4. Perintah reproduksi utama (venv repo)

```bash
# WS-A
.venv/bin/python bench/store_scaling.py explain|verify|lane-a <store>     # S1/S2/S3
.venv/bin/pytest tests/test_store_scaling_guards.py -q                    # proof tests
# WS-B
cd docs/recovery/task2-ws-b && ../../../.venv/bin/python equivalence_check.py
cd docs/recovery/task2-ws-b && ../../../.venv/bin/python measure_ws_b_quiet.py
cd docs/recovery/task2-ws-b && ../../../.venv/bin/python measure_trigger_overhead.py
.venv/bin/python bench/codebase/run.py run                                # recall + determinisme + gate
# WS-C
.venv/bin/pytest tests/test_kernel_rust.py -q                             # 144p (36/36 korpus di dalamnya)
# WS-D
/root/know-your-context/.venv/bin/python /root/.hermes/cache/scratch/sub164-audit/repro_wsd.py all
```

Audit selesai tanpa mengubah kode; satu-satunya tulisan di repo = dokumen ini + board.
