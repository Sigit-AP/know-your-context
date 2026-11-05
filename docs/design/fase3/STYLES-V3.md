# STYLES-V3.md — Fase 3: 5 STYLE MINIMALIS-PROFESIONAL (SUB-145)

Tanggal: 2026-11-05 · Dasar: [../PATTERNS.md](../PATTERNS.md) (A–R, 103 ref) +
[../LEARNING-VERDICT.md](../LEARNING-VERDICT.md) (100%).
**Koreksi atas penolakan V2**: 10 style V2 ("10 arsitektur layout") ditolak
operator — 'tidak ada yang bagus'. V3 mengubah brief: **kelima style adalah
turunan minimalis-profesional** — tanpa gimmick arsitektur (tanpa kanban,
radial, masonry, terminal-fosfor). Yang membedakan kelima style: **mekanisme
hierarki**, tone (terang/gelap), densitas, dan penempatan zona detail — semua
di dalam koridor minimalis-profesional.

## Syarat operator (binding)

1. Minimalis tapi TETAP profesional dan mampu menangani bagian paling kompleks
   (graph ratusan node, log 50 agent padat).
2. Hierarki visual JELAS — 4 tingkat: **utama / standar / sekunder / minim** —
   langsung terbaca dari ukuran, posisi, dan bobot visual; warna hanya
   penguat, bukan satu-satunya pembeda.
3. Interaktif TERGAMBAR: state hover/focus/selected harus terlihat pada layar
   statis; tidak saling tumpuk; tidak kaku; tidak kuno.

## Invariant bersama V3 (kualitas, dari PATTERNS.md)

- SATU aksen (D1); merah/amber/hijau EKSKLUSIF status (H4); tanpa emoji;
  tanpa shadow berat/gradasi (D2); kedalaman via langkah permukaan + hairline.
- Dua famili font (C1): sans untuk teks manusia, mono untuk data mesin
  (sha8, ISO timestamp, angka metrik). Uppercase hanya: label seksi, header
  kolom, status pill micro (C3).
- H1: tanpa ellipsis/potongan pada data primer; tanpa wrap mid-token.
- H2: viewport 1440×900 terisi sampai tepi bawah; jumlah baris eksplisit.
- H3: chrome (nav, status bar, aksi) utuh 100% dalam viewport, satu baris.
- Grid 4px; gutter 14–16px; radius dari skala per style (A3/A4).
- Status tidak pernah warna-saja: dot/glyph + label teks (N3).
- **State interaksi wajib tergambar di setiap layar** (J1/J3/N2): satu item
  nav aktif; satu baris/node TERPILIH (ring/bar aksen); satu baris HOVER
  (tint +8–12%); satu input FOKUS (ring aksen 2px + offset); chip filter
  terpilih vs tidak; satu toggle ON.

## Uji kompleksitas (3 layar, data nyata KYC)

| Layar | Uji |
|---|---|
| 01 Graph Explorer | ratusan node: 3 tingkat LOD (chip berlabel / dot sedang / dot mikro), node terpilih + tetangga full-opacity, sisanya dimmed; inspector |
| 02 Agent Activity Log | 50 agent: stream padat ≥16 baris + roster/faset agent; state hover/selected/focus tergambar |
| 03 Analytics | multi-panel ≥5: KPI, chart utama, distribusi domain, tabel top nodes, health, relearn |

Data kanonik (semua style memakai nilai yang sama): nodes **1,284** · edges
**3,917** · store **46.2 MB** · events today **214** · schema **v5** ·
agents **50** (agent-2, sub-141…sub-145, operator) · actions
`query`/`log_event`/`record_outcome`/`apply_improvement` · node fokus
`heuristics-catalog` (P2) · sha8 `9f2c1ab4` prev `7a1c9e02` · waktu
`2026-11-05T11:58:02Z` · skor P=0.828 M=0.341 E=0.898 unc=0.050 conf=0.912 ·
relasi depends_on → retrieval-core, supersedes → heuristics-v1 · evidence
ev-0081, ev-0082 · VERIFIED 5/5 · domain P1 412 / P2 388 / P3 204 / P4 161 /
P5 119 · health 11/11 checks PASS, exit 0, 0 errors, 2 warnings · relearn
IDLE → SCAN → DIFF → SYNC (aktif: IDLE).

## Matriks diferensiasi (bukti 5 style benar-benar berbeda)

|| V1 ledger-light | V2 ink-mono | V3 slate-calm | V4 modular-gray | V5 console-dark |
|---|---|---|---|---|
| Tone | terang hangat | putih tinta | gelap netral | abu terang dingin | near-black |
| Mekanisme hierarki | posisi + skala tipe | skala tipe murni (tanpa kartu) | langkah permukaan + ruang | ukuran modul grid | tingkat kecerahan + mono |
| Navigasi | topbar + breadcrumb | header text-link 48px | sidebar ringkas 220px | topbar tab 52px | utility bar mono + tab |
| Zona detail | rail kanan 320px | kolom kanan 340px (rule 1px) | panel melayang di kanvas | stack modul kanan | dock bawah horizontal |
| Permukaan | kartu putih r10 | tanpa kartu, hairline | kartu r12 langkah bg | modul putih r12 di atas abu | panel r6 hairline zinc |
| Densitas tabel | dense 36px | dense 34px | standard 44px | dense 38px | dense 36px |
| Aksen tunggal | indigo #4338CA | biru #1D4ED8 | sky #38BDF8 | teal #0D9488 | violet #8B5CF6 |
| Sitasi kunci | A1,A3,B1,C1,D1,E6,J3,N2,Q2 | Q1–Q5,C1,D1,N3,B2,J3,M1 | A1,A2,D2,E1,E6,J1,N2,O1 | A3,A4,B1,D1,E1,E6,H2,K5,P5 | E7,B1,C1,D4,E6,J3,N3,O3 |

