# W20 (SUB-150) — Audit data mentah + visual level kompleks

Tanggal: 2026-11-06. HEAD awal: dbdc77a. TIDAK commit (aturan). Kontrak beku tak disentuh.
Skill: professional-ui-design + PATTERNS.md bab T (foto-1 sales dashboard, foto-2 emura.studio).
Server bukti: localhost :18750, store seed `w20-assets/seed_serve.py` (21 hari event multi-jam
multi-agen, 9 snapshot health, 5 improvement proposals (2 applied), 8 extraction queue rows
(4 status), 10 refs (3 kind × 3 status), 6 bench ops) — turunan ter-verifikasi dari seed W19.

## Bagian 1 — AUDIT DATA MENTAH per tab (jujur, dari kode + screenshot before)

Legenda: RAW = tabel/list/angka polos yang layak divisualkan; LOW = sudah visual tapi
level sederhana (tanpa hover/tooltip/interaksi); OK = sudah visual memadai; BY-DESIGN =
raw memang tepat (inspeksi mesin / escape hatch).

| # | Tab | Yang masih RAW / LOW | Visual kompleks yang tepat | Keputusan |
|---|-----|----------------------|----------------------------|-----------|
| 1 | Graph | kv rows di detail panel | — | BY-DESIGN (inspeksi field); graf DOM + score bar sudah OK |
| 2 | Activity | dot-matrix density LOW (tanpa hover, tanpa nilai per sel, granularitas hari saja); 2 KPI tanpa sparkline; sebaran per agen hanya implisit di stream | dot-matrix HEATMAP hari×daypart / weekday×jam dengan tooltip (count + level mix) + segmented control; proportion bar per agen (avatar/label/nilai/%); sparkline di KPI | BANGUN ✓ |
| 3 | Search | hasil = kartu + score bar (OK); browse = tabel paged | — | BY-DESIGN (browse = escape hatch tabel mentah, terdokumentasi W19) |
| 4 | Metrics | line 1-seri tanpa tooltip (LOW); bench table angka polos (RAW); counters + capacity table (RAW); KPI tanpa sparkline | multi-series line interaktif (events/day + health score, dual-axis honest) dgn crosshair + tooltip + legend toggle + segmented [Both\|Events\|Health]; bench table → heatmap cells per kolom; sparkline per KPI; counters/capacity tetap dense (ops surface) | BANGUN ✓ |
| 5 | Analytics | KPI + bar-rows OK; ukuran komunitas hanya di badge teks | proportion bar share ukuran komunitas | BANGUN ✓ |
| 6 | Audit | refs status table 4 baris angka (RAW); health ledger 9 baris identik (RAW); debt/experience ledger JSON (RAW) | refs → proportion bar (pending/resolved/failed semantik) + heatmap table kind×status; health ledger → line skor (register minimal T4) di atas tabel; ledger JSON tetap raw | BANGUN ✓ |
| 7 | Proposals | signal_counts = 5 kartu KPI polos (RAW); TIDAK ADA pipeline | funnel proposed→applied dari /api/improvements (route sudah milik fitur proposals); proportion bar signal mix menggantikan strip KPI polos | BANGUN ✓ |
| 8 | Debt | KPI + priority bars OK; komposisi aksi implisit | proportion bar kandidat per action (hanya bila >1 jenis aksi — satu jenis = 100% bar = noise, disembunyikan jujur) | BANGUN ✓ |
| 9 | Extractions | kartu OK; TIDAK ADA pipeline queue | funnel submitted→decided→applied dari /api/extractions/summary (route sudah milik fitur) + catatan end-to-end % + rejected | BANGUN ✓ |
| 10 | Code Intel | lanes/chips OK; impacted files = run teks koma | chip list | BANGUN ✓ |
| 11 | System/Health | KPI + finding cards OK | — | OK |
| 12 | System/Store | tables = tabel nama+rows (RAW); kartu dipilih alfabetis (bug: tabel terbesar ke-3 tak dapat kartu) | proportion bar top tabel (share rows) + kartu diranking by rows | BANGUN ✓ |
| 13 | System/Ops | switches/config/routes tabel | — | BY-DESIGN (permukaan ops mesin; tabel dense = densitas tepat) |
| 14 | Setup | stepper + progress OK; prefs 3 baris | — | OK |

## Bagian 2 — Yang dibangun (SVG/DOM hand-made, data API nyata, no library/CDN)

Builder baru di `app.js` (semua via createElementNS/createElement + textContent; sizing via
properti `el.style.*` — CSP memblokir `setAttribute("style")`, pelajaran SUB-149):

1. **`multiLineChart`** (T4 register interaktif, foto-2 "Revenue Breakdown"): 2 seri 2px
   (events/hari aksen + health score netral gray), **dual-axis honest** (events kiri, health
   kanan — unit beda tak pernah berbagi skala diam-diam), gridline hairline y (4), area tint
   lembut seri-1, end-marker, **crosshair vertikal snap nearest-x**, **tooltip kartu floating**
   (header tanggal + baris legend dot+label+nilai per seri, flip di tepi), **legend clickable**
   (toggle dim seri tanpa menghapusnya), **segmented control** Both/Events/Health. Dipakai di
   Metrics ("Activity & health over time", kartu full-width).
