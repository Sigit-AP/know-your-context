# TASK1-PUSH200 — Rencana tertulis (SUB-166)

**Tanggal:** 2026-11-09 · **HEAD:** `b58bfd2` · **Metode:** pm-skills (ICE) + EXPERT-THINKING + REA-any atas referensi (`/root/src/codegraph`, `/root/src/graphify`) · **Aturan:** TDD (RED→GREEN), TIDAK commit, setiap klaim before/after terukur.
**Definisi "200%":** per dimensi (algoritma/performa/level profesional), kedalaman terukur ≥2× standar referensi — dibuktikan lewat (a) rasio instrumen `bench/verdict` per-opponent, (b) bukti file:line bahwa referensi TIDAK punya mekanisme itu, atau (c) before/after pada metrik host yang sama. Angka apa adanya (VERDICT-FRAMEWORK §7).

---

## 1. Inventarisasi SEMUA sub-skor <90% (diverifikasi ulang @b58bfd2)

| # | Item | Skor | Sumber | Kenapa tertahan (dari verdict docs, diverifikasi) |
|---|---|---|---|---|
| I1 | Store — kreativitas | **85** | cluster-a §1.4 (tidak berubah wave1/2) | Inovasi ada (fingerprint drift-strip, capacity estimator, watermark heal) tapi sedikit; `verify_chain` masih re-hash PENUH tiap panggilan (wave1 hanya membuatnya streaming: RSS 951→424 MiB, wall tetap ~8,3 s @160k) — tidak ada verifikasi inkremental |
| I2 | Serve flow — core-algo | **85** | cluster-a §3.4 | −15 eksplisit: "tidak ada adaptive batching di flow (batch cap statis dari plan)" — cap 32 / window 2 ms statis. F1 off-by-one SUDAH fixed @b58bfd2 |
| I3 | Serve flow — kreativitas | **88** | cluster-a §3.4 | early-ppid, heartbeat-lease, skip-excluded — baik tapi tak ada novelty hot-path baru sejak verdict |
| I4 | Cluster B — kreativitas | **88** | cluster-b §3 (tidak berubah wave1) | "inovasi di luar hot path; yield synthesis rendah di korpus kode" |
| I5 | Ops — core-algo | **86** | cluster-c §3 | −untuk C=47/38 monolit + watchtower sekuensial. **Wave2 (edbcdb0) sudah refactor diagnose.py/doctor.py — diverifikasi ulang hari ini: max CC 10/10** (dulu 47/38). Sisa: watchtower sekuensial |
| I6 | Ops — kreativitas | **88** | cluster-c §3 | mirror-switch pinning, float-free metrics — "baik", tak ada mekanisme baru |
| I7 | Hardware — kreativitas | **88** | cluster-c §2 | klasifikasi display-vs-compute DRM; derivasi query_cache — "baik", statis |
| I8 | Superpower — core-algo | 88→~93 | cluster-a §2.4 → wave2 | D1 (writer tak disupervisi) + D3 (publish O(K)) difixed wave2 (writer supervision + incremental publish 4×) — **belum di-rescore resmi**; re-score via bukti test `test_serve_writer_supervision.py` + `test_projection_incremental.py` |

**Level instrumen <200% (ledger 20261108T214406Z, per-opponent):**

| Level | vs codegraph | vs graphify | Penahan utama |
|---|---|---|---|
| l1 profesional | **76,0%** | **117,6%** | changelog_sections 14,7%/2,1%; comment_lines 13,6%/22,4%; LOC 36-42% (cg=172k LOC vs kita 62k — volume murni) |
| l5 kompleksitas codebase | **116,9%** | **116,9%** | test_fixtures 3,8% vs cg (125 vs 3320); total_functions 21,7% (2522 vs 11607); branch_statements 31,2% — volume murni |
| l3 arsitektur | **179,6%** | 871,3% | layers 85,7%, db_tables 155,6%, migrations 115,4%, env_vars 114,4% |

**Temuan jujur (dilaporkan duluan per protokol §7):** l1 dan l5 vs codegraph TIDAK BISA mencapai 200% secara jujur — codegraph adalah codebase 172k LOC / 835 file test / 3320 fixtures; mengejar itu = inflasi volume yang dilarang framework. Yang bisa digerakkan jujur: (a) kedalaman per-metrik yang referensi tidak miliki (unbounded/paritas→memimpin), (b) changelog konten nyata, (c) skor sumbu profesional cluster via mekanisme baru terukur.

## 2. Apa yang membuat tiap item 200% (kedalaman yang referensi TIDAK punya — bukti file:line)

**P1 — Adaptive group-commit batching (menutup I2, mengangkat I3):**
- codegraph: batching = konstanta statis — `FILE_IO_BATCH_SIZE = 10` (src/extraction/index.ts:46), `batchSize: number = 5000` (src/resolution/index.ts:2473), `QueryBuilder.BATCH_SIZES` list tetap (src/db/queries.ts:413). Tidak ada write-arbiter group-commit sama sekali; "adaptive" di codegraph hanya engagement pool resolver (resolver-pool.ts:74-81), BUKAN geometri batch tulis.
- graphify: tidak ada write path teraudit; batching hanya LLM call `_LABEL_BATCH_SIZE = 100` (graphify/llm.py:3408) — statis.
- **200% = EMA arrival-rate-adaptive window di dalam amplop statis** (Tiger Style: batas min/max tetap statis dari plan; perilaku di dalamnya adaptif). Wait proporsional terhadap expected next arrival: beban jarang → window → ~0 (hemat hingga 2 ms/tulis); beban padat → window penuh + cap penuh (throughput maksimal). Hysteresis clamp anti-flapping. Kill switch `KNOW_YOUR_CONTEXT_NO_ADAPTIVE_BATCH=1` mengikuti konvensi `KNOW_YOUR_CONTEXT_NO_GROUP_COMMIT`. Terukur: latensi tulis tunggal idle (before ~window penuh per op) + non-regresi storm (gate A/B group commit).

