# PATTERNS — Pelajaran dari 55 Referensi Web UI Nyata (Dribbble, terverifikasi vision)

Sumber: 88 shot Dribbble diambil dari 12 query pencarian (dark-dashboard,
activity-log-dark-ui, network-graph-dark-dashboard, analytics-metrics-dark-dashboard,
search-interface-dark-ui, settings-dark-ui, admin-panel-dark, table-ui-dark,
wizard-flow-ui, node-graph-ui, monitoring-dashboard-dark, log-viewer-ui).
**55 diterima** (semua URL `cdn.dribbble.com/userupload/…`, diverifikasi satu per satu
via vision bahwa itu web UI nyata — flat, terbaca, bukan avatar/logo/device-mockup/
ilustrasi), **33 ditolak** dengan alasan tercatat. Gambar: `/root/.hermes/cache/scratch/design-refs/refs/`,
manifes: `MANIFEST.json` (ref → judul shot, URL sumber, deskripsi).

HAK CIPTA: pola di bawah dipelajari sebagai *prinsip* (grid, spacing, disiplin warna,
hierarki). Tidak ada desain yang disalin 1:1 — penerapan ke layar Know Your Context
memakai data, palet (#0B0D11), dan komponen milik kita sendiri.

Sitasi memakai id ref (mis. `ref-52`); yang dikutip paling dalam: ref-52 (AI model
leaderboard, tabel mono), ref-54 (SOC dashboard), ref-04 (ML observability "Orbit"),
ref-34 (orders admin gelap), ref-45 (RevenuePulse overview gelap), ref-16 (TWINIZE
network graph).

---

## A. SHELL & LAYOUT

**A1. Shell dua zona: sidebar kiri tetap + topbar + konten.**
Hampir semua dashboard memakai pola sama: sidebar navigasi kiri (fixed), topbar
tipis (search + aksi + user), konten utama. Lebar sidebar terukur: **232–272px**
untuk nav ber-label (ref-34: 272px; ref-04: 232px) atau **56–132px** untuk icon rail
murni (ref-16: 56px; ref-54: 132px). Sidebar punya hairline kanan 1px, bukan shadow.
Topbar berisi: judul/breadcrumb kiri, search center/kiri dengan hint `⌘K`, aksi kanan
(bell dengan badge dot merah, avatar+user card). (ref-04, ref-34, ref-45, ref-52,
ref-55)

**A2. Sidebar dikelompokkan dengan label seksi uppercase kecil.**
Nav di-group di bawah label seksi ~10px uppercase, tracking +0.08em, warna muted
(ref-04: MONITOR/MODELS/PLATFORM; ref-45: ANALYTICS/ENGAGEMENT/OPERATIONS).
Item aktif = pill/rounded-rect ber-tint aksen (~8–12% alpha) + teks aksen — bukan
garis, bukan bold saja. Badge notifikasi = pill/circle 16–20px merah-tint dengan
angka 10–11px. Footer sidebar = user card (avatar 28–32px + nama 13px + email 11px).
(ref-04, ref-34, ref-45, ref-58)

**A3. Margin halaman & gutter konsisten.**
Page padding 24–32px (ref-34: 24px; ref-04: 32px; ref-54: 28px). Gutter antar kartu
**14–16px** dan itu ketat diulang (ref-54: 16px di SEMUA sisi; ref-45: 14px).
Semua ukuran jatuh pada grid **4px base** (4/8/12/16/24/28/32). (ref-45, ref-54, ref-34)

**A4. Radius bertingkat, satu skala.**
Shell/kartu besar 16–20px (ref-54: 18–20; ref-45: 10–12 untuk kartu dalam shell 16),
kontrol (button/input/tab) 6–8px, chip/pill kecil 4–6px atau full-round, bar 3px.
Tidak ada radius acak — maksimal 3–4 nilai radius per desain. (ref-52, ref-54, ref-45)

## B. DENSITAS & TABEL

**B1. Tiga kelas densitas tabel yang sah.**
- *Comfortable*: row **65px** (ref-34, admin orders) — avatar + 2-baris sel.
- *Standard*: row **52px** (ref-04, model health) — 1 baris + sparkline.
- *Dense*: row **36–40px** (ref-45 risk table; ref-52 leaderboard 80px tapi 2-baris
  kaya konten, setara 40px/baris). Header row lebih pendek dari body: 36–50px.
Pilih SATU kelas per tabel; jangan campur.

**B2. Pemisah tabel = hairline horizontal saja.**
Tanpa garis vertikal, tanpa border per sel. Zebra striping opsional dan sangat halus
(white @2–3%, ref-04). Tabel dibungkus container radius ~10px dengan 1px border.
(ref-34, ref-04, ref-45)

**B3. Header kolom = micro-label.**
10–12px, weight 500–600, **UPPERCASE + tracking +0.06–0.1em**, warna muted/tertiary.
Header rata-kiri sejajar isi sel — termasuk kolom angka (left-aligned adalah pilihan
dominan; right-align angka justru langka di referensi gelap modern). (ref-52, ref-34,
ref-04, ref-45)

**B4. Baris state khusus diekspresikan lewat tint + border kiri.**
Baris "alert/leader" = wash tint aksen 8–12% alpha full-width + aksen 2–3px solid di
tepi kiri + status dot — TANPA mengubah border luar atau elevasi (ref-04 alert row;
ref-52 leader row). Kolom data kosong = em-dash `–` muted, bukan string "null"/"N/A"
(ref-52).

**B5. Toolbar tabel: kiri search ghost, kanan sort/filter tebal-teks.**
Search = field ghost/borderless dengan ikon 16px (ref-34) atau input h36 r8 dengan
chip kbd (ref-45). Sort/filter = text-button dropdown 13px/500 + chevron, bukan
select penuh. Pagination split: meta "1-10 of 3452" kiri, rows-per-page chip +
prev/next 32×32 kanan. (ref-34, ref-45)

## C. TIPOGRAFI

**C1. Aturan dua famili: sans untuk manusia, mono untuk mesin.**
Pola paling konsisten di referensi teknis: nama/judul/deskripsi (authored) = sans
(Inter-class); ID, hash, versi, timestamp, latensi, angka metrik (machine) = mono
(JetBrains Mono-class). ref-52 bahkan memakai mono untuk SEMUA label operasional dan
sans hanya untuk nama model. ref-04: "anything machine-generated or measured is mono;
anything human/authored is sans". (ref-52, ref-04; kontras: ref-34/16 tanpa mono —
terasa kurang "teknis")

