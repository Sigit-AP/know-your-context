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

---

## H. ATURAN BARU — hasil studi ulang ref terhadap 3 mode gagal dominan (SUB-141)

Audit vision 12 layar final (butir 7-checklist skill professional-ui-design)
menemukan 25 butir FAIL dari 84: **B2 truncation/wrap mid-token = 10 layar**,
**B6 dead space = 8 layar**, **B7 viewport-fit = 5 layar**, B3 = 1, B1 = 1.
Studi ulang referensi (ref-04, ref-18, ref-24, ref-34, ref-41, ref-45, ref-52,
ref-54 — diverifikasi vision ulang khusus untuk 3 mode ini) menghasilkan aturan
berikut. Inilah kunci prompt untuk Fase 2 (regenerasi).

**H1. Larangan ellipsis mutlak pada data primer — strateginya UKURAN, bukan potongan.**
Di 8 referensi yang dipelajari ulang, TIDAK ADA SATU PUN ellipsis pada data primer:
domain 30 karakter `dbcsskavrpwmtlexouuo.supabase.co` ditampilkan utuh (ref-18);
nama produk panjang "eBook: Designing interfaces" utuh (ref-34); IP/host penuh
`185.220.101.8`, `Finance-Laptop-17` (ref-54); `external / frontier-class` utuh
(ref-52). Aturan turunan:
- Kolom HARUS cukup lebar untuk token terpanjang dalam datanya; jika tidak muat,
  kurangi jumlah kolom / perkecil kolom lain / singkat data di sisi konten
  (mis. `9f2c1ab4` 8-hex tetap utuh) — JANGAN pernah `…` dan JANGAN wrap di
  tengah token.
- Sel satu baris selalu; sel dua baris hanya by-design (baris 1 = nama, baris 2 =
  metadata muted, dipisah `·`) — bukan hasil wrap (ref-52, ref-41).
- Angka panjang diformat (`128,400`), bukan dipotong (ref-52).
- Hash/identifier mono: tampilkan utuh pada lebar yang dialokasikan, atau tidak
  usah ditampilkan sama sekali. "…" palsu pada digest = FAIL.
  (Catatan: ref-24 tidak menampilkan hash sama sekali di tabel utamanya.)

**H2. Viewport harus TERISI sampai tepi bawah — dead space >5% tinggi konten = cacat.**
Semua referensi penuh secara vertikal. Dua pola sah untuk area bawah:
- (a) Tabel/list sebagai komponen TERAKHIR yang berjalan sampai fold; baris boleh
  terpotong di tepi bawah sebagai sinyal "berlanjut via scroll" (ref-24: baris
  ke-6 subnet terpotong; ref-04: baris sentiment-v2 separuh; ref-41: baris ke-6
  transaksi menempel tepi bawah).
- (b) Grid kartu yang baris terakhirnya berakhir dengan padding bersih sebelum
  tepi viewport (ref-54: 3 kartu bawah utuh + link "Open hunts ›"; ref-45: tabel
  Accounts at Risk berakhir rapat).
Yang DILARANG: pita kosong gelap di dalam area konten (semua layar kami yang
gagal B6 punya void 20–45% tinggi), dan footer yang di-pin ke dasar kartu
sehingga isi kartu renggang (pelajaran dari 11-system-C-R1).
Konsekuensi prompt: sebutkan jumlah baris/item yang HARUS terlihat
(mis. "render 18 baris log penuh") dan larang area kosong.

**H3. Disiplin fold: chrome tidak boleh terpotong; konten bergulir boleh.**
Yang gagal B7 pada layar kami bukan "konten di bawah fold" (itu sah, lihat H2a)
melainkan ELEmen STRUKTURAL yang terpotong setengah: status bar terpotong
mid-glyph (01, 04), baris aksi inspector menempel di tepi bawah (03), kontrol
ter-clip tepi atas viewport (06), kolom tabel terpotong panel kanan (09).
Aturan:
- Topbar, status bar, tombol aksi, dan header kolom = chrome: harus utuh 100%
  dalam viewport, SATU baris (topbar multi-baris = tanda overflow = FAIL).
