# TASK2 — SUB-159: Fase 0 (diagnosis drift +4 edge) + WS-A store (S1, S2; S3 via bundle v6)

Tanggal: 2026-11-08 · HEAD dasar: `5952097` · Aturan: kontrak beku v1–v5 tak disentuh, 0 commit (perubahan di working tree).

## Fase 0 / G1 — Diagnosis drift bench/codebase tree-sitter-python (1557 → 1561)

**Kesimpulan: drift = perbaikan resolver yang benar.** +4 edge adalah include C yang seharusnya resolve, diperbaiki oleh tier helper W12 (`src/know_your_context/resolve/lang_helpers.py`) yang ditambahkan di commit `ea4f07d` ("depth push ... 21 helpers"). Pin 1561 di `bench/codebase/baseline.json` sudah benar dan gate di HEAD hijau — tidak perlu re-pin baru.

### Bukti dua arah (sesuai prosedur plan G1)

**Arah 1 — A/B kernel (hipotesis 1 plan: fallback kernel B1):** **DITOLAK.**
```
.venv/bin/python bench/codebase/run.py run                      → tree-sitter-python: 1561 edges, 296/2678 resolved (ledger 20261108T142334Z)
KYS_DISABLE_RUST_KERNEL=1 .venv/bin/python bench/codebase/run.py run → identik: 1561 edges, 296 resolved (ledger 20261108T143046Z)
```
Kernel on/off tidak mengubah satu pun count → drift bukan kontribusi fallback kernel.

**Arah 2 — diff edge-set + biseksi commit** (probe: `/root/.hermes/cache/scratch/sub159/g1_edge_diff.py`, `g1_trace.py`):

| Revisi (worktree, kernel off) | edges | resolved |
|---|---|---|
| `046f983` (era pin) | **1557** | 292 |
| `f35e7f0` | 1557 | 292 |
| `4847be2`, `370d4d5`, `d4067b3` | 1557 | 292 |
| `ea4f07d` (tambah lang_helpers.py) | **1561** | 296 |
| HEAD (`5952097`) | 1561 | 296 |

- Edge set `ea4f07d` ≡ HEAD (identik per-edge).
- Tepat 4 edge baru, semuanya `imports` dari sumber C:
  - `src/scanner.c` → `file:src/tree_sitter/array.h` (`tree_sitter/array.h`)
  - `src/scanner.c` → `file:src/tree_sitter/parser.h` (`tree_sitter/parser.h`)
  - `src/tree_sitter/array.h` → `file:src/tree_sitter/alloc.h` (`./alloc.h`)
  - `bindings/python/tree_sitter_python/binding.c` → `file:bindings/swift/TreeSitterPython/python.h` (`Python.h`)
- Trace resolver (`g1_trace.py`): di HEAD ke-4 ref resolve via `strategy='module_helper'` (tier W12); di kode era-pin gagal `ambiguous_tail:20` (tail `array.h` mengenai 20 kandidat simbol). Ekstraksi identik (set ref byte-sama, `files` 45/45) — hanya resolusi berubah.
- `refs_failed` turun 2386→2382 dan `refs_resolved` naik 292→296 = keempat ref yang sama.

**Jebakan metodologi (untuk rekan):** `bench/codebase/run.py:65` menyisipkan `REPO/src` ke `sys.path[0]` — A/B terhadap worktree revisi lama WAJIB menetralkannya (lihat `g1_edge_diff.py`), kalau tidak kode HEAD yang terukur (hasil palsu).

## WS-A — Store

### S1 — `insert_event` COUNT(*) per insert → anchor O(1)

Fix: `_chain_event_count()` membaca anchor `system_state.chain_event_count` +1 dalam transaksi yang sama. COUNT(*) hanya tersisa sebagai bootstrap satu kali untuk store pra-anchor (events ada, anchor belum); event pertama tanpa scan.

Proof test (TDD, RED→GREEN): `tests/test_store_scaling_guards.py::test_insert_event_does_not_count_event_log_rows` (trace callback membuktikan nol `COUNT(*)` pada insert path), `test_chain_anchor_tracks_appends` (anchor == head nyata + `verify_chain().ok`).

Ukur (perintah: `.venv/bin/python bench/store_scaling.py ...` — rekonstruksi harness B1 lane A dari verdict lampiran A):

| Metrik | Before | After | Bukti |
|---|---|---|---|
| EXPLAIN insert path | `SCAN event_log USING COVERING INDEX event_log_knowledge_seq` | `SEARCH system_state USING INDEX ... (key=?)` | `bench/store_scaling.py explain <store>` |
| Lane A serial @160→170k (salinan store identik, 2 ronde arah bolak-balik) | median 236 / 296 ops/s | median 317 / 376 ops/s | `ab-{before,after}{,2}.json` di scratch sub159 |
| Delta | — | **+30–47% ops/s @165k** (arah konsisten di kedua ronde) | |