2. **`dotHeatmap`** (T6 + intensitas GitHub): grid dot rows×cols, ramp satu-hue 5 langkah,
   **ring merah** pada sel berisi error, **tooltip per sel** (judul day·daypart + count +
   info/warn/error), label baris kiri + label kolom bawah (tiap 2 kolom). Dipakai di Activity
   dgn segmented **Daily (4 daypart × 21 hari) / Hourly (7 weekday × 24 jam)**.
3. **`proportionBar`** (T10 stacked): satu track penuh, segmen ∝ nilai, hover spotlight
   (segmen lain redup) + tooltip count/%, **legend inline** (swatch/avatar + label + nilai
   mono + % kanan). Dipakai: Audit resolution mix (hijau/amber/merah semantik), Activity
   events-by-agent (avatar nomor agen), System/Store table size share (top 6 + other),
   Analytics community size share, Proposals signal mix, Debt action mix.
4. **`funnelChart`**: tier bertingkat centered, lebar ∝ nilai, konversi % di celah antar-tier
   (kontras diperkuat setelah audit), ramp ungu pekat (white text ≥ AA di semua tier).
   Dipakai: Proposals (proposed→applied dari event log nyata), Extractions
   (submitted→decided→applied + catatan end-to-end + in-review + rejected — partisi jujur,
   bukan stage palsu).
5. **`heatmapCols`**: post-pass tabel — kolom numerik dapat latar intensitas aksen
   (alpha 0.06–0.34 ∝ maks kolom), sel terpanas bold. Dipakai: bench table Metrics,
   matriks kind×status di Audit.
6. **`sparkline`** (E4 stroke-only + end dot) masuk `kpiCard` (foto-2: nilai kiri, spark
   kanan). Dipakai: KPI Health score + Events total (Metrics), 2 kartu stat Activity.
7. **`segControl`**: skin .chart-seg skala kartu. Dipakai: Metrics trend, Activity heatmap.
8. **`lineChart`** (register minimal) diperbaiki: domain di-pad 8% (atau ±1 utk seri datar)
   + label min/max muted — garis datar tak lagi menempel tepi atas. Dipakai: health ledger
   (Audit). **`plural()`** helper memperbaiki "1 candidates"/"1 edges"/"+1 corroborators".

## Bug nyata ditemukan audit vision → diperbaiki + dipin

1. **`.barrow .bf` tak pernah terlihat** (Analytics PageRank, co-change): span inline di
   track non-flex mengabaikan width/height → fill 0px. Dibantah-dulu-secara-numerik lalu
   dibuktikan: sebelum fill 0px; sesudah fill 881/881/541/541px pada track 881px
   (100/100/61/61%). Fix: `display: block` (kelas bug yang sama dgn pelajaran W18 span
   inline; pin baru `test_barrow_fill_is_block_level`).
2. **Badge status extraction selalu abu**: mapping lama mengenal `accepted/pending` padahal
   status nyata = `pending_review/corroborated/contradiction_flagged/applied` → semua jatuh
   ke "dim". Fix: mapping status nyata (flagged/rejected=error, pending=warn,
   corroborated=info, applied=ok).
3. **Confidence 12 desimal** ("0.820000000000 → 0.740000000000") → 2dp. Priority debt 12dp
   → 4dp. "+0 corroborators" hijau (bohong) → flat. "1 review candidates" → plural().
4. **Kartu store dipilih alfabetis** (unresolved_refs 10 baris kalah kartu dgn tabel 1 baris)
   → ranking by rows desc.
