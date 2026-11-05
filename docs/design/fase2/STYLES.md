# STYLES.md — Fase 2: 10 Arah Style Desain (SUB-143)

Tanggal: 2026-11-05 · Dasar: [../PATTERNS.md](../PATTERNS.md) (A–R) + 103 ref di `../refs/`.
Setiap style = arah yang BENAR-BENAR berbeda (palet, tipografi, densitas, mood,
komponen khas) — bukan variasi tipis. Semua style tunduk pada invariant bersama
di bawah; yang membedakan hanyalah yang tercantum per style.

## Invariant bersama (wajib di SEMUA style, dari PATTERNS.md)

- Shell A1: sidebar kiri 232–272px + topbar tipis; label seksi uppercase ~10px
  muted (A2); page padding 24–32px, gutter 14–16px, grid 4px (A3).
- SATU aksen (D1); merah/amber/hijau EKSKLUSIF status (H4); tanpa emoji (H4);
  kedalaman via langkah bg + hairline, TANPA shadow berat/gradasi (D2).
- Dua famili font (C1): sans untuk teks manusia, mono untuk data mesin
  (id/hash/timestamp/angka metrik). Uppercase hanya: label seksi, header kolom,
  status pill micro (C3).
- Tabel: satu kelas densitas per tabel (B1); pemisah hairline horizontal saja
  (B2); header kolom micro-label uppercase (B3); data kosong = em-dash (B4).
- H1: TANPA ellipsis/potongan pada data primer; tanpa wrap mid-token; token
  mono (sha8, timestamp ISO) satu baris utuh atau tidak ditampilkan.
- H2: viewport 1440×900 TERISI sampai tepi bawah; void >5% = cacat; sebutkan
  jumlah baris eksplisit per tabel.
- H3: chrome (topbar ≤5 segmen satu baris, status bar satu baris atau tidak
  ada, tombol aksi) utuh 100% dalam viewport.
- H5: prompt menyebut field wajib satu per satu.
- KPI = stack 4 lapis (E1); primary = tint+border+teks aksen (E2); sparkline
  stroke-only (E4); status chip = tint 10–12% + teks saturated (D3).

---

## S1 — obsidian-minimal

- **Arah**: gelap hampir-hitam, sangat tenang, satu aksen indigo; "Linear gelap
  untuk knowledge ops". Arah baseline yang dibawa ke level lebih disiplin.
- **Palet**: base `#0B0D11` · sidebar `#0E1116` · card `#12161D` · raised
  `#161B24` · hairline `rgba(255,255,255,0.07)` · teks primer `#E6E9EF` ·
  sekunder `#9AA3B2` · aksen `#6366F1` (indigo) · ok `#34D399` warn `#FBBF24`
  danger `#F87171`.
- **Tipografi**: Inter (UI) + JetBrains Mono (data); skala C2 (11/13/14/16/24).
- **Densitas**: standard (row 52px) — B1.
- **Mood**: sunyi, presisi, dewasa.
- **Komponen khas**: nav aktif pill tint indigo 10% (A2); KPI sparkline
  stroke-only indigo (E4); footer status bar mono satu baris (E7).
- **Sitasi pola**: A1–A4, B1–B5, C1–C3, D1–D4, E1–E7 (ref-45, ref-04, ref-52).

## S2 — terminal-brutalist

- **Arah**: konsol CRT modern — hitam murni, hijau fosfor, SEMUA teks mono,
  radius ~0, border 1px tegas. "Terminal sebagai antarmuka penuh".
- **Palet**: base `#000000` · panel `#050805` · hairline `#1A2E1A` · teks
  primer `#C9F7D4` · sekunder `#5F8F68` · aksen `#33FF66` (fosfor) · ok
  `#33FF66` dipakai juga utk ok-state (dot + label teks membedakan, N3) · warn
  `#FFD23F` · danger `#FF4D4D`.
- **Tipografi**: JetBrains Mono untuk SEMUA (termasuk judul); judul hanya lebih
  besar/lebih tebal; tracking lebar untuk header seksi.
