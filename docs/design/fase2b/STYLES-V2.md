# STYLES-V2.md — Fase 2b REWORK: 10 Style = 10 ARSITEKTUR LAYOUT (SUB-144)

Tanggal: 2026-11-05 · Dasar: [../PATTERNS.md](../PATTERNS.md) (A–R) + 103 ref.
**Koreksi atas kegagalan fase 2 lalu**: STYLES.md lama memaksa invariant shell A1
(sidebar-kiri + topbar) ke SEMUA style → 10 style menghasilkan layout yang SAMA,
bedanya hanya warna. Operator menolak. Di V2, **yang membedakan style PERTAMA-TAMA
adalah arsitektur layout** (posisi zona navigasi, konten, detail); palet/tipografi/
densitas adalah diferensiator kedua. Setiap style mengikat SATU kerangka layout —
ketiga layarnya (Overview/Activity/Graph) memakai kerangka yang sama.

## Invariant bersama V2 (kualitas, BUKAN struktur)

Yang seragam hanyalah aturan kualitas — struktur bebas per style:

- SATU aksen (D1); merah/amber/hijau EKSKLUSIF status (H4); tanpa emoji;
  kedalaman via langkah bg + hairline, TANPA shadow berat/gradasi (D2).
- Dua famili font (C1): sans untuk teks manusia, mono untuk data mesin —
  KECUALI style yang secara eksplisit menginversinya (S06 full-mono) atau
  membatasi ke satu famili (S10). Uppercase hanya: label seksi, header kolom,
  status pill micro (C3).
- H1: TANPA ellipsis/potongan pada data primer; tanpa wrap mid-token; token
  mono (sha8, timestamp ISO) satu baris utuh atau tidak ditampilkan.
- H2: viewport 1440×900 TERISI sampai tepi bawah; void >5% = cacat; jumlah
  baris eksplisit per tabel/list.
- H3: chrome (nav, status bar, tombol aksi) utuh 100% dalam viewport.
- Grid 4px; gutter 14–16px; radius dari skala per style (A3/A4).
- Status tidak pernah warna-saja (N3): dot/ikon + label teks.

## Matriks struktur (bukti diferensiasi arsitektur)

|| S01 | S02 | S03 | S04 | S05 | S06 | S07 | S08 | S09 | S10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Kerangka | sidebar-kiri | top-nav penuh | command-center | kanban | split-pane | console | masonry | three-pane | timeline-river | radial-focus |
| Navigasi | kolom kiri 240px | bar horizontal atas | tab di status band | — (slim topbar) | header tipis | prompt line | judul seksi bernomor | icon rail 56px | sumbu waktu | — (fokus pusat) |
| Zona konten | 1 area + KPI strip | band bertingkat penuh-lebar | grid panel di bawah band status | 4 kolom kartu | 2 pane 40/60 | scrollback + panel bawah | kolom kartu tak seragam | 3 pane vertikal | sungai event + strip tabel | pusat + satelit orbit |
| Panel detail | dalam area konten | dock bawah | panel grid kanan | kartu per kolom | pane kanan 60% | panel bawah | kartu masonry | pane kanan | kartu pada rel waktu | panel satelit |
| Sitasi pola | A1/A2 (ref-34/04) | M1 + ref-45 topbar | ref-54 SOC band | pola board [arah] | master-detail [arah] | E7 (ref-52) diperluas | ref-45/96 kartu | M4 + A1 rail (ref-16) | rel waktu [arah] | egosentris E6 (ref-16) |

`[arah]` = keputusan arah style (struktur tidak ada di referensi sebagai layar
penuh; komponen di dalamnya tetap tunduk pada pola B–R tersitasi).

---

## S01 — sidebar-classic (obsidian-minimal)

Kerangka: **sidebar navigasi kiri tetap + topbar tipis + satu area konten.**
Ini SATU-SATUNYA style V2 yang boleh memakai shell A1.

```
┌──────────┬────────────────────────────────────────┐
│ KYC      │ topbar: search ⌘K      bell  avatar    │
│ ┌──────┐ ├────────────────────────────────────────┤
│ │SEKSI │ │ judul halaman                          │
│ │ nav  │ │ ┌KPI┐┌KPI┐┌KPI┐┌KPI┐                   │
│ │ nav* │ │ └───┘└───┘└───┘└───┘                   │
│ │ nav  │ │ ┌─────────────────────┬──────────────┐ │
│ │      │ │ │ panel utama         │ panel samping│ │
│ │SEKSI2│ │ │                     │              │ │
│ │ nav  │ │ └─────────────────────┴──────────────┘ │
│ └──────┘ │ status bar mono                        │
│ usercard └────────────────────────────────────────┘
└──────────┴────────────────────────────────────────┘
```

