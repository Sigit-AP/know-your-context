# W13 — Flow depth (SUB-124)

Scope: `src/know_your_context/serve/` + `ingest/watcher.py` + `ops/` + `tests/` +
doc ini. TDD. TIDAK commit. Kontrak beku (v1/v2/v3/v4 fixtures, 17 tool
surface, graph_model kinds) TIDAK disentuh.

Target metrik verdict yang lemah vs codegraph (ledger-20261102T032012Z):
l4.async_functions 8.1% · l4.await_sites 3.6% · l4.timeout_sites 34.1% ·
l4.retry_sites 23.7% · l4.shutdown_handlers 30.6% · l4.worker_sites 29.4% ·
l4.batch_sites 30.6% · l7.pipeline_stages 22.2% · l7.watchdog_sites 40.9% ·
l7.drain_sites 65.2% · l7.throttle_sites 62.5%.

## 7 pertanyaan pakar (dijawab SEBELUM kode)

1. **Masalah**: flow engineering kita dangkal — ingestion berjalan sebagai
   satu pemanggilan monolitik 2-fase (walk+extract lalu resolve) tanpa
   checkpoint: kegagalan di tengah mengulang dari nol; watcher/webview/ops
   berjalan sebagai thread sinkron tanpa cancellation yang benar; worker
   daemon (read lane, heavy lane, acceptor, reaper) tidak punya heartbeat —
   worker yang deadlock hanya terdeteksi oleh LivenessWatchdog global
   (satu denyut untuk seluruh daemon, os._exit(70) — tidak bertingkat);
   retry/timeout ad-hoc: proxy memakai `time.sleep(0.2)` fixed tanpa backoff
   deterministik, socket proxy tanpa timeout eksplisit, dan backoff tidak
   bisa di-seed untuk DST. Masalah NYATA: satu hang di I/O atau satu stage
   gagal = restart penuh / hang permanen / tidak reproducible di simulasi.
2. **Kekurangan/kendala**: (a) kontrak beku + pin test 17 tool tidak boleh
   berubah; (b) `ingest/pipeline.py` di luar scope — staged pipeline harus
   mengorkestrasi API publik yang ada, bukan mengedit pipeline; (c) serve
   gates load-sensitive (storm 50-agent) harus tetap hijau — watchdog tidak
   boleh false-positive saat idle maupun saat badai; (d) perilaku default
   lama harus identik di permukaan API publik (proxy return codes, watcher
   WatchReport shape, webview endpoints); (e) tetap pure-stdlib, no-AI
   boundary (runtime_guard), tanpa subprocess; (f) semua timing injectable
   (clock/sleep/rng) — determinisme lintas PYTHONHASHSEED.
3. **Tujuan terukur**: (a) pipeline eksplisit 5 stage (extract → resolve →
   synthesize → index → seal) dengan kontrak antar-stage + checkpoint
   per-stage: resume dari stage terakhir terbukti oleh test drill
   (kill di stage N → re-run mulai dari stage N, stage < N di-skip);
   (b) ≥150 test baru hijau; (c) async/await nyata: ≥30 async def + ≥80
   await sites baru dengan test cancellation yang membuktikan CancelledError
   merambat benar; (d) watchdog: setiap jenis worker punya heartbeat +
   eskalasi bertingkat warn→restart→dead, dibuktikan drill; drain_all
   mencakup semua worker; (e) retry helper deterministik (seed sama →
   deret delay byte-identik) dipakai di watcher + proxy + ops; timeout
   eksplisit di semua socket call dalam scope; (f) full suite hijau,
   determinisme 2 run identik, ruff/pyright/bandit bersih.
4. **Manfaat**: operator — ingestion besar yang gagal di stage akhir tidak
   mengulang extract mahal; agent harness — proxy yang mem-backoff
   deterministik tidak membadai daemon yang sedang spawn; daemon — worker
   yang macet di-restart bertingkat tanpa membunuh seluruh daemon (os._exit
   hanya milik liveness global); pengembang — backoff ter-seed membuat DST
   (simulation.py) bisa mereplay kegagalan I/O byte-identik.