- Status bar opsional; jika dipakai, satu baris mono pendek yang dijamin muat
  (ref-52: `42 SUITES · 128,400 SAMPLES · …` satu baris). Lebih baik tanpa
  status bar daripada status bar yang wrap/terpotong.
- Inspector/panel kanan: konten di dalamnya boleh scroll, tetapi header panel +
  minimal satu blok konten pertama + aksi utama harus terlihat utuh.
- Batasi jumlah entitas di topbar: maksimal 4–5 segmen status; selebihnya pindah
  ke panel. (Semua wrap topbar kami terjadi pada ≥6 segmen.)

**H4. Warna semantik tidak boleh dipakai sebagai warna identitas.**
Kegagalan B3 pada 02-activity-A-R1: agent `sub-139` diberi amber (= WARN) dan
`operator` hijau (= INFO) sehingga warna agent tertukar-makna dengan level log.
Aturan: entitas kategorikal (agent, domain P1–P7) memakai warna kategori yang
DIBEDAKAN dari tiga warna semantik (hijau/amber/merah), atau cukup satu warna
aksen + bentuk/dot. Merah/amber/hijau eksklusif untuk status (D1 tetap berlaku).
Emoji dilarang total — termasuk glyph dekoratif seperti ⏳ di header panel.

**H5. Kelengkapan spec diverifikasi per-field, bukan per-panel.**
Kegagalan B1 pada 11-system-C-R1 bukan panel hilang melainkan field hilang
(page_count, freelist_pages tidak ada di footer STORE; routes tidak memisahkan
access vs class). Prompt Fase 2 harus menyebut field wajib SATU PER SATU dan
melarang penggabungan field.


---

## Fase 2 (SUB-142): 10 domain baru — 69 kandidat, 48 diterima (ref-56..103), 21 ditolak
Query baru: icon-grid-line-icons, button-states-ui, interaction-states-hover,
empty-state-dashboard-ui, form-validation-ui, command-palette-ui, breadcrumb-ui,
a11y-accessibility-ui-components, ui-micro-interaction, loading-skeleton-ui,
spinner-loader-ui-states, design-tokens-color-system, color-palette-ui-design-system,
primitive-color-palette-design-system, spacing-tokens-design-system,
typography-scale-design-system, type-scale-ui, error-state-ui-design.
Semua penerimaan diverifikasi vision satu per satu; penolakan + alasan:
`refs/REJECTED-phase2.json`. Catatan kejujuran: aturan berbasis **ms/durasi** tidak
bisa diukur dari gambar statis — ditandai `[konvensi]` dan tidak dihitung sebagai
aturan tersitasi; aturan spasial/struktural semua tersitasi ref.

## I. SISTEM IKON (ref-56..60)

**I1. Satu gaya per set — jangan campur outline dan fill.**
ref-56: 35 glyph outline murni (stroke ≈1/12–1/14 bounding box, round caps) 7×5;
ref-60: 6 glyph solid-fill murni two-tone. Keduanya koheren karena TIDAK dicampur.
Untuk KYC: ikon UI = outline stroke 1.5–2px pada grid 24px; glyph status kecil
(dot/check) boleh filled. (ref-56, ref-57, ref-60)

**I2. Grid konstruksi 24 modul; stroke = clearance = 2 unit.**
ref-59 (spec dengan callout dimensi): grid ~24×24 modul, stroke ≈2 modul,
gap ≈2 modul, radius sudut luar ≈2 modul, terminal square/butt atau round —
konsisten. Artinya pada ikon 24px: stroke 2px, clearance minimal 2px, radius 2px.
(ref-59; selaras ref-57/ref-58: stroke ≈1:10 tinggi glyph, round caps)

