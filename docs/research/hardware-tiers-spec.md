# Spesifikasi Tier Hardware — "minimum spec, kualitas menyesuaikan"

Prinsip: seperti game yang punya **minimum spec** lalu menaikkan kualitas saat
hardware lebih kuat, produk ini **selalu jalan** di lantai minimum dan
**memanfaatkan** hardware lebih besar untuk hasil yang lebih baik — bukan
dibebani tanpa alasan. Setiap parameter di bawah harus punya bukti angka;
yang belum terukur diberi label **[estimasi]**.

Host acuan pengukuran: 2 vCPU (Xeon E5-2690v2), 3,9 GB RAM, SSD virtual,
tanpa GPGPU. Semua angka di dokumen ini berasal dari harness yang sama
(`/root/.hermes/cache/scratch/stress3/harness_final.py`) pada HEAD `496ae9f`.

---

## 1. Batas bawah keras (TIER 0 — MIN)

Dijamin jalan, sudah dibuktikan di host 2 core/3,9 GB:

| Sumber daya | Minimum | Bukti |
|---|---|---|
| CPU | 2 core | ingest L4 281–305 s; storm 50 agen selesai 0 error |
| RAM | 2,5 GB tersedia | ingest L4 puncak 718–727 MB; storm L4 puncak 2.556–2.630 MB |
| Disk | 1 GB bebas per 100 rb node | artefak L4 312,6 MB untuk 181.845 node / 239.358 edge |
| Python | 3.14 (venv `uv`) | 16.6 rb test hijau |
| GPU | tidak wajib, 0 % dipakai | verdict GPU 0 % (`GPU-VERDICT.md`) |

Di bawah lantai ini produk **tetap benar** tetapi `storm_rss_mb` akan
melewati RAM host dan OS mulai swap — itu batas keras host, bukan bug.

---

## 2. Tier dan parameter yang berubah

| Parameter | TIER 0 MIN | TIER 1 STANDARD | TIER 2 HIGH | TIER 3 EXTREME | Sifat |
|---|---|---|---|---|---|
| `ingest_workers` | 1 | 2 | min(cores, 8) | min(cores, 16) | kecepatan |
| `commit_batch` | 1 | 64 | 512 | 2048 | kecepatan |
| `RESOLVE_BATCH` | 1024 | 4096 (sekarang) | 16384 | 65536 | memori↔kecepatan |
| sqlite `mmap_size` | 0 | 0 | ≤ ¼ RAM | ≤ ½ RAM | kecepatan baca |
| sqlite page cache | −2000 (1 MiB) | −2000 | 64 MiB | 256 MiB | kecepatan baca |
| `--depth` default traversal | 6 | 8 (sekarang) | 10 | 12 | **kualitas** (jangkauan graf) |
| `--limit` default hasil | 100 | 200 (sekarang) | 500 | 1000 | **kualitas** (kelengkapan) |
| kandidat ambigu yang dikembalikan | 5 | 20 | 100 | semua | **kualitas** |
| `projection_wait_seconds` | 5 | 5 | 15 | 30 | stabilitas |
| query paralel yang disarankan | 10 | 25 | 50 | 100 | kapasitas |

Tiga baris bertanda **kualitas** adalah inti analogi "resolusi naik": di
hardware besar operator mendapat **jawaban lebih lengkap** (traversal lebih
dalam, hasil lebih banyak, semua kandidat ambigu), bukan sekadar lebih cepat.

### Bukti per parameter

- `ingest_workers`/`commit_batch`: SUB-185 mengukur batching resolve
  436 → 330 MB (−24 %) pada bench 5.000 file; L3 ingest nyata 84,6 s.
  **[terukur]**
- `RESOLVE_BATCH`: test `test_resolve_chunked_output_is_batch_size_independent`
  membuktikan output identik untuk batch 17 vs 4096 — naik batch hanya
  mengubah memori/waktu, bukan hasil. **[terukur]**
- sqlite `mmap_size`/cache: probe store L4 (313 MB) menunjukkan **+59 MB RSS**
  untuk satu `COUNT(*)` dengan resep penuh vs **+3 MB** dengan cap. Itu
  sebabnya cap kini default jalur tulis; tier tinggi boleh membukanya lagi
  untuk beban baca berat. **[terukur]**
- `--depth`/`--limit`: `codeintel callers-of --depth 1` sudah memberi 113
  entri pada `parse_args` L4; depth lebih besar menambah jangkauan tetapi
  biayanya tumbuh dengan frontier. Biaya depth 10/12 di korpus L4
  **[estimasi]** — harus diukur sebelum dijadikan default tier atas.
- `projection_wait_seconds`: SUB-198 membuktikan tunggu berbasis `flock`
  menghapus 49/50 `STALE_PROJECTION`; cap 120 s hanya untuk peer macet.
  **[terukur]**

---

## 3. Aturan degradasi (fail-open, tanpa mengubah semantik)

1. **Deteksi**: `hardware/probe.py` membaca core, `MemAvailable`, tipe disk,
   dan keberadaan GPGPU; `hardware_plan()` menerjemahkannya jadi resep.
2. **Turun otomatis** bila: `MemAvailable < 2,5 GB` → TIER 0; disk rotational
   atau antrean > 2 → batch lebih kecil; `hw_tuning_disabled` → resep legacy.
3. **Tidak pernah menaikkan tier saat host sibuk** (governor sopan): tier
   hanya naik bila beban rendah stabil ≥ 30 s.
4. Degradasi **tidak boleh** mengubah isi jawaban semantik: hanya
   `depth`/`limit` yang bisa memangkas kelengkapan, dan itu **selalu
   dilaporkan** di output (`truncated: true`, `depth_capped: true`).

---

## 4. Cara menguji di satu host (simulasi tier)

```bash
# TIER 0 (minimum spec): cap penuh, tanpa tuning
KNOW_YOUR_CONTEXT_TIER=0 .venv/bin/python harness_final.py L4 kyc

# TIER 2 (HIGH): mmap/cache dibuka, depth & limit dinaikkan
KNOW_YOUR_CONTEXT_TIER=2 .venv/bin/python harness_final.py L4 kyc
```

Yang harus dibandingkan per tier: `ingest_wall_s`, `ingest_rss_mb`,
`storm_rss_mb`, `q_p50`, `err_rate`, **dan** `entries_returned` /
`depth_reached` untuk membuktikan kenaikan kualitas.

---

## 5. Status implementasi (jujur)

| Komponen | Status |
|---|---|
| Probe hardware (`hardware/probe.py`) | **ada** — core, RAM, disk kind, GPU |
| Resep tuning (`hardware/tuning.py`) | **ada** — mmap/cache/page_size, cap ingest default |
| Governor adaptif (`hardware/governor.py`) | **ada** — sopan saat host sibuk, fail-open |
| Kill switch (`ops/switches.py`) | **ada** — 19 switch, termasuk `hw.*` |
| **Resolver tier eksplisit** (`hardware/tiers.py`) | **GAP** — belum ada; tier masih implisit di plan |
| **`--depth`/`--limit` default per tier** | **GAP** — masih konstanta CLI (8 / 200) |
| **Pelaporan `truncated`/`depth_capped`** | **sebagian** — `TRUNCATED` ada untuk `--max-files` |

Yang **tidak** akan dilakukan (dengan alasan): GPU untuk traversal/FTS
(transfer > komputasi, lihat `GPU-VERDICT.md`); menaikkan default depth
tanpa pengukuran biaya di korpus L4.