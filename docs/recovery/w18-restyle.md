# W18 — Webview Restyle Total ke Foto-1 (SUB-146)

Restyle penuh webview produk (`src/know_your_context/serve/webview_assets/`) ke
bahasa desain foto-1 (`docs/design/graph-proto/index.html`, light, Linear-class,
aksen ungu `#7C3AED`) dengan mekanik graph foto-2 (expanded card + properties
panel). Cakupan: 19 tab / 30 fitur / 68 route — semua tetap berfungsi.

## Apa yang berubah

### Shell (index.html)
- `data-theme="dark"` → `data-theme="light"`; sidebar 208px: logo row
  (mark + KnowYourCtx + OPS badge), nav ber-group (Knowledge / Intelligence /
  Operations / System, 19 tab, item aktif = tint aksen), promo card "Live
  refresh" (badge `#liveness` pindah ke sini), user row operator.
- Topbar: breadcrumb `Webview / <Tab>` (live via `#crumb-tab`), command pill
  `⌘K` (klik / Ctrl+K / Cmd+K memfokuskan input primer tab aktif), status
  `#status` dengan dot semantik (default "ready"), primary button hitam
  `↻ Refresh` (re-fetch tab aktif).
- Semua `id`/`data-tab`/view yang dipakai app.js dan test dipertahankan.

### style.css — token foto-1
- `--bg-page #EBEDF1`, surface putih, hairline `#F0F0F0`/`#E5E7EB`,
  `--accent #7C3AED` + `--accent-soft #EDE9FE` + hot `#A78BFA`, semantik
  ok/warn/error dicadangkan, radius kartu 12px / kontrol 8px / chip 6px,
  shadow lembut saja, dot-grid 22px di canvas graph.
- Tabel: header uppercase micro-label, hairline horizontal saja, sel mono
  tanpa wrap mid-token (`word-break: normal; overflow-wrap: break-word`),
  sel kosong = em-dash, baris state = tint + border kiri (`row-warn`/`row-error`),
  kelas `dense` (28px) untuk panel sempit.
- Tanpa emoji UI, tanpa gradasi besar, tanpa shadow berat (dipin test).

### Graph (app.js) — mekanik foto-1 + foto-2
- Canvas 2D diganti DOM penuh: kartu node (`div.gnode`) + edge SVG bezier
  kubik lentur (anchor sisi menghadap tetangga, sumbu dominan) via
  `createElementNS` — kontrak no-innerHTML tetap.
- Klik node → kartu expand medium in-place (konten penuh multi-baris +
  footer badge meta kind/P/M/E/version + daftar koneksi berarah) +
  properties panel slide-in kanan (header + ×, seksi General kv / Scores bars /
  Provenance chips / Relations navigable / Hierarchy) + edge hot + refit ke
  area di kiri panel; × / klik luar → collapse + fit penuh kembali.
- Overlap resolver: kartu expand bergeser minimal agar tidak menutup tetangga.
- Pan (drag area kosong), zoom (wheel kursor-sentris + HUD −/+/Fit),
  fit-to-view saat load; marker `+` di midpoint edge (dilewati bila edge
  < 130px — disiplin spacing; klik menjelaskan read-only).
- Search hit dan baris Browse(nodes) membuka panel yang sama
  (`showTab("graph")` + `selectNode`).

### Perbaikan konten yang ditemukan audit (bug nyata, bukan kosmetik)
- `badge("ok")` sebelumnya jatuh ke `dim` — OK health/verify kini hijau benar.
- `audit-verify` hasil ok kini `ok-badge` (hijau).
- Hierarchy list di panel: tombol kini benar-benar wired (sebelumnya mati).
- Store: pluralisasi "1 row"; card-grid tidak lagi nested (bug auto-fill
  kolom tunggal); `auto-fill` → `auto-fit`.