- **Palet**: base `#0B0D11` · sidebar `#0E1116` · card `#12161D` · raised
  `#161B24` · hairline `rgba(255,255,255,0.07)` · teks `#E6E9EF`/`#9AA3B2` ·
  aksen indigo `#6366F1` · ok `#34D399` warn `#FBBF24` danger `#F87171`.
- **Tipografi**: Inter (UI) + JetBrains Mono (data); skala C2 (11/13/14/16/24).
- **Densitas**: standard (row 52px, B1). Radius: kartu 12–16, kontrol 6–8.
- **Sitasi**: A1–A4 (ref-34, ref-04), B1–B5, C1–C3, D1–D4, E1–E7.

## S02 — topnav-saas (enterprise-clean)

Kerangka: **navigasi horizontal penuh di atas; TANPA sidebar sama sekali.**
Konten tersusun sebagai band horizontal penuh-lebar yang bertumpuk; panel
detail berlabuh sebagai **dock bawah** (split horizontal), bukan panel kanan.

```
┌────────────────────────────────────────────────────┐
│ ◆KYC  Overview Activity Graph Metrics   search  ⚙👤│ ← nav utama horizontal
├────────────────────────────────────────────────────┤
│ breadcrumb band: Home / Activity        aksi kanan │
├────────────────────────────────────────────────────┤
│ band konten 1: 3 panel sejajar full-width          │
├────────────────────────────────────────────────────┤
│ band konten 2: tabel full-width 18 baris           │
├────────────────────────────────────────────────────┤
│ DOCK BAWAH: detail item terpilih (horizontal)      │
└────────────────────────────────────────────────────┘
```

- **Palet**: base `#F6F8FB` · card `#FFFFFF` · hairline `#E3E8F0` · teks
  `#0F1B2D`/`#5B6B82` · aksen biru `#2563EB` · ok `#16A34A` warn `#D97706`
  danger `#DC2626`.
- **Tipografi**: Inter + Roboto Mono; judul halaman 18px/600.
- **Densitas**: standard→comfortable (52–56px). Radius: kartu 12, kontrol 8.
- **Komponen khas**: nav item aktif = underline aksen 2px; primary SOLID biru
  (penyimpangan E2 yang disengaja untuk light); dock bawah 200–240px.
- **Sitasi**: M1 breadcrumb (ref-77), B2/B3/B5 (ref-34/45), N1 kontras terang,
  E8; topbar ref-45 tanpa sidebar = keputusan arah.

## S03 — command-center (neon-ops)

Kerangka: **band status raksasa full-width di atas (identitas layar) + grid
panel seragam di bawahnya + ticker bawah.** Navigasi = tab kecil DI DALAM band
status. Tidak ada sidebar.

```
┌────────────────────────────────────────────────────┐
│ ● SYSTEM HEALTHY   1,284 NODES  3,917 EDGES  214 EV│ ← band status besar (KPI raksasa)
│ [Overview][Activity][Graph]            LIVE 11:58Z │ ← tab nav di dalam band
├──────────────┬──────────────┬─────────────┬────────┤
│ panel        │ panel        │ panel       │ panel  │
├──────────────┴──────────────┼─────────────┴────────┤
│ panel lebar                 │ panel                │
├─────────────────────────────┴──────────────────────┤
│ ticker mono: seq 10421 · 9f2c1ab4 · agent-2 · …    │
└────────────────────────────────────────────────────┘
```

- **Palet**: base `#05070D` · panel `#0A0E18` · raised `#0E1424` · hairline
  `rgba(139,92,246,0.12)` · teks `#E8E6F5`/`#8B86A8` · aksen violet `#8B5CF6` ·
  ok `#22C55E` warn `#F59E0B` danger `#EF4444`.
- **Tipografi**: Space Grotesk (judul/KPI) + Inter (body) + JetBrains Mono
  (data) — tiga famili, peran ketat.
- **Densitas**: dense (36–40px). Dot-grid canvas pada panel besar (E6).
- **Sitasi**: ref-54 SOC band, E5 banner, E6, E7 ticker, B1-dense, D4.

## S04 — kanban-board (aurora-soft)

