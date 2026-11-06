# W18 — Sidebar Fidelity Fix (SUB-148)

Menaikkan sidebar ke standar profesional (skill `professional-ui-design` +
`sidebar-nav-design` v2 — prinsip dengan titik awal terukur, bukan kemiripan
piksel; foto-1 = bukti seperti apa level itu). Enam masalah operator:
ikon campur 3 bahasa, state aktif ungu terlalu kuat, label seksi, spacing
item, logo row, promo+user row.

## Yang berubah (per keputusan, dengan alasan dari pola)

1. **Satu set ikon SVG outline untuk 19 item** (PATTERNS I1–I4). Semua glyph
   teks/Unicode lama (◈ ⌕ ≡ ≣ ↗ ⌗ □ ◷ ⇥ ∑ + ▤ ⌁ ▦ § ⇓ ¶ ◐ ▸) diganti inline
   SVG buatan tangan pada grid 16px: `fill:none`, `stroke: currentColor`,
   stroke-width 1.5, round caps/joins — satu famili Lucide-style. Metafora
   generik fungsional (I4) dan semuanya berbeda: graph=node-link,
   search=kaca, browse=list+dot, activity=pulse, analytics=trend,
   codeintel=`</>`, proposals=bohlam, debt=hourglass, extractions=corong,
   metrics=bar, health=heart-pulse, audit=clipboard-check, bench=stopwatch,
   store=slinder db, ledgers=buku, exports=download, contracts=dokumen,
   system=server, setup=wrench. Ikon promo (⌁) ikut jadi SVG refresh.
   Warna ikon: `--icon` #667085 → `--text-primary` #101828 saat aktif.
2. **Aktif = pill abu lembut, bukan ungu** (prinsip "aktif terbaca tanpa
   berteriak"). `.nav-item.active`: `background: var(--active-bg)` #F3F4F6 +
   teks/ikon `--text-primary` #101828; tanpa aksen ungu, tanpa bar/border/
   shadow/perubahan ukuran font. Aksen ungu kini hanya hidup di badge kecil
   (logo "OPS", promo "LIVE · 2S") + tile logo.
3. **Label seksi: title-case, bukan uppercase** (keputusan tone: lebih tenang;
   koreksi operator atas PATTERNS A2). 11px/500, `letter-spacing: 0.015em`,
   `--text-muted` #98A2B3, tepi kiri sejajar kolom ikon (x=20px, diverifikasi
   numerik: label=20, ikon=20, logo mark=21, avatar=20). Pemisah antar seksi
   = whitespace murni, tanpa divider line. Diketahui: #98A2B3 ≈ 2.8:1 kontras
   — deviasi sadar (label = metadata non-esensial; mengikuti standar operator).
4. **Spacing item**: 28px tinggi + gap 2px = pitch 30px, radius 7px,
   padding 0 10px, gap ikon-label 10px, font 13.5px/500 `--text-nav` #344054.
   *Deviasi beralasan* dari titik awal 32px: skill v2 sendiri menyatakan nav
   15+ item → 26–30px "agar tidak scroll"; 19 item kami kini **fit tanpa
   scrollbar pada 900px** (overflow diukur 0px; sebelumnya Setup terpotong).
5. **Logo row = switcher pill** 38px, radius 10px, border hairline
   `--border-strong`, margin 8/10/4 → tile logo di x=20 (sejajar kolom ikon);
   tile 20px gradasi 135° aksen→aksen-hot (identitas brand, satu-satunya
   gradasi di sidebar); wordmark 15px/700/-0.01em; badge "OPS" 9.5px pill
   full-round — aksen tetap kecil.
6. **Promo + user row**: promo sejajar rail (margin 0 10px), judul satu baris
   (bug nyata sebelumnya: "Live / refresh" wrap karena badge), badge LIVE
   `margin-left: auto`; user row padding 10/20 (avatar di kolom x=20).

## Bug/defect nyata yang ditemukan audit & diperbaiki

- **Scrollbar native dengan tombol panah ▲▼** pada nav. Eksperimen terukur di
  engine ini: `::-webkit-scrollbar` hanya dihormati bila `scrollbar-width`
  standar TIDAK ada (webkit path 8px vs standar 10px; keduanya aktif → standar
  menang, webkit diabaikan). Fix: `scrollbar-width` dihapus dari `.nav`,
  styling slim tanpa tombol lewat webkit path (thumb `--edge`, track
  transparan); Firefox jatuh ke default-nya. Terverifikasi: 8px, tanpa panah.
- **Nav overflow 53px → Setup terpotong** pada 900px. Tertutup oleh densitas
  28px + trim ritme grid-4 (label 10/4, nav padding 2/6, pill margin 8,
  promo/user padding dirapatkan 2–4px) — bukan dengan mengecilkan konten.
- **Judul promo wrap** ("Live / refresh") — kini satu baris (sidebar 208→224px
  + badge auto-margin + `white-space: nowrap`). Lebar 224 tetap dalam rentang
  skill 208–240px.
- Klaim auditor "label 15px di kiri kolom ikon" **dibantah numerik** (bbox:
  label x=20 = ikon x=20) — mengikuti lesson: verifikasi angka sebelum FAIL.

## Bukti (server localhost nyata, seed w18_serve.py, 1440×900, hard-reload)

- `w18-assets/23-sidebar-before.png` — sebelum: glyph teks campur, aktif ungu,
  Setup terpotong, scrollbar berpanah, judul promo wrap.
- `w18-assets/24-sidebar-after.png` — sesudah: 19 ikon satu famili, aktif abu
  lembut, semua item + promo + user terlihat tanpa scrollbar.
- `w18-assets/25-sidebar-after-full.png` — full page: graph canvas utuh dengan
  sidebar 224px (tanpa overlap/clip).
- Audit vision terhadap prinsip (bukan angka): **7/7 PASS** — hierarki terbaca,
  ikon satu famili, aktif jelas tanpa mendominasi, napas cukup, tanpa clip/
  scrollbar, promo+user rapi. Nit terdokumentasi (diterima): label #98A2B3
  kontras rendah (metadata, standar operator); item "System" di seksi "System"
  (IA di luar scope); tile promo hitam = penekanan intentional (seperti foto-1).

## Gates

- `test_webview_ui_assets.py` +17 pin `TestSidebarFidelity` (TDD: RED 11 →
  GREEN): 19 SVG satu spec, nol glyph teks di nav, aktif netral tanpa aksen,
  token warna, label title-case, metrik item+pitch, pill logo, scrollbar slim,
  promo SVG.
- Subset webview 12 file: **633 passed**. Determinisme: PYTHONHASHSEED 0/1/42
  → 3×91p identik. Harness DOM graph tetap hijau (tak disentuh).
- `ruff check` + `ruff format --check`: 0. `pyright`: 0/0/0.
  `node --check app.js` OK (app.js hanya tersentuh tidak sama sekali).
- TIDAK commit; scope: `serve/webview_assets/{index.html,style.css}`,
  `tests/test_webview_ui_assets.py`, dokumen ini + 3 PNG bukti.
