# W15 — Integrasi HardwarePlan ke semua subsistem inti (SUB-135)

Meneruskan SUB-134 (lapisan `hardware/` + store) dan memakai seam
`hardware_plan()` yang disiapkan: **satu `HardwarePlan` cached menjadi sumber
kebenaran tunggal** untuk serve, ingest, resolve/synthesis, retrieval_v2,
analytics, backup/projection/ops. Arah operator: scale-UP maksimal, TIDAK ADA
throttle-down; default plan == perilaku historis byte-identik bila deteksi
gagal atau kill switch aktif.

## 7 pertanyaan pakar (sebelum kode)

1. **Masalah**: lapisan hardware ada tapi hanya `store.py` yang meminumnya.
   Serve masih angka tetap (pool 8, queue 256/512/16, batch 8, backlog 128),
   ingest sequential + commit per file, synthesis N+1 `edge_exists`,
   retrieval tanpa query cache, analytics tanpa skala worker / tanpa gate GPU,
   backup/projection buffer I/O tetap. Hardware mahal nganggur.
2. **Kendala keras**: kontrak beku tak disentuh; TIDAK commit; determinisme
   hasil (digest ingest, report, rowid, skor Decimal 12dp) TIDAK boleh
   berubah; kill switch per wiring + fallback aman; tanpa dependensi baru;
   probe tak pernah menggagalkan jalur mana pun (fail-open).
3. **Tujuan terukur**: (a) grep membuktikan SEMUA subsistem mengonsumsi
   `hardware_plan`; (b) satu plan dipin test (tidak ada subsistem menghitung
   sendiri — formula turunan hidup di `hardware/profile.py`); (c) stress 4
   situasi (1/10/50/100 agen × store kecil/besar) membuktikan integrasi
   mengubah perilaku nyata (worker count, throughput ingest) tanpa regresi;
   (d) full suite hijau + ruff/pyright/bandit bersih.
4. **Manfaat**: host besar mendapat pool/queue/cache/batch yang mengikuti
   core+RAM; host kecil (host ini: 2 vCPU/2,7 GB) tetap byte-identik pada
   jalur yang memang sudah pas (pool 8 tetap 8, queue ×1); operator punya
   kill switch per mekanisme.
5. **Pelajaran dari kegagalan umum**: (a) paralelisme yang mengubah urutan
   commit → dicegah: ekstraksi paralel murni, commit SELALU urutan kanonik;
   (b) cache query basi → kunci cache memuat knowledge_high_watermark; cache
   mati saat reranker pengalaman aktif; (c) "plan default" yang diam-diam
   mengubah host kecil → setiap turunan di-clamp agar == historis pada
   hardware kecil (scale-up only, max(default, derived)); (d) failover
   tersembunyi → setiap resolusi plan dibungkus try/except → fallback
   historis + notes jujur.
6. **Prinsip desain**: (1) SATU plan — turunan (queue_scale, io_buffer_bytes,
   query_cache_entries, base_workers) dihitung di `hardware/profile.py`,
   subsistem hanya membaca; (2) kill switch: `DISABLE_HW_AUTOSCALER` untuk
   worker/queue, `DISABLE_HW_TUNING` untuk batch-disk/cache/buffer,
   `KYS_GPU_OFFLOAD` (default off) untuk gate GPU; (3) fail-open pada probe,
   fail-closed pada input operator; (4) paralelisme hanya untuk fase murni —
   urutan tulis store tidak pernah berubah.
7. **Kill criteria**: jika sebuah wiring mengubah output deterministik atau
   membutuhkan perubahan kontrak beku → wiring itu DITOLAK dan dicatat jujur
   (contoh nyata: jumlah worker heavy lane tetap 1 — invariant single-writer
   W13; yang mengikuti plan adalah kapasitas queuenya).

## Keputusan per subsistem

