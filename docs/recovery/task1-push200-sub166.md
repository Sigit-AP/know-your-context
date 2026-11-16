# TASK1-PUSH200 — Build recovery (SUB-166)

**Tanggal:** 2026-11-09 · **HEAD dasar:** `b58bfd2` · **Rencana:** `docs/research/task1-push200-plan.md` · **TIDAK commit** (aturan operator).
**Metode:** TDD (RED→GREEN, setiap mekanisme dipin test deterministik), EXPERT-THINKING 7-pertanyaan (dijawab di rencana), REA-any atas referensi (`/root/src/codegraph`, `/root/src/graphify`).

## 7 pertanyaan pakar (ringkas, dijawab sebelum kode)

1. **Masalah:** sub-skor <90% di 3 cluster verdict + level instrumen <200% vs referensi.
2. **Kendala:** tanpa commit; kontrak beku tak disentuh; host 2 vCPU/3 GiB (angka absolut = host ini); aturan framework: angka apa adanya, tanpa inflasi volume.
3. **Tujuan terukur:** tiap item yang dikerjakan punya before/after + pin test + kill switch.
4. **Manfaat:** sumbu core-algo serve flow (satu-satunya <90 yang actionable di cluster A), kreativitas store (sumbu terendah repo 85), core-algo ops, l1 profesional.
5. **Pelajaran:** dua "celah" verdict ternyata SUDAH ditutup wave2 (writer supervision D1, incremental publish D3, monolit ops CC 47/38→10/10) — diverifikasi independen sebelum diklaim; tiga mekanisme baru dipilih karena referensi TERBUKTI tidak punya (file:line di rencana).
6. **Prinsip:** amplop statis Tiger Style dipertahankan (hanya perilaku di dalamnya yang adaptif); default fail-closed (verify_chain full tetap default; checkpoint hanya akselerasi); setiap optimasi punya kill switch terdaftar.
7. **Kill criteria:** 3 iterasi build tanpa perbaikan terukur → stop. **Tidak terpicu** — ketiga workstream menghasilkan perbaikan terukur pada iterasi pertama.

## P1 — Adaptive group-commit gather window (serve flow core-algo 85)

**Apa yang referensi tidak punya (bukti):** codegraph memakai konstanta batch statis (`FILE_IO_BATCH_SIZE = 10` src/extraction/index.ts:46; `batchSize: number = 5000` src/resolution/index.ts:2473) dan tidak punya write-arbiter group-commit; graphify hanya punya batch LLM statis (`_LABEL_BATCH_SIZE = 100` graphify/llm.py:3408). Tidak ada yang mengadaptasi geometri batch tulis terhadap laju kedatangan.

**Mekanisme:** `_adaptive_window_seconds(window, ema) = min(window, window²/ema)` — window tunggu proporsional terhadap EMA interval antar-submit (alpha 0,25). Beban jarang (ema ≫ window) → tunggu kolaps ~0; burst (ema ≪ window) → window penuh. Cap batch TETAP statis (anggaran Tiger Style: fairness + amortisasi fsync); hanya WAIT yang adaptif. Kill switch `KNOW_YOUR_CONTEXT_NO_ADAPTIVE_BATCH=1`, terdaftar sebagai `daemon.adaptive_batch` di `ops/switches.py`.

**TDD:** 16 test baru (`tests/test_serve_adaptive_batch.py`): pure window (5), EMA (4), gather sparse/dense/unknown/kill-switch/cap-static (5), pin registry (1), +1 invariant. RED 14 failed → GREEN 16/16.

**Before/after terukur** (daemon nyata, 150 tulis sekuensial + 10 warmup, host ini):

| Arm | p50 | p95 | mean |
|---|---|---|---|
| static (kill switch on) | 35,16 ms | 50,77 ms | 36,84 ms |
| adaptive (default) | 34,08 ms | 45,28 ms | 34,85 ms |
| delta | −1,08 ms | **−5,49 ms (−10,8%)** | **−1,99 ms (≈ window 2 ms penuh)** |

**Non-regresi:** `test_serve_gates.py` 11/11 hijau (storm 50 agen, fairness ≥80%, group-commit A/B ≥1×, kill -9 0 acknowledged loss). Combo 7 file serve 200p hijau ×2 run.
**Catatan flake jujur:** `test_writer_restart_budget_exhaustion_is_terminal` gagal 2× di combo besar (timeout drill 30 s di host 2 vCPU), hijau standalone ×4 DAN hijau di clean-HEAD worktree combo — flake kontensi, bukan regresi mekanisme (mekanisme P1 dipin test clock/recording deterministik).

## P2 — Verifikasi chain inkremental ber-checkpoint (store kreativitas 85)

**Baseline masalah:** `verify_chain` menurunkan ulang SELURUH rantai tiap panggilan — terukur 6,70/6,33 s @160k event (SUB-155/164: 8,3–9,0 s @160k; streaming S2 sudah menurunkan RSS 951→424 MiB di wave1, wall tetap O(N)).
**Apa yang referensi tidak punya:** codegraph & graphify tidak punya hash chain sama sekali (scorecard integrity: cg 7,6%, gf 9,8%; kita 100%). Baseline industri = full re-walk (git fsck). Checkpoint = lompatan 2×: audit rutin O(tail).

