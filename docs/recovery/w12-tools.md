# W12 — Permukaan Tools Operasional (SUB-122)

Empat modul OPS baru: `ops/lifecycle.py`, `ops/doctor.py`, `ops/migrate.py`,
`ops/watchtower.py` + wiring CLI (`lifecycle`, `doctor`, `migrate`,
`watchtower`) + bagian baru di `docs/operations.md`.

## Tujuh pertanyaan pakar (dijawab sebelum kode)

1. **Masalah**: operator menjalankan store jangka panjang buta terhadap tiga
   hal: (a) apakah kesehatan store *membaik atau memburuk dari waktu ke
   waktu* (diagnose/metrics hanya snapshot sesaat), (b) apakah *lingkungan*
   store (runtime, dependensi, izin, disk, performa) masih layak — bukan
   hanya integritas datanya, (c) bagaimana memindahkan store antar mesin
   atau antar versi skema tanpa risiko korupsi/downsgrade senyap, dan (d)
   bagaimana memantau *banyak* store dengan satu perintah dan satu exit code.
2. **Kendala keras**: kontrak beku (KNOWLEDGE_OPERATIONS/OPS_OPERATIONS)
   TIDAK bertambah; tidak ada perintah baru yang merusak store (vacuum wajib
   lewat disiplin F-1 coordinated restore); tidak ada write terselubung saat
   probing (watchtower tidak boleh membuat file store yang hilang); ledger
   health history harus tamper-evident; semua output deterministik dan
   integer-only (preseden metrics); TIDAK commit.
3. **Tujuan terukur**: ≥200 test baru hijau; exit code benar (0/1/2) di
   setiap permukaan CLI; vacuum terbukti menurunkan freelist ke 0 dengan
   chain tetap valid; bundle tamper terdeteksi walau manifest ikut diedit;
   probe watchtower tidak membuat file; ruff+pyright+bandit bersih.
4. **Manfaat**: operator mendapat tren kesehatan historis, runbook
   perbaikan terstruktur (setiap finding membawa `suggestions`), migrasi
   antar versi dan transfer antar mesin yang ter-hash dan fail-closed, serta
   satu laporan fleet untuk cron/CI.
5. **Pelajaran dari masalah**: kegagalan klasik tooling operasional adalah
   (a) tool "read-only" yang diam-diam menulis (EventStore membuka file yang
   hilang = membuatnya — dicegah dengan guard `is_file()` di watchtower dan
   `mode=ro&immutable=1` di migrate.inspect), (b) compaction yang
   mengganti byte di bawah handle hidup (WAL chimera — dicegah dengan
   reuse `_restore_into_live_store`), (c) manifest ter-hash yang bisa
   "ditandatangani ulang" oleh penyerang untuk memberkati payload korup —
   dicegah dengan menjalankan payload lewat `inspect_backup`, bukan hanya
   mencocokkan hash.
6. **Prinsip desain**: (1) fail-closed per check — crash menjadi finding
   `CHECK_CRASHED`, tidak pernah exception; (2) reuse disiplin yang sudah
   terbukti (backup_store/restore_store/inspect_backup, F-1 live-store
   coordination) daripada mekanisme swap baru; (3) semua fakta runtime
   (versi Python/SQLite, umask, dependency lookup, clock) injektable —
   determinisme test tanpa monkeypatch global; (4) exit code adalah bagian
   dari kontrak, dipin oleh test.
7. **Kill criteria**: jika vacuum tidak bisa dibuat aman tanpa menyentuh
   store.py, turunkan ke "VACUUM INTO + instruksi restore manual". Tidak
   terjadi — disiplin F-1 `restore_store(overwrite=True)` terbukti cukup
   (test `test_vacuum_store_still_serves_after_swap`: handle yang sama
   langsung melayani health check pasca-swap).

## Mekanisme per komponen

### lifecycle.py
- `HealthSnapshot`: dataclass beku tervalidasi penuh (score ≤ max,
  ok ≤ total, komponen terurut, hash 64-hex) dengan `to_payload` /
  `from_payload` yang memverifikasi hash sha256 atas payload kanonik.
- Ledger: JSONL append-only, setiap baris hash-chained (`prev_hash`),
  genesis `"0"*64`; append di-flush + fsync. `load_health_history`
  memverifikasi setiap hash dan setiap rantai; korupsi/truncation =
  `LifecycleError`, dan `record_health_snapshot` menolak memperpanjang
  ledger yang rusak (fail-closed).
- `analyze_health_trend`: murni fungsi, integer-only — direction
  (first vs last), drop streaks (current + worst), flaky components
  (frekuensi not-ok), regressions (ok di awal, rusak di akhir).
- Retention engine: `RetentionRule` (max_age_seconds/max_entries,
  divalidasi), `RetentionItem` (protected tidak pernah di-evict),
  `plan_generic_retention` = fungsi murni (items, rules, now), deterministik
  (urutan input tidak memengaruhi output; output terurut by key). Age evict
  = strictly-older; tepat di batas bertahan.
- `prune_health_history`: rewrite atomik (temp + fsync + os.replace +
  dir fsync), re-chain suffix yang dipertahankan (re-genesis), laporan
  membawa `previous_head_hash` dan `new_head_hash` — re-genesis sendiri
  teraudit. Dry run = plan identik tanpa menulis.