Kerangka: **papan kolom.** Konten utama = 4 kolom vertikal berisi kartu;
setiap layar memetakan datanya ke kolom (bukan tabel). Topbar tipis saja,
tanpa sidebar, tanpa band status.

```
┌────────────────────────────────────────────────────┐
│ topbar tipis: judul + filter chip + search         │
├─────────┬─────────┬─────────┬──────────────────────┤
│ KOLOM A │ KOLOM B │ KOLOM C │ KOLOM D              │
│ header  │ header  │ header  │ header               │
│ ┌─────┐ │ ┌─────┐ │ ┌─────┐ │ ┌─────┐              │
│ │card │ │ │card │ │ │card │ │ │card │              │
│ └─────┘ │ └─────┘ │ └─────┘ │ └─────┘              │
│ ┌─────┐ │ ┌─────┐ │ ┌─────┐ │ ┌─────┐              │
│ │card │ │ │card │ │ │card │ │ │card │              │
│ └─────┘ │ └─────┘ │ └─────┘ │ └─────┘              │
└─────────┴─────────┴─────────┴──────────────────────┘
```

- **Palet**: base `#0F141E` · kolom `#151C2A` · kartu `#1B2434` · hairline
  `rgba(255,255,255,0.06)` · teks `#E7ECF5`/`#93A0B8` · aksen teal `#2DD4BF` ·
  ok `#4ADE80` warn `#FCD34D` danger `#F87171`.
- **Tipografi**: Inter weight rendah + JetBrains Mono; judul 17px/550.
- **Densitas**: comfortable; kartu radius 16–18 (A4 ujung lembut); kolom
  ber-gutter 16px; kartu berisi 3–5 field + chip status + mono id.
- **Sitasi**: A4 radius (ref-54), D3 chip, K5 konteks penuh, N4 target besar;
  struktur board = keputusan arah.

## S05 — master-detail (paper-dark)

Kerangka: **split-pane dua zona: daftar master kiri (~40%) + permukaan baca
detail kanan (~60%).** Header tipis; tidak ada sidebar nav, tidak ada kolom.
Memilih item di master mengisi detail (direpresentasikan statis: satu item
terpilih).

```
┌────────────────────────────────────────────────────┐
│ header tipis: judul serif + aksi                   │
├──────────────────────┬─────────────────────────────┤
│ MASTER LIST          │ DETAIL                      │
│ ▸ item               │ judul serif besar           │
│ ▸ item               │ rule ganda ────── ────      │
│ ▸ item [TERPILIH]    │ metadata grid (kv)          │
│ ▸ item               │ body / payload              │
│ ▸ item               │ tabel terkait               │
│ ▸ item               │ provenance italic           │
└──────────────────────┴─────────────────────────────┘
```

- **Palet**: base `#141110` · panel `#1A1713` · raised `#211D17` · hairline
  `rgba(217,164,65,0.10)` · teks `#EDE4D3`/`#A3947D` · aksen emas `#D9A441` ·
  ok `#7FB069` warn `#E0A458` danger `#D16666`.
- **Tipografi**: Fraunces serif (judul/angka KPI) + Inter (body) + JetBrains
  Mono (data); rule ganda 1px+1px di bawah judul detail.
- **Densitas**: comfortable (56–65px). Radius 4–8.
- **Sitasi**: B1-comfortable (ref-34), C1/C2, D1, E1; split master-detail =
  keputusan arah (pola daftar+detail dari M4/ref-79).

## S06 — console-terminal (terminal-brutalist)

Kerangka: **konsol full-width.** Seluruh layar = scrollback terminal; chrome
hanya: prompt-line atas (breadcrumb `kyc://…`) + panel bawah (input prompt +
strip status). Tidak ada sidebar, topbar, kartu, atau grid.

```
┌────────────────────────────────────────────────────┐
│ kyc://activity --tail --live                      _│ ← prompt line (chrome atas)
├────────────────────────────────────────────────────┤
│ > system healthy · 11/11 checks · exit 0           │
│ > nodes 1284 · edges 3917 · store 46.2MB · wal     │
│ > …baris output mono full-width…                   │
│ > …                                                │
│ > …  (scrollback mengisi seluruh tinggi)           │
├────────────────────────────────────────────────────┤
│ PANEL BAWAH:                                       │
│ kyc> _  [input prompt]                             │
│ SEQ 10421 · CHAIN 9f2c1ab4 · 3 AGENTS · TAIL ON    │ ← strip status mono
└────────────────────────────────────────────────────┘
```