| Subsistem | Konsumsi plan | Kill switch | Default aman |
|---|---|---|---|
| serve/daemon_config | read_pool=max(8, base_workers) cap 64; queue ×queue_scale(RAM); audit=max(2048, 256×pool); backlog ×scale; max_agents scale-up; group_commit_batch ← profil disk | AUTOSCALER (pool/queue/backlog/agents), TUNING (batch) | semua konstanta historis |
| serve/superpower | `listener.listen(config.listener_backlog)` | (di atas) | LISTENER_BACKLOG |
| ingest/pipeline | worker ekstraksi = base_workers (jendela 4×worker, map berurutan); batch commit = group_commit_batch profil disk; mmap via store (SUB-134) | AUTOSCALER (worker→1), TUNING (batch→1) | sequential + per-file tx |
| resolve/synthesis | batch flush insert ← profil disk (executemany chunk dalam SATU transaksi); prefetch existing-edge set dengan paginasi sebesar batch | TUNING | `_edge_exists` per kandidat |
| retrieval_v2 | query cache LRU = query_cache_entries (dari cache policy; 0=off); kunci memuat watermark; FTS page cache ditegaskan pada rebuild | TUNING (cache_kib=0 → cache off) | tanpa cache |
| analytics | worker suite per-domain (pagerank dsb.) = base_workers; keputusan offload via gpu_gate terukur (T_cpu diukur, bukan dikarang) | AUTOSCALER (worker→1), KYS_GPU_OFFLOAD | sequential, CPU selalu |
| backup / projection / ops.migrate | buffer hash/copy/write = io_buffer_bytes profil disk (SSD/UNKNOWN 1 MiB = historis; HDD 4 MiB) | TUNING | 1 MiB / buffering default |

Bukti akhir: grep `hardware_plan` menjangkau semua subsistem + tabel stress
terukur di bawah (diisi setelah run).

## Bukti terukur (run nyata di host ini, 2 vCPU / 2,7 GiB free / SSD virtual)

### Integrasi mengubah perilaku nyata

| Jalur | Historis | Terintegrasi | Bukti |
|---|---|---|---|
| query cache lookup (200× query sama) | 0,9311 s | 0,0111 s (84×) | `integration-results.json: query-cache-200-lookups` (200 hits, hasil byte-identik) |
| worker ekstraksi ingest | 1 (hardcoded) | 2 (= core, plan) | `test_workers_and_batch_live_on_this_host`: `(2, 32)` live |
| batch commit ingest | per file | 32 file/transaksi (profil SSD) | sama; dump tabel byte-identik vs jalur historis |
| read pool daemon | 8 tetap | max(8, base_workers) cap 64 | `test_serve_hardware.py`: host 32-core sintetis → 32; host ini → 8 (scale-up only) |
| queue daemon | 256/512/16 tetap | ×queue_scale(RAM), cap ×16 | host 64 GiB sintetis → 4096/8192/256 |
| backlog listener | 128 tetap | ×queue_scale cap 4096 | tersambung ke `config.listener_backlog` di `_bind_socket` |
| group commit daemon | 8 tetap | profil disk (SSD 32/HDD 128) | `TestDiskProfileBatch` |
| batch synthesis | per kandidat (N+1) | prefetch set + executemany 32/tx | `test_synthesis_hardware.py` byte-identik |
| analytics suite | sequential | per-domain paralel (canonical merge) | byte-identik workers=1 vs 4 vs plan |
| offload GPU | — | gate terukur per batch (T_cpu diukur) | observer menerima `edges` + `t_cpu_s` nyata; offload False (no GPU) |
| buffer I/O backup/projection/migrate | 1 MiB tetap | SSD 1 MiB (= historis), HDD 4 MiB | `test_io_hardware.py` digest identik |

Jujur (tidak dilunakkan): ingest paralel di host ini = **speedup 1,016×**
(4,10 s vs 4,03 s untuk 96 file / 1440 simbol) — GIL membatasi walker Python
murni dan file kecil didominasi overhead parse per file; wiring terbukti
hidup (worker nyata, batch nyata, output byte-identik) dan siap untuk host
lebih besar, tanpa regresi. Keputusan yang DITOLAK dan dicatat: worker heavy
lane tetap 1 (invariant single-writer W13 + watchdog `_respawn` menegaskannya)
— yang mengikuti plan adalah kapasitas queuenya; paralelisme pass synthesis
ditolak (store single-connection men-serialisasi + urutan rowid harus tetap)
— yang diparalelkan hanya bacaan murni (prefetch) dan flush executemany.

### Stress matrix ulang (8 sel nyata, 0 error)

| Agen | Store | Ops | Throughput | p99 | ΔRSS |
|---|---|---|---|---|---|
| 1 | 10MB | 800 | 15.176 ops/s | 0,198 ms | 0,0 MiB |
| 10 | 10MB | 8.000 | 14.662 ops/s | 9,6 ms | 1,9 MiB |
| 50 | 10MB | 20.000 | 12.272 ops/s | 80,5 ms | 7,5 MiB |
| 100 | 10MB | 20.000 | 10.530 ops/s | 132,6 ms | 9,2 MiB |
| 1 | 500MB | 1.500 | 9.378 ops/s | 0,8 ms | 0,0 MiB |
| 10 | 500MB | 8.000 | 12.982 ops/s | 3,7 ms | 0,0 MiB |
| 50 | 500MB | 20.000 | 9.990 ops/s | 84,8 ms | 0,0 MiB |
| 100 | 500MB | 30.000 | 10.265 ops/s | 181,7 ms | 1,4 MiB |

