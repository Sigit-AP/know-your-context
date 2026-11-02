# W15 — Arsitektur hardware: maksimalkan mesin, satu sumber kebenaran (SUB-134)

Implementasi rencana hardware-aware dari `GPU-VERDICT.md` (SUB-133),
**dengan dua koreksi arah operator**:

1. **Koreksi #1**: TIDAK ADA minimasi RAM dan TIDAK ADA governor yang
   menahan CPU. Produk memakai hardware secara penuh saat dibutuhkan:
   worker mengikuti core dan beban (scale-UP), cache besar saat RAM
   tersedia, mmap untuk throughput. Overload ditangani oleh backpressure
   typed + fairness yang sudah ada — bukan pengurangan kemampuan.
2. **Koreksi #2**: lapisan hardware BUKAN modul sampingan — satu
   `HardwarePlan` cached menjadi sumber kebenaran tunggal yang dikonsumsi
   semua subsistem (store, autoscaler, GPU gate, ops), dan wajib ada stress
   test nyata lintas beban (1/10/50/100 agen × store 10MB/500MB/2GB).

## 7 pertanyaan pakar (arah baru, ditulis ulang)

1. **Masalah**: produk berjalan di hardware pengguna yang tidak diketahui
   (VM 2 vCPU sampai workstation besar). Tanpa arsitektur hardware, resep
   SQLite satu-ukuran-untuk-semua: page cache engine 2 MiB untuk store
   ratusan MB, tanpa mmap, paralelisme tidak mengikuti core — hardware
   mahal nganggur saat beban padat.
2. **Kendala keras**: kontrak beku tak disentuh; `serve/` milik SUB-132 tak
   disentuh (seam konsumsi plan diserahkan via board); probing tak pernah
   menggagalkan pembukaan store (fail-open); tanpa dependensi baru; tanpa
   subprocess/network di lapisan hardware; determinisme hasil query tidak
   boleh berubah (pragma mengubah mekanik akses, bukan hasil).
3. **Tujuan terukur**: (a) satu `HardwarePlan` cached dipakai store +
   autoscaler + GPU gate (dipin oleh test); (b) worker mengikuti demand
   sampai penuh core; (c) mmap = min(db, ½ MemAvailable, cap) dan cache =
   min(db, ⅛ MemAvailable, cap) terpasang nyata (PRAGMA terverifikasi);
   (d) stress matrix 1/10/50/100 agen × 10MB/500MB/2GB dengan angka nyata:
   0 error, scale-up 1→10 agen, tanpa collapse di 100 agen, p99 terbatas,
   RSS di dalam bound map.
4. **Manfaat**: pengguna di hardware besar mendapat throughput penuh;
   pengguna di VM kecil tetap aman (bound RAM adalah guardrail bertahan
   hidup, bukan sopan-santun); operator punya knob eksplisit + kill switch
   per mekanisme.
5. **Pelajaran dari kegagalan umum**: (a) "deteksi" yang mengarang
   (display adapter virtual dianggap compute) → diklasifikasi jujur;
   (b) tuning tanpa kill switch → setiap mekanisme punya saklar;
   (c) klaim performa tanpa angka → stress matrix terukur, bukan prosa;
   (d) asumsi "mmap menghemat RSS" — DIBANTAH pengukuran (lihat
   Mengejutkan): mmap dipakai untuk throughput/no-copy, bukan hemat RAM.
6. **Prinsip desain**: (1) pakai hardware penuh saat dibutuhkan —
   dilarang throttle-down, dilarang cap yang mengurung, dilarang nice;
   (2) fail-open pada probe (UNKNOWN/NONE dengan note jujur berarti
   paralelisme PENUH, bukan pengurungan); (3) fail-closed pada input
   operator yang ambigu; (4) satu sumber kebenaran — tidak ada subsistem
   yang menghitung hardware sendiri; (5) graceful overload = backpressure
   typed + fairness, bukan pengecilan pool.