- **Palet**: base `#000000` · panel bawah `#050805` · hairline `#1A2E1A` ·
  teks `#C9F7D4`/`#5F8F68` · aksen fosfor `#33FF66` · warn `#FFD23F` danger
  `#FF4D4D`.
- **Tipografi**: JetBrains Mono untuk SEMUA; judul hanya lebih besar/tebal;
  radius 0–2px; border 1px tegas.
- **Densitas**: dense (baris 28–36px, ≥20 baris scrollback terlihat).
- **Sitasi**: E7 (ref-52) diperluas jadi seluruh layar, B1-dense, C1
  diinvertasi (mono = default), I1 ikon outline. Radius A4 diganti 0–2px
  sebagai keputusan arah.

## S07 — masonry-cards (swiss-grid)

Kerangka: **masonry kartu besar.** Kolom 2–3 dengan kartu TINGGI TIDAK SERAGAM
(masonry sungguhan, bukan grid rata); satu kolom bisa berisi kartu pendek
bertumpuk, kolom lain satu kartu tinggi. Header = judul raksasa + nomor seksi.
TANPA sidebar; TANPA topbar app.

```
┌────────────────────────────────────────────────────┤
│ 01 — OVERVIEW (display raksasa)        KYC · v5    │
│ ═══════════════════════════════════ (garis 2px)    │
├──────────────┬───────────────────┬─────────────────┤
│ ┌──────────┐ │ ┌───────────────┐ │ ┌─────────────┐ │
│ │KPI raksasa│ │ │ chart tinggi  │ │ │ KPI         │ │
│ └──────────┘ │ │               │ │ └─────────────┘ │
│ ┌──────────┐ │ │               │ │ ┌─────────────┐ │
│ │KPI       │ │ │               │ │ │ list mini   │ │
│ └──────────┘ │ └───────────────┘ │ │             │ │
│ ┌──────────┐ │ ┌───────────────┐ │ └─────────────┘ │
│ │KPI       │ │ │ tabel lebar   │ │ ┌─────────────┐ │
│ └──────────┘ │ │               │ │ │ KPI         │ │
│              │ └───────────────┘ │ └─────────────┘ │
└──────────────┴───────────────────┴─────────────────┘
```

- **Palet**: base `#FAFAF8` · kartu `#FFFFFF` · hairline `#E4E4E0` · teks
  `#111111`/`#6B6B66` · aksen merah swiss `#E03131` · ok `#2F9E6E` warn
  `#B8860B` danger `#C92A2A`.
- **Tipografi**: Inter/Helvetica Now; display KPI 40–56px/650 tracking −2%
  (Q3); IBM Plex Mono untuk data; nomor seksi besar ("01").
- **Densitas**: comfortable (56–65px); whitespace adalah konten; garis
  pemisah hitam 2px struktural.
- **Sitasi**: Q1–Q5 (ref-96), A3 grid 4px, B2, D1, P5 (ref-93); masonry =
  keputusan arah (komposisi kartu ref-45).

## S08 — three-pane (glass-console)

Kerangka: **tiga pane vertikal: icon rail 56px | kolom daftar ~300px | pane
detail lebar.** Berbeda dari S01 (tanpa rail, daftar penuh) dan S05 (dua pane,
daftar 40%).

```
┌────┬──────────────┬─────────────────────────────────┐
│ ◆  │ LIST         │ DETAIL                          │
│ ▢  │ search mini  │ header + KPI mini strip         │
│ ▢  │ ──────────   │ ┌─────────┐ ┌─────────┐         │
│ ▢  │ ▸ item       │ │ subpane │ │ subpane │         │
│ ▢  │ ▸ item [SEL] │ └─────────┘ └─────────┘         │
│ ▢  │ ▸ item       │ panel konten utama              │
│ ▢  │ ▸ item       │                                 │
│ ⚙  │ ▸ item       │                                 │
└────┴──────────────┴─────────────────────────────────┘
```

- **Palet**: base `#070B16` · pane `rgba(148,178,255,0.05)` + border
  `rgba(148,178,255,0.14)` · raised alpha 0.09 · teks `#E3EBFF`/`#8FA3CC` ·
  aksen cyan `#22D3EE` · ok `#34D399` warn `#FBBF24` danger `#FB7185`.
- **Tipografi**: Inter + JetBrains Mono; label seksi uppercase tracking
  +0.12em.
- **Densitas**: standard (52px). Depth = langkah alpha flat (D2 adaptasi;
  TANPA gradasi, TANPA blur-look berlebihan).
