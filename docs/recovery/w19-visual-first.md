# W19 (SUB-149) — Visual-first semua tab + merge 19→14

Tanggal: 2026-11-06. HEAD awal: 93932be. TIDAK commit (aturan). Kontrak beku tak disentuh.

## Bagian 1 — Pembelajaran (loop koreksi)

Dua foto operator dipelajari via vision 3 lintasan (full-frame + 4 zoom region terukur):
foto-1 = sales dashboard light-theme; foto-2 = emura.studio analytics (teal).
Pelajaran ditulis sebagai **bab T (T1–T11) di `docs/design/PATTERNS.md`** dengan sitasi
`foto-1`/`foto-2` per aturan.

Loop koreksi nyata (tercatat di T11): lintasan-1 menghasilkan 3 klaim yang dikoreksi zoom —
(a) value tag = pill → SALAH, rounded-rect r≈6; (b) y-axis kiri → SALAH, kanan untuk kartu
Platform value; (c) anatomi center donut → TIDAK TERBUKTI (terpotong frame), hanya yang
terlihat yang diklaim.

Verdict pembelajaran per domain ilmu:

| Domain | Verdict | Bukti |
|---|---|---|
| KPI cards (4-lapis, 2 anatomi) | TERVERIFIKASI | T1: foto-1 (label→nilai→delta ganda→baseline) + foto-2 (delta di atas nilai) |
| Bar chart (tanpa gridline, value tag) | TERVERIFIKASI | T3 via zoom: bar w≈28 gap≈20, tag r≈6 melayang, y-axis kanan |
| Line chart (tipis, 2 register) | TERVERIFIKASI | T4: register minimal (foto-1) vs interaktif (foto-2) |
| Donut | TERVERIFIKASI-PARSIAL | T5: ring tebal + badge % di ujung arc + legend terbaca; center hole terpotong frame — tidak diklaim |
| Dot-matrix (isotype) | TERVERIFIKASI | T6 via zoom: pitch ≈18, ramp satu-hue per baris, sumbu disupresi |
| Dark card prioritas | TERVERIFIKASI | T7: satu kartu gelap = anchor; foto-2 tanpa dark card = kontras |
| Hierarki/label/format angka | TERVERIFIKASI | T8 + T2 (split-tone desimal/unit) |
| Konsistensi semantik delta | TERVERIFIKASI | T9 dari cacat mockup foto-2 (panah ≠ frasa) |

## Bagian 2 — Merge tab (19 → 14)

| Merge | Hasil |
|---|---|
| Search + Browse | satu tab Search, segmented [Search\|Browse] (`search-seg-*`) |
| Health + Store + System | satu tab System, segmented 3 panel (`system-seg-*`) |
| Audit + Ledgers | satu tab Audit: timeline (capped) → Ledgers → Refs |
| Metrics + Bench | satu tab Metrics: visual hero+charts, bench table di detail |

Nav: Knowledge (Graph/Search/Activity) · Intelligence (Analytics/Code Intel/Proposals/Debt/
Extractions) · Operations (Metrics/Audit/Exports/Contracts) · System (System/Setup).
`WEBVIEW_FEATURES` di `serve/webview.py`: field `tab` 7 fitur diubah ke tab gabungan
(browse/refs→search, health/store→system, bench→metrics, ledgers/experience-rerank→audit)
— fitur dan rute TIDAK berkurang; `test_webview_coverage.py` tetap membuktikan semua.

## Bagian 3 — Visual-first per tab (data API nyata, SVG hand-made, no CDN/library)

- **Metrics**: 4 KPI 4-lapis (label/nilai/delta ganda/baseline+periode) + SATU dark card
  prioritas (health < max → health; capacity !ok → capacity; stale journals → journals;
  else all-nominal; CTA melompat ke tab terkait). Bar chart p50/operasi tanpa gridline
  dengan tag aksen (T3), line chart events/hari tipis 2px tanpa dot (T4), donut
  distribusi audit/domain/other dengan badge % di arc + legend (T5). Bench table dense
  + counters + capacity di bawah. Empty store: semua chart menampilkan empty state jujur
  (bukti: 20-metrics-empty.png, audit 4/4).
- **Activity**: dot-matrix densitas per hari (T6, top dot = level terburuk hari itu) +
  2 stat card (events in window, peak day) + stream yang sudah ada.
- **Search/Browse**: hasil = kartu (nama + kind chip + score bar ternormalisasi per set +
  lokasi mono); browse = strip KPI ukuran tabel + tabel paged.
- **Analytics**: KPI strip (nodes/communities/bridges/top pagerank) + leaderboard
  PageRank bar-list + kartu komunitas (cohesion bar) + kartu bridges (surprise bar).
- **Code Intel**: root card + lane per depth dengan chip (nama + kind badge + chip
  lokasi + chip via-edge); path = rantai chip berpanah; co-change = kartu dengan 2 bar
  kepercayaan; boundary = kartu pelanggaran bertitik merah.
- **Proposals**: strip KPI signal_counts + kartu per sinyal (level badge, reason chips,
  node link ke graph).
