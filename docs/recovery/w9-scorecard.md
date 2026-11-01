# W9 — Capability scorecard (mesin, bukan opini)

**Scope:** `bench/scorecard/` (capabilities.json, run.py), `tests/test_scorecard.py`.
**Tujuan:** mengukur kapabilitas arsitektur/engineering Know Your Soul vs
codegraph vs graphify secara terukur mesin — setiap klaim sisi kita dibuktikan
oleh test nyata yang dieksekusi; sisi lawan dari bukti manual terdokumentasi
(path + baris di repo mereka).

## Cara menjalankan

```bash
uv run python bench/scorecard/run.py check   # validasi statis (exit 0/1)
uv run python bench/scorecard/run.py run     # eksekusi 123 proof tests + skor + ledger
uv run python bench/scorecard/run.py print   # markdown dari ledger terakhir
```

## Metodologi

72 kapabilitas dalam 10 kategori (concurrency, integrity, security,
operability, contracts/governance, recovery, observability, testing rigor,
determinism, extensibility). Setiap kapabilitas: `id`, `category`,
`weight` (1–3), dan:

- **Sisi kita** — `proof_tests`: daftar pytest node id nyata di `tests/`
  (termasuk class-based `file.py::Class::test`). `run.py run` mengeksekusi
  SEMUANYA dalam satu invokasi pytest dengan `--junitxml`; kapabilitas lolos
  iff SEMUA proof-nya hijau (skipped dihitung TIDAK lolos). 123 proof unik.
- **Sisi lawan** — `status` ∈ {supported=1.0, partial=0.5, absent=0.0} +
  `evidence`: path file di repo lawan + baris + catatan. Untuk status absent,
  evidence menunjuk file tempat kapabilitas itu seharusnya hidup. Evidence
  diverifikasi keberadaannya oleh `tests/test_scorecard.py`
  (file exists + baris dalam rentang) saat repo lawan ada di disk.

## Formula (dibaca dari capabilities.json, bukan hardcode)

```
total_weight           = sum(weight)                        = 165
percent(subject)       = 100 * points(subject) / total_weight
points(ours)           = sum(weight) atas kapabilitas yang proof-nya hijau
points(opponent)       = sum(weight * status_points[status])
relative_vs(opponent)  = 100 * percent(ours) / percent(opponent)
final_vs(opponent)     = 0.6 * capability_relative + 0.4 * bench_relative
composite              = mean(final_vs(codegraph), final_vs(graphify))
```

Komponen bench (40%) TIDAK pernah di-hardcode: `run.py` membaca ledger
kompetitif (SUB-113) dari `$KYS_COMPETITIVE_LEDGER` atau
`bench/competitive/results/ledger-*.json` terbaru. Dua bentuk dikenali:
(1) mapping `relative_percent_vs` eksplisit; (2) `summary.per_system` dengan
hitungan OK/FAILED/NOT_SUPPORTED (rate = OK/total, relative = 100×ours/opp).
Jika ledger tidak ada/tak dikenali: `bench_component.status` dicatat
(absent/unrecognized/unreadable) dan bobot bench ter-renormalisasi ke 0 —
tidak pernah diasumsikan diam-diam.

## Hasil nyata (ledger-20261101T104648Z)

- Proofs: **123/123 passed** (satu invokasi pytest nyata, exit 0).
- Capability: ours **100.0%** · codegraph 32.42% · graphify 27.27%.
- Relative: **308.41% vs codegraph** · **366.67% vs graphify**.
- Bench kompetitif (ledger SUB-113 101204Z, 6/6 OK ketiga sistem): 100% vs
  keduanya.
- **Final: 225.05% vs codegraph · 260.00% vs graphify · komposit 242.52%**.

Kategori terkuat lawan: extensibility (codegraph 61.5%, graphify 80.8% —
bahasa & format mereka lebih luas) dan concurrency (codegraph 61.8% —
watchdog/lockfile/pool mereka nyata). Keunggulan kita mutlak di integrity,
recovery, governance, security — kategori yang hampir seluruhnya absent di
kedua lawan.

## Kejujuran alat ukur

- Setiap proof test diverifikasi ADA (AST, bukan text grep) sebelum dijalankan;
  node id salah → `check` merah.
- Parametrized proof: semua parametrizasi harus hijau.
- `compute_capability_scores` fungsi murni — determinisme + order-independence
  dipin oleh test.
- Kegagalan apa pun (proof merah/hilang) → exit 1, ledger tetap tertulis apa
  adanya.
- Angka lawan adalah evidence manual — bagian yang tidak bisa dieksekusi mesin
  (repo mereka bukan milik kita); presisinya dijaga dengan evidence path:line
  yang diuji keberadaannya.

## Temuan saat pembangunan (diperbaiki)

1. Node id class-based (24 proof) — pytest exit 4 "no tests ran" saat node id
   tanpa class; diperbaiki dengan resolusi AST dan mapping JUnit
   classname→file+class.
2. Evidence graphify salah prefix (`graphify/tests/` → `tests/`) dan 1 baris
   codegraph melebihi panjang file — tertangkap oleh test evidence sendiri.
