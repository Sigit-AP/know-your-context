# W18 — Graph Fidelity Fix (SUB-147)

Menutup 3 celah terukur verdict-vs-foto-1 pada tab Graph, lalu audit ulang
dengan standar level profesional (koreksi operator: tujuan = level profesional
setara, BUKAN kemiripan piksel; foto-1 hanya bukti seperti apa level itu).

## 3 celah yang ditutup

1. **Gradasi chip unik per node** — `assignTileGradients()` di app.js: FNV-1a
   32-bit dari `node.id` memilih slot dasar pada palet kurasi 10 gradasi
   (violet→pink→amber, blue→orange, teal→violet, magenta→violet,
   pink→lavender, amber→orange→red, cyan→indigo, lime→teal, rose→orange,
   sky→emerald); kolisi di-probe maju dalam urutan id menaik sehingga dua
   node berbeda tidak pernah berbagi gradasi selama palet punya slot bebas
   (reuse hanya bila node > 10 — langit-langit terdokumentasi). Endpoint
   hierarki (`kind` root/leaf) tetap dark ink `#1F2937` — sinyal anchor.
   Deterministik murni dari node id/kind: stabil lintas reload (dipin
   harness) dan lintas proses (dipin test determinisme 2 run + hash seeds).
2. **Kartu hairline + menu selalu terlihat** — semua `box-shadow` dihapus
   dari `.gnode` (base/hover/selected/expanded); border 1px hairline
   `var(--border)`, hover = border aksen, selected = border aksen 1.5px.
   `.gnode-menu` (`···`) kini elemen nyata di setiap kartu, tidak pernah
   hover-gated (dipin: tidak ada `:hover`/`display: none` pada rule-nya).
3. **Stats di strip HUD bawah** — `#graph-stats` pindah dari toolbar ke
   `.hud-stats` di samping zoom pill: `Nodes: N · Edges: M · Chain: verify`.
   Pill Chain ter-wire ke `/api/audit/verify` (endpoint heavy yang sama
   dengan tab Audit; hanya atas aksi user) → `valid · <n>` hijau / `BROKEN @
   seq <n>` merah. Mode layout + flag TRUNCATED pindah ke atribut `title`
   (tetap jujur, tak menghilang).

## Bug nyata tambahan yang ditemukan audit vision (diperbaiki + dipin)

- **Heading "Hierarchy (callers / callees)" hilang** saat fetch resolve:
  `clear(hierarchy)` menghapus eyebrow heading beserta loading note. Fix:
  hanya loading note yang di-remove. Dipin harness ("hierarchy heading
  survives async load").
- **Panel Relations under-report**: API `/api/node/<id>` hanya mengembalikan
  relasi outgoing non-parent; edge `parent_of` dan incoming tak tampil di
  panel padahal terlihat di kartu. Fix: merge edge level-graph (dedupe
  kind+target, incoming diberi panah `←`). Dipin harness ("panel relations
  match the card connections", 3 baris pada fixture).
- **Baris relasi terkonkatenasi** (`supports → n-deltaparent_of → n-delta`):
  `.linkish` inline → `display: block`. Dipin di test_webview_ui_assets.py.

## Bukti (server localhost nyata, seed w18_serve.py, 1440×900)

- `w18-assets/21-graph-fidelity.png` — graph normal: 6 kartu, tile gradasi
  unik (n-root dark ink), hairline tanpa shadow, menu `···` terlihat di
  semua kartu, HUD bawah `− 89% + Fit · Nodes: 6 · Edges: 7 · Chain: valid ·
  18` (pill diklik nyata → endpoint verify asli).
- `w18-assets/22-graph-selected-fidelity.png` — graph selected: panel penuh
  tanpa scroll (General/Scores/Provenance/Relations 3 baris/Hierarchy
  heading + empty state miliknya), hot edges violet, gradasi kartu lain
  utuh.

### Verifikasi numerik (meng-ungguli klaim visual auditor)

- DOM nyata: 6 node → 5 gradasi berbeda + 1 dark; `getComputedStyle`
  box-shadow `none`, border `1px rgb(240,240,240)`; `.hud-stats` di dalam
  `#graph-hud`; toolbar tanpa stats.
- Klaim auditor "expanded card menutup Rollback" **DIBANTAH angka**: bbox
  expanded bottom=726 vs n-delta top=734 (gap 8px, overlap=False) pada 89%;
  konfigurasi sama pada 59% (gap 5px). Overlap resolver menjamin
  non-intersection di koordinat kanvas; transform uniform menjaganya di
  layar.

## Audit profesional — 10 signature, before → after

Standar: skill professional-ui-design (hierarki, usability, state interaksi,
disiplin spacing/tipe/warna, kejelasan data). Before = HEAD e48e719
(SUB-146, bukti 01/02-graph*.png + kode).

| # | Signature | Before | After |
|---|---|---|---|
| 1 | Identitas visual node: gradasi unik per node, deterministik dari id | FAIL (semua tile uniform `var(--ink)`) | PASS (5 gradasi beda pada 6 node, stabil lintas reload) |
| 2 | Anchor hierarki: root/leaf dark ink | FAIL (tak ada pembedaan) | PASS (n-root dark; aturan kind root/leaf) |
| 3 | Disiplin elevasi: kartu hairline, tanpa shadow noise | FAIL (shadow base+hover+ring+float) | PASS (`box-shadow: none` dipin) |
| 4 | Affordance menu selalu terlihat | FAIL (elemen menu tidak ada) | PASS (`···` di semua kartu, non-hover) |
| 5 | Telemetri kanvas di HUD bawah | FAIL (stats di toolbar) | PASS (Nodes/Edges/Chain + verify ter-wire) |
| 6 | Kejelasan data panel: Relations = koneksi kartu | FAIL (parent_of/incoming hilang) | PASS (3/3 edge tampil, dedupe benar) |
| 7 | Ketahanan async: heading section tak hilang | FAIL (clear() menghapus heading) | PASS (dipin harness) |
| 8 | Satu baris per relasi | FAIL (laten inline, terbukti saat 3 relasi) | PASS (`display: block` dipin) |
| 9 | Edge bezier bersih + hot state + marker disiplin | PASS (SUB-146) | PASS (tak berubah) |
| 10 | Hierarki tipografi + spacing + satu aksen | PASS (SUB-146) | PASS (tak berubah) |

**Skor: before 2/10 (20%) pada signature ini → after 10/10 (100%).** Target
≥90% terlampaui. Dua audit vision awal menemukan 3 FAIL nyata (heading
hilang, relations konkatenat, klaim overlap) — dua diperbaiki, satu
terbantahkan numerik.

## Gates

- Harness DOM: 48/48 passed (36 → 48 asersi: +12 untuk identitas tile,
  determinisme reload, menu, HUD stats, chain pill, kelengkapan panel).
- Determinisme: harness 2 run identik (test pytest) + PYTHONHASHSEED 0/1/42
  → 76p identik (ui_assets + graph_dom); subset webview 9 file = 582p.
- `node --check app.js` ✓; `ruff check` + `ruff format --check` 0 pada
  tests yang disentuh; `pyright` 0/0/0 pada 2 file test.
- TIDAK commit; scope terjaga: `serve/webview_assets/{app.js,index.html,
  style.css}`, `tests/{webview_dom_harness.mjs,test_webview_ui_assets.py}`,
  dokumen ini. `test_webview_graph_dom.py` tidak perlu diubah (kontrak
  harness-nya menguat via file harness itu sendiri).
