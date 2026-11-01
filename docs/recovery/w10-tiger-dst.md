# W10 — Tiger Style Hardening + DST-lite untuk Superpower Server (SUB-118)

Dua pola SOTA diterapkan pada `serve/superpower.py` (daemon multi-agent W7):
**(1) Tiger Style** (TigerBeetle): assertion density di jalur kritis + budget
statik eksplisit + invariant drills. **(2) DST-lite** (FoundationDB, tahap 1):
harness simulasi deterministik dengan clock/RNG/urutan antrian ter-injeksi dan
ter-seed — skenario storm 50-agent replay byte-identik dari seed.

## Jawaban 7 pertanyaan pakar (ditulis SEBELUM kode, sesuai EXPERT-THINKING.md)

1. **Masalah**: keselamatan daemon 50-agent hari ini bertumpu pada typed error
   (kondisi lingkungan) dan gate storm wall-clock. Dua kelas kegagalan NYATA
   belum terjawab: (a) *kesalahan programmer* di jalur kritis (akuntansi
   antrian drift, watermark mundur, ack-sebelum-commit, fairness batch
   dilanggar, dedup in-flight bocor) akan diserap diam-diam — persis kelas
   yang dieliminasi Tiger Style; (b) bug konkurensi storm tidak bisa di-replay
   — test storm memakai thread + wall clock, jadi temuan tak bisa dipadatkan
   jadi regression test deterministik.