- **Debt**: KPI (candidates/budget/overflow) + kartu ber-rank badge + bar prioritas.
- **Extractions**: kartu provenance (status badge, confidence bar reported→effective
  dengan marker, pill ± corroboration); empty state K1 (ikon+heading+penyebab+hint).
- **Audit**: timeline rantai (rail dot; failed=merah, conflict=amber) + verify + refs.
- **Exports**: form + kartu hasil (dot hijau + link download + jumlah node) + empty state.
- **Contracts**: KPI (surface/knowledge ops/ops) + grup chip + aturan.
- **System**: Health = KPI status + chips checks + kartu findings; Store = cardGrid stats +
  tabel; Ops = hardware cards + relearn/switches/config/routes.
- **Setup**: stepper E8 (lingkaran state: SVG check/dash/nomor) + progress bar + teks
  "n / m steps done" (N6 ganda) + panel prefs.

## Audit vision per tab (server localhost nyata :18747, 1440×900, viewport dipin numerik)

| Tab | Screenshot | Verdict | Catatan |
|---|---|---|---|
| Graph | 01-graph.png | PASS | mekanik tak diubah; klaim auditor "tile P2 hilang" DIBANTAH numerik (tile 22×22 gradient ada); overlap marker `+` paralel DIPERBAIKI (pitch 26px, dipin harness; bbox dist 20.7 > diameter 17.5 layar) |
| Activity | 02-activity.png | PASS | iterasi: label tanggal tabrakan (label tiap kolom ke-2), svg ter-stretch 3.4× (akar: CSP memblokir setAttribute("style") → pakai style property), legend semantics |
| Search | 03/18 | PASS | score bar awalnya tak terlihat (akar: span inline di konteks non-flex; fix display:inline-block di CSS) — numerik 70×6 |
| Metrics | 04 / 20 | PASS | iterasi: overflow horizontal legend donut (grid 5/5/4 + svg donut fixed 150px), line di bawah fold (3 kolom satu baris), ellipsis legend "dom…" (H1: lg-label tanpa ellipsis), empty-store 4/4 |
| Analytics | 05 | PASS* | dead space data-jarang = residual H2b terdokumentasi; klaim "bar identik" dibantah numerik (100/61/48%) |
| Audit | 06 | PASS | timeline di-cap 320px agar ledgers + refs terjangkau fold |
| Proposals | 07 | PASS | seed diperkaya (node isolated) agar sinyal nyata tampil |
| Debt | 08 | PASS* | 4 kandidat → void bawah = H2b legal (grid berakhir rapi); didokumentasikan |
| Code Intel | 09/19 | PASS | chip diperkuat (kind badge + lokasi/via chips); "no callers" alpha_fn = semantik benar (dia caller, bukan callee) — bukti callees menampilkan lane |
| Extractions | 10 | PASS | empty state K1 menggantikan teks polos |
| System | 11/16/17 | PASS 5/5 per panel | iterasi: ellipsis "publish_cap…" di kpi-base (fix: wrap, bukan potong) + chips checks |
| Exports | 12/21 | PASS | iterasi: placeholder terpotong (fix) + empty state + kartu hasil ekspor nyata |
| Contracts | 13 | PASS* | void bawah = H2b terdokumentasi (data memang pendek) |
| Setup | 14 | PASS | stepper dibuktikan dengan sidecar progress nyata (6 langkah, 5 done 1 skipped) |

PASS* = lolos dengan residual dead-space data-jarang yang didokumentasikan (pola H2b:
grid kartu berakhir dengan padding bersih; tidak ada void ANTAR komponen).

## Gates

- pytest subset webview (12 file): **640 passed** exit 0.
- Full suite: 16085 passed / 2 failed — kedua failure di `tests/test_watcher_async.py`
  (timing async watcher); terbukti flake: file yang sama 15/15 hijau di HEAD bersih
  (stash) DAN dengan perubahan saya, dua arah.
- Determinisme: PYTHONHASHSEED 0/1/42 → 193 passed identik (ui_assets+coverage+graph_dom).
- Harness DOM: 65/65 (36 lama + pin overlap marker + mode switch + metrik visual).
- node --check app.js OK; ruff check + format OK; pyright 0 error.
- CSP: temuan nyata — inline `setAttribute("style")` diblokir; semua sizing via
  `el.style.*` (CSSOM). Tidak ada innerHTML/eval/CDN/emoji (test pins hijau).

## File

- `src/know_your_context/serve/webview_assets/{index.html,style.css,app.js}` — merge +
  visual-first semua tab.
- `src/know_your_context/serve/webview.py` — `WEBVIEW_FEATURES` tab fields (7 fitur).
- `tests/test_webview_ui_assets.py` — EXPECTED_TABS 14 + TestMergedTabs + TestVisualFirst.
- `tests/test_webview.py` — pin tab disesuaikan ke struktur baru.
- `tests/webview_dom_harness.mjs` — 14 tab + payload metrik + 17 asersi baru (65 total).
- `docs/design/PATTERNS.md` — bab T (T1–T11).
- `docs/recovery/w19-assets/*.png` — 21 screenshot bukti (14 tab + mode + empty store).