- **Densitas**: dense (row 36–40px) — B1-dense.
- **Mood**: ops room, teknis, tanpa dekorasi.
- **Komponen khas**: prompt-line topbar `kyc://overview _`; panel dengan border
  1px hijau-redup + judul seksi di border (fieldset-style); status bar mono
  uppercase (E7 diperas jadi identitas utama); sparkline ASCII-look stroke 1px.
- **Sitasi pola**: E7 (ref-52 status bar), B1-dense, B3, C1 (diinvertasi: mono
  = default), D1, I1 (ikon outline stroke 2px hijau). Anti: tanpa radius besar
  (A4 diganti radius 0–2px sebagai keputusan arah).

## S3 — swiss-grid

- **Arah**: TERANG — International Typographic Style; grid kaku, banyak ruang
  putih, satu aksen merah, angka besar. Satu-satunya arah light-strict.
- **Palet**: base `#FAFAF8` · card `#FFFFFF` · hairline `#E4E4E0` · teks
  primer `#111111` · sekunder `#6B6B66` · aksen `#E03131` (merah swiss —
  dicadangkan interaktif; status ok `#2F9E6E` warn `#B8860B` danger tetap
  `#C92A2A` namun beda teks/ikon, N3).
- **Tipografi**: Inter/Helvetica Now; display KPI 40–56px weight 650 tracking
  −2% (Q3); mono = IBM Plex Mono untuk data.
- **Densitas**: comfortable (row 56–65px) — B1-comfortable; whitespace besar
  adalah konten.
- **Mood**: museum, cetak, otoritatif.
- **Komponen khas**: garis pemisah hitam 2px struktural; nomor seksi besar
  ("01"); sidebar diganti rail kiri tipis dengan label vertikal; KPI tanpa
  kartu — angka raksasa di atas garis.
- **Sitasi pola**: Q1–Q5 (ref-96), A3 grid 4px, B2 hairline, C3, D1 (aksesn
  tunggal), P5 (ref-93 spacing tokens). Catatan: shadow DILARANG juga di sini.

## S4 — glass-console

- **Arah**: navy gelap dengan panel translusen "frosted" (alpha + border
  cahaya), aksen cyan. Depth via translucency, BUKAN shadow/gradasi.
- **Palet**: base `#070B16` · panel `rgba(148,178,255,0.05)` + border
  `rgba(148,178,255,0.14)` · raised `rgba(148,178,255,0.09)` · teks primer
  `#E3EBFF` · sekunder `#8FA3CC` · aksen `#22D3EE` (cyan) · ok `#34D399` warn
  `#FBBF24` danger `#FB7185`.
- **Tipografi**: Inter + JetBrains Mono; label seksi uppercase tracking
  +0.12em.
- **Densitas**: standard (52px).
- **Mood**: mission control malam hari, dingin, lapis kaca.
- **Komponen khas**: panel kaca dengan border atas 1px lebih terang (cahaya
  atas); node graph dengan glow cyan halus pada edge aktif (E6); chip status
  tint cyan.
- **Sitasi pola**: D2 (diadaptasi: depth = alpha steps, tetap tanpa shadow),
  E6 (ref-16 graph), A2, D3, I4. Larangan gradasi TETAP berlaku — translucency
  flat saja.

## S5 — enterprise-clean

- **Arah**: SaaS enterprise terang-kebiruan (Stripe/Linear light); paling
  konvensional, paling "aman". Kontras tajam, abu kebiruan.
- **Palet**: base `#F6F8FB` · card `#FFFFFF` · hairline `#E3E8F0` · teks
  primer `#0F1B2D` · sekunder `#5B6B82` · aksen `#2563EB` (biru) · ok
  `#16A34A` warn `#D97706` danger `#DC2626`.
- **Tipografi**: Inter + Roboto Mono; skala C2; judul halaman 18px/600.
- **Densitas**: standard→comfortable (52–56px).
- **Mood**: rapi, bisa dijual ke CIO, tanpa kejutan.
- **Komponen khas**: kartu putih border 1px `#E3E8F0` radius 12; tab bar
  underline aksen; tombol primary SOLID biru (penyimpangan E2 yang disengaja
  untuk arah light — primary solid `#2563EB` teks putih, ghost border abu);
  badge count biru.
