# W15 — Storm connection kill: root cause dan fix (SUB-132)

## Gejala

`tests/test_serve_gates.py::TestStormAllVerbs::test_sixteen_ops_fifty_agents_with_latency_per_lane`
flake ~1/3 standalone, lebih sering di bawah beban: ~16/50 agen gagal dengan
`ConnectionError: daemon closed the connection` (EOF di tengah menunggu respons).

## Dugaan awal (koordinator) — DIBANTAH dengan instrumentasi

Dugaan: `watchdog_stall_seconds=45.0` = timeout klien 45.0 → heavy op storm >45s
→ watchdog menyatakan worker stalled → RESTART memutus koneksi in-flight → EOF.

Bantahan (3 run storm terinstrumentasi, probe monkeypatch pada reaper + watchdog):

1. **Nol** alert/restart watchdog di 3 run (logger + `daemon.status` worker section).
2. `SupervisedThread.restart()` **menolak** me-restart thread yang hidup
   (Python tak bisa membunuh thread; menduplikasi consumer queue lebih buruk) —
   jalur watchdog secara konstruksi tidak bisa memutus koneksi.
3. Drill B (watchdog stall 4s + ingest 2000 file = 49s > stall): koneksi
   in-flight **selamat**, respons ok, ping pasca-stall ok.

## Root cause sebenarnya — session reaper memutus koneksi in-flight

`SuperpowerDaemon._reap_expired_sessions` menutup koneksi yang diam lebih dari
`session_lease_seconds` (**default 30.0s, tetap**). `token.last_seen` hanya
diperbarui saat request **tiba** — bukan saat daemon sibuk menjawabnya. Maka
setiap request dengan latensi respons > 30s membuat kliennya "diam" secara artifisial:
reaper menutup soket di tengah request → EOF.

Di bawah storm, latensi itu sah: target gate sendiri mengizinkan write p95
hingga 18s×factor dan heavy p95 hingga 30s×factor (hingga 72s/120s pada
factor=4 di host lambat). ~16/50 agen = yang heavy-op-nya mengantre paling
akhir di heavy lane satu-worker. Klien timeout 45s tidak sempat berlaku —
server memutus lebih dulu pada 30s. Fail-open di jalur yang salah.

**Reproduksi deterministik (Drill A, pra-fix)**: `session_lease_seconds=2.0` +
`mutation_delay_seconds=4.0`, satu write → `ConnectionError: daemon closed the
connection after 3.0s`. Persis signature flake-nya.

## Bug sekunder yang ditemukan drill (watchdog)

Worker hidup-yang-sibuk (restart ditolak) ditandai `dead` **permanen** —
hanya SUSPECT yang bisa recover — plus satu alert `dead` per tick watchdog
(89 alert dalam satu ingest 49s). Di produksi, satu ingest >45s (repo nyata
mudah melampauinya) meracuni observabilitas selamanya.

## Fix (bukan pelemahan gate)

1. **Lease mengukur idleness, bukan in-flight work.** `_SessionToken` kini
   mencatat `in_flight` (begin/end di sekitar dispatch); reaper mengecualikan
   sesi dengan `in_flight > 0`, dan `end_request()` me-refresh `last_seen`
   (menjawab juga aktivitas — sesi yang baru dijawab tidak langsung reapable
   saat turnaround klien). Sesi mati-dengan-kerja tetap membebaskan slot:
   penulisan respons gagal → thread sesi keluar.
   *Trade-off vs alternatif "typed retryable error (SERVER_RESTARTING) ke
   klien in-flight"*: alternatif itu memutus request yang sedang dieksekusi
   sah dan memaksa semantik retry pada verb non-idempotent; menyelesaikan
   in-flight lebih sederhana dan benar. Lease gate tidak dilemahkan: sesi
   idle tetap di-reap (dibuktikan dalam test yang sama — contender masuk
   setelah respons).