**C2. Skala tipe disiplin.**
Skala yang berulang: 10–11 (micro-label/badge) · 12–13 (body/sekunder) · 14 (body
utama/nav) · 15–18 (judul panel/halaman) · 20–28 (nilai KPI) · 64–72 (gauge hero,
jarang). Weight: 400 body, 500–600 label/nilai, 650–700 hanya untuk angka hero dan
judul halaman. Numeral selalu tabular/lining. (ref-04, ref-45, ref-52, ref-54)

**C3. Uppercase hanya untuk 3 hal.**
Label seksi sidebar, header kolom tabel, status pill micro — selalu dengan tracking
positif. Semua teks lain sentence case. Judul halaman TIDAK uppercase. (ref-04,
ref-45, ref-54, ref-34)

## D. WARNA

**D1. Satu aksen, semantik dicadangkan.**
Setiap referensi kuat punya TEPAT satu warna aksen yang membawa semua makna
interaktif/aktif (indigo ref-34/45, cyan ref-04, teal ref-54, green ref-52) —
dipakai di: nav aktif, link, primary action, seri data utama. Merah/amber/hijau
DIRESERVASI ketat untuk semantik status (danger/warning/ok) dan tidak pernah dekoratif.
Aksen menempati ~5% area pixel. (ref-04, ref-34, ref-45, ref-52, ref-54)

**D2. Kedalaman tanpa shadow.**
Hierarki permukaan dibangun dari langkah bg (base → sidebar → card → raised) +
hairline 1px rgba(255,255,255,.06–.08). Drop shadow hampir nol di dalam app
(ref-04 eksplisit: "no shadows; depth via bg steps + hairlines"). (ref-04, ref-45,
ref-52)

**D3. Status/chip = tint 10–12% + teks saturated + (opsional) dot.**
Pill status: bg = warna semantik @10–15% alpha, teks = warna semantik penuh,
radius 4–8px, kadang border 30% alpha atau dot 5–6px. Empat varian cukup
(ok/info/warn/danger). (ref-34, ref-04, ref-45, ref-54)

**D4. Nilai netral-abu, warna hanya saat bermakna.**
Angka biasa = text secondary; angka ber-warna HANYA jika menyandang status
(SLA breach cyan di ref-04, leader green di ref-52, delta up/down green/red).
Sparkline netral = gray/indigo; berwarna hanya untuk seri yang flagged.
(ref-04, ref-52, ref-45)

## E. KOMPONEN

**E1. KPI card = stack 4 lapis.**
label (10–13px, kadang uppercase) → nilai (20–28px/650–700 tabular) → delta
(panah + % berwarna + teks baseline abu "vs …") → sparkline/chart mini footer.
Variasi sparkline bebas (area/bars/dots/gauge) tapi satu kartu satu fokus.
(ref-45, ref-04, ref-27, ref-58)

