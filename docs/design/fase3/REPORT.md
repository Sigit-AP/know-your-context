# REPORT.md — Fase 3 Audit: 5 Style × 3 Layar Kompleks (SUB-145)

Tanggal: 2026-11-05 · Metode: generate Stitch (project 15376293374719786204,
satu design system per style via `create_design_system`) → audit vision per
layar (7 butir checklist skill professional-ui-design + 3 kriteria operator:
hierarki jelas tanpa warna-saja? interaksi tergambar? tidak menumpuk/kaku/
kuno?) → iterasi prompt maks 2x per layar gagal (R1, R2) → verdict jujur.

Skala per layar: **PASS** (semua butir) / **PASS-minor** (≤2 butir gagal
minor, kriteria operator terpenuhi) / **FAIL-residual** (butir gagal tersisa
setelah 2 iterasi — dicatat apa adanya).

## Skor per layar

| Style | 01 Graph | 02 Activity | 03 Analytics |
|---|---|---|---|
| V1 ledger-light | PASS-minor | PASS | PASS-minor (R2) |
| V2 ink-mono | PASS-minor | PASS-minor | PASS-minor (R2) |
| V3 slate-calm | FAIL-residual (R2) | PASS | PASS |
| V4 modular-gray | FAIL-residual (R1) | PASS | FAIL-residual (R2) |
| V5 console-dark | PASS (R1) | PASS (R1) | PASS |

## Detail per style (alasan, jujur)

### V1 — ledger-light (9 butir gagal minor total: 2)
- **01-graph PASS-minor**: seluruh komponen + 3-tier LOD + state lengkap
  (hover/selected/focus/nav). Minor: legend P1–P7 tanpa count terpisah dari
  panel partitions (P6/P7 tidak ber-count); header "RELATIONS (4)" merender
  3 baris.
- **02-activity PASS**: 16 baris penuh, 0 ellipsis, state lengkap, rail
  "AGENTS — 50" + roster. Nit: dot bell amber-adjacent; sedikit ruang kosong
  di bawah tabel.
- **03-analytics PASS-minor (R2)**: R0 gagal (chart overflow menimpa panel),
  R1 memperbaiki chart tapi dead band, R2 flush-bottom. Residual minor:
  label "TARGET" garis referensi terselip sebagian di belakang bar D-01.

### V2 — ink-mono (estetika paling berisiko, tereksekusi baik)
- **01-graph PASS-minor**: hierarki tipe-murni TERBUKTI kuat tanpa warna
  (node terpilih = satu-satunya isi tinta solid). Minor: focus ring search
  tidak tergambar; hover wash menyatu dengan selected.
- **02-activity PASS-minor**: 18 baris hairline, konsistensi data internal
  luar biasa (hash berantai, count cocok). Minor: biru bocor ke teks
  non-interaktif (P2 INGEST, hash terpilih); dead space di rail kiri ~40%.
- **03-analytics PASS-minor (R2)**: R0 gagal (dead space 40% bawah + biru
  pada data), R2 memperbaiki keduanya + RECENT EVENTS 5 baris. Residual:
  rail kiri masih punya void ~40% tinggi kolom.

### V3 — slate-calm
- **01-graph FAIL-residual (R2)**: konten/hierarki/state semua kuat
  (truncasi R0 teratasi, relasi tepat 2 baris, skor P/M/E persis).
  Residual: **dead band 10–12% di bawah status bar** — 2 iterasi tidak
  berhasil membuat Stitch mengisi penuh tinggi capture (1440×1024 vs desain
  fix 900). Satu-satunya butir gagal.
- **02-activity PASS**: 12 baris + group header waktu + pagination; state
  lengkap. Nit: INFO chip filter (sky) vs tabel (hijau) tidak seragam;
  filter INFO tampil aktif tapi baris WARN tampil (kontradiksi filter-data).
- **03-analytics PASS**: semua panel + state; nit: chart garis bukan area;
  stepper aktif kontrasnya justru terendah; 2 panel bawah agak kurang terisi.