**Desain (fail-closed):**
- Sidecar `<db>.chain-checkpoint.json` (0600, tmp+rename atomic) — ditulis HANYA pasca verify SUKSES; berisi `{seq, hash, checked, verified_at}`; tidak ditulis ulang bila sudah menutup head (anti-churn untuk poller).
- `verify_chain(incremental=True)`: re-derive baris checkpoint (O(1) tamper anchor) → hash mismatch/foreign sidecar/tanpa sidecar → **fallback full**; lalu verifikasi tail + anchor + journal links (di-scope `> seq`).
- **Default `verify_chain()` TIDAK BERUBAH (full).** Scope disclosure di-pin test: tamper pra-checkpoint tidak terlihat incremental (checkpoint meng-atestasi) dan tertangkap full — alasan audit forensik tetap full.
- `write_checkpoint=False` untuk probe murni (watchtower, metrics — invariant "probe tak membuat file" dijaga). Kill switch `KNOW_YOUR_CONTEXT_NO_CHAIN_CHECKPOINT=1` (registry `store.chain_checkpoint`).

**TDD:** 14 test (`tests/test_chain_checkpoint.py`): sidecar ditulis/tidak-ditulis-saat-gagal/0600, tail-only exact count, 0-tail, tanpa sidecar, corrupt, foreign-history, kill switch, post-checkpoint tamper, scope-limit disclosed, registry pin, no-rewrite, write_checkpoint=False.

**Before/after terukur** (store nyata 161k event, salinan `sub155/b1_a.sqlite3`):

| Operasi | Wall | checked |
|---|---|---|
| full verify | 6,83–6,93 s | 161.000 |
| incremental, 0 tail | **0,0005–0,001 s** | 0 |
| incremental, tail 1000 event | **0,043 s** | 1.000 |

→ akselerasi **~6900× (0-tail)** dan **161× (tail 1000)** pada re-audit rutin; full kontrol identik.
**Gates:** store suites 148p; ops (watchtower/metrics/diagnose/doctor/lifecycle/migrate) 207p + 91p hijau; ruff/format 0; pyright 0; PoT PASS (verify_chain di-shrink via `_chain_audit_reads`, bukan ratchet-up).

## P3 — Re-score ops core-algo + fleet probe paralel (ops core-algo 86)

**Verifikasi independen wave2:** `ops/diagnose.py` max CC 47→**10**, `ops/doctor.py` 38→**10** (AST walker, metode sama dengan cluster-c verdict) — deduksi monolit HILANG.
**Sisa deduksi (watchtower sekuensial) ditutup:** `aggregate_status` fan-out `ThreadPoolExecutor(min(4, n))`; `Executor.map` mempertahankan urutan nama → laporan byte-identik dengan serial; kill switch `KNOW_YOUR_CONTEXT_WATCHTOWER_SERIAL=1` (registry `watchtower.serial`).
**TDD:** 5 test (`tests/test_ops_watchtower_parallel.py`): urutan+kelengkapan, overlap (6×0,3 s < 1,4 s), switch serial (4×0,15 s ≥ 0,6 s), pin registry, dup ditolak sebelum probe.
**Terukur:** fleet 6 probe × 0,3 s → serial **1,80 s** vs paralel **0,60 s** = **2,99×**.

## P4 — CHANGELOG konten nyata (l1 profesional)

5 → **11 seksi** `##`, semua ber-anchor commit nyata (W14–W17, W18–W20, disk-mode v5, task2 wave1/wave2/final) — konten dari git log + recovery docs, bukan padding. Ekspektasi jujur: `l1.changelog_sections` vs codegraph 14,7% → ~32% (bukan 200%: codegraph 34 seksi; volume murni).

## Yang TIDAK dikerjakan (jujur)

- **P5 substring miss-path (cluster B kreativitas 88)**: codegraph SUDAH punya in-memory substring index (`src/db/name-substring-index.ts`, #2620) — paritas saja tidak memenuhi bar 2×; diferensiasi jujur butuh desain durable-watermark yang tidak selesai di sesi ini. **Kreativitas cluster B tetap 88.**
- **Hardware kreativitas 88**: tidak disentuh.
- **l1/l5 vs codegraph 200%**: TIDAK MUNGKIN jujur — codegraph 172k LOC / 835 file test / 3320 fixture vs kita 62k/289/125; mengejar = inflasi volume yang dilarang VERDICT-FRAMEWORK §7. Dilaporkan apa adanya per-metrik.

## File

| File | Perubahan |
|---|---|
| `src/know_your_context/serve/superpower.py` | `_adaptive_window_seconds`, `_note_arrival`, window adaptif di `_gather_batch`, kill switch env |
| `src/know_your_context/store.py` | `verify_chain(incremental=, write_checkpoint=)`, sidecar checkpoint (`_valid_chain_checkpoint`, `_write_chain_checkpoint`, `_chain_audit_reads`) |
| `src/know_your_context/ops/switches.py` | 3 switch baru (`daemon.adaptive_batch`, `store.chain_checkpoint`, `watchtower.serial`) + `_mirrored_legacy_states` |
| `src/know_your_context/ops/watchtower.py` | fleet probe fan-out + switch serial; probe pakai `write_checkpoint=False` |
| `src/know_your_context/ops/metrics.py` | `verify_chain(write_checkpoint=False)` (probe murni) |
| `docs/operations.md` | dokumentasi checkpoint + fan-out |
| `CHANGELOG.md` | 6 seksi nyata baru (W14–W20, disk-mode, task2) |
| `tests/test_serve_adaptive_batch.py` | BARU, 16 test |
| `tests/test_chain_checkpoint.py` | BARU, 14 test |
| `tests/test_ops_watchtower_parallel.py` | BARU, 5 test |
| `docs/research/task1-push200-plan.md` | BARU, rencana + inventarisasi |