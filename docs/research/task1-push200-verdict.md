# TASK1-PUSH200 — Verdict ulang per dimensi (SUB-166)

**Tanggal:** 2026-11-09 · **HEAD:** `b58bfd2` + perubahan TIDAK ter-commit (SUB-166) · **Instrumen:** `bench/verdict` ledger `20261109T192827Z` (resmi, 70 metrik × 3 repo) · **Build doc:** `docs/recovery/task1-push200-sub166.md` · **Rencana:** `docs/research/task1-push200-plan.md`.

Aturan yang dipegang: angka apa adanya; per-opponent; metrik LLM = N/A; rasio vs 0 = unbounded (bukan angka); kapabilitas tanpa proof test = 0; **tidak ada inflasi volume**.

---

## 1. Jawaban langsung: 200% per dimensi

| Dimensi | Ukuran | vs codegraph | vs graphify | Status |
|---|---|---|---|---|
| **Algoritma** | level instrumen l2 (inventaris struktural: fungsi/kelas/typed-errors/enum/assertion) | **1019,48%** | **3736,31%** | **≥200% TERCAPAI (5–18×)** |
| **Performa** | before/after terukur per mekanisme + level flow l4/l7 (machinery operasional) | l4 **455,34%** · l7 **685,97%** | l4 **2145,85%** · l7 **1144,95%** | **≥200% TERCAPAI**; mekanisme baru: 161–6.900× (P2), 2,99× (P3), −1,99 ms mean/tulis (P1) |
| **Level profesional** | level instrumen l1 (LOC/test/doc/changelog/comment/pin) | **78,35%** (dari 76,04) | **118,36%** (dari 117,61) | **TIDAK TERCAPAI — dan TIDAK BISA dijujurkan** (lihat §5) |

**Komposit instrumen:** 404,56% → **405,88% vs codegraph**; 1361,04% → **1362,92% vs graphify**. Target 400% per-opponent: **MET keduanya**.

## 2. Sub-skor <90% — status akhir per item