5. **Pelajaran dari masalah**: pola kegagalan yang harus dicegah desain:
   (a) retry tanpa deadline = loop tak berujung (proxy lama punya deadline
   tapi sleep fixed — burst reconnect menabrak daemon yang belum siap);
   (b) watchdog tanpa tingkat = kill prematur; (c) checkpoint tanpa digest
   input = resume dari state basi; (d) cancellation yang menelan
   CancelledError = task zombie; (e) thread tanpa heartbeat = hang diam-diam.
6. **Prinsip desain** (tidak bisa dilanggar):
   - **Fail open non-kritis, fail closed data/keamanan**: stage gagal →
     checkpoint `failed` + raise typed error (tidak ada tulis setengah
     jadi tanpa jejak); watchdog dead → worker di-stop + alert, daemon
     tetap melayani lane lain.
   - **Determinisme injektable**: semua delay dari `random.Random(seed)`
     atau rng yang diinjeksikan; semua waktu dari clock injektable; tidak
     ada `random.random()` global di jalur retry.
   - **Cancellation sakral**: CancelledError/KeyboardInterrupt tidak pernah
     di-retry, tidak pernah ditelan; drain selalu bounded oleh timeout.
   - **Kill switch per mekanisme**: watchdog worker, retry, dan staged
     pipeline masing-masing bisa dimatikan (config flag / policy
     max_attempts=1) tanpa mengubah perilaku lama.
7. **Kill criteria**: berhenti + laporkan angka apa adanya jika:
   (a) mengaktifkan watchdog default-on membuat serve gates hijau→merah
   secara reproduktif setelah kalibrasi threshold → fallback default-off
   dokumenkan alasannya; (b) resume checkpoint tidak bisa dibuat benar
   tanpa mengedit ingest/pipeline.py → turunkan ke checkpoint
   orchestration-level (digest = output stage sebelumnya) dan laporkan
   batasannya; (c) async wrapper webview mengubah observable behavior
   endpoint (165 test webview harus tetap hijau tanpa diubah).

## Rencana modul (semua file BARU kecuali tercatat)

| Modul | Fitur | Isi |
|---|---|---|
| `serve/flow_retry.py` BARU | 4 | RetryPolicy (seed-injectable), compute_delay_ms murni, retry/retry_async, Deadline, run_with_timeout(+async), RetryExhaustedError/FlowTimeoutError |
| `serve/pipeline_stages.py` BARU | 1 | StageName(5), StageContract, StageMetrics, StageCheckpoint, CheckpointStore (atomic+digest), StagedPipelineRunner (resume/invalidasi downstream/run_stage), build_ingestion_stages (extract=ingest_path resolve=False, resolve=resolve_pending, synthesize=run_synthesis, index=ensure_fts_index, seal=hash-chained seal JSONL) |
| `serve/watchdog.py` BARU | 3 | Heartbeat, SupervisedWorker (restart factory), Watchdog (eskalasi SUSPECT→RESTART→DEAD bertingkat), DrainReport, drain_all bounded |
| `ingest/watcher.py` EDIT additif | 2+4 | AsyncIngestionWatcher (asyncio nyata: sleep/timeout/cancellation, per-file cancel check, retry per ingest), tidak mengubah kelas lama |
| `serve/webview.py` EDIT additif | 2+3 | AsyncWebviewServer: serve via handle_request loop berheartbeat di SupervisedWorker, async stop dengan drain bounded |
| `serve/proxy.py` EDIT additif | 4 | retry+backoff deterministik di _forward, socket timeout eksplisit |
| `serve/superpower.py` EDIT additif | 3 | heartbeat di read/heavy/acceptor/reaper loops + Watchdog wiring + drain coverage (di balik config) |
| `serve/daemon_config.py` EDIT additif | 3+4 | field watchdog_* + proxy_io_timeout (default aman) |
| `ops/async_lifecycle.py` BARU | 2+4 | AsyncLifecycleScheduler: record/prune/fragmentasi sebagai task asyncio dengan cancel+timeout+retry |

## Test (target ≥150, TDD RED dulu)

