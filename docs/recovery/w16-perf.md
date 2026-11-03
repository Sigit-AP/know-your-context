# W16 — Performance hardening (SUB-137)

Scope: `src/` + `bench/` + `tests/` + doc ini. TIDAK commit. Kontrak beku
(14 ops + v3/v4 addendum, fixture byte-frozen) tidak disentuh. Gate latensi
storm TIDAK dilemahkan (target 6000/18000/30000 ms dan REFERENCE_READ_P50_MS
tetap); yang dipercepat adalah sistemnya.

## 7 pertanyaan pakar (SEBELUM kode)

1. **Masalah**: storm latency gate (`test_sixteen_ops_fifty_agents_with_latency_per_lane`)
   kadang merah di host 2-vCPU ini: read p95 ~6,2s vs target 6,0s pada
   factor≈1.0 (dibuktikan pre-existing oleh SUB-132/134/135 dengan A/B di
   HEAD bersih). Masalah NYATA: tail latency di bawah beban 50 agen, bukan
   kecepatan mesin (factor tidak menyerapnya karena solo-read cepat).
2. **Kekurangan/kendala**: (a) semantik/kontrak beku — 16 verb, audit
   exactly-once, ack-implies-durability (ack hanya setelah COMMIT),
   fairness per-aktor, kapasitas statis; (b) gate tidak boleh dilemahkan;
   (c) determinisme test tetap hijau (PYTHONHASHSEED 0/1/42); (d) hanya 2
   vCPU — optimasi tidak boleh mengasumsikan core lebih.
3. **Tujuan terukur**: storm gate hijau 5/5 run berturut-turut di host ini
   TANPA mengubah target; setiap optimasi membawa angka before/after
   (p50/p95/p99 + throughput) pada benchmark yang sama; tidak ada optimasi
   tanpa bukti lebih cepat.
4. **Manfaat**: operator mendapat gate yang stabil-hijau di CI/host lambat
   karena sistem benar-benar lebih cepat; pengguna daemon melihat read/write
   latency lebih rendah di bawah beban campuran.
5. **Pelajaran dari masalah**: (SUB-132) flake storm sebelumnya adalah
   session-reaper yang memutus koneksi — diperbaiki dengan mengukur idleness,
   bukan menebak. Pola yang harus dicegah: "memperbaiki" gate dengan
   melonggarkan angka; klaim speedup tanpa angka dua-arah; profiling dari
   asumsi (N+1, fsync, lock) tanpa cProfile nyata.
6. **Prinsip desain**: (a) ukur dulu, optimasi kemudian — setiap hotspot
   punya bukti cProfile; (b) ack hanya setelah COMMIT (durabilitas tidak
   pernah dikorbankan untuk kecepatan); (c) setiap mekanisme baru punya kill
   switch; (d) fallback mempertahankan semantik byte-identik.
7. **Kill criteria**: jika setelah hotspot #1–#3 storm p95 masih >6,0s pada
   5 run, berhenti menambah optimasi, laporkan angka apa adanya + analisis
   struktural (mis. batas single-writer fundamental) + rencana berikutnya.

## Hipotesis kandidat (WAJIB diverifikasi dengan profiling, bukan ditebak)

- H1: writer loop tidur sampai 50ms di `_write_q.get(timeout=0.05)` sementara
  item audit menunggu → latensi floor per audit round-trip (2 per op read).
- H2: `asyncio.run()` per `_call_tool` — event loop baru per panggilan di
  writer/read/heavy lane.
- H3: setiap audit begin/complete = transaksi SQLite sendiri (synchronous
  FULL → fsync per journal) di luar group commit.
- H4: STALE_PROJECTION retry + `wait_for_publish` memanjangkan read p95 saat
  writer sibuk.
- H5: serialisasi JSON/model_dump berulang per call.

Bukti profiling + angka before/after diisi di bawah saat kerja berjalan.

## Bukti (diisi selama eksekusi)

(lihat bagian HASIL di akhir dokumen)