### V4 — modular-gray (paling bermasalah)
- **01-graph FAIL-residual (R1 dikirim, R2 dibuang)**: R2 justru regresi
  (truncasi baru: vector-indexer-p3 tertelan chip, DEPR terpotong,
  schema-contract-0) → dikirim R1. Residual R1: dead band bawah; label
  heuristics-v1 bersinggungan dengan tag DEPRECATED. Hover, pill tab,
  inspector, LOD semua PASS.
- **02-activity PASS**: 14 baris + roster 11 + level mix; state lengkap.
  Nit: count toolbar (16) vs level mix (18) vs "214 filtered" tidak
  rekonsili; search filter dekoratif.
- **03-analytics FAIL-residual (R2)**: R0 gagal total (chart tidak
  ter-render), R2 chart 14 bar muncul. Residual: dead band bawah; hover
  wash hilang di R2 (selected + focus + tab masih ada); label "150 TARGET"
  tertutup bar tertinggi.

### V5 — console-dark (terkuat)
- **01-graph PASS (R1)**: R0 gagal (label P6 terpotong, CTA gradient +
  glyph ⚡, glow) → R1 bersih 5/5: label lengkap, CTA flat text-only,
  border 2px flat, relasi tepat 2, dock 4 kolom, status strip flush.
- **02-activity PASS (R1)**: R0 gagal (payload JSON terpotong mid-token +
  dock tidak sinkron dgn baris terpilih) → R1 bersih: payload 1 baris penuh,
  dock konsisten dgn baris 9 (seq 10414 / 43dcb102 / 11:54:52Z, chain link
  benar).
- **03-analytics PASS**: 7/7 ronde pertama — satu-satunya layar yang lolos
  penuh tanpa iterasi; data internal konsisten (domain counts sum = 1,284;
  3,917/1,284 = 3.05 deg/node cocok).

## Jawaban kriteria operator (per style)

| Style | Hierarki jelas (bukan warna-saja)? | Interaksi tergambar? | Tidak menumpuk/kaku/kuno? |
|---|---|---|---|
| V1 | YA — lebar zona 70/30 + penomoran seksi + skala tipe | YA (4 state) | YA |
| V2 | YA — murni skala tipe + tinta solid utk terpilih (terkuat di kriteria ini) | YA-minor (focus ring graph hilang) | YA — modern Swiss, bukan kuno |
| V3 | YA — langkah permukaan + padding + opacity | YA (5 state) | YA |
| V4 | YA — ukuran modul 8/4 kolom terbaca langsung | YA (kecuali hover hilang di 03-R2) | YA |
| V5 | YA — 4 tingkat kecerahan + posisi | YA (5 state, paling lengkap) | YA |

## Temuan lintas-style (jujur)

1. **Dead band bawah** adalah mode gagal paling persisten (v3/01, v4/01,
   v4/03 tersisa setelah 2 iterasi): Stitch meng-capture 1440×1024 tetapi
   model mendesain fixed 1440×900 — prompt "fill entire image height"
   menyembuhkan v1/v2/v5 tapi tidak v3/v4.
2. **Stitch menambah konten di luar spec** (versi v2.4.1, tombol Deploy,
   RAFT 3/3) — umumnya memperkaya, tapi sumber kegagalan "filter dekoratif"
   (chip terpilih vs data tidak konsisten) di v3/02 dan v4/02.
3. **Label anotasi chart** ("TARGET") berulang kali bertabrakan dengan bar
   tertinggi (v1/03, v4/03) — pola gagal yang konsisten pada chart padat.
4. Semantic colors (hijau/amber/merah) sesuai aturan D1/H4; auditor vision
   kadang salah-baca sebagai pelanggaran satu-aksesn — verdict di atas sudah
   mengkoreksi itu.

## Artefak

- `vX-name/`: 01-graph / 02-activity / 03-analytics (.png + .html) final +
  STYLE.md. Varian iterasi yang kalah dihapus; yang menang dipromosikan.
- `style-vX-name.zip`: 3 PNG + 3 HTML + STYLE.md per style.
- `design_systems_v3.json`: asset id design system per style.
- Driver: `/root/.hermes/cache/scratch/fase3_driver.py` (+ iterasi R1/R2).
- Tidak ada yang di-commit (aturan).