7. **Kill switches** (setiap mekanisme bisa dimatikan sendiri dan
   mengumumkan aksinya): `KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING` (resep
   pragma historis persis), `KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER`
   (worker statis), `KYS_GPU_OFFLOAD` default off (GPU tak pernah aktif
   tanpa opt-in), plus knob per parameter (`storage.*`, `hw.*`).

## Arsitektur (satu sumber kebenaran)

```
hardware/probe.py      → deteksi nyata /proc + /sys (disk, RAM, CPU, GPU), cached, fail-open
hardware/tuning.py     → komputasi murni: mmap, cache, page_size, sync, profil disk
hardware/autoscaler.py → worker = clamp(ceil(load1), min_workers, min(cores, RAM allowance))
hardware/gpu_gate.py   → formula verdict (edges > 5M ∧ T_cpu > 3×(T_transfer+20µs) ∧ COMPUTE ∧ env on)
hardware/profile.py    → HardwarePlan TUNGGAL cached per path; dikonsumsi:
                           store.py (resep pragma via _resolve_storage_tuning),
                           autoscaler (knob yang sama), gpu_gate (probe yang sama)
ops/switches.py        → 2 kill switch + 11 knob (storage.*×6, hw.*×4, hw.gpu_offload)
```

Yang TIDAK terintegrasi (jujur): `serve/` (milik SUB-132, sedang diperbaiki)
— seam konsumsi `hardware_plan()` tersedia dan terdokumentasi di board untuk
koordinator; ingestion/resolver/retrieval saat ini sequential di src/ (tidak
ada keputusan hardware yang mereka hitung sendiri — diverifikasi grep:
nol pemakaian cpu_count/max_workers di luar serve/ dan hardware/).

## Keputusan desain utama

- **mmap = throughput**: min(db_size, ½ MemAvailable, cap 1 GiB default).
  Fresh db (size 0) mendapat hint sebesar memory bound — map adalah hint,
  bukan alokasi; db tumbuh ke dalamnya.
- **cache agresif terukur**: `PRAGMA cache_size = -min(db, ⅛ MemAvailable,
  cap 4 GiB)` KiB; di bawah ~2 MiB dibiarkan ke default engine. Default ON.
- **synchronous=NORMAL**: hanya SSD + opt-in operator (default FULL =
  historis); HDD/UNKNOWN tetap FULL dengan note jujur.
- **page_size hint per disk**: HDD → 8192 untuk db BARU saja (SQLite
  mengabaikan pada db existing — jujur, tanpa rewrite); knob eksplisit
  menimpa profil; non-power-of-two ditolak typed.
- **autoscaler**: `workers = clamp(ceil(load1), min_workers, min(cores,
  MemAvailable ÷ 512 MiB))`. Tidak ada nice, tidak ada throttle-down;
  saturasi = penuh core; RAM tak diketahui = fail-open ke core penuh.
- **disk-aware I/O**: `DISK_PROFILES` (SSD 25ms/32/4096, HDD 100ms/128/8192,
  UNKNOWN 50ms/64/4096) — flush cadence + group-commit batch diekspos via
  plan untuk konsumen (serve menunggu SUB-132).

## Bukti terukur (angka nyata host ini, 2 vCPU / 4 GB / SSD virtual)

### Probe
- `sda` ROTA=0 → SSD; MemAvailable ~2,7 GB; 2 vCPU; GPU VIRTUAL_DISPLAY
  (vmwgfx) — tidak pernah COMPUTE. Temuan TDD nyata: store men-probe
  SEBELUM file db ada → `probe_disk_kind` kini ancestor-walk.

### Drill RSS (165,3 MiB db, 3000 point reads acak, /proc/self/statm)
| Konfigurasi | ΔRSS |
|---|---|
| mmap=0 (jalur read()) | 2,6 MiB |
| mmap=8 MiB | 4,0 MiB |
| mmap=db/4 (41 MiB) | 27,4–32,9 MiB |
| mmap=penuh (165 MiB) | 144,3 MiB |