---

## V1 — ledger-light (Editorial Minimal, terang hangat)

**Prinsip hierarki**: mata dibimbing oleh **posisi + skala**. Tingkat-1
(utama) = zona kiri-atas terluas (±60% lebar, panel pertama sesudah header);
tingkat-2 = panel standar di samping/bawahnya; tingkat-3 = rail kanan 320px
(konteks/inspector, teks lebih kecil); tingkat-4 = baris status mono 11px di
dasar. Label seksi dinomori ("01 · GRAPH") sebagai micro-label uppercase.
Judul halaman 28px/650 langsung menjawab "di mana saya".

**Spec graph canvas**: kanvas = panel tingkat-1 (±880×640px). Tiga tingkat
LOD: (a) 12–16 node chip berlabel (rounded 8px, border hairline, label 12px);
(b) ±50 dot 8px; (c) 100–150 dot mikro 3px opacity 30% sebagai konteks.
Node terpilih `heuristics-catalog`: ring aksen 2px + label tebal; edge
tetangganya aksen 2px; edge lain hairline 1px; node non-tetangga dimmed 40%.
Legend floating kiri-bawah (chip P1..P7 + count); zoom pill vertikal tepi
kanan (+ / − / fit); search-to-focus field di toolbar kanvas (state FOKUS
tergambar: ring aksen 2px). (E6, ref-16/ref-54; H1; J3)

**Spec log stream**: toolbar filter (chip level INFO/WARN/ERROR — INFO
terpilih terisi tint aksen; chip agent; search ghost; toggle AUTOSCROLL ON).
Tabel dense 36px (B1), ≥16 baris terlihat, kolom: TIME (mono) · LEVEL chip ·
AGENT · ACTION (mono) · NODE · SHA8 (mono 8 hex utuh) · detail. Satu baris
HOVER (wash abu hangat +8%), satu baris SELECTED (bar aksen 3px kiri + tint),
satu WARN wash amber. Rail kanan 320px: "AGENTS — 50" roster 10 baris
(agent · events mono · ttl) + tombol ghost "View all 50". (B3/B4/B5, J3, N3)

- **Palet**: base `#FAFAF8` · kartu `#FFFFFF` · hairline `#E7E5E0` · tinta
  `#1C1917` · sekunder `#78716C` · aksen indigo `#4338CA` · ok `#15803D`
  warn `#B45309` danger `#B91C1C`.
- **Tipografi**: Inter + JetBrains Mono; skala Q2: 11 micro / 12.5 body /
  14 nav / 16 judul panel / 28 judul halaman / 24 KPI.
- **Radius**: kartu 10, kontrol 6, chip 4. **Densitas**: dense 36px.
- **Sitasi**: A1 (topbar ref-45), A3, B1-dense, B2–B5, C1–C3, D1–D4, E1, E6,
  E7, H1–H5, J1/J3/J5, N2/N3, P5, Q2–Q4, R2.

## V2 — ink-mono (Swiss Ink, putih, hierarki tipe murni)

**Prinsip hierarki**: **skala tipografi + ruang putih semata** — TANPA kartu,
TANPA panel ber-bg. Zona dipisah hairline 1px; tingkat-1 = angka/teks 40–48px
/700; tingkat-2 = 16px/600; tingkat-3 = 12px/400 abu; tingkat-4 = micro 10px
uppercase tracking +0.12em. Aksen biru HANYA menandai interaktif/terpilih
(link, focus ring, selected) — hierarki konten sendiri tidak pernah
mengandalkan warna. Austerity dijaga tetap modern (tidak kuno) lewat skala
tipe ekstrem dan whitespace agresif (Q1–Q5).