5. **LineChart seri datar menempel tepi atas tanpa label** → domain pad + min/max label.
6. **Funnel tier kontras ~3:1** (white on #8B5CF6) → ramp pekat (#5B21B6..#8B5CF6).
7. **Funnel "reviewed" ambigu** (4 decided vs 4 in-review = angka sama, makna beda) →
   stage di-rename "decided" + catatan end-to-end/rejected eksplisit.
8. **"Peak day" unit terpotong** ("32 eve…") → label membawa unit, value bersih.
9. **Label hari heatmap terlalu jarang** (celah 11 kolom) → label tiap 2 kolom.

Klaim auditor yang DIBANTAH numerik: "heat-hi tak bold" — computed fontWeight 700 vs 400,
bg rgba(124,58,237,0.34), 5 sel (harness + browser nyata). "Proporsi bar signal mix
bergradasi" — bar 1 segmen 100% solid #7C3AED (multi-segmen terbukti di Activity/Audit).

## Verdict level profesional terukur per tab (vs referensi, per butir ilmu)

Checklist 7 butir skill (B1 komponen spec, B2 tanpa trunc/mid-token, B3 satu aksen/semantik,
B4 grid/radius, B5 mono/hierarki, B6 densitas/state, B7 viewport-fit) + butir kompleks W20:
**C1** tooltip/crosshair interaktif bekerja, **C2** nilai terbaca tanpa hover (legend/label),
**C3** hover state ada, **C4** data nyata API (bukan mock).

| Tab | B1-B7 | C1 | C2 | C3 | C4 | Verdict | Bukti |
|-----|-------|----|----|----|----|---------|-------|
| Graph | 7/7 (W19, tak diubah) | n/a | ✓ | ✓ | ✓ | PASS | final-01 |
| Activity | 7/7 | ✓ tooltip sel (count+mix) | ✓ legend ramp+ring | ✓ hover dot | ✓ /api/activity | PASS | final-02, 21, 22 |
| Search | 7/7 | n/a | ✓ score bar terlihat | ✓ | ✓ /api/search | PASS | final-03, 15 |
| Metrics | 7/7 | ✓ crosshair+tooltip+legend toggle | ✓ legend nilai terbaru + axis ganda | ✓ hover snap | ✓ /api/metrics{,/series}+/api/bench | PASS | final-04, 19, 20 |
| Analytics | 7/7 | n/a | ✓ share legend + bar terisi | ✓ | ✓ /api/analytics | PASS | final-05 |
| Audit | 7/7 | ✓ tooltip segmen mix | ✓ legend nilai/% + matriks label | ✓ hover segmen | ✓ /api/refs(+3 status)+/api/ledger/health | PASS | final-06, 26 |
| Proposals | 7/7 | n/a | ✓ funnel nilai + konversi % | ✓ hover tier | ✓ /api/proposals+/api/improvements | PASS | final-07 |
| Debt | 7/7 | n/a | ✓ (action bar disembunyikan jujur: 1 jenis) | ✓ | ✓ /api/debt | PASS | final-08 |
| Code Intel | 7/7 | n/a | ✓ lanes/chips | ✓ | ✓ /api/codeintel | PASS | final-09 |
| Extractions | 7/7 | n/a | ✓ funnel + catatan end-to-end | ✓ hover tier | ✓ /api/extractions{,/summary} | PASS | final-10 |
| System | 7/7 per panel | n/a | ✓ share tabel | ✓ hover segmen | ✓ /api/store/* | PASS | final-11, 16, 17 |
| Exports | 7/7 (W19) | n/a | ✓ | ✓ | ✓ | PASS | final-12, 21→25 |
| Contracts | 7/7 (W19) | n/a | ✓ | ✓ | ✓ | PASS | final-13 |
| Setup | 7/7 (W19) | n/a | ✓ | ✓ | ✓ | PASS | final-14 |

Iterasi audit vision: ronde-1 menemukan 9 bug nyata (di atas), ronde-2 PASS semua.
Residual terdokumentasi jujur: % legend bisa berjumlah 99/101 (pembulatan per-segmen);
health series mulai pertengahan chart bila snapshot memang baru ada (data jujur, bukan bug);
crash renderer browser antar-panggilan = flake harness, bukan app (halaman + 14 tab +
interaksi terbukti sehat saat tab hidup; DOM numerik diverifikasi).

## Gates

- Harness DOM node: **109/109** (65 W19 + 44 pin W20 baru: multi-line/crosshair/tooltip/seg/
  legend toggle, heatmap sel+ring+tooltip+mode, funnel×2, proportion bar+avatar, sparkline,
  heat cells, refs matrix, health line) — 2 run identik.
- pytest subset webview (12 file): **650 passed**.
- Determinisme: PYTHONHASHSEED 0/1/42 → 203 passed identik (ui_assets+coverage+graph_dom).
- ruff check + format: 0. pyright: 0/0/0. node --check: OK.
- Pin struktural baru: TestVisualComplex (10 test) di tests/test_webview_ui_assets.py.
- Full suite: **16097 passed, 1 skipped, 0 failed** (exit 0, 2148s).

## File

- `src/know_your_context/serve/webview_assets/app.js` — +builder kompleks (multiLineChart,
  dotHeatmap, proportionBar, funnelChart, heatmapCols, sparkline, segControl, makeTooltip,
  plural) + rewiring 8 tab + 9 perbaikan bug.
- `src/know_your_context/serve/webview_assets/style.css` — skin W20 (mls-*, viz-tip,
  dotheat, propbar, funnel, heat, chart-seg, kpi-spark) + fix `.barrow .bf` display:block +
  funnel-conv kontras + pb-swatch border.
- `tests/webview_dom_harness.mjs` — 65 → 109 asersi + payload canned endpoint baru.
- `tests/test_webview_ui_assets.py` — +TestVisualComplex (10 test) + pin barrow fill.
- `docs/recovery/w20-assets/` — 42 screenshot (before/final per tab + interaksi) + seed_serve.py.