- **Sitasi**: A1 rail 56px (ref-16), M4 baris daftar (ref-78), D3, I4; rail +
  list + detail = keputusan arah.

## S09 — timeline-river (data-dense)

Kerangka: **sungai waktu.** Sumbu waktu vertikal di kiri (~120px, label jam
mono); event mengalir sebagai baris/kartu yang menempel pada rel waktu,
terbaru di atas; strip bawah = tabel mikro dense. Konten diorganisir oleh
WAKTU, bukan oleh panel topik.

```
┌───┬────────────────────────────────────────────────┤
│KPI│ strip dense: 6 metrik kecil satu baris         │
├───┼────────────────────────────────────────────────┤
│11:│──○ EVENT terbaru (kartu melebar ke kanan)      │
│58Z│  │                                             │
│11:│──○ EVENT                                       │
│54Z│  │                                             │
│11:│  ├──○ sub-event / cascade                      │
│49Z│──○ EVENT (WARN — wash amber)                   │
│ … │  │  (rel berlanjut sampai tepi bawah)          │
├───┴────────────────────────────────────────────────┤
│ strip tabel mikro: 8 baris × 8 kolom mono 11px     │
└────────────────────────────────────────────────────┘
```

- **Palet**: base `#0E1116` · panel `#12161C` · hairline `#1E2630` · teks
  `#D8DEE6`/`#7C8794` · aksen oranye `#FB923C` · ok `#4ADE80` warn `#FACC15`
  danger `#F87171`.
- **Tipografi**: Inter 11–13px + JetBrains Mono 11–12px; KPI 20–24px (ruang
  untuk data lebih penting dari angka raksasa).
- **Densitas**: dense EKSTREM sah (row 34–38px; ≥18 baris event + 8 baris
  strip, H2). Kolom angka right-aligned (penyimpangan B3 yang disengaja,
  konvensi terminal).
- **Sitasi**: B1-dense (ref-45), B3/B5, D4 (ref-52), E4, H2; rel waktu =
  keputusan arah.

## S10 — radial-focus (monochrome-type)

Kerangka: **fokus pusat.** Satu entitas/angka DOMINAN di tengah layar; panel
satelit kecil mengorbit di sekelilingnya (kiri-atas, kanan-atas, kiri-bawah,
kanan-bawah), terhubung garis tipis ke pusat; strip mikro bawah. Hierarki
radial, bukan grid.

```
│ micro header tipis                                 │
│                                                    │
│   ┌satelit┐                    ┌satelit┐           │
│      │      ╭──────────────╮      │                │
│      └──────│  FOKUS:      │──────┘                │
│             │  angka/node  │                       │
│   ┌satelit┐ │  RAKSASA     │ ┌satelit┐             │
│      └──────╰──────────────╯─────┘                 │
│                                                    │
│ strip bawah: fakta mikro mono satu baris           │
```

- **Palet**: base `#FFFFFF` · panel `#FAFAFA` · garis struktur `#0A0A0A` 1–2px
  · hairline dalam `#D4D4D4` · teks `#0A0A0A`/`#525252` · aksen = tinta hitam
  solid (elemen aktif = isi hitam, non-aktif = outline) · status = BENTUK +
  kata: ok `● OK` warn `◐ WARN` danger `■ FAIL` (N3 ekstrem).
- **Tipografi**: Inter dimainkan penuh: Display 48–72px/700, judul 20/650,
  body 13, micro 10 uppercase tracking +0.14em; JetBrains Mono untuk data.
- **Densitas**: standard; whitespace agresif; garis 2–3px hitam struktural.
- **Sitasi**: Q1–Q5 (hierarki tipe murni), N3 (status bukan warna), B2, C3,
  I1; komposisi radial/egosentris diadaptasi dari E6 (ref-16).

---

## Aturan prompt V2 (diterapkan driver)

1. Prompt = blok STRUCTURE (zona + posisi + ukuran eksplisit) + blok THEME
   (palet/tipe/densitas verbatim dari style ini) + blok CONTENT (field per
   field, H5) + HARD RULES (H1–H5, D1, tanpa emoji, 1440×900 viewport-fit).
2. Ketiga layar satu style memakai STRUCTURE yang sama — perbedaan antar
   layar hanya isi zona.
3. `designSystem` Stitch dibuat SEKALI per style (dari layar pertama) dan
   dipakai ulang untuk 2 layar lainnya.
4. Verifikasi P3: montase 3 layar per style diaudit vision — struktur harus
   cocok dengan ASCII skeleton DAN berbeda dari 9 style lain.