`tests/test_flow_retry.py` · `tests/test_pipeline_stages.py` ·
`tests/test_serve_watchdog.py` · `tests/test_watcher_async.py` ·
`tests/test_webview_async.py` · `tests/test_ops_async_lifecycle.py` ·
`tests/test_flow_integration.py` (drill: resume-setelah-gagal, watchdog
restart bertingkat, cancellation watcher, determinisme seed 0/1/42).

## Hasil (diisi setelah build)

**Terbukti (semua dengan exit code nyata):**

- `serve/flow_retry.py` BARU — RetryPolicy fail-closed (seed-injectable),
  compute_delay_ms murni (jitter clamp hard ke max_delay), retry/retry_async
  (CancelledError/KeyboardInterrupt tidak pernah di-retry/ditelan), Deadline,
  run_with_timeout(+async), typed RetryExhaustedError/FlowTimeoutError.
  61 test hijau.
- `serve/watchdog.py` BARU — Heartbeat monoton thread-safe, SupervisedWorker
  (restart budget sliding-window, terminal saat exhausted), SupervisedThread
  (adapter thread existing; restart MENOLAK thread hidup — tidak ada duplikasi
  consumer antrean), Watchdog eskalasi suspect→restart→dead + recovered,
  drain_all dengan DrainReport per-worker. 47 test hijau.
- `serve/pipeline_stages.py` BARU — 5 stage kontrak (extract→resolve→
  synthesize→index→seal), CheckpointStore atomik + digest tamper-proof
  (CheckpointCorruptError fail-closed), resume dari stage terakhir (drill
  nyata: kill di index → extract/resolve/synthesize di-skip, terbukti dari
  call counter), retry per-stage (hanya OSError+sqlite3.Error), invalidasi
  downstream di run_stage, seal JSONL hash-chain + verify_seal_chain.
  37 test hijau.
- `ingest/watcher.py` additif — AsyncIngestionWatcher: cancellation nyata
  (task.cancel() saat sleep/interval merambat; cancel point antar-file
  membuktikan file berikutnya tidak disubmit), request_stop kooperatif
  (status "cancelled"), timeout per siklus (FlowTimeoutError → cycle failed,
  tidak crash), retry per-ingest ter-seed. 15 test hijau; 135 test watcher
  lama tetap hijau.
- `serve/webview.py` additif — AsyncWebviewServer: serve loop handle_request
  berheartbeat di SupervisedWorker, watchdog registration, stop bounded async;
  165 test webview lama hijau tanpa diubah. 9 test hijau.
- `ops/async_lifecycle.py` BARU — AsyncLifecycleScheduler: record/prune
  sebagai task asyncio dengan timeout+retry deterministik; fail-open tick
  gagal tercatat; invariant assert sequence monoton. 29 test hijau.
- `serve/superpower.py` additif — heartbeat di SEMUA worker (read-0..N,
  heavy, acceptor, reaper), Watchdog wiring di balik kill switch
  worker_watchdog (default True), respawn factory per lane dengan assert
  anti-duplikasi, drain_all di _finalize (reaper kini ikut di-join —
  coverage drain yang sebelumnya absen), stats()["worker_watchdog"].
- `serve/daemon_config.py` additif — 9 field tervalidasi (watchdog_*,
  proxy_*_timeout, proxy_retry_seed).
- `serve/proxy.py` — connect timeout eksplisit, io timeout eksplisit di
  setiap round-trip (min(io_timeout, sisa deadline)), backoff deterministik
  menggantikan sleep fixed 0.2/0.1, keepalive lease dibatasi agar tidak
  menahan socket lock.
- `tests/test_flow_integration.py` — drill SupervisedThread + daemon nyata
  (semua worker hidup+beating, drain all_drained, guard respawn menolak
  worker hidup) + determinisme digest pipeline. 25 test hijau.

Total test baru: 223. Semua invariant punya drill yang menembak nyata.

**Temuan nyata saat build:**
1. Race inkarnasi SupervisedWorker: finally thread lama bisa menimpa state
   HEALTHY inkarnasi baru pasca-restart → difix dgn identitas thread.
