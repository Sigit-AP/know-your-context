# W15 — Permukaan API & Konfigurasi Env (SUB-131)

## Tujuh pertanyaan pakar (dijawab SEBELUM menulis kode)

1. **Masalah**: operator dan alat eksternal hanya bisa membaca sebagian kecil
   isi store lewat HTTP. 21 route `/api` melayani payload tab agregat, tapi
   tidak ada akses baris-per-baris yang jujur ke tabel nyata (symbols,
   graph_edges, unresolved_refs per status, evidence, revisions, ledger
   hash-chain, per-operasi bench, provenance per node, export subgraph).
   Metrik verdict terukur: `http_routes` 30 vs codegraph 66, `env_vars` 43 vs
   97. Masalahnya BUKAN "kurang endpoint" — masalahnya adalah state nyata
   store tidak dapat diaudit lewat permukaan baca-saja.
2. **Kekurangan keras / tidak boleh rusak**: (a) viewer tetap read-only
   murni — tidak ada route yang menulis file/store (sidecar extraction dan
   experience ledger hanya dibuka bila SUDAH ada, ledger debt JSONL hanya
   dibaca); (b) kontrak beku 14 ops + fixture v1/v2 tak disentuh; (c)
   middleware.py, http_api.py, ops/config.py di luar scope — error typed dan
   rate-limit diimplementasikan di dalam webview.py dengan envelope yang
   identik (`{"error": {code, message, retryable}}`); (d) determinisme:
   payload tetap fungsi murni state store; (e) fail-closed: semua parse env
   strict, semua bound dicek, id tak dikenal 404.
3. **Tujuan terukur**: (a) API_ROUTES 21 → 55+ route (literal `/api` terukur
   verdict 30 → 60+); (b) 40+ env var baru teregistrasi di ops/switches.py
   sebagai config knob ter-parse strict fail-closed, semuanya DIKONSUMSI
   webview (bukan dekorasi); (c) 200+ test baru hijau; (d) gate: ruff,
   pyright, bandit bersih pada file scope; full suite hijau.
4. **Manfaat**: operator dapat men-drill-down setiap tab UI sampai baris
   tabel; alat eksternal dapat men-tail ledger hash-chain, memverifikasi
   rantai audit, dan mengambil export subgraph tanpa menulis disk; setiap
   limit/budget viewer dapat di-tune operator lewat env tanpa menyentuh kode.
5. **Pelajaran dari masalah**: route dummy/payload kosong adalah kebohongan
   antarmuka — setiap route baru WAJIB punya test yang membuktikan isi
   respons berasal dari baris store nyata yang di-seed test itu sendiri.
   Env var tanpa konsumen adalah inflasi metrik — setiap knob baru WAJIB
   mengubah perilaku nyata yang dipin test.
6. **Prinsip desain**: (a) fail-closed di semua batas (parse, range,
   keberadaan); (b) fail-open TIDAK dipakai di jalur data — sidecar absen =
   jawaban jujur "absent", bukan pembuatan file; (c) satu situs baca per env
   var (greppable, preseden switches.py); (d) rate-limit per kelas route
   (read/heavy/export) dengan bucket token ber-clock-injeksi, mematikan
   dirinya hanya lewat knob eksplisit.
7. **Kill criteria**: berhenti menambah route bila route baru tidak punya
   data nyata untuk disajikan; berhenti menambah knob bila knob tidak punya
   konsumen nyata; bila full suite merah karena perubahanku setelah 2
   perbaikan, revert ke HEAD dan laporkan.

## Rencana (arsitektur → TDD → verify → review)

### A. ops/switches.py — registry config knob (46 env var baru)

`ConfigKnob` (frozen): name dotted-lowercase, env `KNOW_YOUR_CONTEXT_WEBVIEW_*`,
kind int/float/bool, default, minimum/maximum inklusif, affects. `load_knobs()`
membaca tiap env eksplisit (satu situs greppable per var, preseden
`load_switches`), parse strict (`SwitchError INVALID_VALUE` pada input
ambigu), range fail-closed. `KnobBoard`: `.value(name)`, `.source(name)`
(default|env), `.dump()`, `describe_knobs()` markdown. switches.py tetap
stdlib + enums saja (tanpa import webview) — default knob adalah source of
truth, test mem-pin parity dengan konstanta webview.

### B. serve/webview.py — 37 route baru (21 → 58) + rate limit + wiring knob

Route baru (semua GET, data nyata):