2. **Watchdog: dead-episode, bukan hukuman mati.** `check_once` kini
   me-recover worker DEAD-non-terminal yang kembali beat (operasi panjang sah
   selesai), dan alert `dead` akibat restart-ditolak hanya sekali per episode
   (transisi tetap dilaporkan; exhaustion budget tetap selalu dilaporkan).
   Restart thread hidup tetap **ditolak** — graceful respawn ala Python:
   tidak ada respawn atas thread hidup, titik.
3. **Deadline separation, fail-closed.** Validasi config baru:
   `watchdog_stall_seconds > read_publish_wait_seconds` — read lane boleh
   menunggu publish selama itu secara sah; threshold stall di dalam jendela
   bounded-wait itu ambigu → ConfigError. (Heavy lane unbounded by design;
   di sana watchdog hanya mengalert — restart thread hidup selalu ditolak.)

## Bukti

- RED pra-fix: 4 test baru gagal dengan signature persis (EOF di gate test;
  recovery; alert-sekali-per-episode; validasi stall≤publish-wait).
- Drill A pra-fix: EOF @3.0s. Pasca-fix: write 4s terjawab, slot idle
  kemudian di-reap.
- Drill B / `TestWatchdogStallDrill`: stall terpicu (suspect+dead),
  restarts==0, respons in-flight ok, recovery ke healthy, tepat 1 alert dead.
- Storm gate: **5/5 hijau berturut-turut** di loop tenang (plus 2 run manual
  hijau sebelumnya = 7/7) — dicatat 17:0x UTC pada tree saat itu.
- A/B pada tree TERKINI (sudah memuat `StorageTuning` mmap/sync in-flight dari
  sub W15 lain di `store.py`): **pra-fix = 9 agen mati
  `ConnectionError: daemon closed the connection`** (bug asli tereproduksi);
  **pasca-fix = 0 EOF di ~20 run storm** (tenang, berbeban, dan full suite).
  Read p95 pra-fix 6213ms vs pasca-fix 6208ms pada tree yang sama → lonjakan
  latensi ~3.5% di tree terkini berasal dari perubahan storage di luar scope,
  BUKAN dari fix ini.
- Sisa kegagalan storm di tree terkini = margin latensi (read p95 6.2s vs
  6.0s target; heavy p95 33s vs 30s) dan/atau deadline klien 45s di bawah
  beban host — selalu dengan **nol koneksi diputus**. Sensitivitas beban ini
  pre-existing (A/B: arm pra-fix gagal dengan bentuk yang sama) dan tidak
  boleh "diperbaiki" dengan melunakkan target.
- Full suite run-1 (tree tanpa hardware_tuning in-flight): 15352 passed,
  2 skipped, 1 failed — `test_watcher_async.py::TestRunLoop::test_cancellation_mid_batch_skips_remaining_files`,
  flake timing asyncio yang **terbukti pre-existing** (A/B: gagal 1/5 dengan
  DAN tanpa perubahan ini; file itu hanya mengimpor `ingest.watcher` +
  `serve.flow_retry.RetryPolicy`, tak tersentuh).
- Full suite run-4 (tree dengan perubahan sub lain yang sedang in-flight):
  15485 passed, 1 skipped, 5 failed = 4x `test_hardware_tuning.py` (modul
  sub lain, gagal saat `store.py` sedang setengah-teredit; lulus 38/38
  standalone sesudahnya) + 1x storm overshoot latensi 7ms (0 agen gagal).
- ruff bersih; pyright 0 error; bandit 0 Medium/0 High (hanya B101 assert
  baseline + 2 B311 lama).

## Yang sengaja TIDAK diubah

- `serve/simulation.py`: DST mengendarai `_SessionToken` secara langsung
  dengan model reaper sendiri; mengubahnya menggeser pinned-seed digests
  (kontrak beku). Perubahan token aditif dan kompatibel; sim tetap memodelkan
  expiry-lama sebagai skenario drop (konservatif — memproyeksikan lebih
  banyak drop daripada daemon pasca-fix, bukan lebih sedikit).
- Target latensi storm gate, kuota, kapasitas: tak disentuh.