- CodeIntel empty wording per op ("(no callers)" dst); placeholder layers
  diperlebar; Metrics 2 kolom (counts | capacity+latency); Analytics 2 kolom
  (centrality | communities) + bridges full-width; System 2×2 (relearn |
  switches | config | routes) dengan panel scroll-cap; Audit events full-width
  + refs 2 kolom + baris failed/conflict tinted; skeleton header tabel pada
  empty state (proposals/debt/extractions/ledgers).

## Test
- `tests/test_webview_ui_assets.py`: tema pin dark→light (diperkuat: 3 asersi
  token), + `TestGraphMechanics` (9 pin struktural mekanik graph).
- `tests/test_webview.py`: `test_index_light_theme_default`.
- BARU `tests/test_webview_graph_dom.py` + `tests/webview_dom_harness.mjs`:
  app.js nyata dijalankan di atas DOM shim minimal (node, tanpa jsdom) —
  35 asersi DOM-level: render kartu/edge bezier/marker, klik expand + panel
  open + kv + scorebar + hot edges + re-bend, navigasi via gnode-link, ×
  collapse, klik-luar collapse, zoom HUD, marker `+` inert-informatif, tab
  switch + breadcrumb; determinisme 2 run identik.
- Determinisme: PYTHONHASHSEED 0/1/42 → 334 passed identik (subset webview).

## Bukti visual (server localhost nyata, seed `w18_serve.py`, 1440×900)
`docs/recovery/w18-assets/01..20-*.png` — 19 tab + state graph-selected.
Audit vision per tab thd checklist 7 butir skill professional-ui-design,
iterasi sampai lolos:

| Tab | Skor | Catatan iterasi |
|---|---|---|
| 01 graph | 7/7 | edge+ clearance, parallel-edge lanes, fit center terverifikasi numerik |
| 02 graph-selected | 7/7 | setelah refit-ke-panel + overlap resolver (2 iterasi) |
| 03 activity | 7/7 | setelah seed lease TTL + expires_at conflict |
| 04 search | 7/7 | — |
| 05 browse | 7/7 | — |
| 06 metrics | 7/7 | 2-kolom; dead space dinilai intentional document surface |
| 07 health | 7/7 | badge ok hijau (bug badge() diperbaiki) |
| 08 analytics | 7/7 | 2-kolom; dead space intentional white surface |
| 09 audit | 7/7 | setelah nowrap AT/ACTOR + tint failed/conflict + refs 2 kolom |
| 10 proposals | 7/7 | skeleton header + empty state |
| 11 debt | 7/7 | em-dash merge target |
| 12 codeintel | 7/7 | setelah min-width layers + wording per op |
| 13 extractions | 7/7 | skeleton header |
| 14 store | 7/7 | setelah fix grid nested + pluralisasi |
| 15 system | 7/7 | 3 iterasi layout (2-kol → 3-kol → 4-kol → 2×2 + panel scroll-cap) |
| 16 bench | 7/7 | — |
| 17 ledgers | 7/7 | skeleton header ketiga ledger + badge jujur 0-checked |
| 18 exports | 7/7 | placeholder domain + link aksen + mono result |
| 19 contracts | 7/7 | badge frozen → info ungu (bukan teal) |
| 20 setup | 7/7 | command dalam `<code>` mono |

Residu diterima (dokumentasi jujur): butir "dead space" pada tab dengan data
memang sedikit (metrics/analytics/contracts/debt dengan seed 6 node) — void di
bawah konten adalah empty-state yang jujur, bukan celah antar komponen;
membesar-besarkan konten artificial akan melanggar anti-slop. Butir graph
"dead space" serupa: fit-to-view terverifikasi centering numerik; bentuk arc
radial 6 node adalah properti data.

## Gates
- `ruff check` + `ruff format --check` + `pyright`: 0 pada semua file test yang
  disentuh (webview_assets bukan Python).
- `node --check app.js` ✓; harness DOM 35/35; determinisme ✓.
- Full suite: lihat board (dijalankan; hasil dicatat di board).
- TIDAK commit; kontrak beku tak disentuh (hanya webview_assets + tests +
  dokumen ini).