2. Regresi _ReadPool.start (refactor _spawn_worker tidak append) — tertangkap
   probe langsung, difix sebelum gate.
3. Deadline menelan tick pertama clock injeksi (test bug, didokumentasikan).
4. Reaper cadence min(1.0, idle/2) → beat pertama di ~1s (test disesuaikan;
   cadence semua worker ≪ stall 45s — tidak ada false-positive).
5. Proxy lease keepalive tanpa timeout bisa menahan _socket_lock di belakang
   daemon wedged → dibatasi max(1.0, interval*2).

**Gate (semua exit code nyata):**

- pytest tests/ FULL SUITE: **8927 passed, 1 skipped (jujur: codegraph CLI
  absent), 2 failed** (1854s) — 2 failed dianalisis tuntas:
  1. `test_docs_changelog.py::test_public_class_docstrings_complete_in_scope`
     — gate docstring SUB-123 menangkap 3 kelas publikku tanpa docstring
     (StageName/StageRunRecord/FlowRunRecord) → DIPERBAIKI, re-run 69p exit 0.
  2. `test_serve_gates.py::TestStormAllVerbs::test_sixteen_ops_fifty_agents…`
     — DIBUKTIKAN lingkungan, bukan regresi W13: (a) kontrol git worktree
     HEAD bersih gagal dengan tanda tangan IDENTIK (`ConnectionError: daemon
     closed the connection`, 8 agen) pada host yang sama saat itu juga;
     (b) tree-ku dengan `worker_watchdog=False` tetap gagal (watchdog
     dibebaskan); (c) tree-ku LULUS standalone 92s saat host sepi; (d) HEAD
     pair-run gagal dengan varian latency. Preseden repo: gate storm
     load-sensitive (docs/recovery pitfalls; board SUB-118/122).
- Determinisme: fokus 145 test (flow_retry + pipeline_stages + watchdog)
  PYTHONHASHSEED=1 dan =42 → 145p/145p identik; digest pipeline byte-identik
  dua run (test_flow_integration).
- ruff check src tests bench tools scripts kernel-rust: **0 error** (exit 0);
  ruff format --check . : 511 file bersih.
- pyright repo-wide: **0 errors, 0 warnings** (exit 0).
- bandit -ll -r src: 0 issue (exit 0), identik pre/post perubahanku.
- uv lock --check: OK. license_guard: OK. bench/run.py check: OK.

**Metrik verdict (instrumen bench/verdict/run.py metric, tree saat ini,
read-only — bench/ tidak kuubah):**

| metrik | sebelum | sesudah | codegraph | relatif sblm → ssdh |
|---|---|---|---|---|
| l4.async_functions | 20 | 37 | 246 | 8.1% → 15.0% |
| l4.await_sites | 25 | 54 | 693 | 3.6% → 7.8% |
| l4.timeout_sites | 42 | 59 | 123 | 34.1% → 48.0% |
| l4.retry_sites | 53 | 99 | 224 | 23.7% → 44.2% |
| l4.worker_sites | 30 | 42 | 102 | 29.4% → 41.2% |
| l4.batch_sites | 92 | 96 | 301 | 30.6% → 31.9% |
| l7.pipeline_stages | 2 | 17 | 9 | 22.2% → **188.9%** |
| l7.watchdog_sites | 94 | 204 | 230 | 40.9% → **88.7%** |
| l7.drain_sites | 15 | 26 | 23 | 65.2% → **113.0%** |
| l7.throttle_sites | 30 | 32 | 48 | 62.5% → 66.7% |

**Yang TIDAK terbukti / mengejutkan (jujur):**
- l4.shutdown_handlers datar (30.6%) — tidak ada signal handler baru; drain
  memakai pola stop/event, bukan signal. Diterima: shutdown Windows-portable.
- Async watcher tidak meng-cancel thread ingest yang sedang berjalan (batas
  keras Python); cancellation nyata dibuktikan di sleep/interval/antar-file.
- Kontribusi angka metrik adalah tree-level W13 (milikku + sibling in-flight);
  modulku mendominasi l4.async/await/retry/timeout/worker + l7.pipeline/
  watchdog/drain.