**I3. Ukuran OPTIK seragam, bukan matematis.**
Semua glyph di-set dalam keyline cell equal-pitch (ref-57: 6 kolom ~211px;
ref-60: pitch 303×302px) dan dishift optik agar terasa sama besar — bounding box
135–165px pada canvas yang sama (ref-60). Aturan: ikon 16/20/24px dari skala
4px; optical correction ±1px untuk bentuk bulat/segitiga. (ref-56, ref-57, ref-60)

**I4. Metafora fungsional generik; warna maksimal 2.**
Ikon memakai metafora UI standar (home, gear, bell, trash, search, upload, share)
— bukan logo/merek (ref-57, ref-60). Palet ikon: 1 warna tinta + maksimal 1 aksen
untuk detail semantik (ref-60: near-white + periwinkle pada biru; ref-89: mono
putih/abu). Ikon tidak pernah memakai merah/amber/hijau kecuali status (D1/H4).
(ref-56, ref-60, ref-89)

## J. INTERAKSI & MICRO-INTERACTION (ref-61..64, 84)

**J1. Setiap kontrol interaktif mendefinisikan ≥4 state: default / hover /
focus / disabled (+ error untuk input).**
ref-63 mendokumentasikan 8 state untuk satu komponen (Default, Focused, Hover,
Disabled, Error, With-value, Valid, Warning); ref-64: 4 varian × 2 tema dengan
cursor hover eksplisit. Kontrol tanpa state terdefinisi = spec belum selesai.
(ref-63, ref-64)

**J2. Disabled ≠ opacity rendah semata.**
Pola referensi: fill abu netral (#F2F4F7-family pada terang; surface raised redup
pada gelap) + teks abu + tanpa border aksen; kursor `not-allowed`. Terbukti pada
tombol submit saat form error (ref-74, ref-76) dan chip filter disabled
(ref-81: chip "Images" redup). (ref-74, ref-76, ref-81)

**J3. Hover menambah sinyal, bukan warna baru.**
Hover = tint aksen +8–12% pada fill, atau border/ring aksen muncul — tanpa hue
baru (ref-63 focus ring biru; ref-77 hover crumb = pill highlight; ref-82 hover
menu = tint pink pada item destruktif). Focus WAJIB ring terlihat 2–4px,
kontras ≥3:1 vs latar (ref-84: dashed ring hitam ~4px dengan offset; WCAG
1.4.11). (ref-63, ref-77, ref-82, ref-84)

**J4. Aksi destruktif bertahap + selalu ada jalan undo.**
ref-62: hold-to-delete (afordansi "Hold to delete" di bawah tombol bahaya
merah Ø~260px pada skala presentasi); ref-82: toast "Customer deleted" dengan
aksi "↩ Undo 5s" + "Dismiss". Aturan: delete/destructive tidak pernah langsung
eksekusi tanpa konfirmasi ATAU undo window 5 detik. (ref-62, ref-82)

**J5. Feedback kopian/aksi instan = perubahan label di tempat.**
ref-61: pill "Copy" dengan ikon copy di dalam input URL — pola state berikutnya
(label→"Copied" + ikon check) terjadi di komponen yang sama, bukan toast terpisah.
Aturan: feedback aksi kecil (copy, save) maksimal 1 komponen yang berubah state,
durasi tampil 1.5–2s [konvensi]. (ref-61)

## K. EMPTY STATE & ONBOARDING (ref-65..70, 102)

**K1. Anatomi 4 lapis, urutan tetap.**
visual (ikon/ilustrasi kecil, ≤15% tinggi panel, kontras rendah) → heading 1 baris
(semibold) → body 1–2 baris abu (sebutkan penyebab) → CTA recovery.
(ref-65, ref-66, ref-67, ref-68, ref-102 — semua memakai urutan identik)

