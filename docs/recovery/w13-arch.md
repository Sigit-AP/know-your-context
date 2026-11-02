# W13 — Architecture Surface (SUB-125)

Tujuan: menutup metrik permukaan arsitektur yang lemah vs codegraph
(l3 kill_switch_sites/env_vars/http_routes/migrations, l6 middleware/config_keys,
l2 enums/interfaces/named_constants) dengan kontrol NYATA untuk operator —
bukan dekorasi. Setiap mekanisme di sini berfungsi, teruji, dan fail-safe.

## 7 pertanyaan pakar (EXPERT-THINKING.md)

1. **Masalah**: operator tidak bisa mematikan fitur opsional satu per satu
   (12 synthesis pass, helper tier, capability mode, watcher, group commit,
   DST/simulation) tanpa mengubah kode; webview API hanya melayani tab UI,
   bukan JSON API nyata per tab; tidak ada middleware layer eksplisit;
   status/kategori masih string bebas; tidak ada rollback migrasi schema.
2. **Kekurangan keras**: kontrak beku (contracts v1..v4, graph_model kinds,
   `contracts.py`) TIDAK disentuh; semua switch default = perilaku lama;
   env parsing fail-closed (nilai invalid = error typed, bukan tebakan);
   store v1..v4 existing harus tetap terbuka byte-identik; TIDAK commit.
3. **Tujuan terukur**: ≥150 test baru hijau; setiap switch terbukti mematikan
   fiturnya dalam test; 8 route JSON API baru melayani data asli dari store;
   rollback v4→v3→v4 round-trip pada store nyata; gate ruff/pyright/bandit 0.
4. **Manfaat**: operator bisa menonaktifkan pass synthesis yang mahal/berisiko
   di produksi via env; konsumen webview (dashboard/skrip) mendapat API JSON
   nyata; insiden schema bisa di-rollback tanpa restore backup penuh.
5. **Pelajaran dari masalah**: kegagalan terselubung dilarang — switch yang
   aktif mengumumkan dirinya (report/describe); rollback tanpa verifikasi =
   korupsi terselubung, jadi setiap migrasi membawa `verify` yang dijalankan
   pasca-apply/rollback; env boolean ambigu harus ditolak, bukan ditebak.
6. **Prinsip desain**: (a) fail-safe default — disable selalu mengembalikan
   perilaku lama yang aman; (b) fail-closed parsing — input invalid = typed
   error; (c) additive-only — tidak ada perubahan bentuk kontrak beku;
   (d) setiap switch terdokumentasi di registry + `describe()`.
7. **Kill criteria**: jika wiring switch memaksa mengubah kontrak beku, atau
   rollback tidak bisa dibuat aman (trigger/ledger), berhenti, laporkan angka
   apa adanya + alternatif (mis. rollback hanya via restore backup).

## Desain

### A. Kill switches (`ops/switches.py`, BARU)

Registry kanonik `KILL_SWITCHES`: tiap entri `KillSwitch(name, env, default,
affects, fail_safe)`. Semua nama env adalah konstanta level modul (literal,
AST-detectable). Parsing boolean ketat (`1/true/yes/on`, `0/false/no/off`),
selain itu `SwitchError` (fail closed).

Switch baru (default = fitur AKTIF = perilaku lama):

| env | efek saat `=1` |
|---|---|
| `KNOW_YOUR_CONTEXT_DISABLE_SYNTH_<PASS>` ×12 | pass synthesis itu di-skip (report berisi nol + announced) |
| `KNOW_YOUR_CONTEXT_DISABLE_SYNTHESIS` | semua pass synthesis di-skip |
| `KNOW_YOUR_CONTEXT_DISABLE_HELPER_TIER` | tier `module_helper` resolver nonaktif (21 helper tidak dipakai) |
| `KNOW_YOUR_CONTEXT_DISABLE_NAME_MATCH` | tier name-match di-skip dalam `resolve_and_synthesize` |
| `KNOW_YOUR_CONTEXT_CAPABILITY_MODE` | override `DaemonConfig.capability_mode` (`off`/`enforce`; nilai lain = ConfigError) |
| `KNOW_YOUR_CONTEXT_DISABLE_WEBVIEW` | webview menolak start (exit 2, fail closed) |
| `KNOW_YOUR_CONTEXT_DISABLE_SIMULATION` | entry point DST-lite menolak jalan (freeze switch) |

Switch yang sudah ada (didokumentasikan di registry, tidak diubah):
`KNOW_YOUR_CONTEXT_NO_WATCH`, `KNOW_YOUR_CONTEXT_FORCE_WATCH`,
`KNOW_YOUR_CONTEXT_NO_GROUP_COMMIT`, `KYS_DISABLE_RUST_KERNEL`.

### B. HTTP routes nyata (webview, additive)

Route table eksplisit `API_ROUTES` (literal `/api/...` per route). 8 route
baru, semuanya read-only, melayani data asli:

| route | data asli |
|---|---|
| `/api/query?node_id=&kind=&direction=&limit=` | tetangga graph nyata (node_relation + parent_of) |
| `/api/metrics/series` | deret skor/event dari health history hash-chained + bucket harian event_log |
| `/api/audit/tail?since=&limit=` | event audit dengan `event_seq > since`, urut naik (tail semantics) |
| `/api/proposals/{id}` | detail proposal improvement dari event_log (`knowledge.propose_improvement`/`apply`/`rollback`) |
| `/api/health/detail` | diagnose lengkap + statistik store + switch aktif |
| `/api/bench` | ringkasan latency per-operasi dari invocation_journal (p50/p95/mean, integer) |
| `/api/store/stats` | row count per tabel, schema_versions, page_count/freelist, ukuran file |
| `/api/switches` | status semua kill switch (resolved) + deskripsinya |

