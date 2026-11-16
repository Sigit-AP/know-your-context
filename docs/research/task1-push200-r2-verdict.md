# TASK1-PUSH200 RONDE 2 — Verdict ulang per dimensi (SUB-167)

**Tanggal:** 2026-11-16 · **HEAD:** `b58bfd2` + perubahan tidak ter-commit (SUB-166 + SUB-167) · **Instrumen:** `bench/verdict` ledger `20261116T063957Z` (resmi, ~70 metrik × 3 repo) · **Build doc:** `docs/recovery/task1-push200-sub167.md` · **Rencana:** `docs/research/task1-push200-r2-plan.md`.
Aturan yang dipegang: angka apa adanya; per-opponent; rasio vs 0 = unbounded; kapabilitas tanpa proof test = 0; tidak ada inflasi volume.

---

## 1. Jawaban langsung: 200% per dimensi

| Dimensi | Ukuran | vs codegraph | vs graphify | Status |
|---|---|---|---|---|
| **Algoritma** | level instrumen l2 | 1019,48 → **1022,22%** | 3736,31 → **3748,53%** | **≥200% TERCAPAI (5–18×)** |
| **Performa** | l4 / l7 instrumen + before/after mekanisme | l4 455,57% · l7 688,29% | l4 2147,28% · l7 1159,17% | **≥200% TERCAPAI**; mekanisme ronde 2: LIKE miss **~91×** @1M simbol |
| **Level profesional** | level instrumen l1 | 78,35 → **80,18%** | 118,36 → **119,68%** | **TIDAK TERCAPAI vs codegraph — tetap tidak bisa dijujurkan** (§4) |

**Komposit instrumen:** 405,88 → **407,07% vs codegraph**; 1362,92 → **1367,37% vs graphify**. Target 400% per-opponent: **MET keduanya** (naik di keduanya).

## 2. Sub-skor <90% — status akhir ronde 2

| # | Item | Lama | Baru | Bukti (metode + angka) |
|---|---|---|---|---|
| R1 | Cluster B — kreativitas | **88** | **90** | Trigram durable + freshness O(1) watch_seq — inovasi kini DI hot path retrieval (dulu: "di luar hot path"): LIKE miss @1.000.000 simbol **246,4 → 2,7 ms (~91×)**; referensi: in-memory rebuild-penuh per perubahan (cg queries.ts:477/2340) atau nol (gf). 37 proof test, 3 seed identik |
| R2 | Store — flow (serial degrade) | 93 | 93 (tidak naik — jujur) | Diagnosis lengkap: H3 gugur (WAL stabil 4,19 MB @160k), H1 negatif terukur (A/B autocheckpoint 4000: −2,6% avg, degrade 21,8→38,0%), konsisten H2 inheren (B-tree vs cache 371 MB). Tidak ada klaim perbaikan |
| R3 | Hardware — kreativitas | **88** | **90** | Kalibrasi fsync TERUKUR menggantikan asumsi flag: host ini p50 0,359 ms → ssd (cross-validated); klasifier murni 8 batas dipin; gap 1–8 ms jujur UNKNOWN; 16 proof test |
| R4 | Ops — kreativitas | 89 | 89 | Tidak dikerjakan (ICE terendah dari tiga kandidat; ruang tipis) |
| R5 | l1 profesional vs codegraph | 78,35% | 80,18% | +1,83 poin jujur (53 test baru + 2 seksi docs nyata); sisanya volume murni — §4 |

**Rata-rata sumbu per bagian (rubrik 5-sumbu yang sama):**

| Bagian | Lama | Baru | Delta |
|---|---|---|---|
| Cluster B (retrieval dsb) | 90,6 | **91,0** | +0,4 (kreativitas 88→90) |
| Hardware | 91,0 | **91,4** | +0,4 (kreativitas 88→90) |
| Store | 94,0 | 94,0 | — (B = diagnosis jujur) |
| Lainnya | — | — | tidak berubah dari verdict SUB-166 |

## 3. Metode pengukuran (reproducible)

```bash
# A: exactness + lifecycle (37p) — PYTHONHASHSEED 0/1/42 identik
.venv/bin/python -m pytest tests/test_retrieval_v2_trigram.py -q
# A: skala — populate 1M simbol + before/after (kill switch = scan)
.venv/bin/python /root/.hermes/cache/scratch/sub167_trigram_scale.py 1000000
# B: diagnosis + A/B autocheckpoint
.venv/bin/python /root/.hermes/cache/scratch/sub167_lane_a_diag.py 160000
.venv/bin/python /root/.hermes/cache/scratch/sub167_lane_a_ab.py default|ac4000
# C: 16p + pengukuran nyata host
.venv/bin/python -m pytest tests/test_hardware_fsync_probe.py -q
.venv/bin/python -c "from know_your_context.hardware.probe import probe_hardware as p; print(p('.', refresh=True).disk)"
# Instrumen resmi
.venv/bin/python bench/verdict/run.py run   # ledger 20261116T063957Z
```

## 4. Yang TIDAK mencapai 200% — tidak berubah dari verdict SUB-166

l1 vs codegraph 80,18%: sembilan dari sepuluh metrik adalah volume murni (cg 172k LOC / 835 file test / 3320 fixture vs kita 62k/289+/125). Mengejar 200% = inflasi volume yang dilarang VERDICT-FRAMEWORK §7. Yang digerakkan jujur ronde ini: +53 test proof nyata, +2 seksi dokumentasi operasional nyata. **Tidak ada klaim 200% untuk l1-vs-codegraph, R2, R4.**

## 5. Kill criteria

3 iterasi tanpa perbaikan terukur → stop. **Tidak terpicu**: A dan C menghasilkan perbaikan terukur pada iterasi pertama (A: 91×; C: klasifikasi terukur + cross-validation). B dihentikan setelah diagnosis + 1 iterasi mekanisme negatif terukur — keputusan berbasis bukti (satu-satunya lever tersisa, pacing checkpoint, TERUKUR memperburuk), dilaporkan jujur tanpa klaim.