**E2. Tombol primary gelap = tint + border + teks aksen, bukan solid fill.**
Primary: bg aksen @10% + border aksen @40% + teks aksen (ref-52 "RUN EVALUATION").
Ghost: border rgba(255,255,255,.12) + teks sekunder. Tinggi kontrol 34–40px, radius
6–8px, padding 9–16px. (ref-52, ref-04)

**E3. Filter pill = surface raised + label mono/teks + chevron.**
h~34px, r6px, border 1px halus, trailing `▾` tertiary. Tanggal range selalu pill
("AUG 1 – AUG 13, 2026 ▾"). (ref-52, ref-45)

**E4. Sparkline: stroke-only.**
1.5px stroke, no fill, no axes, no dots, ~64–120×20–28px. Area-fill hanya untuk
chart besar, bukan sparkline. (ref-52, ref-04, ref-45)

**E5. Alert banner full-width.**
Tint aksen + border aksen 30% + **aksen bar 2–3px di tepi kiri** + ikon + kalimat
(entitas offending dalam mono aksen) + ghost button kanan. h ~46px. (ref-04)

**E6. Graph canvas.**
Dot-grid bg (2px dots, pitch 20–24px, sangat redup) di atas base; node = disc filled
dengan label di dalam/dekat; edge dua tingkat (aktif ber-aksen ~1.5–2.5px + glow
halus vs konteks redup ~1px); label relasi = chip pill kecil di midpoint edge;
legend = bar floating bawah; kontrol zoom = pill segmented vertikal di tepi kanan.
(ref-16, ref-54, ref-44)

**E7. Footer status bar.**
Baris mono uppercase kecil berisi fakta operasional dipisah `·` ("42 SUITES ·
128,400 SAMPLES · LAST RUN 12 MIN AGO") + status dot kanan — pola terminal/console
yang sangat cocok untuk layar teknis. (ref-52)

**E8. Wizard/stepper.**
Stepper 3-langkah: lingkaran/nomor + label, langkah aktif ber-aksen, konektor garis
tipis; panel kanan = ringkasan live (summary) yang ter-update; footer = Cancel ghost +
Next/Continue primary. Radio-card untuk pilihan bercabang (border aksen + tint saat
terpilih). (ref-08, ref-14, ref-25, ref-33, ref-35)

## F. ANTI-PATTERN (dari 33 shot yang DITOLAK — jangan ditiru)

- Presentasi 3D miring / device frame / lifestyle backdrop — merusak keterbacaan
  pola (21 dari 33 penolakan).
- Kolase multi-screen satu gambar — tidak bisa dipelajari grid-nya (6 penolakan).
- Logo/ilustrasi dominan (4 penolakan), blank/unreadable (1), foto staged (1).
- Pelajaran positif: referensi yang lolos SEMUA flat head-on dengan UI utuh terbaca —
  standar yang sama kita tuntut dari screen Stitch (inilah mengapa PASS kita mensyaratkan
  komponen terlihat, dan mengapa overlap/ellipsis = FAIL).

## G. PETA PENERAPAN → layar Know Your Context

| Layar | Pola utama yang diterapkan | Ref kunci |
|---|---|---|
| 01 Overview | A1+A3, E1 KPI strip, D1 aksen tunggal | ref-45, ref-04 |
| 02 Activity Log | B1-dense/B3/B4, C1 mono utk sha8/timestamp, B5 toolbar, E7 footer | ref-52, ref-34, ref-04 |
| 03 Graph Explorer | E6 canvas+legend+zoom, A1 inspector | ref-16, ref-54, ref-44 |
| 04 Search+Query | B5 search ghost + kbd, B3 tabel hasil | ref-34, ref-18 |
| 05 Metrics | E1 KPI, E4 sparkline, B2 tabel bench | ref-45, ref-04 |
| 06 Health | E5 banner, D3 status chips, B2 daftar checks | ref-04, ref-54 |
| 07 Analytics | B3 leaderboard mono, D4 nilai netral | ref-52, ref-45 |
| 08 Audit Ledger | B1 tabel hash mono, E7 status bar, B4 verify state | ref-52, ref-04 |
| 09 Proposals | B4 baris state, D3 chips, E7 | ref-34, ref-04 |
| 10 Debt Queue | B3/B5 tabel prioritas, D4 severity | ref-34, ref-45 |
| 11 System | A1 4-kuadran, B2 tabel kv, D2 hairline depth, C1 mono utk nilai | ref-55, ref-45, ref-52 |
| 12 Setup | E8 stepper, E2 tombol, B2 manifest | ref-25, ref-35 |