Scale-up 1→10 agen (500MB) = 1,38×; 100 agen = 79% throughput 10 agen (tanpa
collapse, backpressure typed); semua sel 0 error; p99 maks 182 ms. Angka
mentah: `$TMPDIR/kys-hw-stress/integration-results.json`.

### Satu sumber kebenaran (dipin + grep)

`hardware/profile.py` memegang SEMUA turunan (`base_workers`, `queue_scale`,
`io_buffer_bytes`, `query_cache_entries`); konsumen hanya memanggil:

```
store.py                 hardware_plan (SUB-134: mmap/cache/page_size/sync)
serve/daemon_config.py   hardware_plan (pool, queue, audit, backlog, agents, batch)
serve/superpower.py      config.listener_backlog (dari plan via DaemonConfig)
ingest/pipeline.py       _hardware_ingest_params → plan (workers, commit batch)
resolve/synthesis.py     _synthesis_hardware → plan (prefetch batch, insert batch)
retrieval_v2/lookup.py   plan.query_cache_entries (LRU watermark-stamped)
retrieval_v2/fts.py      plan.storage.cache_kib (page cache build FTS)
analytics/hardware.py    plan.base_workers + gpu_gate (T_cpu terukur)
backup.py                io_buffer_bytes (hash + salinan artefak)
projection.py            _artifact_buffering → io_buffer_bytes
ops/migrate.py           io_buffer_bytes (hash migrasi)
```

`extraction/` (engine queue v4) tidak punya keputusan hardware sendiri
(diverifikasi grep: nol cpu_count/worker/batch di sana) — ia menulis lewat
jalur store yang sudah ter-tuning.

## Gate akhir

- **Full suite**: 15616 passed / 1 skipped (codegraph CLI tidak terpasang —
  skip historis) / **1 failed** =
  `test_serve_gates.py::TestStormAllVerbs::test_sixteen_ops_fifty_agents_with_latency_per_lane`.
  DIBUKTIKAN pre-existing di 3 titik A/B pada host yang sama: (1) worktree
  HEAD murni (tanpa satu pun perubahan SUB-135 — diverifikasi modul tak
  punya `HARDWARE_AUTO`) GAGAL standalone (139 s); (2) tree-ku dengan KEDUA
  kill switch (= resep historis byte-identik) GAGAL (heavy p95 38,6 s);
  (3) tree-ku dengan plan aktif GAGAL (read p95 15,9 s). Kelas kegagalan
  identik: margin latensi storm sensitif beban host (didokumentasikan
  SUB-132/SUB-134: p95 ~19,7 s di HEAD bersih). 10/11 gate storm lain hijau
  di tree terkini. Kode-ku tidak bisa memengaruhi jalur yang gagal melebihi
  resep historis: di host ini (2 vCPU/2,7 GiB) satu-satunya delta default =
  `group_commit_batch` 8→32 (SSD) — dan A/B kill-switch menunjukkan kegagalan
  tetap ada tanpa delta itu.
- **Determinisme**: 69 test integrasi baru hijau di PYTHONHASHSEED=1 dan 42;
  pin byte-identik (dump tabel, digest, report) di ingest/synthesis/
  retrieval/analytics/io.
- **ruff**: repo-wide `check` + `format --check` = 0.
- **pyright**: repo-wide 0 errors / 0 warnings.
- **bandit -ll**: 0 Medium/High (hanya peringatan nosec pada baris ber-anotasi
  historis).
- **Tangkapan TDD nyata**: (1) ambiguitas "== default berarti auto" menimpa
  pin sibling (`test_batch_never_exceeds_group_commit_cap[8]`) → DIPERBAIKI
  dengan sentinel `HARDWARE_AUTO` (eksplisit selalu menang); (2) token digest
  untuk file tak-terbaca salah urutan di fase commit → DIPERBAIKI
  (`test_unreadable_file_typed`); (3) `knowledge_high_watermark` TAK bergerak
  saat ingest (proposal-only) → kunci cache memakai `event_high_watermark`
  (setiap mutasi yang menyentuh `symbols` ter-audit → watermark bergerak).