Catatan host: absolut lebih rendah dari verdict (645→290) karena host 2 vCPU sedang menjalankan agen paralel lain; perbandingan dilakukan pada salinan store yang sama, berurutan, dua arah untuk menetralkan bias cache.

### S2 — `verify_chain` fetchall → streaming (batch 4096)

Fix: iterator `fetchmany(_VERIFY_CHAIN_BATCH=4096)` untuk `event_log` dan journal links (diekstrak ke `_verify_journal_links`); semantik deteksi (per-link, anchor count/head, missing-anchor fail-closed, journal↔audit) identik — diverifikasi suite chain yang ada.

Proof test: `tests/test_store_scaling_guards.py::test_verify_chain_streams_the_event_log` — tracemalloc peak @12.000 event×4 KiB: 69,5 MiB (fetchall, RED) → lulus budget 64 MiB (GREEN, batch ≈ 24 MiB).

Ukur @160k event (store sama, `.venv/bin/python bench/store_scaling.py verify <store>`):

| Metrik | Before | After | Kill criteria | Status |
|---|---|---|---|---|
| Wall verify | 13,38 s (median 2 run) | 13,60 s (+1,6%) | memburuk >20% → rollback | **di dalam toleransi** |
| RSS puncak proses | 739–760 MiB | 422–433 MiB (−43%) | — | membaik |
| Python materialization (tracemalloc @160k) | ~660 MiB (delta fetchall) | **15,6 MiB** (−97,6%) | RSS after >200 MiB | lihat catatan |
| `verify_chain().ok` | True | True | gagal → rollback | **True** |

**Catatan kill criteria RSS:** target absolut ≤150 MiB tidak tercapai pada total RSS proses (422–433 MiB). Dekomposisi: DB 353 MiB; store dikonfigurasi hardware-aware `cache_size` 290 MiB + `mmap_size` 375 MiB — full-scan mengisi cache itu (konstan terhadap N, keputusan tuning yang disengaja, berlaku untuk SEMUA full-table read). Komponen patologis O(N) (materialisasi baris Python) turun 660→15,6 MiB; ekstrapolasi 1M event verdict (~6 GiB → OOM) tereliminasi: memori verify kini konstan dalam panjang chain. Keputusan: fix dipertahankan (rollback akan mengembalikan jalur OOM); angka penuh tercatat di sini untuk evaluasi operator.

### S3 — indeks `invocation_journal.effective_idempotency_key`

Dikerjakan SUB-160 dalam bundle migrasi v6 (sesuai plan "S3+name_tail bundle"). Terverifikasi di store 160k:
`EXPLAIN QUERY PLAN SELECT request_hash FROM invocation_journal WHERE effective_idempotency_key = ?` →
`SEARCH invocation_journal USING INDEX idx_invocation_journal_effective_key (effective_idempotency_key=?)` — **0 SCAN di `_conflicting_reuse`** ✓

## Gate (final, run nyata di host ini)

- `pytest tests/test_store_scaling_guards.py` — 3 test (RED→GREEN terverifikasi), hijau pasca-refaktor PoT
- Subset store/chain pasca-refaktor: 209 passed
- **Suite penuh `pytest -q`: 16270 passed, 20 failed → 19 di antaranya lulus saat di-run terisolasi (flake beban host 2 vCPU yang dibagi agen paralel; crash/sigkill timing tests). Sisa 1: `test_power_of_ten.py::TestRepoGate` — 0 unjustified (scope saya bersih: insert_event kembali ke baseline 95 baris), 2 STALE justification di `resolve/name_matcher.py` + `resolve/synthesis.py` — kedua file sedang diedit agen sibling (WS-B/D); PoT di HEAD bersih = PASS (168 justified). Bukan milik SUB-159.**
- **`bench/codebase/run.py run`: gate PASS exit 0 (correctness PASS, regression PASS, determinisme 2-run byte-identik per target) — ledger 20261108T171619Z** — membuktikan fix S1/S2 tidak mengubah output ingestion satu byte pun
- **`pytest tests/test_serve_gates.py -q`: 11 passed exit 0** (kill criterion S1 terpenuhi)
- ruff check / ruff format --check (file yang disentuh): bersih; pyright: 0 error; bandit -ll: 0 medium/high baru; `tools/power_of_ten.py`: 0 UNJUSTIFIED di scope

## File

- `src/know_your_context/store.py` — `_chain_event_count` (S1), `verify_chain` streaming + `_verify_journal_links` (S2), `_VERIFY_CHAIN_BATCH=4096`
- `tests/test_store_scaling_guards.py` — 3 proof test (baru)
- `bench/store_scaling.py` — harness ukur lane A / append A/B / verify RSS / EXPLAIN (baru, rekonstruksi B1 verdict agar tak bergantung scratch)
- Laporan ini.