- `vacuum_store`: `VACUUM INTO` staging (nama tersembunyi ber-pid, satu
  direktori) → `inspect_backup` (quick_check + anchor event count/chain
  head) → `restore_store(staging, db, overwrite=True)` (koordinasi F-1:
  close → purge sidecar → verified replace → reopen in-place) → verifikasi
  chain pada handle yang dibuka ulang. `:memory:` ditolak; semua
  sqlite3.Error dibungkus `LifecycleError`; staging dibersihkan di finally.

### doctor.py
- 5 kategori × ~16 probe: environment (python window sesuai
  requires-python, sqlite floor 3.27 untuk VACUUM INTO expression, probe
  tulis 0600 di direktori store, headroom disk ≥ max(16MiB, 2× ukuran
  store)), dependencies (9 distribusi runtime, presence + versi),
  permissions (mode file store, umask world-writable), integrity
  (quick_check, integrity_check opt-in, foreign_key_check, chain, drift
  migrasi), performance (latensi baca p50/p95 integer via
  `ops.metrics.latency_summary`, fragmentasi via `lifecycle.analyze_fragmentation`,
  ukuran WAL, stale journals).
- Setiap finding: `DoctorFinding(code, category, severity, message,
  suggestions, details)` — saran perbaikan terstruktur, semua string lewat
  `redact_value`. Report mengelompokkan per kategori; `exit_code` = 1 jika
  ada error. Semua fakta eksternal injektable.

### migrate.py
- `inspect_store_version`: baca `mode=ro&immutable=1` (tanpa sidecar WAL),
  kedua ledger (`schema_migration` file-based + `schema_versions` in-code
  v3+), klasifikasi 5 status terhadap ladder penuh (file + kode).
- `plan_migration`/`migrate_store`: plan deterministik (steps dengan nama
  file atau `in-code-vN`); apply lewat migrator EventStore yang ada;
  newer-than-build ditolak SEBELUM file dibuka (test membuktikan byte
  identik).
- Bundle: direktori `store.sqlite3` (verified backup) + `bundle-manifest.json`
  kanonik (format_version, created_at injektable → deterministik, sha256,
  size, event_count, chain_head_hash, applied_migrations). `verify_bundle`
  tidak pernah raise; tamper pada payload ATAU manifest terdeteksi; payload
  korup yang "ditandatangani ulang" tetap gagal di `inspect_backup`.
  `import_bundle` verify-first fail-closed, install lewat `restore_store`.
  Nama file di manifest divalidasi anti-traversal.

### watchtower.py
- `probe_store`: TIDAK pernah membuat file yang hilang (guard `is_file`),
  TIDAK pernah raise (open gagal → reachable=False; probe gagal → error
  string), busy_timeout 1s agar tidak menggantung pada store terkunci.
- `aggregate_status`: urut by name, duplikat name/path = `WatchtowerError`,
  totals + unreachable/degraded names; `exit_code` = 1 jika ada store
  unreachable/error/chain-rusak/migrasi-drift.
- `render_markdown`: tabel stabil per store + bagian Errors.

## Bukti gate

- RED nyata: keempat file test collection-error sebelum modul ada
  (ModuleNotFoundError); setelah implementasi hijau.
- Test baru: 209 (lifecycle 67, doctor 43, migrate 44, watchtower 27,
  CLI wiring 28) — semua exit 0.
- Suite tetangga: test_cli + test_ops_* + test_no_ai_runtime 299 passed.
- ruff check + format: 0 pada semua file scope.
- pyright: 0 errors / 0 warnings / 0 informations pada file scope.
- bandit -ll: exit 0 pada file scope.
- Live E2E via `cli.main`: record→trend→prune, vacuum nyata (freelist
  98→0, chain valid, handle tetap melayani), export→verify→import round
  trip (event count + chain preserved), verify tampered → exit 1,
  watchtower fleet 2 store exit 0 + missing store exit 1 tanpa membuat file.

## Temuan selama pembangunan

- F-122-1: store punya DUA ledger versi (`schema_migration` v1/v2 +
  `schema_versions` v3/v4 in-code) — inspector awal hanya membaca yang
  pertama dan salah mengklasifikasikan store v2 sebagai "current". Diperbaiki
  dengan ladder penuh (union kedua ledger + range in-code); test v2-asli
  (fixture `monkeypatch SCHEMA_VERSION=2`, preseden test_store_v3) meminnya.
- F-122-2: `VACUUM INTO ?` dengan bound parameter bekerja di SQLite modern
  (≥3.27 menerima ekspresi string) — dipakai agar tanpa interpolasi SQL.
- F-122-3: perbandingan byte vacuum harus menghitung WAL: file utama
  pra-vacuum bisa 4096 byte dengan data di WAL; report membandingkan
  footprint total (file + wal) dan membuktikan pages_after < pages_before
  + freelist_after == 0.
- F-122-4: event_log append-only triggers menolak UPDATE/DELETE bahkan dari
  koneksi langsung — test chain-rusak memakai stub verify_chain (preseden
  test_ops_diagnose), korupsi fisik memakai raw page overwrite +
  koneksi read-only.
- DEEP-CODEGRAPH.md TIDAK ada di repo (find 0 hit) — pola kerja diambil dari
  EXPERT-THINKING.md + modul ops yang ada (diagnose/retention/backup).
