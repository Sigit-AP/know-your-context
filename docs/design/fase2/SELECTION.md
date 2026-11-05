# SELECTION.md — Fase 2: Audit & Seleksi 10 Style (SUB-143)

Tanggal: 2026-11-05 · Metode: 30 sampel (10 style × Overview/Activity/Graph),
diaudit `vision_analyze` per style (montase 3 layar) terhadap checklist 7 butir
skill professional-ui-design (B1 komponen · B2 tanpa ellipsis/wrap · B3 satu aksen
+ semantik · B4 grid/radius · B5 mono+hierarki · B6 viewport terisi · B7 chrome utuh).
Bukti: PNG per layar di `s1/`–`s10/` + `_montage.png` per style.

## Skor per style per layar (maks 7/layar, 21/style)

| Style | 01 Overview | 02 Activity | 03 Graph | Total | Verdict |
|---|---|---|---|---|---|
| S1 obsidian-minimal | 7 | 7 | 6 (B2) | 20/21 | lolos, tidak top |
| S2 terminal-brutalist | 7 | 7 | 7 | **21/21** | **TOP** |
| S3 swiss-grid | 7 | 7 | 7 | **21/21** | **TOP** |
| S4 glass-console | 6 (B2) | 7 | 7 | 20/21 | lolos, tidak top |
| S5 enterprise-clean | 7 | 7 | 6 (B2) | 20/21 | lolos, tidak top |
| S6 neon-ops | 7 | 7 | 7 | **21/21** | **TOP** |
| S7 paper-dark | 7 | 7 | 7 | **21/21** | **TOP** |
| S8 data-dense | 7 | 7 | 5 (B2+B6) | 19/21 | **TOLAK** |
| S9 monochrome-type | 7 | 6 (B3) | 6 (B2) | 19/21 | **TOLAK** |
| S10 aurora-soft | 6 (B6) | 7 | 7 | 20/21 | lolos, tidak top |

## Alasan gugur / tidak terpilih (konkret, dari bukti vision)

- **S8 data-dense — TOLAK.** Graph Explorer 5/7: status bar memotong hash
  `MERKLE: 0x9f8a...c3b1` (ellipsis mid-token, B2) + canvas graph jarang
  (~11 node di area besar → void, B6); label edge/koordinat ~9–10px di bawah
  batas keterbacaan. Densitas ekstrem terbukti mengorbankan disiplin data.
- **S9 monochrome-type — TOLAK.** Janji inti style (nol kroma) dilanggar oleh
  desainnya sendiri: baris FAIL di Activity Log memakai wash merah + teks merah
  (B3) — status kembali ke warna, bukan shape+word; Graph Explorer memotong
  tag `cluster-0…` (B2). Style yang mengingkari aturan dirinya bukan profesional.
- **S1 obsidian-minimal (20/21).** Kuat, tapi B2 gagal sekali (Merkle
  `0x9f8a...c5b1` ellipsis di status bar Graph). Kalah head-to-head dari 4
  style 21/21; arah ini juga sudah diwakili baseline fase 1.
- **S4 glass-console (20/21).** Overview B2: strip metadata header bertumpuk/
  garbled ("SHACL INVARIANT…AUDIT(…)"). Translucency berisiko gimmick untuk
  konsol ops jangka panjang.
- **S5 enterprise-clean (20/21).** Solid tapi B2 gagal (Merkle ellipsis) dan
  paling generik — nilai diferensiasi terendah dari kandidat 20/21.
- **S10 aurora-soft (20/21).** B6 gagal: void lebar di bawah tabel Domain
  Health Overview. Radius besar + teal kurang tegas untuk alat ops.

## TOP 4 (unggul di SEMUA butir pada ketiga sampel — aturan seleksi ketat)

1. **S2 terminal-brutalist (21/21)** — paling distingtif; mono penuh, disiplin
   status sempurna (INFO hijau/WARN amber/ERROR merah + wash baris), 18 baris
   penuh, nol dekorasi. Catatan: ikon ⚡/⧉ di tombol graph = borderline,
   diawasi di P4.
2. **S3 swiss-grid (21/21)** — satu-satunya arah terang yang lolos sempurna;
   disiplin tipografi print, aksen merah tunggal, angka raksasa terbaca.
3. **S6 neon-ops (21/21)** — SOC violet; disiplin warna terbaik di tabel log;
   graph "konstelasi keamanan" kuat. Nits (format hash lintas layar) dicatat.
4. **S7 paper-dark (21/21)** — editorial hangat; serif+mono kontras terkendali;
   satu-satunya arah warm. Nits: drift panjang hash antar layar, roman numeral
   sidebar tidak konsisten — diperketat di prompt P4.

Kill criteria: tidak ada style yang 2× gagal audit sampel; S8/S9 gugur oleh
seleksi kualitas (bukan kill), sisanya lolos tapi tidak top.

## Konsistensi P4

Per top style: prompt = blok STYLE identik (verbatim dari library) + COMMON +
spec layar; `designSystem` = asset yang sama untuk ke-12 layar:
S2 `assets/11192877034597098088` · S3 `assets/2940579337020552708` ·
S6 `assets/16989391640836114384` · S7 `assets/13900753665212520441`.

## Verifikasi P4 (konsistensi 12 layar per top style)

Audit vision `_grid12.png` (montase 12 layar) per top style:

| Style | Verdict konsistensi | Temuan |
|---|---|---|
| S2 terminal-brutalist | CONSISTENT | 12/12 gaya sama; nit data (06 count mismatch, 12 math); **08 KNOWN DEFECT** — 4 generasi Stitch gagal menata ledger padat ini tanpa cacat layout (R0 ellipsis sistematis → R1 clip tepi kanan → R2 kolisi topbar → R3 divider terpotong + pita kosong). Kill criteria: tidak dipaksa; R3 diship dengan catatan di INDEX.md. |
| S3 swiss-grid | CONSISTENT | 12/12 bersih, tanpa defect terdeteksi. |
| S6 neon-ops | CONSISTENT | drift chrome sidebar di 10/11/12 (label grup bernomor), counter drift (Debt 12 vs 14), void ringan 05/07. |
| S7 paper-dark | CONSISTENT | 09 diregenerasi 2× (R0 judul terpotong → R1 overflow kanan+void → R2 PASS 4/4); nit: drift format hash. |

Style tidak tertukar antar top (palet/font/aksen unik per style terverifikasi di grid).

## Paket (P5)

`top1-terminal-brutalist.zip` · `top2-swiss-grid.zip` · `top3-neon-ops.zip` ·
`top4-paper-dark.zip` — masing-masing 12 PNG + 12 HTML + INDEX.md (25 entri),
diverifikasi programatik.