**K2. Empty ≠ error — CTA berbeda makna.**
Empty menawarkan langkah pertama ("Add user", "Start a campaign", "Przejdź do
tworzenia"); error menawarkan pemulihan ("Clear search", "Clear filters",
"Request access"). ref-67 memuat keduanya berdampingan (6 state unik: 3 empty,
403, registrasi, widget kosong). (ref-67, ref-65, ref-70)

**K3. No-result WAJIB menggema kueri + saran perbaikan.**
"We couldn't find anything for \"mri scheduling\"." (ref-68); "Try adjusting
your keywords, filters, or check for typos." (ref-69, ref-102). Kueri ditampilkan
ulang verbatim dalam body copy; saran berupa kata kerja (adjust/clear/browse).
(ref-68, ref-69, ref-102)

**K4. Maksimal 1 CTA primer + 1 sekunder ghost.**
ref-70: 1 pill primer putih "+ Add transactions manually" + opsi koneksi sebagai
kartu sekunder; ref-68: "Clear search" (aksen) + "Browse all procedures" (ghost).
Tidak ada empty state referensi dengan >2 CTA. (ref-70, ref-68, ref-65)

**K5. Empty state hidup DI DALAM konteks penuh, bukan halaman kosong.**
ref-65: nav lengkap + tab + 4 KPI card bernilai "0 zł" tetap dirender; ref-66:
5 KPI card berisi data + hanya panel chart yang empty. Nol adalah DATA
("0 zł", "156 companies" pada ref-87), bukan absennya UI. (ref-65, ref-66, ref-87)

**K6. Onboarding bertahap memakai langkah ternomor.**
ref-67 ("Steps to activate your account": daftar 3 langkah bernomor di sub-panel);
ref-85 (stepper "Étape 3 sur 5" teks + bar 5 segmen). Selaras E8. (ref-67, ref-85)

## L. FORM & VALIDASI (ref-71..76)

**L1. Label persisten di atas field; placeholder bukan label.**
ref-72/74/76: label 13–14px weight 500 di atas input; hint abu di bawah.
Anti-pattern terukur: ref-75 memakai placeholder-as-label (3 input tanpa label
persisten) — jangan ditiru untuk form data penting. (ref-72, ref-74, ref-75)

**L2. Error = 3 sinyal sekaligus: border merah + ikon + pesan spesifik.**
ref-74: label ikut merah + border merah ~2px + glow ring + pesan "Please enter
a valid name using letters only" tepat di bawah field; ref-73 (WCAG): error
memakai ikon ⊗ + teks "Entry invalid" + hint — eksplisit "don't rely on color
alone" (SC 1.4.1). Pesan menyebut ATURAN yang dilanggar, bukan "Invalid".
(ref-74, ref-76, ref-73, ref-72)

**L3. Submit ter-disabled sampai form valid.**
ref-74 + ref-76: saat error, tombol primer berubah abu (fill #F2F4F7-family,
teks abu) — validasi mencegah submit, bukan hanya menandai. (ref-74, ref-76)

**L4. Dimensi field: tinggi 40–44px, radius 6–12px.**
ref-72: input ~320×42–44px; textarea ~320×128px; OTP box ~80×80px; prefix/suffix
addon ("$", "http://", dropdown "US") menempel sebagai segmen terpisah border
hairline. (ref-72)

**L5. File upload: batas dinyatakan, error per-file inline.**
ref-71: dropzone dashed + hatch, teks "(4 MB max)", error = pill merah "Upload
failed" + ikon ⓧ di BARIS file + aksi "Retry" (ikon refresh) + "×" remove di
tempat — bukan toast global. (ref-71, ref-98)

## M. NAVIGASI: BREADCRUMB, TAB, COMMAND PALETTE (ref-77..82, 17)