- **Sitasi pola**: A1–A3, B2/B3/B5, E8 (ref-25 stepper), L1–L4 (ref-72 form),
  N1 kontras ≥4.5:1 (terang memaksa disiplin rasio).

## S6 — neon-ops

- **Arah**: SOC/NOC gelap pekat, aksen violet elektrik; data padat, grid
  dot-canvas, indikator live. Paling "cinematic" tapi tetap disiplin D1.
- **Palet**: base `#05070D` · panel `#0A0E18` · raised `#0E1424` · hairline
  `rgba(139,92,246,0.12)` · teks primer `#E8E6F5` · sekunder `#8B86A8` · aksen
  `#8B5CF6` (violet) · ok `#22C55E` warn `#F59E0B` danger `#EF4444`.
- **Tipografi**: Space Grotesk (judul/KPI) + Inter (body) + JetBrains Mono
  (data) — tiga famili by-design, peran ketat.
- **Densitas**: dense (36–40px); info per px tertinggi setelah S8.
- **Mood**: ruang kendali serangan siber, waspada.
- **Komponen khas**: dot-grid canvas (E6) di SEMUA panel besar; "LIVE" dot
  berdenyut; timeline event dengan rail vertikal; gauge radial mono.
- **Sitasi pola**: E6 (ref-16, ref-54), B1-dense, E5 banner (ref-04), D4 nilai
  netral-abu, O3 spinner arc tunggal.

## S7 — paper-dark

- **Arah**: gelap HANGAT seperti kertas tua/lampu baca; serif display +
  sans UI; aksen amber-emas. Editorial, tenang, "perpustakaan digital".
- **Palet**: base `#141110` · panel `#1A1713` · raised `#211D17` · hairline
  `rgba(217,164,65,0.10)` · teks primer `#EDE4D3` · sekunder `#A3947D` · aksen
  `#D9A441` (emas tua) · ok `#7FB069` warn `#E0A458` danger `#D16666`
  (semantik di-desaturasi hangat agar koheren tapi tetap terbaca, N1 ≥4.5:1).
- **Tipografi**: Fraunces/Georgia serif untuk judul halaman & angka KPI besar;
  Inter untuk body/label; JetBrains Mono untuk data (kontras hangat-dingin
  disengaja, C1 tetap).
- **Densitas**: comfortable (56–65px), breathing room luas.
- **Mood**: arsip, jurnal, kerajinan.
- **Komponen khas**: ornamen garis ganda tipis (rule ganda 1px+1px) di bawah
  judul; nomor bab romawi kecil; kutipan provenance italic serif; chip status
  border emas redup.
- **Sitasi pola**: C1/C2 (ref-04 dua famili, diperluas tiga peran), B1-
  comfortable (ref-34), A2, D1, E1 (KPI serif 4 lapis).

## S8 — data-dense

- **Arah**: terminal finansial/Bloomberg — densitas maksimum yang masih sah;
  tabel di mana-mana, type kecil tajam, satu aksen cyan-hijau.
- **Palet**: base `#0E1116` · panel `#12161C` · hairline `#1E2630` · teks
  primer `#D8DEE6` · sekunder `#7C8794` · aksen `#2DD4BF` (teal) · ok
  `#4ADE80` warn `#FACC15` danger `#F87171`.
- **Tipografi**: Inter 11–13px dominan + JetBrains Mono 11–12px untuk data;
  KPI 20–24px (bukan raksasa — ruang untuk data lebih penting).
- **Densitas**: dense EKSTREM tapi sah (row 34–38px, B1-dense batas bawah);
  jumlah baris eksplisit minimal 18–22 per tabel (H2).
- **Mood**: trading desk, semua terlihat, nol hiasan.
- **Komponen khas**: multi-panel grid 3 kolom; setiap panel = mini-tabel dengan
  header micro-label; kolom angka tabular right-aligned KHUSUS style ini
  (penyimpangan B3 yang disengaja, konvensi terminal finansial); delta
  berwarna naik/turun (D4).
