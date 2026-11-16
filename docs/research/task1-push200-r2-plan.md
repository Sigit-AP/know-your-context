# TASK1-PUSH200 RONDE 2 — Rencana tertulis (SUB-167)

**Tanggal:** 2026-11-16 · **HEAD dasar:** `b58bfd2` + perubahan SUB-166 (tidak ter-commit, terverifikasi 34/34 hijau) · **Metode:** pm-skills (ICE) + EXPERT-THINKING + REA-any atas `/root/src/codegraph` + `/root/src/graphify` · **Aturan:** TDD RED→GREEN, TIDAK commit, before/after terukur per klaim, angka apa adanya (VERDICT-FRAMEWORK §7).
**Warisan:** `docs/research/task1-push200-plan.md` + `task1-push200-verdict.md` + `docs/recovery/task1-push200-sub166.md` (SUB-166).

---

## 0. Status warisan (diverifikasi ulang hari ini, bukan dipercaya)

- Kode P1/P2/P3 SUB-166 ADA di worktree (grep: `_adaptive_window_seconds` di serve/superpower.py, `verify_chain(incremental=` di store.py:1047, `ThreadPoolExecutor` di ops/watchtower.py:237).
- Test P1/P2/P3: **34/34 hijau** (`pytest tests/test_serve_adaptive_batch.py tests/test_chain_checkpoint.py tests/test_ops_watchtower_parallel.py -q` → 34 passed, 7.59s).
- Klaim "hasil SUB-166 tidak ada" di briefing SUB-167 **tidak benar** — dok + kode + test ada; yang benar: SUB-166 tidak sempat commit (memang dilarang) dan tidak mengerjakan P5.

## 1. Inventarisasi SEMUA sub-skor <90% yang MASIH terbuka (post SUB-166)

| # | Item | Skor | Sumber | Kenapa tertahan |
|---|---|---|---|---|
| R1 | Cluster B — kreativitas | **88** | cluster-b §3; task1-push200-verdict I4 | Inovasi di luar hot path; P5 (substring miss-path) sengaja ditunda SUB-166 — codegraph sudah punya in-memory index → paritas bukan 2× |
| R2 | Store — flow engineering | **93** (−7 serial degrade) | task2-audit-wave1 §3 | Degradasi first→last window ~52% @160k append serial — diklaim "inheren B-tree/cache" TANPA serangan mekanisme |
| R3 | Hardware — kreativitas | **88** | cluster-c §2; verdict I7 | Klasifikasi statis (rotational flag, DRM); tidak ada kalibrasi terukur |
| R4 | Ops — kreativitas | **89** | verdict I6 | Sudah 89 pasca fan-out; ruang sisa tipis |
| R5 | l1 profesional vs codegraph | **78,35%** | verdict §5 | Volume murni (172k vs 62k LOC) — TIDAK BISA dijujurkan ke 200% (inflasi dilarang) |

Sub-skor <90 yang SUDAH ditutup SUB-166 (tidak dikerjakan ulang): store kreativitas 85→91, serve core-algo 85→92, serve kreativitas 88→90, ops core-algo 86→91, superpower core-algo 88→93, security core-algo 92→94.

## 2. Analisis pakar per item — kedalaman yang referensi TIDAK punya (bukti file:line)

### A. Durable trigram substring index (menutup R1; mengangkat retrieval performa)

**Bukti celah referensi:**
- codegraph `src/db/name-substring-index.ts` (222 baris): index **in-memory**, dibangun ulang PENUH per change-stamp (`memoizedRead` di queries.ts:477 — stamp berubah → `readNameSubstringIndex` baca ulang SEMUA row nodes, queries.ts:2340-2350). Tidak durable (mati tiap proses), tidak inkremental (O(N) per invalidasi), tidak ada di disk.
- graphify: tidak punya FTS/substring index sama sekali (grep: hanya substring untuk deteksi secrets di detect.py).
- Kita: `_like_channel` (retrieval_v2/lookup.py:357-372) = `LIKE '%t%'` SCAN penuh symbols+path saat FTS miss — Q2 miss cold terukur **896,6 ms @1,85M simbol** (task2-audit-wave1).

**Diferensiasi 2× yang jujur (bukan paritas):** index trigram **durable** (FTS5 `tokenize='trigram'`, tabel virtual di db — hidup lintas proses, nol RAM steady-state) + freshness **event-driven O(1)** via counter `symbols_watch` (schema v6, milik kita — codegraph membayar rebuild O(N) per perubahan; graphify nol).

**Properti exactness (diverifikasi eksperimen SQLite 3.53.1 hari ini):** kandidat trigram ⊇ hasil LIKE-literal **hanya bila panjang literal needle ≥3** (trigram minimum). Term <3 → fallback SCAN (semantik identik). Case-folding trigram adalah superset dari folding ASCII LIKE → filter LIKE tetap diterapkan pada kandidat → hasil byte-identik dengan SCAN. Uji scratch: 10 kasus adversarial (unicode ö/ä, `_` literal, `%` literal, `.`, case) — superset terpenuhi untuk semua needle ≥3, gagal hanya di `_b`/`r2` (<3) sesuai prediksi.