| group | routes |
|---|---|
| graph CRUD-ish | `/api/nodes` (paged, filter kind/domain), `/api/edges` (paged node_relation), `/api/node/{id}/edges`, `/api/node/{id}/evidence`, `/api/node/{id}/revisions` |
| symbols | `/api/symbols` (paged, filter), `/api/symbols/{node_id}`, `/api/search/symbols` (prefix autocomplete) |
| refs per status | `/api/refs/pending`, `/api/refs/failed`, `/api/refs/resolved` |
| queue | `/api/extractions/{receipt_id}`, `/api/extractions/summary` |
| ledger tails | `/api/ledger/health`, `/api/ledger/debt`, `/api/ledger/experience`, `/api/audit/verify` (verify_chain) |
| bench | `/api/bench/{operation}` |
| debt | `/api/debt/report` (queue + burndown ledger) |
| provenance | `/api/provenance/{node_id}` (5-tautan verify_provenance) |
| switches/config | `/api/switches/{name}`, `/api/config`, `/api/config/{name}` |
| health breakdown | `/api/health/findings?severity=`, `/api/health/checks` |
| metrics timeseries | `/api/metrics/buckets?bucket=day|kind|actor`, `/api/metrics/operations` |
| store | `/api/store/tables`, `/api/store/tables/{name}`, `/api/store/schema` |
| analytics pecahan | `/api/analytics/centrality`, `/api/analytics/communities`, `/api/analytics/bridges` |
| export (read-only, in-memory) | `/api/export/subgraph`, `/api/export/node/{id}` |
| discovery | `/api/routes` |

Konsistensi: `ApiRoute` +field `route_class` (read|heavy|export, default read);
rate limiter token-bucket per kelas di dalam handler (429 `RATE_LIMITED`,
`retryable: true`, header `Retry-After`); feature-flag knob mematikan grup
route secara fail-closed (404 seperti route tak dikenal); semua error lewat
envelope typed yang sama; caps/limits membaca KnobBoard (default = konstanta
lama, perilaku historis identik tanpa env).

### C. Test (target 200+)

- `tests/test_ops_knobs.py` (~80): registry wellformed/unik/prefix, parse
  strict per kind, range fail-closed, provenance, describe, parity default vs
  konstanta webview, konsumsi nyata oleh server.
- `tests/test_webview_api_v3.py` (~170): per route — bukti data nyata dari
  seed test, validasi 400, 404, flag-disable 404, rate-limit 429 nyata
  (clock injeksi), read-only invariance byte-level.

## Hasil (diisi setelah build)

### Terbukti

- **REST surface**: API_ROUTES 21 → 59 (58 milik W15 + `/api/codeintel` milik
  sibling sa-1 yang merge bersih ke tabelku). Metrik verdict terukur NYATA
  via `bench/verdict/metrics.py` di tree kerja: `http_routes` 30 → **68**
  (target 60+; codegraph 66 — kini di atasnya), `env_vars` 43 → **90**
  (+47 env var baru; target 40+ terpenuhi; codegraph 97 belum terlampaui).
- **Env config**: 46 knob `webview.*` di `ops/switches.py` (ConfigKnob
  int/float/bool, bounds inklusif + minimum eksklusif, parse strict
  `SwitchError INVALID_VALUE/OUT_OF_RANGE`, cross-check pasangan
  default≤max fail-closed). Satu situs baca `environ.get(ENV_...)` per knob
  (greppable). SEMUA knob dikonsumsi nyata oleh webview (caps, default,
  rate limiter, trace capacity, shutdown join, heartbeat poll, feature
  flags) — dibuktikan test konsumsi, bukan dekorasi.
- **Konsistensi**: envelope error tunggal `{error:{code,message,retryable}}`
  di semua jalur (400/404/405/413/429/500); rate-limit token-bucket per
  kelas route (read/heavy/export) dengan `Retry-After` + `retryable: true`,
  clock injeksi; feature-flag knob mematikan grup route sebagai 404
  fail-closed; capability gate (token) tetap di depan semua route.
- **Read-only terbukti byte-level**: sha256 store + jumlah event identik
  setelah mengeksekusi semua 36 route baru; sidecar experience/extraction
  TIDAK pernah dibuat oleh route (dipin test).
- **TDD**: RED nyata dua file (exit=2, ImportError) → GREEN.

### Angka test

| suite | test | status |
|---|---|---|
| tests/test_webview_api_v3.py (baru) | 169 | hijau |
| tests/test_ops_knobs.py (baru) | 129 | hijau |
| **total baru** | **298** | exit 0 |
| tetangga (webview, api_v2, async, debt, extraction, switches) | 179 | hijau |

### Mengejutkan / pelajaran

- Pyright menolak dispatch if/elif 50-cabang ("too complex to analyze") —
  dispatch W15 jadi table-driven (lookup eksak + rantai prefix), lebih
  testable dan lolos ceiling.
- Default rate-limit pertama (export 2/s burst 4) terlalu ketat untuk UI
  interaktif: test-suite yang menembak 9 route export berurutan kena 429 —
  dinaikkan ke 10/s burst 20; limiting tetap dibuktikan dgn knob kecil.
- Temuan jujur: `Finding` diagnose tidak membawa atribusi check →
  `/api/health/checks` melaporkan `finding_total` agregat, TIDAK mengada-ada
  pemetaan per check.
- Knob cross-pair (default≤max) fail-closed menangkap konfigurasi
  inkonsisten yang lolos range-check per-knob.

### Gate

ruff check+format 0 pada 4 file scope; pyright 0/0/0 repo-wide;
bandit -ll exit 0 (B608 dinosec dengan justifikasi placeholder/allowlist);
uv lock --check OK; license_guard OK; bench check OK.
Full suite: 15000 passed / 3 failed (1555s) — ketiga kegagalan dibuktikan
milik sibling (cli.py codeintel tanpa docs; fixture corpus SUB-129 WIP);
nol kegagalan dari scope-ku.
