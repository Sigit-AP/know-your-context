# W21 — Backend Parity: webview vs graphify (SUB-152)

Scope: `src/know_your_context/serve/webview.py` + `serve/` HTTP layer
(middleware). Jalur tulis (service/extraction) milik SUB-151 — tidak disentuh.

## 7 pertanyaan pakar (EXPERT-THINKING.md)

1. **Masalah**: webview adalah satu-satunya permukaan HTTP live produk, tetapi
   belum pernah diukur terhadap backend HTTP nyata graphify
   (`graphify/serve.py` transport Streamable HTTP + `graphify/security.py`).
   Klaim "profesional" tanpa inventarisasi berbasis kode = tebakan.
2. **Kendala keras**: kontrak beku tidak disentuh; viewer tetap read-only
   GET/HEAD; stdlib `ThreadingHTTPServer` tetap; default loopback-only +
   token wajib; tidak ada fitur berlebih (prinsip operator); TIDAK commit.
3. **Tujuan terukur**: % parity per butir kapabilitas yang ADA di kode
   graphify, dengan bukti baris per baris; 100% pada butir relevan; butir yang
   sengaja tidak ditiru terdokumentasi alasannya.
4. **Manfaat**: operator + auditor mendapat verdict bernomor; script/API client
   bisa auth via header (token tak lagi wajib di URL); deploy/systemd
   mendapat SIGTERM bersih + peringatan bind publik.
5. **Pelajaran dari masalah**: token di query string bocor ke history/log;
   bind wildcard tanpa peringatan adalah kegagalan tersambunyi; proses yang
   mati oleh SIGTERM tanpa shutdown meninggalkan SQLite setengah tertutup.
6. **Prinsip desain**: (a) fail-closed pada auth/host/origin; (b) parity hanya
   untuk butir yang ADA di kode graphify, bukan fitur framework lain; (c) setiap
   penutupan gap membawa test; (d) desain kita yang lebih ketat dari graphify
   dipertahankan dan didokumentasikan, bukan dilonggarkan.
7. **Kill criteria**: berhenti bila butir relevan = 100% hijau ATAU butir
   tersisa hanya yang N/A-by-design dengan alasan tertulis. Alternatif bila
   sebuah gap ternyata mahal/berisiko: dokumentasikan sebagai deliberate-skip.

## Inventarisasi graphify (bukti kode)

HTTP surface graphify = `graphify/serve.py` `_build_http_app`/`serve_http`
(Starlette + uvicorn, MCP Streamable HTTP) + `graphify/security.py`.

| # | Kapabilitas graphify | Bukti (file:baris) |
|---|---|---|
| G1 | Auth API-key via header `X-API-Key` ATAU `Authorization: Bearer`, skema bearer case-insensitive (RFC 6750), `hmac.compare_digest`, 401 JSON `{"error":"unauthorized"}`, key kosong dinormalisasi ke None (auth off) | serve.py:2756-2794, 2835 |
| G2 | DNS-rebinding protection: allowed_hosts = {host, localhost, 127.0.0.1} ± port utk bind spesifik; dimatikan sengaja utk wildcard bind | serve.py:2839-2848 |
| G3 | WARNING stderr saat bind wildcard tanpa api-key | serve.py:2928-2933 |
| G4 | Banner startup menyebut status auth (`api-key required` / `no auth`) | serve.py:2923-2927 |
| G5 | Session idle reaping (`session_idle_timeout`, default 3600, 0/None off) | serve.py:2850-2858 |
| G6 | Mode stateless (load-balanced/CI) | serve.py:2856, 2977-2981 |
| G7 | Mode json_response (JSON polos vs SSE) | serve.py:2855 |
| G8 | Middleware ASGI murni agar SSE stream tidak ter-buffer | serve.py:2756-2763 |
| G9 | Satu mount path eksak semua metode, tanpa redirect trailing-slash | serve.py:2740-2753 |
| G10 | Lifespan mengelola task group session manager | serve.py:2861-2866 |
| G11 | Graceful shutdown SIGINT+SIGTERM via uvicorn | serve.py:2934 (uvicorn.run) |
| G12 | Cache konteks graph LRU + pinned default, kunci mtime+size, kapasitas env `GRAPHIFY_MAX_CONTEXTS` | serve.py:116-192 |
| G13 | Error tool gagal-open: graph proyek rusak → tool error, server tetap melayani (`ToolError` → `isError`) | serve.py:34-41, 148-152 |
| G14 | SSRF guard outbound: allowlist skema, blokir metadata cloud, blokir IP privat, DNS resolve-sekali anti-rebind | security.py:83-256 |
| G15 | Cap ukuran file input graph.json | security.py:357+ |
| G16 | Sanitasi output: strip control-char pada label/metadata | security.py:390-461 |
| G17 | Query log JSONL opt-in, fail-silent, default OFF (postur privasi) | querylog.py:15-80 |
| G18 | CLI flags utk semua knob transport | serve.py:2937-3003 |
| G19 | Versi server dari metadata paket | serve.py:2704-2716 |
| G20 | Build terpisah dari run utk testability in-process | serve.py:2797-2876 |