**M1. Breadcrumb: separator "/" atau "›", halaman aktif non-link.**
ref-77 (spec): tinggi bar 28px, padding/gap 22px/14px, gap ikon-label 8px;
crumb aktif = teks lebih gelap + chip ikon; crumb lain = link dengan hover.
Separator konsisten satu glyph per sistem ("/" di ref-77, "›" di ref-28-reject
— pola tetap terbaca). (ref-77)

**M2. Breadcrumb bisa membawa scope-switcher.**
ref-77 varian dropdown: crumb dengan chevron ▾ membuka menu (search field +
5 item + current ter-highlight); chevron flip ▴ saat terbuka. Cocok untuk
switcher run/graph di topbar KYC. (ref-77)

**M3. Command palette: lebar 585–640px, radius 12–14px, satu kolom.**
ref-78 (~585×322px, r12), ref-79 (~600-640px, r14), ref-80 (~45% lebar canvas).
Input row 40–64px dengan ikon kaca + hint kbd invoke ("⌘K" / "⌘/") di kanan.
(ref-78, ref-79, ref-80, ref-17)

**M4. Hasil dikelompokkan; baris 38–48px; grup header uppercase kecil.**
ref-78: grup "Recent searches" + commands (6 baris ~38–40px); ref-79: RECENT /
ACTIONS header + "Clear" kanan (10 baris ~44–48px); ref-80: chip scope ber-count
("All 17, Procedures 6…") + grup RECENT/JUMP TO. Baris = ikon 16–18px + judul +
meta kanan (badge/kbd/chevron). (ref-78, ref-79, ref-80)

**M5. Seleksi = fill terang inset ~8px + hint kontekstual trailing.**
ref-78: baris terpilih fill slate terang inset 8px + muncul "Jump to…" di kanan;
ref-79: "Create new issue" ter-highlight + cursor; ref-80: baris terpilih tint
aksen + ikon tile terisi. (ref-78, ref-79, ref-80)