**Spec graph canvas**: kanvas full-bleed di dalam frame hairline; node
primer = lingkaran 20px outline 1.5px + label 12px di kanan; node terpilih =
ISI tinta solid + ring aksen biru 2px; node sekunder = dot 6px; konteks =
dot 2.5px; edge = garis 1px #111 untuk neighborhood terpilih, #D4D4D4 untuk
sisanya; label relasi = micro uppercase di midpoint. Inspector = kolom kanan
340px dipisah rule vertikal 1px hitam, judul 20px/650, tabel kv tanpa border
(baris dipisah hairline #E5E5E5). (E6 adaptasi tanpa-kartu, N3, H1)

**Spec log stream**: baris log = baris teks dipisah hairline (B2 tanpa
kontainer kartu), 34px, kolom: TIME mono · LEVEL kata+glyph (INFO ·,
WARN ▲, ERROR ■ — bukan warna-saja, N3) · AGENT · ACTION mono · NODE · SHA8.
≥18 baris. HOVER = wash #F5F5F5; SELECTED = bar hitam 3px kiri + teks 600;
FOKUS pada field filter = ring biru 2px offset 2px (N2). Kolom faset kiri
220px: LEVELS (3 baris + count) dan AGENTS (10 dari 50 + "Show all 50" link
biru). (B1-dense, C1, J3, N2/N3)

- **Palet**: base `#FFFFFF` · hairline `#E5E5E5` · rule struktur `#111111`
  · tinta `#0A0A0A` · sekunder `#525252` · tersier `#A3A3A3` · aksen biru
  `#1D4ED8` (interaktif saja) · status: ok/warn/danger = kata + glyph
  (warna semantik boleh sebagai penguat: `#15803D`/`#B45309`/`#B91C1C`).
- **Tipografi**: Inter penuh (Q3 tracking −2% untuk display) + JetBrains
  Mono data. Radius 0–2px.
- **Sitasi**: Q1–Q5 (ref-94/95/96), C1, D1, B2, E6, H1, J1/J3, M1, N2/N3,
  P5.

## V3 — slate-calm (Dark Calm, hierarki permukaan)

**Prinsip hierarki**: **langkah permukaan + ruang**. Tiga langkah bg
(base → surface → raised, D2) + padding yang tumbuh sesuai kepentingan:
tingkat-1 duduk di surface raised dengan padding 24–28px dan radius 12;
tingkat-2 padding 16–20; tingkat-3 teks sekunder opacity 60%; tingkat-4 =
status bar mono 11px. Mata jatuh dulu ke panel paling terang dan paling
lapang. Sidebar ringkas 220px (label seksi uppercase + item dengan ikon
outline 16px, aktif = pill tint aksen, A2).

**Spec graph canvas**: kanvas pada panel tingkat-1 (paling terang, padding
24px); node = chip rounded 10px; terpilih = ring aksen 2px + glow tint 8%
(bukan shadow, D2); hover tergambar pada satu node tetangga (border aksen
1px + tint); non-tetangga dimmed 45%. Inspector = **panel melayang** di atas
kanvas kanan (raised + border hairline, 300px) — satu-satunya elemen
overlay; zoom pill vertikal; legend chip bawah-kiri. (E6, D2, J3, ref-16)

**Spec log stream**: tabel standard 44px (B1) dengan grouping header waktu
("11:58 — 11:50", micro uppercase); baris hover tint +8%; baris selected =
tint aksen 10% + teks aksen pada kolom ACTION; level = chip tint (D3);
≥12 baris + footer pagination (B5). Panel kanan dalam area konten: "Agent
load" = 8 baris agent dengan bar horizontal tipis (bukan angka saja) +
"50 agents total". (B4/B5, D3, J3, O1)

- **Palet**: base `#101216` · surface `#16181D` · raised `#1C1F26` ·
  hairline `rgba(255,255,255,0.06)` · teks `#E8EAEE` · sekunder `#9BA1AC` ·
  aksen sky `#38BDF8` · ok `#34D399` warn `#FBBF24` danger `#F87171`.
- **Tipografi**: Inter + JetBrains Mono; judul 18px/600; KPI 26px/650.
- **Radius**: kartu 12, kontrol 8, chip 6. **Densitas**: standard 44px.
- **Sitasi**: A1/A2 (ref-04/ref-34), D2 (ref-04 "no shadows"), E1, E6,
  B1-standard, C1–C3, D3/D4, J1/J3, N2, O1, Q2/Q4.

## V4 — modular-gray (Light Modular, hierarki ukuran modul)

**Prinsip hierarki**: **ukuran modul pada grid 12 kolom**. Semua konten
hidup dalam modul putih r12 di atas kanvas abu dingin; prioritas dibaca dari
LUAS modul: tingkat-1 = modul 8 kolom penuh-tinggi; tingkat-2 = modul 4
kolom; tingkat-3 = modul 4 kolom setengah-tinggi; tingkat-4 = strip status
mono di dalam topbar/dasar. Tidak ada dua modul tingkat-1 dalam satu layar.
Navigasi = topbar putih 52px dengan tab (aktif = pill tint aksen).

**Spec graph canvas**: modul kanvas 8 kolom; LOD tiga tingkat (16 chip
berlabel / ±50 dot / 100+ dot mikro); terpilih = border aksen 2px pada chip +
label aksen; satu node hover (tint); legend = modul 4 kolom di bawah
inspector; kontrol zoom = segmented pill di sudut kanvas. Stack kanan 4
kolom: modul INSPECTOR (atas, setengah tinggi) + modul LEGEND (bawah).
Modul FILTER (chip P1..P7 multi-select, 3 terpilih) membentang di atas
kanvas. (E6, A3/A4, K5)

**Spec log stream**: modul tabel 8 kolom, dense 38px, ≥14 baris, kolom
TIME mono · LEVEL chip · AGENT · ACTION mono · NODE · SHA8; hover wash +
selected bar aksen kiri 3px; stack kanan 4 kolom: modul AGENT ROSTER
("50 agents", 12 baris terlihat, count per baris mono, satu baris hover)
+ modul LEVEL MIX (3 bar horizontal tipis). Fokus tergambar pada search
roster (ring aksen 2px). (B1-dense, B3–B5, J1/J3, H2)

- **Palet**: kanvas `#EEF1F5` · modul `#FFFFFF` · hairline `#DCE2EA` ·
  teks `#101828` · sekunder `#667085` · aksen teal `#0D9488` · ok `#16A34A`
  warn `#D97706` danger `#DC2626`.
- **Tipografi**: Inter + JetBrains Mono; judul modul 13px/600; KPI 24px/650.
- **Radius**: modul 12, kontrol 8, chip 6. **Densitas**: dense 38px.
- **Sitasi**: A3/A4, B1-dense, B3–B5, C1–C3, D1–D4, E1, E6, H2/H3, J1/J3,
  K5, N2/N3, P5.

## V5 — console-dark (Modern Ops, hierarki kecerahan + mono)

**Prinsip hierarki**: **tingkat kecerahan teks + disiplin mono** di atas
near-black zinc. Empat tingkat terang: tingkat-1 `#F4F4F5` (100%) untuk nilai
utama 24px; tingkat-2 `#D4D4D8` (85%); tingkat-3 `#A1A1AA` (65%);
tingkat-4 `#52525B` (40%, micro uppercase). Posisi: zona baca utama
kiri-tengah; faset/filter di kolom kiri 220px; detail di **dock bawah
horizontal 200px**. Utility bar atas 44px mono (brand · env PROD · clock);
tab nav di dalamnya; status bar dasar mono (E7).

**Spec graph canvas**: kanvas mengisi zona utama dengan dot-grid redup (E6);
node = chip mono 11px r6; tiga tingkat LOD; terpilih = border aksen violet
2px + label 100% bright; edge tetangga aksen 1.5px; hover pada satu node
(tint 8%); non-tetangga 35%. Dock bawah = inspector horizontal: kolom
summary · skor (bar tipis) · relasi · evidence — tersebar mendatar, bukan
panel kanan. (E6, E7, C1, D4)

**Spec log stream**: kolom faset kiri 220px: LEVEL (3 baris + count) ·
AGENTS (search mini FOKUS dengan ring aksen + 8 baris agent + "all 50").
Stream utama: tabel dense 36px ≥16 baris, kolom TIME mono · LEVEL (kata
caps kecil + dot, N3) · AGENT · ACTION mono · NODE · SHA8 mono · detail;
hover wash `#18181B`; selected = border kiri aksen 2px + wash tint violet
8%; WARN wash amber 6% + label WARN. Dock bawah: hash chain terpilih
(prev `7a1c9e02` → entry `9f2c1ab4`) + payload satu baris. (B1-dense, B3/B4,
E7, J3, N3, O3)

- **Palet**: base `#09090B` · panel `#111113` · raised `#18181B` · hairline
  `#232328` · teks `#F4F4F5/#D4D4D8/#A1A1AA/#52525B` · aksen violet
  `#8B5CF6` · ok `#4ADE80` warn `#FACC15` danger `#F87171`.
- **Tipografi**: Inter untuk label UI, JetBrains Mono untuk SEMUA data +
  micro label struktural; judul 15px/600 (kecil — data yang besar).
- **Radius**: 6px seragam. **Densitas**: dense 36px.
- **Sitasi**: E7 (ref-52), B1-dense, C1, D4, E6, B4, J1/J3, N3, O3, P5.

---

## PROMPT per style (kanonik — dipakai driver apa adanya)

Format blok: INTRO · THEME · STRUCTURE · STATES · SCREEN · HARD RULES.
HARD RULES bersama (dari resep fase2b + H1–H5): 1440×900 viewport-fit; tanpa
ellipsis; tanpa wrap mid-token; satu aksen; merah/amber/hijau hanya status;
flat tanpa shadow/gradasi; tanpa emoji; jumlah baris eksplisit; chrome utuh.

### V1 ledger-light

**THEME**: Light warm minimal professional: base #FAFAF8, cards #FFFFFF,
hairline #E7E5E0, ink #1C1917, secondary #78716C, ONE accent indigo #4338CA,
status ok #15803D, warn #B45309, danger #B91C1C. Inter for all UI text,
JetBrains Mono for ALL data values/hashes/timestamps. Card radius 10px,
controls 6px, chips 4px. Dense table rows 36px. Page padding 24px, gutters
16px.

**STRUCTURE**: A 56px topbar spans the full width: small square logo mark +
'Know Your Context' left; text nav Overview · Graph · Activity · Analytics ·
Health (the current screen's item = indigo-tinted pill 10% + indigo text);
search field with ⌘K hint + bell + avatar right. Below it a page header row:
28px/650 page title left + mono meta right ('2026-11-05T11:58:02Z · schema
v5'). Content: a wide PRIMARY zone left (~65-70%) holding the screen's main
panel, a NARROW right rail 320px for context/inspector, and a one-line mono
status bar 11px at the very bottom ('11/11 CHECKS PASS · SCHEMA v5 · WAL ·
LAST EVENT 2026-11-05T11:58:02Z'). Numbered section micro-labels (uppercase
10px secondary, e.g. '01 · GRAPH') sit above major panels.

**STATES TO DRAW (mandatory, visible in this static screen)**: active nav
pill on the current item; one table row HOVERED (warm gray wash); one row
SELECTED (3px indigo left bar + light indigo tint); the main search/filter
input FOCUSED (2px indigo focus ring with offset); filter chips shown both
selected (indigo tint fill) and unselected (outline only); autoscroll toggle
drawn ON; one primary button default and one ghost button.

**01-graph**: SCREEN: Graph Explorer. Primary zone: one large graph canvas
panel (~880x640) with section label '01 · GRAPH': a toolbar row inside the
panel (focused search-to-focus input with 2px indigo ring, domain chips
P1..P7 with P1+P2 selected, zoom pill +/−/fit at the right edge); the canvas
shows ~150 nodes at three levels of detail: 14 labeled rounded node chips
(border hairline, 12px labels), ~50 medium 8px dots, and ~100 tiny 3px
context dots at 30% opacity; thin 1px hairline edges; the selected node
'heuristics-catalog' has a 2px indigo ring + bold label, its neighbor edges
are 2px indigo, all non-neighbor nodes dimmed to 40%; a floating legend
bottom-left inside the canvas: chips P1..P7 with counts (412, 388, 204, 161,
119…). Right rail 320px, label '02 · INSPECTOR': selected node card —
name 'heuristics-catalog' 16px/600, chip 'P2 · active', one-line summary,
score bars P=0.828 M=0.341 E=0.898 with mono values, confidence 0.912,
relations list (depends_on → retrieval-core · supersedes → heuristics-v1),
evidence ev-0081, ev-0082, provenance chip 'VERIFIED 5/5'; below it a small
'NEIGHBORS' list (5 rows). Bottom status bar as specified.

**02-activity**: SCREEN: Agent Activity Log. Primary zone: label '01 ·
EVENT STREAM'; filter toolbar (level chips INFO selected/WARN/ERROR, agent
chip 'all 50 ▾', ghost search field FOCUSED with 2px indigo ring, autoscroll
toggle ON); then a full-width dense table with EXACTLY 16 visible rows,
36px rows, columns: TIME (mono 11:58:02Z), LEVEL chip, AGENT, ACTION (mono:
query / log_event / record_outcome / apply_improvement), NODE
(heuristics-catalog), SHA8 (mono full 8 hex like 9f2c1ab4), DETAIL one line.
Row 4 is HOVERED (warm gray wash), row 7 is SELECTED (3px indigo left bar +
indigo tint), one WARN row 'unreadable payload' has an amber wash + WARN
chip. Right rail 320px, label '02 · AGENTS — 50': roster of 10 rows (agent
name, mono event count, ttl 03m 12s) with one row hovered; footer ghost
button 'View all 50 agents'. Bottom status bar.

**03-analytics**: SCREEN: Analytics. Page title 'Analytics'. KPI strip of 4
cards (NODES 1,284 · EDGES 3,917 · EVENTS TODAY 214 · STORE 46.2 MB — 11px
uppercase label, 24px mono value, stroke-only sparkline). Primary zone: one
large panel 'EVENTS PER DAY' (14 bars rising 120 → 214, indigo bars, mono
axis labels) + below it a half-height panel 'TOP NODES' table 6 rows (name,
domain chip, score mono 0.828, edges mono). Right rail 320px: panel 'DOMAIN
DISTRIBUTION' (5 horizontal bars P1..P5 with mono counts) + panel 'HEALTH'
(4 rows with PASS ok chips: store_readable, chain_integrity,
projection_fresh, leases_consistent) + panel 'RELEARN' (stepper IDLE → SCAN
→ DIFF → SYNC with IDLE active). Bottom status bar.

### V2 ink-mono

**THEME**: Pure white minimal, hierarchy by typographic scale ONLY — NO
cards, NO panel backgrounds, zones separated by 1px hairlines #E5E5E5 and
structural rules #111111. Ink #0A0A0A, secondary #525252, tertiary #A3A3A3.
ONE accent blue #1D4ED8 used EXCLUSIVELY for interactive elements (links,
focus rings, selected states) — never for decoration. Status = word + glyph
(INFO ·, WARN ▲, ERROR ■; semantic color may support: ok #15803D, warn
#B45309, danger #B91C1C). Inter with aggressive scale (display 40-48px/700
tracking −2%, section titles 16px/600, body 12px, micro 10px uppercase
tracking +0.12em) + JetBrains Mono 12px for all data. Radius 0-2px. Generous
whitespace.

**STRUCTURE**: A 48px header: wordmark 'KNOW YOUR CONTEXT' (13px/650,
letterspaced) left; nav as plain text links Overview Graph Activity
Analytics Health — the current one with a 2px solid black underline; right:
mono clock '11:58:02Z' + avatar circle. A 1px black rule under the header.
Content zones separated by hairlines only — no boxes. A left facet column
220px (filters), the main zone center, and a right column 340px separated by
a 1px black vertical rule for the inspector/detail. Bottom: one-line micro
strip 10px uppercase letterspaced ('50 AGENTS · 214 EVENTS TODAY · CHAIN
VERIFIED · 2026-11-05T11:58:02Z').

**STATES TO DRAW**: nav link current = 2px black underline; one log/list row
HOVERED (light gray wash #F5F5F5); one row SELECTED (3px black left bar +
600 weight); the filter search input FOCUSED (2px blue focus ring with 2px
offset); selected facet values in blue text with a small ✓; one blue text
link 'Show all 50'.

**01-graph**: SCREEN: Graph Explorer. Main zone: a hairline-framed graph
canvas (~750x640) containing ~150 nodes: 14 primary nodes as 20px outline
circles (1.5px stroke) with 12px labels to their right, ~50 secondary 6px
dots, ~100 context 2.5px dots; edges 1px #D4D4D4 except the selected
neighborhood in #111111; selected node 'heuristics-catalog' = SOLID INK
FILLED circle + 2px blue ring + bold label; one neighbor node drawn in hover
state (blue 1px ring); relation labels as 10px uppercase micro text at edge
midpoints (depends_on, supersedes); legend as a plain text block bottom-left
inside canvas ('P1 CORE 412 · P2 RETRIEVAL 388 · P3 INGEST 204 · P4 OPS 161
· P5 AUX 119'); zoom as text buttons '+ − FIT' top-right of canvas. Right
column 340px: 'heuristics-catalog' 20px/650; kv rows separated by hairlines
(domain P2 · kind leaf · version 3 · status active); scores as thin 2px
black bars with mono values P=0.828 M=0.341 E=0.898 conf 0.912; relations
list; evidence ev-0081 ev-0082; 'VERIFIED 5/5 ·' line. Left facet column:
'DOMAINS' checklist P1..P7 (P1+P2 checked in blue), 'KIND' (leaf/root),
search input focused.

**02-activity**: SCREEN: Agent Activity Log. Left facet column 220px:
'LEVEL' 3 rows with counts (INFO 15 ·, WARN 2 ▲, ERROR 1 ■ — WARN checked
blue); 'AGENTS' with a FOCUSED search input (2px blue ring) + 8 agent rows
with mono counts + blue link 'Show all 50'. Main zone: 18 log rows
separated by hairlines, 34px rows, columns: TIME mono 11:58:02Z · LEVEL
word+glyph · AGENT · ACTION mono (query / log_event / record_outcome /
apply_improvement) · NODE heuristics-catalog · SHA8 mono 9f2c1ab4; row 5
HOVERED (#F5F5F5 wash); row 9 SELECTED (3px black left bar, 600 weight); the
WARN row 'unreadable payload' marked 'WARN ▲' in amber. Bottom micro strip.

**03-analytics**: SCREEN: Analytics. Main zone: giant display row of 3
numerals 44px/700 ('1,284 NODES' · '3,917 EDGES' · '214 EVENTS TODAY') with
10px uppercase labels; below a hairline-separated chart zone 'EVENTS PER
DAY' — 14 thin 2px black vertical bars rising 120 → 214 with mono axis
labels; below that a two-column split by hairline: left 'TOP NODES' 6 rows
(name 12px/600 + mono score 0.828 + mono edges), right 'DOMAIN
DISTRIBUTION' 5 thin bars P1..P5 with mono counts. Right column 340px:
'HEALTH' 4 rows with 'PASS ·' marks (store_readable, chain_integrity,
projection_fresh, leases_consistent); 'RELEARN' as text steps 'IDLE → SCAN
→ DIFF → SYNC' with IDLE in 600 weight + blue; 'STORE' kv mono (46.2 MB ·
schema v5 · WAL). Bottom micro strip.

### V3 slate-calm

**THEME**: Calm dark minimal: base #101216, surface #16181D, raised #1C1F26,
hairline rgba(255,255,255,0.06), primary text #E8EAEE, secondary #9BA1AC,
ONE accent sky #38BDF8, status ok #34D399, warn #FBBF24, danger #F87171.
Depth ONLY via these flat surface steps + hairlines — absolutely no shadows,
no gradients. Inter + JetBrains Mono for data. Card radius 12px, controls
8px, chips 6px. Standard table rows 44px. Page padding 24px, gutters 16px.

**STRUCTURE**: A compact left sidebar 220px: brand row ('Know Your Context'
14px/600); section label 'CONSOLE' (10px uppercase, secondary) with
icon+text nav items (16px outline icons: Overview / Graph / Activity /
Analytics / Health — active item = sky-tinted pill + sky text); section
'SYSTEM' with Search / Settings; user card at the bottom (28px avatar +
'operator' + mono op@kyc.local). Main area: page title 18px/600 + subtitle
secondary; the most important panel sits on the RAISED surface with 24-28px
padding (brightest + roomiest = primary); supporting panels on surface with
16-20px padding; tertiary text at 60% opacity. A one-line mono status bar at
the bottom ('SEQ 10421 · CHAIN 9f2c1ab4 · 50 AGENTS · 11/11 CHECKS PASS').

**STATES TO DRAW**: active nav pill; one row HOVERED (surface wash +8%);
one row SELECTED (10% sky tint + sky text on its key column); one input
FOCUSED (2px sky ring); chips selected vs unselected; toggle ON; one graph
node in hover state (1px sky border + tint).

**01-graph**: SCREEN: Graph Explorer. Main area: one large raised graph
panel (padding 24px, ~860x620): toolbar inside (focused search input with
2px sky ring, domain chips P1..P7 with P1/P2 selected, zoom pill right);
canvas with ~150 nodes in 3 detail tiers: 14 rounded labeled node chips
(raised fill, hairline border), ~50 medium dots, ~100 tiny 30%-opacity dots;
selected node 'heuristics-catalog' with 2px sky ring + 8% tint halo, its
edges 1.5px sky, one neighbor node in hover state (1px sky border + tint),
all other nodes dimmed to 45%; floating legend bottom-left (chips P1..P7 +
counts). A FLOATING inspector panel (300px, raised + hairline border)
overlapping the canvas right side: 'heuristics-catalog' 16px/600, chip 'P2 ·
active', summary line, score bars P=0.828 M=0.341 E=0.898 conf 0.912,
relations (depends_on → retrieval-core · supersedes → heuristics-v1),
evidence ev-0081 ev-0082, chip 'VERIFIED 5/5'. Bottom status bar.

**02-activity**: SCREEN: Agent Activity Log. Main area: filter bar (level
chips — INFO selected sky tint; agent chip 'all 50 ▾'; search input FOCUSED
with sky ring; autoscroll toggle ON); log table on a surface panel, 44px
rows, grouped under time group headers ('11:58 — 11:50' micro uppercase
secondary), EXACTLY 12 visible rows + pagination footer ('1-12 of 214 ·
rows 12 ▾ · ‹ ›'); columns TIME mono · LEVEL chip (tint bg + colored text) ·
AGENT · ACTION mono · NODE · SHA8 mono full 8 hex; row 3 HOVERED (+8% wash),
row 6 SELECTED (10% sky tint + sky ACTION text), one WARN row 'unreadable
payload' amber chip. Right panel (surface, 300px): 'AGENT LOAD' — 8 rows
each with agent name + a thin horizontal load bar + mono count; footer line
'50 agents total · ttl 03m 12s'. Bottom status bar.

**03-analytics**: SCREEN: Analytics. Main area: KPI strip of 4 raised cards
(label 11px uppercase secondary, 26px/650 mono value, delta line, stroke-only
sparkline): NODES 1,284 · EDGES 3,917 · EVENTS TODAY 214 · STORE 46.2 MB;
below: one wide raised panel 'EVENTS PER DAY' (smooth area chart, sky
stroke, 14 points rising 120 → 214, mono axis labels); below it two
half-width panels side by side: 'TOP NODES' table 6 rows (name, domain chip
P1..P5, score mono 0.828, edges) and 'DOMAIN DISTRIBUTION' (5 thin bars
P1..P5 + mono counts); right column or bottom row panel 'HEALTH + RELEARN':
4 PASS rows (store_readable, chain_integrity, projection_fresh,
leases_consistent) + stepper IDLE → SCAN → DIFF → SYNC (IDLE active).
Bottom status bar.

### V4 modular-gray

**THEME**: Light cool-gray modular: canvas #EEF1F5, modules #FFFFFF with 1px
hairline #DCE2EA, primary text #101828, secondary #667085, ONE accent teal
#0D9488, status ok #16A34A, warn #D97706, danger #DC2626. Inter + JetBrains
Mono for data. Module radius 12px, controls 8px, chips 6px. NO shadows.
Module title 13px/600, KPI values 24px/650 mono. Dense table rows 38px.
Canvas padding 16px, gutters 16px.

**STRUCTURE**: A white 52px topbar: product name left; CENTER = tab nav
(Overview · Graph · Activity · Analytics — active tab = teal-tinted pill);
right = search field + avatar. Content = a strict 12-column modular grid on
the gray canvas: module SIZE encodes priority — exactly ONE primary module
spans 8 columns full height; secondary modules span 4 columns; tertiary
modules 4 columns half height; no two primary modules on one screen. Every
module has a 13px/600 title row with a hairline under it.

**STATES TO DRAW**: active tab pill; one row HOVERED (light gray wash); one
row SELECTED (3px teal left bar); one search input FOCUSED (2px teal ring);
multi-select filter chips with 3 selected (teal tint) and the rest outline;
toggle ON.

**01-graph**: SCREEN: Graph Explorer. Top strip module (12 cols, short):
'FILTERS' — multi-select chips P1..P7 (P1, P2, P4 selected in teal tint),
a FOCUSED search-to-focus input (2px teal ring), layout segmented control
(FORCE | TREE | RADIAL with FORCE active). Left primary module (8 cols,
full remaining height): graph canvas with ~150 nodes in 3 tiers (14 labeled
rounded chips, ~50 dots 8px, ~100 micro dots 30%), hairline edges; selected
node 'heuristics-catalog' = 2px teal border + teal label, neighbor edges
1.5px teal, one node in hover tint, non-neighbors at 35%; zoom segmented
pill at canvas corner; mini legend row inside the module bottom (P1..P7 +
counts). Right stack (4 cols): module 'INSPECTOR' (half height):
'heuristics-catalog' 14px/600, chip 'P2 · active', kv rows (kind leaf ·
version 3), score bars P=0.828 M=0.341 E=0.898 conf 0.912, relations
(depends_on → retrieval-core · supersedes → heuristics-v1), chip 'VERIFIED
5/5'; module 'EVIDENCE + NEIGHBORS' (half height): ev-0081, ev-0082 + 5
neighbor rows.

**02-activity**: SCREEN: Agent Activity Log. Left primary module (8 cols,
full height): 'EVENT STREAM' — toolbar (level chips INFO selected / WARN /
ERROR, agent chip 'all 50 ▾', FOCUSED search input with teal ring,
autoscroll toggle ON) + dense table EXACTLY 14 visible rows, 38px rows:
TIME mono 11:58:02Z · LEVEL chip · AGENT · ACTION mono (query / log_event /
record_outcome / apply_improvement) · NODE heuristics-catalog · SHA8 mono
full 8 hex 9f2c1ab4; row 4 HOVERED (gray wash), row 8 SELECTED (3px teal
left bar + tint), one WARN row 'unreadable payload' amber chip. Right stack
(4 cols): module 'AGENT ROSTER — 50' (taller half): 12 visible rows (agent
name + mono event count + ttl 03m 12s), one row hovered, footer link 'View
all 50'; module 'LEVEL MIX' (shorter half): 3 thin horizontal bars INFO 15 /
WARN 2 / ERROR 1 with mono counts.

**03-analytics**: SCREEN: Analytics. Primary module (8 cols, top, tall):
'EVENTS PER DAY' — 14 teal bars rising 120 → 214, mono axis labels, with 4
inline KPI readouts in a row at the module top (NODES 1,284 · EDGES 3,917 ·
EVENTS 214 · STORE 46.2 MB — 24px mono values). Right stack (4 cols): module
'HEALTH' (4 PASS rows: store_readable, chain_integrity, projection_fresh,
leases_consistent — ok chips) + module 'RELEARN' (stepper IDLE → SCAN →
DIFF → SYNC, IDLE active). Bottom row: module 'TOP NODES' (6 cols): table 6
rows (name, domain chip, score mono 0.828, edges mono); module 'DOMAIN
DISTRIBUTION' (6 cols): 5 thin bars P1..P5 with mono counts.

### V5 console-dark

**THEME**: Modern ops minimal on near-black zinc: base #09090B, panel
#111113, raised #18181B, hairline #232328. FOUR text brightness tiers:
#F4F4F5 (100%, primary values 24px), #D4D4D8 (85%), #A1A1AA (65%), #52525B
(40%, micro uppercase labels). ONE accent violet #8B5CF6, status ok #4ADE80,
warn #FACC15, danger #F87171. Inter for UI labels, JetBrains Mono for ALL
data and structural micro-labels. Radius 6px everywhere. Dense rows 36px.
Flat only — no shadows, no gradients, no emojis.

**STRUCTURE**: A 44px utility bar in mono: 'KYC ▪ PROD' left; tab nav
OVERVIEW · GRAPH · ACTIVITY · ANALYTICS (active tab = violet underline 2px +
100% bright text, others 40%); right = mono clock 11:58:02Z + status dot +
'LIVE'. Left facet column 220px (filters, micro labels 40% bright, values
mono 85%). Main zone center. A HORIZONTAL BOTTOM DOCK 200px spans the main
zone width = the detail/inspector surface (raised, hairline top border).
Very bottom: one-line mono status strip 11px ('SEQ 10421 · CHAIN 9f2c1ab4 ·
AGENTS 50 · TAIL ON · 11/11 CHECKS PASS').

**STATES TO DRAW**: active tab underline; one row HOVERED (wash #18181B);
one row SELECTED (2px violet left bar + 8% violet wash); facet search input
FOCUSED (2px violet ring); facet checkboxes with 2 checked (violet);
autoscroll toggle ON; one node hover (1px violet border).

**01-graph**: SCREEN: Graph Explorer. Left facet column: 'DOMAIN' checklist
P1..P7 (P1+P2 checked violet), 'KIND' (leaf · root), FOCUSED search input
(violet ring). Main zone: graph canvas on dot-grid (faint 2px dots, pitch
24px) with ~150 nodes in 3 tiers: 14 mono-labeled chips (raised fill,
hairline border, 11px mono), ~50 dots 8px, ~100 micro dots 30%; selected
node 'heuristics-catalog' = 2px violet border + 100% bright label, neighbor
edges 1.5px violet, one hover node (1px violet border + tint), all others
35%; legend bottom-left inside canvas (P1..P7 + counts, micro mono); zoom
pill right edge. BOTTOM DOCK 200px = inspector arranged as 4 horizontal
columns: col 1 'heuristics-catalog' (100% bright 15px + chip 'P2 · ACTIVE')
+ summary line; col 2 scores as thin bars with mono values P=0.828 M=0.341
E=0.898 CONF 0.912; col 3 relations (depends_on → retrieval-core ·
supersedes → heuristics-v1); col 4 evidence ev-0081 ev-0082 + 'VERIFIED
5/5'. Status strip bottom.

**02-activity**: SCREEN: Agent Activity Log. Left facet column: 'LEVEL'
3 rows (INFO 15 · WARN 2 · ERROR 1, mono counts, WARN checked violet);
'AGENTS' — FOCUSED search input (violet ring) + 8 rows (agent + mono count)
+ 'ALL 50' micro link. Main zone: log table dense 36px, EXACTLY 16 visible
rows: TIME mono 11:58:02Z · LEVEL (small caps word + 6px dot) · AGENT ·
ACTION mono (query / log_event / record_outcome / apply_improvement) · NODE
heuristics-catalog · SHA8 mono full 8 hex 9f2c1ab4; row 4 HOVERED
(#18181B wash), row 9 SELECTED (2px violet left bar + 8% violet wash), one
WARN row 'unreadable payload' with 6% amber wash + WARN label. BOTTOM DOCK
200px = selected event detail in 3 horizontal columns: col 1 'EVENT #10421'
(100% bright) + agent sub-144 + node; col 2 hash chain 'prev 7a1c9e02 →
entry 9f2c1ab4' full mono; col 3 payload preview one mono line + occurred
2026-11-05T11:58:02Z. Status strip bottom.

**03-analytics**: SCREEN: Analytics. Main zone: top KPI band of 4 mono
readouts in a row (40% micro label above, 24px/650 mono value 100% bright):
NODES 1,284 · EDGES 3,917 · EVENTS TODAY 214 · STORE 46.2 MB; below: wide
panel 'EVENTS PER DAY' (14 violet bars 120 → 214, mono axis labels) +
below it two panels side by side: 'TOP NODES' table 6 rows (name mono,
domain micro label, score 0.828, edges) and 'DOMAIN DISTRIBUTION' 5 thin
bars P1..P5 mono counts. Left facet column becomes 'SCOPE': time range
chips (24H selected violet / 7D / 30D), domain checklist P1..P7. BOTTOM DOCK
200px in 3 columns: 'HEALTH' (4 rows PASS · store_readable, chain_integrity,
projection_fresh, leases_consistent) · 'RELEARN' (IDLE → SCAN → DIFF →
SYNC, IDLE violet) · 'STORE' (46.2 MB · schema v5 · WAL · journal ok).
Status strip bottom.

---

## Aturan eksekusi V3 (driver)

1. Satu `designSystem` per style: dibuat dari generate pertama (01-graph),
   dipakai ulang untuk 02-activity dan 03-analytics. Style tidak boleh
   tercampur.
2. Urutan generate per style: 01-graph → 02-activity → 03-analytics.
3. Audit vision per layar: 7 butir checklist skill + 3 kriteria operator
   (hierarki jelas? interaktif tergambar? tidak menumpuk/kaku/kuno?).
   FAIL → perkuat prompt (BOOST per layar) → regenerate, maks 2 iterasi.
4. Artefak: `fase3/vX-name/{01-graph,02-activity,03-analytics}.{png,html}` +
   `STYLE.md` ringkas + `style-vX-name.zip` + `REPORT.md` (skor + alasan).
5. Tidak commit.
