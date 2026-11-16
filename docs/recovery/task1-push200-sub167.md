# TASK1-PUSH200 RONDE 2 — Build (SUB-167)

**Tanggal:** 2026-11-16 · **HEAD dasar:** `b58bfd2` + warisan SUB-166 (tidak ter-commit) · **Rencana:** `docs/research/task1-push200-r2-plan.md` · **TIDAK commit** (aturan operator).
**Metode:** TDD (RED→GREEN per mekanisme), REA-any atas `/root/src/codegraph` + `/root/src/graphify`, kill criteria 3 iterasi tanpa perbaikan terukur.

## Verifikasi warisan (koreksi briefing)

Briefing SUB-167 menyatakan "SUB-166 mati karena billing, hasilnya tidak ada". **Fakta di worktree:** kode P1/P2/P3 ada dan test-nya 34/34 hijau (`tests/test_serve_adaptive_batch.py`, `test_chain_checkpoint.py`, `test_ops_watchtower_parallel.py`). Dilaporkan duluan per VERDICT-FRAMEWORK §6.7.

## Item A — Durable trigram substring index (cluster B kreativitas 88; retrieval performa)

**Referensi tidak punya (bukti):** codegraph `src/db/name-substring-index.ts` = in-memory, rebuild PENUH per change-stamp (`memoizedRead` queries.ts:477 → `readNameSubstringIndex` queries.ts:2340 baca ulang SEMUA nodes per invalidasi); mati tiap proses. graphify: nol.
**Milik kita:** FTS5 `tokenize='trigram'` durable (lintas proses, nol RAM steady-state) + freshness O(1) via counter `symbols_watch` v6 (bukan rebuild per perubahan).
**Kontrak exactness (dipin 37 test):** kandidat trigram ⊇ hasil LIKE-literal iff semua term ≥3 char (diverifikasi eksperimen SQLite 3.53.1: 10 kasus adversarial unicode/`_`/`%`/`.`/case — superset hanya patah di needle <3, sesuai prediksi); predikat LIKE tetap diterapkan pada kandidat → byte-identik dengan SCAN termasuk orde kanonik + cap. Term <3 / kill switch / error trigram → SCAN historis.
**TDD:** RED (collection error: modul tak ada) → 37 test hijau; determinisme 3 seed (PYTHONHASHSEED 0/1/42) identik.
**Before/after terukur** (store sintetis 1.000.000 simbol, host ini, `scratch/sub167_trigram_scale.py`):

| Ukur | Scan (kill switch) | Trigram | Rasio |
|---|---|---|---|
| LIKE miss (FTS-kosong, kasus Q2) | 246,4 ms | **2,7 ms** | **~91×** |
| LIKE hit-semua (5000 cap) | 41,7 ms | 1.069,3 ms | 0,04× — regresi, DIDOKUMENTASIKAN |
| Rebuild (sekali, background) | — | 14,3 s | — |
| Ukuran db | ~184 MB @500k | ~2× porsi symbols | biaya jujur |

Konteks regresi hit-semua: `_like_channel` hanya berjalan bila kanal FTS kosong (lookup.py `_search_uncached`), jadi term yang cocok dengan hampir semua simbol tidak pernah sampai ke kanal LIKE di produksi. Dilaporkan apa adanya, bukan disembunyikan.
Kill switch `KNOW_YOUR_CONTEXT_NO_TRIGRAM=1` (registry `retrieval.trigram`, pin count 29 di test_ops_knobs).

## Item B — Store serial degrade: DIAGNOSIS SELESAI, tanpa perubahan kode (jujur)

| Hipotesis | Uji | Hasil |
|---|---|---|
| H3: WAL tumbuh tak ter-checkpoint | ukur `-wal` per window @160k | **GUGUR** — WAL stabil 4,19 MB (autocheckpoint bekerja) |
| H1: pacing checkpoint membantu | A/B 160k: default(1000) vs 4000 pages | **TIDAK** — avg 659,1 → 642,2 ops/s (−2,6%); degrade 21,8% → 38,0% |
| H2: pertumbuhan B-tree/cache inheren | degradasi berkorelasi db_mb mendekati cache 371 MB | **KONSISTEN** — @60k (117 MB): −5,0%; @160k (349 MB): 17,3–21,8% |

Keputusan: tidak ada mekanisme yang jujur membaik di lane serial (group-commit sudah milik lane daemon; cache mentok di RAM 3 GiB; synchronous=FULL disengaja). Iterasi mekanisme pertama negatif terukur → dihentikan berbasis bukti, dilaporkan sebagai diagnosis, bukan kegagalan tersembunyi. Skor store flow tetap 93.

## Item C — Kalibrasi fsync terukur (hardware kreativitas 88)

**Referensi tidak punya:** codegraph wal-valve.ts memakai timer + threshold konstan tanpa mengukur fsync; graphify nol. Kita sebelumnya: klasifikasi via flag sysfs `queue/rotational` — asumsi, dan virtualisasi sering tidak mengekspos flag (UNKNOWN) atau menyesatkan.
**Mekanisme:** `probe_fsync_latency()` (write+fsync 4 KiB × N sampel di direktori db — bentuk beban = commit WAL nyata) + `classify_fsync()` murni (p50 <1 ms → SSD; >8 ms → HDD; gap 1–8 ms JUJUR UNKNOWN) + `resolve_disk_kind()` arbitrase: flag dikenal menang (pengukuran jadi note), flag UNKNOWN direklasifikasi oleh pengukuran. Dilewati total saat `KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING=1`. Fail-open + strict typed (`ProbeError`).
**TDD:** 16 test (8 batas klasifier murni, probe nyata + fail-open + strict, 3 arbitrase, 2 integrasi probe_hardware). RED (import error) → GREEN.
**Terukur host ini:** p50 fsync **0,359 ms**, p95 1,622 ms, 16 sampel → kelas ssd (setuju flag — validasi silang, bukan sekadar paritas).

## File (ronde 2)

| File | Perubahan |
|---|---|
| `src/know_your_context/retrieval_v2/trigram.py` | BARU: durable trigram index + freshness watch_seq |
| `src/know_your_context/retrieval_v2/lookup.py` | `_like_rows` prefilter + fallback scan |
| `src/know_your_context/hardware/probe.py` | `FsyncProbe`, `probe_fsync_latency`, `classify_fsync`, `resolve_disk_kind`, `measure_fsync` di probe_hardware |
| `src/know_your_context/ops/switches.py` | switch `retrieval.trigram` + accessor (count 29) |
| `tests/test_retrieval_v2_trigram.py` | BARU, 37 test |
| `tests/test_hardware_fsync_probe.py` | BARU, 16 test |
| `tests/test_ops_knobs.py` | pin count 28→29 (additive) |
| `tests/test_hardware_switches.py` | allowed stdlib +`time` (beralasan) |
| `docs/operations.md` | 2 seksi nyata (trigram, fsync) |
| `docs/research/task1-push200-r2-plan.md` | rencana tertulis |

## Gates (semua hijau)

- Test baru: 37 + 16 = 53; non-regresi retrieval_v2 penuh + ops switches/knobs + store guards + chain checkpoint + adaptive batch + watchtower + serve gates 11/11 (storm 50 agen, kill-9 zero-loss).
- ruff check/format scope: 0; pyright scope: 0; PoT: 162/162 justified → PASS; determinisme 3 seed identik.
- Catatan flake: tidak ada kegagalan baru di scope ronde 2.