2. **Kekurangan/kendala**: kontrak beku (16 tool + envelope + wire protocol)
   tak disentuh; perilaku produksi tidak boleh berubah (assert hanya untuk
   kesalahan programmer, bukan mekanisme kontrol baru; typed error tetap satu-
   satunya jawaban untuk kondisi lingkungan); TIDAK commit; scope additive di
   `src/know_your_context/serve/` + `tests/` + dokumen ini; assert di jalur
   panas harus murah (O(#lane), lane ≤ cap); DST tidak boleh memakai
   socket/thread nyata — single-threaded, virtual time, byte-identik.
3. **Tujuan terukur**:
   - Setiap fungsi kritis (`_FairQueue.put/get/drain`, `_TokenBucket`,
     `_SessionToken`, `_WriteArbiter` submit/batch/execute/resolve/publish,
     session reap, accept, inflight) memverifikasi pre/post-condition-nya.
   - Invariant drills: test yang sengaja merusak state internal → assert
     MENANGKAP (AssertionError), bukan lolos diam-diam.
   - Tabel budget: setiap antrian/buffer/cache punya batas atas eksplisit yang
     diketahui saat start, terdokumentasi (bagian Budget Statik di bawah) +
     diverifikasi test; tidak ada struktur tumbuh tak terbatas (audit lane,
     stats per-actor, latency map ditutup).
   - DST: 2 run seed sama → trace sha256 identik (termasuk lintas
     PYTHONHASHSEED via subprocess); seed berbeda → cakupan skenario berbeda;
     storm 50-agent replayable; invariant checker aktif tiap tick
     (ack⇒commit, watermark monoton, cap tak pernah dilampaui).
   - ≥100 test baru; full suite hijau; ruff check+format 0; pyright 0/0/0.
4. **Manfaat**: operator/integrator — bug programmer meledak sebagai
   AssertionError berpesan jelas di dev/test, bukan korupsi senyap di
   produksi; auditor me-replay bug konkurensi dari satu angka seed; tabel
   budget = jawaban satu halaman atas "apa batas terburuk daemon ini".
5. **Pelajaran dari masalah** (pola kegagalan yang harus dicegah desain):
   (i) akuntansi antrian drift (`_total` ≠ Σ lane) → kapasitas terlampaui
   senyap; (ii) ack sebelum COMMIT (bug nyata SUB-104, harus dijaga assert
   agar tak kembali lewat refactor); (iii) watermark publish mundur → read
   lane membaca masa lalu; (iv) fairness batch ditembus phased arrivals;
   (v) struktur per-actor tumbuh tak berbatas (`_per_actor`, `_latencies`,
   audit lane) → memory exhaustion lambat; (vi) dedup in-flight bocor →
   sharer menunggu selamanya.
6. **Prinsip desain** (tak bisa dilanggar):
   - Assert = kesalahan programmer; typed error = kondisi lingkungan. Tidak
     ada assert yang bisa dipicu input jaringan.
   - Semua kapasitas statik diketahui saat start; struktur per-actor berbatas
     eksplisit dengan kebijakan overflow terdokumentasi.
   - Determinisme lewat injeksi: clock/RNG/urutan adalah parameter; default
     produksi = `time.monotonic` (tak berubah), harness menyuntikkan virtual.
   - Fail closed di jalur data/audit; setiap mekanisme punya kill switch atau
     batas yang jelas.
7. **Kill criteria**: bila invariant assert memaksa perubahan perilaku yang
   melanggar kontrak beku atau menggagalkan gate storm existing tanpa bug
   nyata → berhenti, pindahkan invariant ke harness saja, laporkan. Bila
   byte-identitas DST tak tercapai karena nondeterminisme tak terkendali →
   persempit ke state machine murni + dokumentasikan batasnya.

## Rancangan

### A. Tiger Style — assertion density (serve/superpower.py, additive)

- `_FairQueue`: `_check_invariants_locked()` (Σ lane == `_total`; `_total` ≤
  cap; set(rotation) == lane non-empty; tanpa duplikat rotasi; lane ≤
  per-actor cap) dipanggil pasca `put/get/drain`; assert konstruktor
  (`1 ≤ per_actor_cap ≤ total_cap`); assert fairness di `get` (anggota
  `exclude` hanya terpilih bila semua lane ter-exclude); clock injeksi
  `now` (default `time.monotonic`).
- `_TokenBucket` / `_SessionToken`: clock injeksi + assert batas token
  (`0 ≤ tokens ≤ capacity`).
- `_WriteArbiter`: assert batch ≤ `group_commit_batch`; guard
  `_batch_transaction_open` → `_resolve_mutation` assert ack tak pernah di
  dalam transaksi (menjaga perbaikan SUB-104); post `_execute_mutation`:
  tepat satu dari outcome/error; post `_resolve_mutation`: future done +
  dedup key ter-pop; `_publish_now`: watermark monoton tak-mundur; bound
  dedup in-flight ≤ `write_queue_size + group_commit_batch`; audit lane
  berbatas (`audit_queue_size`, backpressure blocking + typed ShuttingDown
  saat stop — analisis deadlock: writer drain audit dulu tiap iterasi).
- `SuperpowerDaemon`: assert sesi ≤ `max_agents` di accept; post-reap assert
  tak ada token kedaluwarsa tersisa; `_inflight_end` assert begin/end
  berpasangan; stats per-actor (`_per_actor`, `_latencies`) dibatasi
  `_MAX_TRACKED_ACTORS = 1024` (kebijakan: actor baru tak dilacak, yang ada
  tak ter-evict — observability saja).

### B. Tiger Style — budget statik (serve/budgets.py, BARU)

`budget_table(config) -> tuple[BudgetEntry, ...]` + `render_markdown` +
`assert_static_budgets(config)` (dipanggil di `SuperpowerDaemon.start()`).
Tabel lengkap: lihat bagian **Budget Statik** (tabel di-render dari kode,
bukan disalin tangan — test memverifikasi render == tabel kode).

### C. DST-lite (serve/simulation.py, BARU)

Discrete-event simulator single-threaded yang mengendarai `_FairQueue`,
`_TokenBucket`, `_SessionToken` ASLI (bukan model ulang) dengan `SimClock`
virtual + `random.Random(seed)`; model writer menjalankan aturan group
commit (batch cap, exclude-fairness) dan resolve-pasca-commit; injeksi
kegagalan member (savepoint rollback), deadline, kedaluwarsa lease,
staleness read/publish. Trace kanonik per kejadian → sha256. Invariant
checker tiap tick: ack⇒commit, watermark monoton, cap queue, akuntansi.
Temuan bug → regression test permanen dengan seed ter-pin.

## Budget Statik (dirender dari `budget_table()` — bukan salinan tangan)

Nilai di bawah untuk konfigurasi default; `render_markdown(budget_table(config))`
menghasilkan tabel ini dari kode, dan test memverifikasi render == tabel.

| Budget | Bound | Unit | Enforced at | On overflow |
|---|---|---|---|---|
| `sessions_active` | 50 | sessions | `SuperpowerDaemon._accept_loop` | typed SERVER_BUSY + retry_after_ms at connect |
| `session_socket_map` | 50 | entries | `SuperpowerDaemon._session_sockets` | follows sessions_active (only registered sessions enter) |
| `write_queue_total` | 256 | items | `_FairQueue (write lane)` | typed SERVER_BUSY + retry_after_ms |
| `write_queue_per_actor` | 64 | items | `_FairQueue lane (write)` | typed SERVER_BUSY; other actors unaffected |
| `read_queue_total` | 512 | items | `_FairQueue (read lane)` | typed SERVER_BUSY + retry_after_ms |
| `read_queue_per_actor` | 128 | items | `_FairQueue lane (read)` | typed SERVER_BUSY; other actors unaffected |
| `heavy_queue_total` | 16 | items | `_FairQueue (heavy lane)` | typed SERVER_BUSY + retry_after_ms |
| `heavy_queue_per_actor` | 4 | items | `_FairQueue lane (heavy)` | typed SERVER_BUSY; other actors unaffected |
| `audit_lane` | 2048 | items | `_WriteArbiter._audit_q` | blocking backpressure on read lanes; typed ShuttingDown on stop |
| `dedup_in_flight` | 264 | entries | `_WriteArbiter._in_flight` | structural bound (queued + executing batch), asserted on submit |
| `group_commit_batch` | 8 | mutations | `_WriteArbiter._gather_batch` | batch cap, asserted |
| `admission_buckets` | 512 | actors | `SuperpowerDaemon._admit` | clear-and-rebuild (buckets are cheap to recreate) |
| `writer_adapter_cache` | 256 | adapters | `writer_adapter_factory` | clear-and-rebind |
| `read_adapter_cache` | 2048 | adapters | `_ReadPool._adapter_for (per worker)` | clear-and-rebind (per-worker cap applies) |
| `heavy_adapter_cache` | 256 | adapters | `_HeavyLane._main` | clear-and-rebind |
| `stats_actor_map` | 1024 | actors | `SuperpowerDaemon._note_actor_request / _record_latency` | new actors untracked; tracked actors never evicted |
| `latency_reservoir` | 256 | samples | `SuperpowerDaemon._record_latency (per actor+lane)` | oldest sample dropped (bounded deque) |
| `frame_bytes` | 1048576 | bytes | `rpc.FrameReader` | typed PROTOCOL_ERROR, connection closed |
| `listener_backlog` | 128 | pending connects | `SuperpowerDaemon._bind_socket` | kernel-side refusal/queueing |
| `session_lease` | 30000 | millis | `SuperpowerDaemon._reap_expired_sessions` | session closed, slot released, queued work dropped typed |
| `drain_timeout` | 10000 | millis | `SuperpowerDaemon.shutdown / _finalize` | remaining work fails typed ShuttingDown |
| `read_publish_wait` | 30000 | millis | `_ReadPool._execute_with_freshness` | typed STALE_PROJECTION result |

Audit budget sebelum wave ini: antrian tulis/baca/heavy + per-actor cap sudah
ada (W7); yang DITUTUP wave ini: audit lane (unbounded → 2048 + backpressure),
dedup in-flight (tak terbukti berbatas → bound struktural + assert), stats
per-actor & latency map (unbounded → 1024 aktor, reservoir 256 sudah ada),
konstanta magic 256/512/128 dipindah ke `budgets.py` sebagai sumber kebenaran.

## Temuan nyata (ditemukan oleh desain invariant / drill / TDD)

1. **F-118-1 — orphan lane pada `_FairQueue.put` (diperbaiki).** Lane dibuat
   SEBELUM cek cap: put yang ditolak (total cap penuh) meninggalkan deque
   kosong yatim di `_lanes` — meracuni invariant rotation↔lanes dan bocor
   satu deque per actor yang pernah kena cap. Fix: lane dibuat setelah semua
   penolakan; drill + regression test (`test_busy_put_leaves_no_orphan_lane`).
2. **F-118-2 — dedup `_in_flight` bocor saat stop (diperbaiki).** `stop()`
   menguras antrian tulis dan menggagalkan future-nya, tapi entri dedup-nya
   tidak pernah di-pop — map tumbuh melampaui bound strukturalnya. Fix: pop
   saat drain; test `test_stop_fails_queued_items_typed_and_pops_dedup`.
3. **F-118-3 — hang `run_audit` pada antrian berbatas (diperbaiki saat TDD).**
   Bounding audit lane tanpa cek stop: put sukses ke antrian yang tak akan
   pernah dikuras → `_await(future)` hang selamanya (terbukti: run test
   pertama timeout 300s). Fix: cek `_stop` sebelum put + retry loop
   stop-aware yang melempar typed `ShuttingDown`.
4. **F-118-4 — koreksi diri: `group_commit_batch ≤ write_queue_size` BUKAN
   invariant yang sah.** Assert lintas-budget itu mematahkan 2 test
   backpressure existing (write_queue_size=2 dengan batch 8 legal: batch
   menguras antrian berulang di dalam window-nya). Dihapus; bound dedup
   `write_queue_size + group_commit_batch` tetap terbukti tanpa relasi itu.

## Bukti gate

Semua perintah nyata di host ini, HEAD=d4067b3 (+ WIP sibling SUB-117 yang
hidup berdampingan):

- **Test baru**: `pytest tests/test_serve_invariants.py tests/test_serve_simulation.py -q`
  → **172 passed** (104 invariant/budget/drill + 68 DST), exit 0.
- **Invariant drills menembak nyata**: 9 drill `_FairQueue` (counter drift,
  rotasi duplikat, lane yatim, lane over-cap), drill resolve-di-dalam-transaksi
  (guard SUB-104), drill watermark mundur, drill dedup-bound, drill lease,
  drill inflight tanpa pasangan, drill kapasitas sesi — semuanya ditangkap
  `AssertionError`, bukan lolos.
- **DST determinisme**: 6 seed × 2 run → trace sha256 identik; digest cocok
  lintas `PYTHONHASHSEED=0/1` di subprocess terpisah (seed 7, 42) dan lintas
  dua proses (seed 1234); CLI replay
  `python -m know_your_context.serve.simulation 42 400` → exit 0, semua check
  true. Seed berbeda → cakupan berbeda (pasangan ter-pin 7/8 dan 13/17 pada
  60 langkah: symmetric difference non-kosong — `admission_busy`,
  `dedup_shared`, `busy_write` muncul hanya di salah satu).
- **Storm 50-agent** (seed 2026, 4000 langkah, burst konkurensi): 12 kelas
  skenario tercakup (write_commit, read_ok, busy_write, busy_read,
  admission_busy, dedup_shared, idempotent_replay, deadline_drop,
  session_expired, member_failure, batch_multi, stale_read); 6 invariant
  hijau (ack⇒commit, watermark monoton, write/read cap, fairness, no
  unresolved at halt); counter nyata: 5713 commit spammer + 1986 commit
  sopan, busy_write spammer 62 > sopan 25, max depth 32/32 dan 8/8 (budget
  penuh ter-eksplorasi, tak pernah terlampaui).
- **Regression seeds ter-pin**: 7/42/1337 pada agents=16 steps=800 dengan
  digest sha256 eksak di `TestRegressionSeeds`.
- **Regresi existing**: tests/test_serve_* 127 + gates/concurrency 13 +
  spawn/cli/proxy/watcher/daemon 97 + mcp/extraction-serve 23, semua exit 0.
  Dua gate (fairness 20.3% vs 20%, crash-safety respawn) sempat merah SAAT
  sibling membangun paralel di mesin yang sama; keduanya hijau saat diulang
  sepi (tercatat jujur sebagai flake beban, bukan regresi — perubahan saya
  tak menyentuh rasio fairness, hanya assert O(#lane)).
- **Full suite**: `pytest -q` → **7159 passed, 1 skipped** (skip jujur:
  codegraph CLI tak terinstal), 0 failed, exit bersih, 802s.
- **Lint/type**: `ruff check` 0 pada keenam file scope; `ruff format` bersih
  pada file milikku (2 baris drift tersisa di superpower.py milik sibling
  SUB-117, sengaja tak kusentuh); `pyright` 0/0/0 pada budgets.py,
  simulation.py, daemon_config.py, superpower.py, dan kedua file test.
  `uv lock --check` OK; `license_guard` OK.
- **Kontrak beku tak disentuh**: 16 tool + envelope + wire protocol utuh;
  satu-satunya perubahan konfigurasi = field additive `audit_queue_size`
  (default 2048) dengan validasi ConfigError typed.

## Sesudah membangun — jujur

- **Terbukti**: assertion density di jalur kritis menangkap sabotase state
  (drill), bukan hanya ada di kode; budget statik lengkap (22 entri) dan
  lintas-budget ter-bukti di start; DST replay byte-identik dari seed
  termasuk lintas PYTHONHASHSEED; bug nyata F-118-1/2/3 ditemukan dan
  diperbaiki SELAMA pembangunan (sebelum sempat melaut).
- **Tidak terbukti / batas jujur**: DST tahap-1 memodelkan writer/store
  (bukan SQLite nyata) — determinisme dibeli dengan abstraksi commit;
  fault injection sebatas member failure/deadline/lease, belum crash
  proses maupun partisi. Assert menambah O(#lane) per operasi antrian —
  tak terukur di gate latency (fairness 20.3%→hijau saat sepi menunjukkan
  ambang tipis itu sensitif beban mesin, bukan perubahan saya).
- **Mengejutkan**: dua dari empat temuan (F-118-3 hang, F-118-4 assert
  berlebihan) adalah bug yang saya perkenalkan sendiri saat hardening dan
  tertangkap TDD — persis pola yang Tiger Style prediksi: invariant
  memaksa Anda membuktikan hal yang "jelas benar", dan dua kali ternyata
  tidak benar.