## Inventarisasi kita (webview.py + serve/middleware.py)

| # | Kapabilitas kita | Bukti |
|---|---|---|
| O1 | Auth token via `?token=` ATAU cookie HttpOnly+SameSite=Strict; `hmac.compare_digest`; 401 envelope; kill switch `NO_AUTH` opt-out eksplisit; token divalidasi format | webview.py:3042-3110, 451-453, 528-529 |
| O2 | DNS-rebinding guard: Host harus allowlist loopback DAN port server persis; host tanpa port ditolak | webview.py:468-503 (LEBIH KETAT dari G2: port wajib cocok) |
| O3 | Origin guard: Origin bila ada harus loopback + port server | webview.py:506-525 |
| O4 | Security headers di SEMUA respons: CSP deny-all-kecuali-self, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` | webview.py:195-198, 3030-3036 |
| O5 | JSON deterministic + markup-safe (`<`,`>`,`&` di-escape, sort_keys) | webview.py:456-465 |
| O6 | Error model typed envelope `{error:{code,message,retryable}}`; 400/404/405/413/429; untyped → 500 INTERNAL tanpa bocor internal | webview.py:212-247, 2899-2900, 3165-3170; middleware.py:207-230 |
| O7 | Middleware stack fail-closed: RequestId (`X-Request-Id` echo/generate), Timing (`Server-Timing` + trace ring + p50/p95/max), CapabilityGate, TypedError | middleware.py (penuh); webview.py:3142-3172 |
| O8 | Rate limit token-bucket per kelas route (read/heavy/export), knob-driven, fail-closed, 429 + `Retry-After` | webview.py:239-291, 3215-3226 |
| O9 | Feature-flag per route group: group mati menjawab persis 404 unknown | webview.py:294-310, 3209-3214 |
| O10 | Read-only: hanya GET/HEAD; 405 dgn guards-first (tak bocorkan metode) | webview.py:3114-3140 |
| O11 | Static asset allowlist eksak, anti-traversal | webview.py:3185-3204 |
| O12 | Kill switch operator `KNOW_YOUR_CONTEXT_DISABLE_WEBVIEW` | webview.py:2916-2918 |
| O13 | Validasi port/token fail-closed | webview.py:2919-2925 |
| O14 | Graceful shutdown idempoten + join timeout + app.close(); AsyncWebviewServer: drain async berbatas + watchdog heartbeat | webview.py:2999-3012, 3473-3567 |
| O15 | Cap output export (413), validasi param (regex int/kind/lang, segmen id tunggal) | webview.py:228-230, 3250-3260 |
| O16 | Bind loopback default; bind publik opt-in env; allowlist host bisa diperluas env | webview.py:2954-2964 |
| O17 | Observability: trace ring → `/api/health/detail`, `Server-Timing`, `X-Request-Id` | middleware.py:128-186; webview.py:3341 |
| O18 | CLI `--store/--port/--token/--open/--serve-once`, exit 2 fail-closed | webview.py:3570-3603 |
| O19 | Route table sebagai data (`API_ROUTES`) + endpoint discovery `/api/routes` | webview.py:344-445, 2878 |

## GAP MATRIX (verdict awal)

Butir relevan = kapabilitas HTTP-layer yang masuk akal untuk viewer read-only
loopback. Butir MCP-transport-spesifik dinilai N/A-by-design (bukan gap).

| Butir graphify | Status awal | Aksi |
|---|---|---|
| G1 header auth (Bearer + X-API-Key) | **GAP** — kita hanya query+cookie | TUTUP (nilai nyata: token keluar dari URL utk API client) |
| G2 DNS-rebinding allowed_hosts | PARITY+ (kita lebih ketat: port wajib) | — |
| G3 warning bind wildcard tanpa auth | **GAP** | TUTUP (peringatan bind non-loopback) |
| G4 banner menyebut status auth | **GAP** parsial (URL selalu menampilkan token, bahkan saat NO_AUTH=1 → menyesatkan) | TUTUP (banner jujur soal auth) |
| G5 session idle reaping | N/A — viewer stateless per-request, tak ada sesi server-side | dokumentasi |
| G6 mode stateless | N/A — kita memang stateless by design | dokumentasi |
| G7 json_response vs SSE | N/A — tak ada SSE; semua respons JSON polos | dokumentasi |
| G8 middleware tak men-buffer SSE | N/A — tak ada stream; middleware kita tak menyentuh body | dokumentasi |
| G9 satu mount semua metode | N/A/deliberate — viewer read-only; 405 guards-first lebih ketat | dokumentasi |
| G10 lifespan task group | PARITY (AsyncWebviewServer + watchdog mengelola lifecycle) | — |
| G11 SIGTERM graceful shutdown | **GAP** — main() hanya menangkap SIGINT; SIGTERM (systemd/kill) melewatkan shutdown()+app.close() | TUTUP |
| G12 LRU graph cache | N/A — kita baca SQLite live per request (sumber kebenaran tunggal; tak ada file graph.json utk di-cache) | dokumentasi |
| G13 error fail-open per tool | PARITY (typed envelope; server tetap hidup; BrokenPipe/ConnectionReset ditelan) | — |
| G14 SSRF outbound guard | N/A — webview tak pernah fetch keluar | dokumentasi |
| G15 cap ukuran input | PARITY-sebanding (cap output export 413 + validasi param; input kita SQLite, bukan upload) | — |
| G16 sanitasi output | PARITY+ (escape markup utk threat model stored-XSS kita) | — |
| G17 query log opt-in | N/A/deliberate — postur no-telemetry; jejak in-memory + audit store sudah ada | dokumentasi |
| G18 CLI flags knob transport | PARITY | — |
| G19 versi server dari metadata | DELIBERATE-SKIP — `Server: KysWebview/1` statis cukup utk tool loopback; versi paket sudah terekspos di `/api/config` | dokumentasi |
| G20 build/run terpisah utk test | PARITY (WebviewServer in-process, port 0) | — |

**Verdict awal: butir relevan = G1,G2,G3,G4,G10,G11,G13,G15,G16,G18,G20 (11).
Hijau awal = 8/11 = 72,7%. Gap = G1, G3, G4, G11.**

Kapabilitas kita yang MELEBIHI graphify (bukan gap, dicatat utk kejujuran
dua arah): O3 Origin guard, O4 CSP+nosniff+no-store, O5 JSON markup-safe,
O6 envelope error terstruktur, O7 X-Request-Id + Server-Timing + trace ring,
O8 rate limiting, O9 feature flags, O10 405 guards-first, O12 kill switch,
O14 drain berbatas + watchdog, O19 route discovery.

## Penutupan gap (TDD)

- **Fix A (G1)**: terima `Authorization: Bearer <token>` (skema
  case-insensitive, token di-strip — persis graphify serve.py:2777-2780) dan
  `X-API-Key: <token>` sebagai kanal auth alternatif, compare constant-time.
  Query→cookie→header; header tidak menerbitkan cookie.
- **Fix B (G3+G4)**: di `main()`, cetak peringatan stderr saat bind
  non-loopback; banner menyebut status auth jujur (token-required vs
  NO_AUTH aktif).
- **Fix C (G11)**: `main()` memasang handler SIGTERM yang membangkitkan
  KeyboardInterrupt sehingga jalur `finally: shutdown()` yang sama dipakai
  (drain bersih, app.close() terpanggil).

## Hasil (verdict akhir + bukti gates)

### Gap yang ditutup (masing-masing dengan test)

| Gap | Fix | Test |
|---|---|---|
| G1 header auth | `_token_authorized` menerima `Authorization: Bearer` (skema case-insensitive RFC 6750, credential di-strip — persis graphify serve.py:2777-2780) + `X-API-Key`, compare constant-time; header TIDAK pernah mencetak cookie | tests/test_webview_backend_parity.py::TestHeaderAuth (11 test) |
| G3+G4 bind/auth posture | `security_notes()` (pure) + `main()` mencetak WARNING stderr saat bind non-loopback dan/atau `NO_AUTH=1`; diam saat postur aman | TestSecurityNotes (7 test: 4 unit + 3 integrasi main/capsys) |
| G11 SIGTERM drain | `_sigterm_as_keyboard_interrupt` dipasang SEBELUM banner (tak ada jendela tak terlindungi); KeyboardInterrupt ditangkap di sekitar SELURUH body main → satu jalur drain (`finally: restore handler + shutdown()`) | TestSigtermGracefulShutdown (unit handler + subprocess nyata: spawn, SIGTERM, exit 0) |

### Gap bonus yang DITEMUKAN oleh test overload (bukan dari matriks awal)

| Gap | Fix | Bukti |
|---|---|---|
| Listen backlog stdlib `request_queue_size=5` meluap saat flood connection-per-request → SYN retransmit ~1s | `WebviewServer._HTTPServer` subclass: `request_queue_size=128`, `daemon_threads=True` | p99 flood: **1023,7ms → 43,4ms** (sebelum/sesudah, test yang sama) |

### Verdict parity akhir (butir relevan)

**11/11 = 100%** pada butir relevan: G1, G2, G3, G4, G10, G11, G13, G15, G16,
G18, G20. N/A-by-design terdokumentasi (G5–G9, G12, G14, G17, G19) — sesi MCP,
SSE, stateless-mode, cache file graph, SSRF-outbound, query-log plaintext, dan
versi dinamis memang bukan milik viewer read-only stateless; desain kita yang
lebih ketat (Host+port wajib cocok, 405 guards-first, CSP deny-all)
dipertahankan.

### Perluasan operator (terukur, test nyata)

tests/test_webview_isolation_overload.py (4 test, server nyata in-process):

1. **Isolasi — kill**: webview mati (connection refused terbukti) → daemon
   tetap menjawab read (`knowledge_query`) DAN write (`knowledge_log_event`)
   tanpa error. PASS.
2. **Isolasi — flood**: flood webview 8 thread terhadap store LIVE daemon →
   daemon read p95 base=**111,5ms** vs during-flood=**109,0ms** (tidak
   melambat; bound 557,5ms), webview tetap menjawab 1594 respons. PASS.
3. **Overload webview**: 1410 request/3s (470 req/s), semua typed
   (200=192, 429=1218 + Retry-After + retryable:true), 0 transport error, 0
   5xx, p95=63,1ms p99=94,5ms, thread 3→15→3 (tidak bocor), recovery 200 <5s.
   PASS.
4. **Overload simultan**: flood webview + storm daemon 4 agent bersamaan →
   webview n=1131 semua typed (p99=36,3ms), daemon n=107 p95=127,6ms
   p99=136,4ms @ 35,7 ops/s, 0 kegagalan, kedua surface recover. PASS.

### Gates

- 24/24 test W21 hijau; subset webview+serve 333p; storm gates SUB-104 55p
  hijau (kegagalan sesaat di tengah sesi = breakage in-flight SUB-151 pada
  `extraction/engine.py`→`autoreview.journal_verdict`, di luar scope-ku,
  sembuh sebelum verdict; rantai impor daemon terbukti tak menyentuh
  serve.webview).
- **Full suite: 16149p/1s/1f** (28m18s). 1f = `test_power_of_ten` repo gate —
  9 pelanggaran tersisa SEMUA di file write-path milik SUB-151 yang sedang
  in-flight (autoreview/cli/extraction-engine/improvement/service/store;
  ratchet baseline mereka belum di-update). Bagian webview: BERSIH — 2
  pelanggaran yang sempat ada (`__init__` 62>60, `_make_handler` 461>444)
  ditutup dengan SHRINK (ekstraksi helper `_auth_disabled` /
  `_session_cookie_token` / `_header_token` / `_bind_host` /
  `_extended_host_allowlist`), BUKAN mengendurkan ratchet: `__init__` 52 ≤ 60,
  `_make_handler` 442 ≤ 444. Setelah shrink, 891/891 test pengimpor
  serve.webview hijau.
- Determinisme: PYTHONHASHSEED=0/1/42 → 24p identik ×3 (diulang pasca-refactor).
- ruff check + format: 0. pyright: 0/0/0. bandit: 0 temuan baru (2 LOW
  pre-existing di HEAD: B105 false-positive dict literal, B101 assert guard).

TIDAK commit. File: src/know_your_context/serve/webview.py,
tests/test_webview_backend_parity.py, tests/test_webview_isolation_overload.py,
docs/recovery/w21-backend-parity.md.