| # | Item | Lama | Baru | Bukti (metode + angka) |
|---|---|---|---|---|
| I1 | Store — kreativitas | **85** | **91** | Verifikasi chain **inkremental ber-checkpoint** (sidecar 0600, anchor O(1), fallback full fail-closed, kill switch): re-audit 161k event **6,83 s → 0,0005 s (0-tail, ~6.900×)** / **0,043 s (tail 1.000, 161×)**; 14 proof test. Referensi tidak punya hash chain sama sekali (integrity cg 7,6%/gf 9,8%) — baseline industri full re-walk |
| I2 | Serve flow — core-algo | **85** | **92** | **Adaptive group-commit window**: wait = min(window, window²/EMA) di dalam amplop statis; A/B daemon nyata 150 tulis: mean 36,84 → **34,85 ms (−1,99 ms ≈ window 2 ms penuh)**, p95 −10,8%; 16 proof test; referensi: batch statis (cg `FILE_IO_BATCH_SIZE=10`, `batchSize=5000`; gf `_LABEL_BATCH_SIZE=100`, tanpa write-arbiter) |
| I3 | Serve flow — kreativitas | **88** | **90** | Mekanisme adaptif di hot path dengan pin deterministik (pure fn + EMA + kill switch terdaftar `daemon.adaptive_batch`) |
| I4 | Cluster B — kreativitas | **88** | **88** | TIDAK berubah — P5 substring miss-path sengaja tidak dikerjakan: codegraph sudah punya in-memory substring index (`src/db/name-substring-index.ts` #2620) → paritas bukan 2×; lapor jujur |
| I5 | Ops — core-algo | **86** | **91** | Monolit TERVERIFIKASI fixed wave2 (max CC diagnose 47→10, doctor 38→10, AST walker independen) + fleet probe fan-out 6×0,3 s: serial **1,80 s → 0,60 s (2,99×)**; 5 proof test; switch `watchtower.serial` |
| I6 | Ops — kreativitas | **88** | **89** | Fan-out berbatas dengan determinisme byte-identik (Executor.map urutan nama) + switchable |
| I7 | Hardware — kreativitas | **88** | **88** | Tidak disentuh |
| I8 | Superpower — core-algo | 88 | **93** | D1 writer supervision + D3 incremental publish 4× (wave2, `edbcdb0`) — diverifikasi ada pin test (`test_serve_writer_supervision.py` 4p, `test_projection_incremental.py`) |
| I9 | Security — core-algo | 92 | **94** | Capability audit O(n²)→O(1) (wave2, `test_capability_audit_scaling.py` 185 baris) — diverifikasi |

**Rata-rata sumbu per bagian (rubrik 5-sumbu yang sama):**

| Bagian | Lama | Baru | Delta |
|---|---|---|---|
| Store | 92,6 | **94,0** | +1,4 (kreativitas 85→91, core 96→97) |
| Serve flow | 89,6 | **91,8** | +2,2 (core 85→92, kreativitas 88→90, detail 90→92) |
| Superpower server | 91,6 | **92,6** | +1,0 (core 88→93, re-score wave2) |
| Ops | 90,0 | **90,8** | +0,8 (core 86→91, kreativitas 88→89) |
| Security | 93,0 | **93,4** | +0,4 (core 92→94, re-score wave2) |
| Audit/journal | 92,8 | 92,8 | — (A1 beban memori sudah ditutup wave1; checkpoint milik store) |
| Cluster B (ingest/resolve/retrieval/analytics) | 90,6 | 90,6 | — (kreativitas tetap 88; jujur) |
| Hardware | 91,0 | 91,0 | — |

## 3. Metode pengukuran (semua dapat direproduksi)

```bash
# P1 — A/B adaptive window (daemon nyata, 150 tulis sekuensial)
.venv/bin/python /root/.hermes/cache/scratch/sub166/ab_adaptive.py 150
# P2 — full vs incremental @161k event
.venv/bin/python -c "from know_your_context.store import EventStore; s=EventStore('/path/copy.sqlite3'); \
  print(s.verify_chain()); print(s.verify_chain(incremental=True))"
# P3 — fleet fan-out
.venv/bin/python -m pytest tests/test_ops_watchtower_parallel.py -q
# CC ops (metode cluster-c: AST walker)
.venv/bin/python /root/.hermes/cache/scratch/cx_scan.py src/know_your_context/ops/diagnose.py src/know_your_context/ops/doctor.py
# Instrumen resmi
.venv/bin/python bench/verdict/run.py run
```

## 4. Test & gate (bukti hijau)

| Gate | Hasil |
|---|---|
| Test baru P1 | `tests/test_serve_adaptive_batch.py` 16/16 (RED 14 failed → GREEN) |
| Test baru P2 | `tests/test_chain_checkpoint.py` 14/14 (RED 10 failed → GREEN) |
| Test baru P3 | `tests/test_ops_watchtower_parallel.py` 5/5 (RED 2 failed → GREEN) |
| Storm/kill-9/group-commit | `test_serve_gates.py` 11/11 hijau |
| Store + ops terdampak | 148p (store suites) + 207p + 91p (ops) hijau |
| Serve combo | 200p hijau ×2 (1 flake kontensi terbukti hijau standalone + clean-HEAD worktree) |
| Lint/format/type/PoT | ruff 0, format 0, pyright 0, PoT PASS (162 violation semua justified — shrink, bukan ratchet-up) |
| Full suite | lihat `docs/recovery/task1-push200-sub166.md` (hasil akhir dicatat di board) |

## 5. Yang TIDAK mencapai 200% — dan mengapa (aturan "angka apa adanya")

**l1 profesional 78,35% vs codegraph / 118,36% vs graphify.** Dekomposisi vs codegraph (172.242 LOC, 835 file test, 3.320 fixture):
`source_loc` 36,3% · `test_loc` 42,3% · `test_files` 35,0% · `test_cases` 89,7% · `doc_files` 279,7% · `doc_lines` 76,1% · `changelog_sections` 32,4% (naik dari 14,7% via 6 seksi nyata ber-anchor commit) · `comment_lines` 13,7% · `license` 100% · `pinned_deps` unbounded.

Sembilan dari sepuluh metrik adalah **volume murni** yang diskalakan ukuran codebase: mengejar 200% berarti menulis ~110k baris komentar/LOC dan ~4.700 seksi changelog — persis "inflasi volume" yang dilarang VERDICT-FRAMEWORK §7 dan instruksi operator ("bukan menambah fitur membabi buta"). Yang bisa digerakkan jujur sudah digerakkan (changelog +17,6 poin rasio, l1 +2,3 poin level). l5 vs codegraph 117,2% terkunci alasan sama (`test_fixtures` 3,8%: 125 vs 3.320).

**Klaim 200% yang TIDAK dibuat:** tidak ada klaim bahwa kreativitas cluster B, hardware, atau l1/l5 vs codegraph mencapai 200%.

## 6. Kill criteria

3 iterasi build tanpa perbaikan terukur → stop. **Tidak terpicu**: P1, P2, P3 masing-masing menghasilkan perbaikan terukur pada iterasi pertama (P1 −1,99 ms mean; P2 161–6.900×; P3 2,99×). P5 sengaja tidak dimulai karena bukti REA menunjukkan bar 2× tidak bisa dijujurkan dengan paritas codegraph — keputusan berbasis bukti, bukan kegagalan iterasi.