**Mengejutkan (jujur)**: mmap TIDAK menurunkan RSS — readahead kernel
menarik halaman tetangga ke RSS. Klaim awal brief ("RSS tidak naik berarti")
dibantah pengukuran; yang berlaku dan dipin test: **ΔRSS ≤ bound map
terkonfigurasi** — worst-case pertumbuhan RSS ≤ ½ MemAvailable. mmap dipakai
untuk throughput (no-copy, tanpa double-buffering, halaman reclaimable),
sesuai koreksi operator #1.

### Stress matrix (tests/test_hardware_stress.py, run nyata 201 s, 9/9 hijau)

Host: 2 vCPU Xeon E5-2690 v2, 4 GB RAM, SSD virtual. Setiap agen = thread
dengan EventStore sendiri (WAL multi-reader), point reads 64 KiB + 1% write
mix, seed deterministik.

| Situasi | Agen | Store | Ops | Throughput | p50 | p95 | p99 | ΔRSS | I/O read | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
| rendah | 1 | 500MB | 2.000 | 8.331 ops/s | 0,048 ms | 0,109 ms | 0,635 ms | 1,6 MiB | 0 MB | 0 |
| medium | 10 | 500MB | 10.000 | 9.468 ops/s | 0,067 ms | 0,311 ms | 10,8 ms | 2,9 MiB | 0 MB | 0 |
| berat | 50 | 500MB | 30.000 | 8.532 ops/s | 0,074 ms | 3,478 ms | 106 ms | 8,8 MiB | 0 MB | 0 |
| sangat padat | 100 | 500MB | 40.000 | 6.857 ops/s | 0,085 ms | 20,6 ms | 330 ms | 11,1 MiB | 0 MB | 0 |
| padat | 50 | 10MB | 12.800 | 9.441 ops/s | 0,059 ms | 3,765 ms | 58 ms | 0,2 MiB | 0,1 MB | 0 |
| medium | 10 | 2GB | 800 | 2.179 ops/s | 0,093 ms | 0,798 ms | 1,24 ms | 0,7 MiB | 23,1 MB | 0 |
| sangat padat | 100 | 2GB | 20.000 | 5.373 ops/s | 0,089 ms | 19,5 ms | 432 ms | 3,7 MiB | 118,2 MB | 0 |

Gate kematangan (terassert di test, semua lolos): 0 error di semua sel;
scale-up 1→10 agen = **1,14×** (≥ 1,1× — 2 vCPU + GIL membatasi, jujur);
100 agen = 72% throughput 10 agen (≥ 30%, tanpa collapse); p99 maksimum
432 ms << 5 s; ΔRSS maksimum 11,1 MiB, jauh di dalam bound map. Catatan
jujur: sel 2GB/10-agen (2.179 ops/s, 23 MB I/O nyata) adalah first-touch di
luar cache — disk host yang membatasi, bukan arsitektur. Angka mentah:
`$TMPDIR/kys-hw-stress/stress-results.json`.

## Tidak terbukti / batas jujur

- Speedup mmap terhadap latency query interaktif tidak diukur terpisah dari
  stress matrix (throughput sudah mencakupnya).
- Konsumen worker pool (serve/daemon) belum ter-wire — menunggu SUB-132;
  seam `hardware_plan()` sudah ada.
- GPU offload hanya keputusan terukur; tidak ada kernel GPU (by design
  verdict: jangan bangun sebelum graf >5M edge nyata).
- Probe non-Linux fail-open UNKNOWN (host ini Linux; path lain tidak diuji).
- Cache agresif adalah heap per-koneksi (tidak reclaimable seperti mmap) —
  bound ⅛ MemAvailable adalah guardrail bertahan hidup, bukan sopan-santun.