- **Sitasi pola**: B1-dense (ref-45 risk table), B3, B5 toolbar, D4 (ref-52),
  E4 sparkline stroke 1.5px, H2 (baris ke fold).

## S9 — monochrome-type

- **Arah**: nol kroma — hitam/putih/abu murni; hierarki 100% via tipografi,
  berat, dan garis. Status via BENTUK (dot/diamond/square) + teks, bukan warna
  (N3 ekstrem).
- **Palet**: base `#FFFFFF` · panel `#FAFAFA` · hairline `#0A0A0A` @100% untuk
  garis struktur 1px, `#D4D4D4` untuk hairline dalam · teks primer `#0A0A0A` ·
  sekunder `#525252` · "aksen" = `#0A0A0A` solid (inversi: elemen aktif =
  isi hitam, non-aktif = outline) · status: ok=● + "OK", warn=◐ + "WARN",
  danger=■ + "FAIL" (bentuk+kata, N3).
- **Tipografi**: satu famili (Inter) dimainkan penuh: Display 48–72px/700,
  judul 20/650, body 13/400, micro 10 uppercase tracking +0.14em; mono = JetBrains
  Mono untuk data (satu-satunya famili kedua, C1).
- **Densitas**: standard; whitespace agresif.
- **Mood**: galeri, manifesto, tegas.
- **Komponen khas**: blok hitam solid sebagai "kartu aktif"; garis 2–3px hitam
  sebagai struktur; tabel tanpa zebra, hanya rule hitam; angka KPI 64px.
- **Sitasi pola**: Q1–Q5 (hierarki murni tipe), N3 (status bukan warna),
  B2 hairline, C3, I1 (ikon outline hitam 2px). Catatan: D1 diadaptasi —
  "satu aksen" = satu tinta.

## S10 — aurora-soft

- **Arah**: slate gelap lembut dengan aksen teal "aurora"; radius paling
  membulat (16–20px), densitas paling lega, paling ramah. Ujung lembut dari
  spektrum.
- **Palet**: base `#0F141E` · panel `#151C2A` · raised `#1B2434` · hairline
  `rgba(255,255,255,0.06)` · teks primer `#E7ECF5` · sekunder `#93A0B8` ·
  aksen `#2DD4BF` (teal) · ok `#4ADE80` warn `#FCD34D` danger `#F87171`.
- **Tipografi**: Inter (rounded feel via weight rendah) + JetBrains Mono;
  judul 17px/550 (lebih lembut), KPI 26px/600.
- **Densitas**: comfortable (56–65px) — paling lega; padding 32px.
- **Mood**: tenang, ramah, "produk konsumen untuk tim platform".
- **Komponen khas**: kartu radius 18–20px dengan tint panel lembut; toggle &
  segmented control besar (N4 40–44px); empty state ilustrasi garis tipis teal
  ≤15% panel (K1); progress bar rounded-full.
- **Sitasi pola**: A4 radius bertingkat (ref-54), K1–K6 empty states (ref-65,
  ref-70), N4 target besar, D3 chip tint, B1-comfortable.

---

## Peta perbedaan (bukti bukan variasi tipis)

| Sumbu | S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 | S9 | S10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Tema | gelap | hitam | TERANG | navy kaca | terang | gelap | gelap hangat | gelap | putih | gelap lembut |
| Aksen | indigo | fosfor | merah | cyan | biru | violet | emas | teal | hitam | teal |
| Font khas | Inter | full-mono | Helvetica+raksasa | Inter | Inter | 3 famili | serif+sans | micro 11px | 1 famili | Inter lembut |
| Densitas | std | dense | comfort | std | std | dense | comfort | ultra-dense | std | comfort |
| Radius | 8–16 | 0–2 | 2–6 | 10–14 | 12 | 8–12 | 4–8 | 4–8 | 0–4 | 16–20 |
| Mood | sunyi | ops-room | museum | kaca | CIO | SOC | arsip | trading | manifesto | ramah |