**Desain:** modul `retrieval_v2/trigram.py` — tabel `retrieval_v2_symbols_trigram(name, path)` external-content; `ensure_trigram_index(store)` mengikuti pola `ensure_fts_index` (watch_seq O(1) → fingerprint arbiter → rebuild); `_like_channel` memakai prefilter trigram bila semua term ≥3, filter LIKE penuh pada kandidat, ORDER BY path,line,col,node_id + cap dipertahankan. Kill switch `KNOW_YOUR_CONTEXT_NO_TRIGRAM=1` (registry `retrieval.trigram`). Fail-closed: trigram unavailable/error → SCAN lama.

### B. Store serial degrade (menyerang R2 — flow 93)

**Hipotesis terukur (belum ada di verdict manapun):** (H1) checkpoint WAL autocheckpoint makin mahal saat db melewati page cache; (H2) pertumbuhan B-tree murni (inheren, tak bisa diperbaiki jujur); (H3) WAL tak pernah ter-checkpoint selama run → read amplification.
**Metode:** `bench/store_scaling.py lane-a` pada store lebih kecil (60k event, ~2-4 menit host ini) + instrumentation ukuran file `-wal` per window + `PRAGMA wal_checkpoint` manual pacing A/B.
**Batas kejujuran REA:** codegraph SUDAH punya WAL valve untuk bulk-index (`src/db/wal-valve.ts` #1231: defer autocheckpoint + growth-beyond-baseline trigger + backpressure). Klaim kami BUKAN "valve" (itu paritas); klaim = diagnosis terukur penyebab degrade steady-state lane append + mekanisme pacing bila H1 terbukti. Bila H2 (inheren) → laporkan jujur tanpa perubahan (iterasi dihitung terhadap kill criteria).

### C. Kalibrasi fsync terukur di hardware probe (menyerang R3 — kreativitas 88)

**Bukti celah:** codegraph wal-valve memakai timer + threshold konstan, tanpa pengukuran fsync; probe hardware codegraph: tidak ada (grep: tidak ada fsync/rotational probing di luar wal-valve). graphify: nol. Kita: `probe_disk_kind` klasifikasi via sysfs rotational — **asumsi, bukan ukuran**.
**Mekanisme:** `probe_fsync_latency(dir)` — tulis+fsync N=16 halaman di direktori db nyata, ukur p50/p95 ms, derivasi kelas durability TERUKUR (battery-backed/SSD/HDD-ish) yang memberi tahu tuning (group-commit window). Pure classifier + probe terisolasi; kill switch ikut `hw_tuning_disabled`; fail-closed (error → klasifikasi lama).
**Terukur:** klasifier deterministik (test batas murni) + probe nyata di host ini (p50 fsync SSD vs ambang).

### D. Yang TIDAK dikerjakan (jujur, dulu-duluan)

- **R5 l1 vs codegraph 200%**: tidak mungkin jujur (volume; larangan §7). Test baru dari A/B/C menggerakkan `test_cases` sedikit — dilaporkan apa adanya via re-run instrumen.
- **R4 ops kreativitas 89**: ruang tipis, ROI rendah vs A/B/C — tidak dikerjakan kecuali sisa waktu.

## 3. Prioritas ICE

| Item | Impact | Confidence | Ease | ICE | Urutan |
|---|---|---|---|---|---|
| A trigram durable | 9 (menutup satu-satunya kreativitas <90 yang actionable + performa retrieval 900ms→?) | 9 (properti superset terbukti eksperimen; pola freshness ada) | 6 (modul baru + wiring + migrasi meta) | **486** | 1 |
| B serial degrade | 8 (store flow −7; dimensi performa) | 4 (bisa inheren → kill) | 7 (harness ada) | **224** | 2 |
| C fsync kalibrasi | 5 (hardware kreativitas 88→~91) | 8 | 8 (pure classifier + probe kecil) | **320** | 3 |

**Kill criteria:** 3 iterasi build tanpa perbaikan terukur → stop + laporkan jujur. Iterasi dihitung per item.

## 4. Rencana pengukuran (before/after wajib)

| Klaim | Before | After | Metode |
|---|---|---|---|
| A: LIKE miss @scale | SCAN penuh (Q2: 896,6 ms @1,85M) | target <50 ms | store sintetis ≥500k simbol di scratch; `EXPLAIN QUERY PLAN` SCAN→SEARCH; stopwatch |
| A: exactness | hasil LIKE SCAN | byte-identik | test adversarial + equivalence sweep acak deterministik (seed) pada korpus nyata repo ini |
| A: biaya tulis/storage | — | terukur, dilaporkan apa adanya | waktu rebuild + ukuran db delta |
| B: degrade first→last | ~52% @160k (audit) | ukur ulang @60k + A/B pacing | `bench/store_scaling.py lane-a` + wal-size instrumentation |
| C: klasifikasi | rotational flag saja | fsync p50 terukur + kelas | probe nyata + test pure classifier |

## 5. Re-verdict akhir

Per sumbu berubah: skor lama→baru + bukti + metode. Dimensi: algoritma (re-run `bench/verdict/run.py run` — l2), performa (before/after A/B/C), level profesional (l1 re-run + sumbu kreativitas cluster B/hardware). Termasuk yang TIDAK tercapai dan mengapa.