**M6. Footer kbd wajib + fuzzy-match di-highlight.**
Footer 1 baris: ↑↓ navigate · ↵ open · esc close (· ← parent · # tags) sebagai
chip kbd atau teks muted (ref-78, 79, 80, 81, 82). Substring kueri yang cocok
di-highlight tint aksen di dalam hasil (ref-82: "la" lavender). (ref-78..82)

## N. AKSESIBILITAS (ref-83, 84, 85, 73)

**N1. Kontras: teks ≥4.5:1, komponen/state ≥3:1.**
ref-84 (sitasi eksplisit WCAG 1.4.11 non-text contrast; ring fokus hitam ≈21:1);
ref-73 (4.5:1 teks, 3:1 antar-state, error text terukur 14.7:1); ref-92/90
(meng-grade tiap swatch AA/AAA — 3 swatch ditandai FAIL dan tidak dipakai untuk
teks). Aturan KYC: teks body pada #0B0D11 ≥4.5:1; border kontrol & indikator
≥3:1 vs latar tetangga. (ref-84, ref-73, ref-92)

**N2. Focus ring selalu terlihat: 2–4px, offset, kontras ≥3:1.**
ref-84: dashed outline hitam ~4px dengan offset di sekitar pill fokus;
ref-63: focus ring biru pada input. Di tema gelap KYC: ring aksen 2px +
offset 2px. (ref-84, ref-63)

**N3. Status tidak pernah warna-saja.**
ref-85: status step = ikon ✓ + kata ("Terminé/En cours/À venir"); ref-73: error
= ikon ⊗ + teks; ref-33: toast delete = ikon trash + teks. (ref-85, ref-73, ref-33)

**N4. Target pointer ≥24px minimum, 34–44px untuk aksi utama.**
Kontrol referensi: tombol 34–40px (E2), baris palette 38–48px (M4), toggle/CTA
besar 56–72px (ref-70, ref-62). Batas bawah WCAG 2.5.8 = 24px; KYC memakai
≥28px untuk chip terkecil, 34–40px tombol. (ref-70, ref-78, ref-84; angka 24px
= WCAG [konvensi standar], sisanya tersitasi)

**N5. Mode aksesibilitas adalah fitur, bukan afterthought.**
ref-83: 8 kartu kontrol (high-contrast, dyslexia-friendly font, large text,
icon labels, simplified layout, reading mode, focus highlight, color-friendly
palette) masing-masing dengan toggle ON/OFF ber-label teks. Untuk KYC: minimum
focus-highlight selalu-ON + ikon ber-label di nav (A2 sudah label). (ref-83)

**N6. Progress ganda: teks + visual.**
ref-85: "Étape 3 sur 5" (teks) + bar 5 segmen (visual). Stepper E8 KYC wajib
label teks langkah, bukan hanya titik/segmen. (ref-85)

## O. MOTION, LOADING & TRANSISI (ref-86, 87, 88, 89)

**O1. Loading = skeleton yang mencerminkan layout final.**
ref-87: tabel loading = 13–14 baris skeleton dengan placeholder per kolom
(checkbox outline 44px, avatar circle 52/84px, text bar 300–570×44px) — struktur
kolom PERSIS tabel final. Bukan spinner di tengah. (ref-87)

**O2. Skeleton flat satu warna, radius mengikuti komponen.**
ref-87: fill uniform ~#ECEEF1 (terang) — pada tema gelap KYC: white @4–6%;
radius 10–16px mengikuti komponen yang digantikan; animasi pulse opacity
[bentuk animasi: konvensi — yang tersitasi = bentuk & warna flat]. (ref-87)

**O3. Spinner hanya untuk aksi lokal <10s; satu gaya per aplikasi.**
ref-88: 3 varian (3-dot bounce, arc 260–280°, pulse konsentris); ref-89:
katalog 24 varian ~8 famili. Pelajaran seleksi: PILIH SATU (untuk KYC: arc
spinner stroke ≈1:8 diameter, aksen) dan pakai konsisten; jangan kebun spinner.
Untuk proses panjang (ingest/index): progress determinate (bar/segmented
"4 / 5" pada ref-42-reject — pola bar segmented tetap sah) atau skeleton. (ref-88,
ref-89)

**O4. Setiap perubahan state interaktif harus bisa direpresentasikan sebagai
pasangan frame statis.**
ref-86: toggle OFF (knob kiri + dot merah + ikon mute) vs ON (knob kanan + dot
hijau); scrubber 2023 vs 2024. Jika sebuah interaksi tidak bisa digambar
sebelum/sesudah, desainnya belum terdefinisi. (ref-86)

**O5. Durasi [konvensi — tidak tersitasi dari gambar statis]:**
micro-feedback 100–200ms, transisi panel/hover 150–250ms, skeleton pulse
1–1.5s loop. Angka ini standar industri; diverifikasi strukturnya via ref,
bukan durasinya. (struktur: ref-86, ref-87)

## P. WARNA & TOKEN SISTEM (ref-90, 91, 92, 93)

**P1. Skala 11 langkah per famili; famili wajib: neutral, brand, success,
warning, error.**
ref-92: 6 famili × 11 langkah (25..900 + White/Black anchor) = 66 swatch;
ref-90: 3 famili × 11 + anchor 0/1000 = 34 token. Setiap langkah bernomor
(25/50/100…900) sehingga bisa dirujuk ("primary-600"). (ref-92, ref-90)

**P2. Token dua lapis: primitive → semantic × state.**
ref-91: semantic `background-neutral-primary` → primitive `neutral/light-0`,
dengan baris terpisah untuk `-hover` dan `-pressed`, per tema Light/Dark
(12+ hex terbaca). KYC: token `surface-{base,raised}` / `content-{primary,
secondary}` / `accent-{default,hover,pressed}` di atas skala primitive. (ref-91)

**P3. Setiap token warna terdokumentasi dengan hex + grade kontras.**
ref-92: tiap swatch mencetak rasio (4.24 FAIL … 20.73 AAA) — swatch FAIL
ditandai eksplisit dan bukan kandidat teks; ref-90: badge AAA per swatch.
Aturan KYC: token tanpa rasio kontras terukur tidak masuk sistem. (ref-92, ref-90)

**P4. Dark theme = skala tersendiri, bukan invert.**
ref-91: kolom Dark memakai primitive `neutral/dark-0..200` yang berbeda dari
light; ref-56: ikon putih tetap terbaca di dua latar berbeda. (ref-91, ref-56)

**P5. Token spacing linear 4px, diberi nama.**
ref-93: s0..s18 = 0,4,8,12,16,20,24,28,32,36,40,44,48,52,56,60,64,68,72px
(1 unit = 4px = 0.25rem), divisualkan sebagai bar proporsional. Ini formalisasi
grid 4px (A3) menjadi token bernama. (ref-93)

## Q. SKALA TIPOGRAFI (ref-94, 95, 96)

**Q1. Penamaan semantik bertingkat, bukan angka lepas.**
ref-94: H1–H4 + Body 1/2 + Subtitle/Caption/Overline + Button/Link; ref-95:
t-shirt xs/sm/md; ref-96: Display lg/md · Heading 2xl..xs · Text 2xl..sm
(13+ step). KYC: Display/KPI · Heading · Body · Micro (label/caption) — C2
tetap berlaku, kini dengan nama step. (ref-94, ref-95, ref-96)

**Q2. Setiap step teranotasi: size px(rem) · line-height · tracking.**
ref-96: "Display lg 180px (11.25rem) / 188px / −5%"; ref-95: "size: 12px,
line-height: 140%, letter-spacing: 0, paragraph-spacing: 10px". Tanpa anotasi
lengkap, step bukan token. (ref-96, ref-95)

**Q3. Tracking negatif progresif untuk ukuran besar.**
ref-96: −5% (Display 180px) → −1.8% (H2xl 60px) → −0.5% (Text sm 12px);
uppercase micro justru tracking POSITIF +0.06–0.1em (C3, B3). (ref-96, ref-94)

**Q4. Rasio line-height turun saat ukuran naik.**
ref-96: Display ~104% (180/188), Heading ~108–133% (48/56, 24/32), Text
~137–140% (16/22, 14/20); ref-95: body 140%. Aturan KYC: KPI/display 100–110%,
judul 115–130%, body 140–150%, mono data 140%. (ref-96, ref-95)

**Q5. Base 16px = 1rem; weight terbatas 4 langkah.**
ref-96: "16px base", 4 weight (ExtraBold/Bold/SemiBold/Medium); C2 KYC:
400/500/600 + 650–700 hanya angka hero. (ref-96)

## R. ERROR & EDGE STATES (ref-97..103)

**R1. Sistem alert 4 varian semantik dengan anatomi tetap.**
ref-99: info/warning/success/error × 2 tema = 8 kartu, masing-masing: ikon
semantik + judul bold + subjudul inline + body 2–3 baris + timestamp + 2 link
aksi + dismiss ×; radius ~12px, tint semantik + border tonal 1px, TANPA shadow.
ref-100: struktur identik untuk toast gelap. (ref-99, ref-100)

**R2. Banner page-level untuk error kontekstual: 1 baris + recovery eksplisit.**
ref-97: banner pill salmon (#FDE8E8/teks #9B1C1C) selebar konten (~45px tinggi)
"We couldn't find some of your target accounts. Remove or edit them from
'Target accounts' in the 'More' menu." + × dismiss — pesan menyebut jalur
perbaikan persis. Selaras E5 (banner KYC: tint + aksen bar kiri 2–3px). (ref-97)

**R3. Error inline per-entitas dengan aksi di tempat.**
ref-98: baris file gagal = pill merah "Upload failed" + Retry + × dalam baris
yang sama; ref-101: badge ⚠2 + tooltip hover "2 Emails delivery failed" pada
baris transfer. Error tinggal SEDEKAT mungkin dengan entitasnya. (ref-98, ref-101)

**R4. Kegagalan parsial bulk = laporan rasio + baris gagal di-pin.**
ref-103: banner amber "43 of 47 adjustments posted. 4 need you." + garansi
integritas ("posted lines locked; stock unchanged until resolved") + 4 baris
gagal di-PIN ke atas tabel dengan tint pink + status spesifik per baris
("Blocked — Batch locked by open RMA", "Needs approval — Over ₹2,00,000
write-off limit") + CTA bertingkat (Resolve 4 lines / View the 43 posted /
Download error report) + bulk bar "47 selected · 4 need attention". Inilah pola
untuk batch ingest/proposal KYC. (ref-103)

**R5. Destructive action = undo window, bukan modal konfirmasi bila reversible.**
ref-33: toast "Customer deleted" + "↩ Undo 5s" + Dismiss. (ref-33, ref-62)

**R6. Edge data kosong di tabel/list: em-dash (B4); di panel: empty state K1;
di chart: pesan no-data dengan filter aktif disebut.**
ref-66: chart empty = "No data available. …choose different filters" + CTA
"Clear All filters" (menyebut penyebab: filter). (ref-66, ref-65)

## S. PETA PENERAPAN DOMAIN BARU → 12 layar (Fase 2 Stitch)

| Layar | Pola baru yang akan mengubah layar | Ref kunci |
|---|---|---|
| 01 Overview | K1/K5 empty modul saat 0 data; O1 skeleton loading panel; N2 focus ring nav; P2 token semantic | ref-65, ref-87, ref-91 |
| 02 Activity Log | J1/J3 state baris (hover/selected); R1 banner ingest gagal; M3–M6 ⌘K filter agent/level; O1 skeleton tabel | ref-63, ref-97, ref-79, ref-87 |
| 03 Graph Explorer | I1–I4 ikon legend/kontrol konsisten stroke; J3 hover node (ring aksen); K1 empty seleksi ("pilih node") | ref-56, ref-63, ref-68 |
| 04 Search+Query | M3–M6 palette penuh (grup, seleksi inset, footer kbd, fuzzy highlight); K3 no-result menggema kueri; O1 skeleton hasil | ref-78, ref-80, ref-82, ref-68 |
| 05 Metrics | P1/P3 token warna seri + grade kontras; Q2/Q4 anotasi skala KPI; O3 satu spinner gaya | ref-92, ref-96, ref-88 |
| 06 Health | R2 banner check gagal + jalur fix; R3 error inline per check; K2 empty findings vs error; N3 status ikon+teks | ref-97, ref-98, ref-67, ref-85 |
| 07 Analytics | P2/P4 token tema; Q1 step nama; J1 state tombol filter; K1 empty leaderboard | ref-91, ref-96, ref-63, ref-65 |
| 08 Audit Ledger | R4 kegagalan parsial verify (banner rasio + baris gagal pin); R5 undo rollback; M2 scope-switcher breadcrumb | ref-103, ref-33, ref-77 |
| 09 Proposals | R1/R3 alert per proposal + aksi di tempat; J2 disabled sampai form review valid; K1 empty queue | ref-99, ref-74, ref-70 |
| 10 Debt Queue | R6 badge severity + tooltip; J3 hover baris; K5 konteks penuh saat queue 0 ("0 open debt") | ref-101, ref-63, ref-65 |
| 11 System | L1–L4 form config berlabel + validasi 3-sinyal; P5 token spacing; N4 target ≥34px | ref-72, ref-74, ref-93 |
| 12 Setup | K6 onboarding langkah; N6 progress teks+bar; L2 validasi inline; R3 error per-artifact + Retry | ref-67, ref-85, ref-74, ref-98 |