### C. Middleware (`serve/middleware.py`, BARU)

`Middleware` Protocol + `MiddlewareStack` eksplisit, dijalankan di dispatch
webview: `RequestIdMiddleware` (echo/generate `X-Request-Id`),
`TimingMiddleware` (`Server-Timing` + ring buffer durasi untuk
`/api/health/detail`), `TypedErrorMiddleware` (typed error → envelope JSON
terpetakan, tak ada 500 telanjang), `CapabilityGateMiddleware` (pemeriksaan
token sesi/host sebagai layer eksplisit, menggantikan guard inline tanpa
mengubah perilaku).

### D. Struktur kode

- `enums.py` (BARU): `RefStatus`, `CapabilityMode`, `SwitchState`,
  `MigrationDirection`, `HealthState`, `RouteAccess` — semuanya `StrEnum`
  (serialisasi identik dengan string lama; kontrak tak berubah).
- `interfaces.py` (BARU): `runtime_checkable` Protocol — `StoreProtocol`,
  `ExtractorProtocol`, `ResolverProtocol`, `SynthesizerProtocol`,
  `AuditSinkProtocol`. `EventStore`/`ReferenceResolver`/`Synthesizer`
  terverifikasi memenuhi protokolnya dalam test.
- Named constants: semua env name, limit, default di modul baru adalah
  konstanta UPPER_SNAKE level modul; magic value baru dilarang.

### E. Migrations eksplisit + rollback (`ops/schema_migrations.py` BARU)

Registry `MIGRATIONS`: `Migration(version, name, up_sql, down_sql, verify)`
untuk v3 dan v4, SQL eksplisit di `migrations/versions/000{3,4}_{up,down}.sql`
(subdir → tidak diambil loop file-migration store yang non-rekursif).
`rollback_to(path, target)`: BEGIN IMMEDIATE, jalankan down SQL dari versi
tercatat ke target, hapus baris `schema_versions`, jalankan `verify` —
gagal = rollback transaksi penuh. Data-loss guard: rollback v4→v3 menolak
jika tabel v3 berisi baris kecuali `allow_data_loss=True` (fail closed).
`verify_schema(path)` memeriksa ledger↔objek konsisten (mirror fail-closed
store). Store itu sendiri TIDAK diubah — idempotensi v3→v4→v3→v4 dibuktikan
dengan membuka `EventStore` setelah rollback.

## Bukti (diisi saat DONE)

(lihat entri board subagent-board.md)

### Terbukti

- **Kill switches**: 19 switch (12 per-pass synthesis + synthesis-all +
  helper tier + name match + capability mode + webview + simulation + 4
  mirrored legacy dengan semantics owner persis). Fail-safe default diverifikasi
  per switch; parsing ambigu = `SwitchError` typed. Wiring nyata dibuktikan
  perilaku: containment pass emit 1 edge default → 0 saat env disable;
  helper tier rust resolve → failed saat disable; `DaemonConfig` override
  env (env tak pernah melemahkan `enforce` eksplisit); simulation exit 2 saat
  freeze.
- **HTTP routes**: literal `/api/...` di src 8 → 29 (diukur AST/regex probe
  vs HEAD); 9 route JSON baru melayani data asli store (query tetangga,
  metrics series dari health history hash-chained + bucket harian event_log,
  audit tail ascending, improvements list/detail dari event_log, health
  detail, bench latency dari invocation_journal, store stats PRAGMA,
  switches resolved-at-request-time).
- **Middleware**: `MiddlewareStack` eksplisit (RequestId echo/generate,
  Timing `Server-Timing` + ring buffer, CapabilityGate menggantikan token
  check inline tanpa mengubah urutan guard, TypedError mapping — 500 INTERNAL
  tak membocorkan internals; dibuktikan dengan menyuntik RuntimeError).
- **Struktur**: +6 StrEnum (`enums.py`, nilai = string kontrak lama), +6
  Protocol runtime-checkable (`interfaces.py` + `Middleware`), konstanta
  bernama untuk semua limit/env.
- **Migrations**: 4 artifact SQL kanonik (`migrations/versions/000{3,4}_{up,down}.sql`,
  parity dipin vs konstanta store), registry `MIGRATIONS`, `plan_rollback`
  read-only, `rollback_to` atomik (BEGIN IMMEDIATE; gagal = ROLLBACK penuh),
  data-loss guard fail-closed, round-trip v4→v3→v4 terbukti di store nyata.
- **Test baru**: 198 (switches 90, middleware 27, interfaces/enums 21,
  webview api v2 36, schema migrations 24).

### Mengejutkan

- `except A, B:` tanpa kurung legal di Python 3.14 (PEP 758) — kode lama
  sudah memakainya.
- `# noqa: B608` tidak menekan bandit (bandit hanya menghormati `# nosec`);
  store.py memakai pola itu untuk ruff — dua linter, dua komentar berbeda.

### Tidak terbukti / batas

- Rollback hanya untuk v3/v4 (era file-migration v1/v2 sengaja di luar scope —
  fail closed); rollback v3→v2 menghapus permukaan graph (data-loss guard).
- Metrik verdict keseluruhan tidak dijalankan ulang di sini (milik SUB-120;
  instrumentasi probe lokal di atas yang dipakai).