**P2 — Verifikasi chain inkremental ber-checkpoint (menutup I1, mengangkat audit performa):**
- Referensi: codegraph/graphify tidak punya hash chain sama sekali (scorecard integrity: cg 7,6%, gf 9,8%; kita 100%) — baseline-nya nol. Standar industri (git fsck, verify penuh) = O(N) tiap audit; pola checkpoint (SPV/ZFS scrub) adalah lompatan 2×: audit rutin O(tail), full verify tetap ada.
- Desain: **sidecar checkpoint file** (bukan migrasi schema — mengikuti pola sidecar ledger experience-reranker), isi `(event_seq, chain_hash, prev_checkpoint_hash)` hash-chained; verify inkremental: validasi sidecar + re-derive SATU row di checkpoint (O(1) tamper-evidence anchor) + hash tail saja. Tamper sidecar → mismatch → fallback full verify + typed note. Kill switch; `verify_chain(full=True)` tetap default untuk audit forensik.
- Terukur: verify ke-2 @160k event: before 8,3–9,0 s → target <1 s (>8×); rows dibaca dipin via trace callback (teknik proof WS-A).

**P3 — Ops core-algo re-score + watchtower paralel (menutup I5):**
- Refactor wave2 terverifikasi (max CC 47/38 → 10/10). Sisa deduksi: probe fleet sekuensial → `ThreadPoolExecutor` berbatas (probe "never raises, never creates" tetap dipin). Terukur: wall probe N store sekuensial vs paralel.

**P4 — CHANGELOG konten nyata (l1: 76,0% vs cg):**
- cg=34 sections (CHANGELOG.md 627 KB nyata), gf=234; kita=5. Git log kita punya gelombang nyata (W1–W23, task1, task2 wave1/2) → seksi per-milestone ber-tanggal dari `git log` = konten jujur, bukan padding. Ekspektasi jujur: l1 vs cg naik moderat (76→~82), BUKAN 200 (dilarang inflasi); dilaporkan apa adanya.

**P5 (stretch, hanya jika P1–P4 hijau) — Cluster B kreativitas:** miss-path substring index. Fakta REA: codegraph SUDAH punya in-memory substring index (src/db/name-substring-index.ts, #2620) → paritas saja tidak cukup untuk klaim 2×; graphify tidak punya FTS/substring sama sekali. Diferensiasi 2× yang jujur = invalidasi via durable `symbols_watch` watermark (milik kita, wave1) vs rebuild-per-process (milik mereka). Diputuskan nanti berdasarkan sisa waktu.

## 3. Prioritas ICE (ROI)

| Item | Impact | Confidence | Ease | ICE | Urutan |
|---|---|---|---|---|---|
| P1 adaptive batch | 9 (menutup satu-satunya core-algo <90 di cluster A yang actionable; −15 eksplisit) | 8 (mekanisme dipahami penuh; gate storm ada) | 8 (1 fungsi murni + wiring + kill switch) | **576** | 1 |
| P2 incremental verify | 9 (sumbu terendah repo: store kreativitas 85; audit besar 8,3 s→<1 s) | 8 (sidecar pola existing; trace-callback proof ada) | 6 (desain tamper-evidence perlu hati-hati) | **432** | 2 |
| P3 ops rescore+paralel | 5 (ops core-algo 86→~91; sebagian besar sudah dicapai wave2) | 9 | 9 | **405** | 3 |
| P4 changelog nyata | 4 (l1 +~6 poin jujur) | 10 | 9 | 360 | 4 |
| P5 substring index | 6 | 5 (paritas cg dulu, beda tipis) | 4 | 120 | stretch |

**Kill criteria:** 3 iterasi build tanpa perbaikan terukur → stop + laporkan.

## 4. Rencana pengukuran (before/after wajib per klaim)

| Klaim | Before | After | Metode |
|---|---|---|---|
| P1 latensi tulis idle | window statis penuh per op (2 ms floor) | ~0 | unit test clock-injected + A/B arbiter nyata (pola `test_serve_gates.py`) |
| P1 non-regresi throughput | group-commit A/B 8,13× (ledger gate) | ≥ sama | re-run gate storm + A/B |
| P2 verify ke-2 @160k | 8,3–9,0 s / 424,7 MiB RSS (audit SUB-164) | target <1 s, rows-read dipin trace | store 160k replika `bench/store_scaling.py`-style |
| P2 ekuivalensi verdict | ChainReport full | byte-identik incremental | test pin |
| P3 probe fleet | sekuensial wall | paralel wall | N store dummy di tmp |
| P4 l1 | ledger 214406Z: 76,0/117,6 | re-run `bench/verdict/run.py run` | instrumen resmi |

## 5. Re-verdict akhir (yang akan dilaporkan)

Per sumbu yang berubah: skor lama → baru + bukti. Per dimensi: algoritma (l2/l5 + core-algo axes), performa (before/after terukur P1/P2), level profesional (l1 + kreativitas/detail axes). Termasuk bagian yang TIDAK mencapai 200% dan alasannya (volume metrics vs codegraph) — sesuai aturan "angka apa adanya".
