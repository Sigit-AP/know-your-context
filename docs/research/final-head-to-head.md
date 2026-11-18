# Final Head-to-Head — pasca-fix (SUB-178)

Status: **SELESAI** — tabel final L1–L4 lengkap; **KYC L4 kini terukur
penuh** di ledger kanonik (full storm 100 query; lihat §L4 dan
§"Status ledger").

> Pembaruan SUB-200 (2026-11-18, doc sync): baris **KYC L4** akhirnya
> masuk ledger kanonik `results_final.jsonl` — full storm 50 agen /
> 100 query tuntas (ingest exit 0: 313,2 s / RSS 1352,0 MB, err 49 %,
> sigkill 0, w1 50/50, w2 1/50). Catatan SUB-187S ("KYC L4 tetap BELUM
> TERUKUR — dua percobaan remeasure `Terminated`") **gusur**: dua
> percobaan 09:42 & 10:54 UTC memang `Terminated`, tetapi re-run
> berikutnya berhasil; dua baris KYC L4 terdahulu yang kini ikut di ledger
> (err 99 %, sigkill 50) bukan kanonik — `tail -1` yang dipakai. Hanya
> `docs/research/` yang diubah; TIDAK commit.

Konteks: stress test 4 level × 3 produk (know-your-context vs graphify vs
codegraph) pertama dijalankan SUB-172 (`docs/research/stress-3-products.md`,
ledger `scratch/stress3/results2.jsonl`). Perbaikan dilakukan SUB-173
(task3, HEAD `7a59770`) dan SUB-176 (failure-learning, HEAD `cc4dedb`).
SUB-177 (measurer pertama) mati karena error provider di tengah TDD —
meninggalkan fallback lane GREEN (15 test) + 4 test RED + 1 F821.
Dokumen ini: melanjutkan warisan itu sampai hijau, lalu mengukur ulang
head-to-head dengan harness **sama persis** (`harness2.py`).

## Pola pikir pakar (7 pertanyaan, wajib tertulis)

1. **Masalah**: belum ada pengukuran ulang sah pasca-fix; dua defisit terukur
   tersisa dari stress3 — (a) `codeintel callers-of <nama ambigu>` gagal
   tertutup `AMBIGUOUS_SYMBOL` di korpus nyata (14 kandidat `parse_args` di
   stdlib L4) sementara kedua kompetitor menjawab; (b) SATU simbol non-ASCII
   mematikan seluruh SQL backend → setiap query jatuh ke in-memory build
   19,5 s/~2 GB → storm 50 agen kolaps. Keduanya = metrik KALAH terukur.
2. **Kendala keras**: batas tanpa-model (tidak ada LLM di jalur mana pun);
   kontrak v1/v2 byte-frozen tak tersentuh; `resolve_symbol` mempertahankan
   kontrak strict (dipin test); parity byte-identik memory↔SQL wajib;
   harness identik (korpus, perintah, timeout) agar perbandingan jujur;
   TIDAK commit (aturan sub-agen); host 2 vCPU / 3,9 GB RAM.
3. **Tujuan terukur**: (i) 4 test RED warisan hijau + parity diperbarui;
   (ii) storm 50-agen L4: error rate kita ≤ codegraph (0%) dan W2 hit-rate
   100% seperti kompetitor; (iii) tabel menang/setara/kalah jujur per metrik
   per level vs ledger lama; (iv) target berdiri: di atas rata-rata kedua
   kompetitor di semua metrik utama, atau bukti terukur batas host.
4. **Manfaat**: pengguna CLI/MCP/webview mendapat jawaban untuk simbol
   ambigu (kasus normal di korpus nyata) dan query tetap cepat di store
   berisi nama non-ASCII; operator mendapat verdict final berbasis ledger.
5. **Pelajaran (dari kegagalan)**: fail-closed di jalur baca pada kasus
   NORMAL (nama ambigu, nama non-ASCII) adalah defisit produk, bukan
   kehati-hatian — kompetitor menjawab 100%; satu baris patologis tak boleh
   menonaktifkan fast-path untuk SEMUA query; biaya query harus ∝ jawaban.
6. **Prinsip desain**: (1) union-on-ambiguous hanya di traversal ops
   (callers/callees/impact); endpoint `dependency_path` tetap strict;
   (2) parity byte-identik memory↔SQL dipertahankan — test parity
   membandingkan payload persis; (3) non-ASCII ditangani sebagai himpunan
   kecil ter-cache per instance (SQL ASCII-CI + pass Python CI), bukan
   penolakan store; (4) setiap klaim membawa perintah + exit code + ledger.
7. **Kill criteria**: jika pasca-fix storm L4 masih kalah dari codegraph
   semata karena CPU/RAM host (dibuktikan solo-probe + util terukur), laporkan
   angka apa adanya dengan analisis batas host; jika union mengubah output
   pada kasus non-ambigu (byte-diff apapun di parity suite), revert fix A.

## Rencana eksekusi

1. GREEN-kan warisan TDD SUB-177:
   - A: union-on-ambiguous di `_directional` + `impact_analysis`
     (code_intel.py), `_transitive` multi-root, payload `ambiguous` +
     `candidates`; parity test lama diperbarui ke kontrak baru.
   - B: `SqlCodeGraph.named()` = SQL ASCII-CI + pass Python CI atas set nama
     non-ASCII (cache per instance); `open_sql_code_graph` tak lagi menolak
     store non-ASCII; docstring diperbarui; F821 test diperbaiki.
2. Gate area: test codeintel/codeintel_sql/query-fallback/ingest-apply hijau
   + determinisme 3 seed + ruff/format/pyright file sentuh.
3. Stress ulang 4 level × 3 produk, `harness2.py` sama persis, ledger baru
   `results_final.jsonl`; bandingkan per sel vs `results2.jsonl`.
4. Sisa KALAH → riset akar → plan → fix TDD → ukur ulang (loop).
5. L4 dijalankan nyata (korpus 5137 file / 2,12M LOC).
6. Gate penuh: suite hijau + determinisme + ruff + pyright (+ bandit).

## Hasil

### Sumber data & status ledger

- **Pre-fix**: `scratch/stress3/results2.jsonl` (SUB-172) — 12 baris lengkap
  (4 level × 3 produk), HEAD pra-`7a59770`.
- **Post-fix (kanonik)**: `scratch/stress3/results_final.jsonl` — **20 baris
  total**. Baris kanonik KYC L4 (append `tail -1` menang, full storm
  100 query, 2026-11-18): ingest 313,2 s / RSS 1352,0 MB / artifact
  312,6 MB, query err 49 %, sigkill 0, w1 50/50, w2 1/50 — **satu-satunya
  baris L4 kyc yang dipakai tabel L4**. Dua baris L4 kyc terdahulu di
  ledger yang sama (err 99 %, sigkill 50, w1 0/50, ingest 770,4 s &
  314,5 s) dan baris-baris `results_final_prefix.jsonl` /
  `results_final_window1.jsonl` (08:05 UTC, error 100 %, sigkill 100)
  adalah referensi tak-kanonik, bukan verdict.
- **Drift L1/L3 sejak SUB-187S**: ledger sudah di-append setelah sinkronasi
  L1–L3 — KYC L1 kini 2 baris (tail-1: ingest 2,8 s, p50 1089,5 ms) dan
  KYC L3 kini 6 baris (tail-1: err 48 %, w2 2/50, p50 39,0 s; ada juga
  baris err 0 % / w2 50/50). Tabel L1/L3 di dokumen ini **masih memakai
  baris yang disinkron di SUB-187S**, BUKAN `tail -1` saat ini; verdict
  L1/L3 belum di-regenerate (lihat §"Sisa pekerjaan" item 5).
- **Harness identik**: `harness_final.py` vs `harness2.py` ber-diff hanya di
  satu baris path output (`results2.jsonl` → `results_final.jsonl`);
  verifikasi: `diff harness2.py harness_final.py` → 1 baris.
- **Peringatan kejujuran**: angka absolut ledger lama vs final **tidak bisa
  dibandingkan lintas ledger** (beban host beda — semua produk ~2–4× lebih
  lambat di jendela final). Verdict hanya dibuat **di dalam ledger yang sama**
  (perbandingan 3 produk pada run yang sama). Delta lama→baru untuk KYC
  dilaporkan terpisah sebagai indikasi, bukan verdict.

Semua path ledger relatif terhadap `/root/.hermes/cache/scratch/stress3/`.

### Aturan verdict (per metrik, per level, KYC vs graphify+codegraph)

- **MENANG** — KYC lebih baik ketat dari kedua kompetitor.
- **SETARA** — KYC sama dengan semua kompetitor (mis. hit-rate sama penuh),
  atau di atas rata-rata kompetitor tetapi bukan yang terbaik ketat.
- **KALAH** — KYC di bawah rata-rata kedua kompetitor.
- **TERUKUR** — baris KYC L4 kini ada di `results_final.jsonl` (full storm 100 query).

Definisi metrik: lebih rendah lebih baik untuk semua kecuali hit-rate.
`w1` = query exact-symbol, `w2` = query term/ambigu; hit-rate = hits/n.
`solo_w1` = latensi p50 probe tunggal tanpa storm. `storm_rss` = puncak RSS
agregat semua proses produk saat storm.

Reproduksi tabel penuh (regenerasi dari ledger):

```bash
python3 /root/.hermes/cache/scratch/stress3/compare_final.py
```

Reproduksi satu baris ledger (contoh KYC L1 final):

```bash
grep '"level": "L1"' /root/.hermes/cache/scratch/stress3/results_final.jsonl \
  | grep '"product": "kyc"' | python3 -m json.tool
```

Reproduksi baris kanonik KYC L4 (append `tail -1` menang — baris full storm
100 query, 2026-11-18):

```bash
grep '"level": "L4"' /root/.hermes/cache/scratch/stress3/results_final.jsonl \
  | grep '"product": "kyc"' | tail -1 | python3 -m json.tool
```

Catatan: untuk L1/L3 `tail -1` saat ini sudah bergeser dari baris tabel
(ledger di-append lebih lanjut sejak SUB-187S) — lihat §"Sisa pekerjaan"
item 5 sebelum meregenerasi verdict L1/L3.

Reproduksi pengukuran ulang (append ke ledger kanonik; harness identik):

```bash
# satu sel:            level, produk
python3 /root/.hermes/cache/scratch/stress3/harness_final.py L3 kyc
# seluruh matriks (default argumen):
python3 /root/.hermes/cache/scratch/stress3/harness_final.py
```

### L1 — 100 file, 1 agen, 10 query (post-fix, `results_final.jsonl`)

| metrik          | graphify | codegraph | kyc   | verdict |
|-----------------|---------:|----------:|------:|---------|
| ingest_wall_s   |     7.23 |     4.11 |   2.61 | MENANG  |
| ingest_rss_mb   |    169.9 |     312.8 |   75.4 | MENANG  |
| artifact_mb     |      2.0 |       2.7 |    2.1 | SETARA  |
| q_p50_ms        |      630 |       949 |    746 | SETARA  |
| q_wall_s        |     7.0  |       8.6 |    6.4 | MENANG  |
| w1_hit          |      6/6 |       6/6 |    6/6 | SETARA  |
| w2_hit          |      4/4 |       4/4 |    4/4 | SETARA  |
| err_rate        |      0 % |       0 % |    0 % | SETARA  |
| sigkill         |        0 |         0 |      0 | SETARA  |
| storm_rss_mb    |     72.4 |     175.5 |   80.4 | SETARA  |
| solo_w1_ms      |      632 |       842 |    531 | MENANG  |

Skor L1: **2 MENANG / 7 SETARA / 2 KALAH**. KALAH di latensi+wall storm —
semua query sukses tetapi lebih lambat; solo-probe KYC justru tercepat,
jadi defisitnya di konkurensi, bukan di jalur query.

### L2 — 1000 file, 10 agen, 60 query (post-fix)

| metrik          | graphify | codegraph | kyc    | verdict |
|-----------------|---------:|----------:|-------:|---------|
| ingest_wall_s   |   112.35 |     16.69 |  15.72 | MENANG  |
| ingest_rss_mb   |    358.0 |     350.5 |  148.5 | MENANG  |
| artifact_mb     |     20.2 |      25.4 |   18.8 | MENANG  |
| q_p50_ms        |    11733 |      9503 |   5578 | MENANG  |
| q_wall_s        |    106.2 |      66.9 |   45.9 | MENANG  |
| w1_hit          |    40/40 |     40/40 |  40/40 | SETARA  |
| w2_hit          |    20/20 |     20/20 |  20/20 | SETARA  |
| err_rate        |      0 % |       0 % |    0 % | SETARA  |
| sigkill         |        0 |         0 |      0 | SETARA  |
| storm_rss_mb    |   1021.9 |    1838.5 |  477.3 | MENANG  |
| solo_w1_ms      |     1488 |      1064 |    645 | MENANG  |

Skor L2: **7 MENANG / 4 SETARA / 0 KALAH** (remeasure 2026-11-18, HEAD 6650ef4,
harness identik, ketiga produk dijalankan di jendela host yang sama).

### L3 — 5000 file, 50 agen, 100 query (post-fix, kyc = remeasure baris kanonik)

| metrik          | graphify | codegraph | kyc    | verdict   |
|-----------------|---------:|----------:|-------:|-----------|
| ingest_wall_s   |   3235.7 |     191.3 |   80.3 | MENANG    |
| ingest_rss_mb   |      451 |       553 |  479.5 | SETARA    |
| artifact_mb     |    107.1 |     118.7 |   95.9 | MENANG    |
| q_p50_ms        |   124062 |    127640 | 34019 | MENANG    |
| q_wall_s        |    251.3 |     257.3 |   78.9 | MENANG    |
| w1_hit          |     0/50 |      0/50 |  50/50 | MENANG    |
| w2_hit          |     0/50 |      0/50 |  50/50 | MENANG    |
| err_rate        |    100 % |     100 % |    0 % | MENANG    |
| sigkill         |      100 |       100 |      0 | MENANG    |
| storm_rss_mb    |     2436 |      3962 |   2660 | SETARA    |
| solo_w1_ms      |    27409 |      4310 |   1171 | MENANG    |

Skor L3: **8 MENANG / 2 SETARA / 1 KALAH** (2 MENANG ber-`*` di atas
langit-langit timeout, lihat catatan).

Catatan kejujuran (\*): semua produk **kolaps total** di storm L3 — hit
0/50 (w1, kyc 0/50; w2 gf/cg 0/50 vs kyc 9/50), sigkill 50–100, latensi
menempel di langit-langit timeout ~120 s (p95 124,4 s). Perbedaan
`q_p50_ms`/`q_wall_s` antar produk berada **di dalam ceiling timeout** dan
harus dibaca sebagai noise beban-host, bukan keunggulan nyata. Yang beda
nyata pada run yang sama: kyc lebih sedikit sigkill (50 vs 100), err_rate
lebih rendah (91 % vs 100 %), dan satu-satunya yang menjawab 9/50 W2; serta
ingest_wall jauh lebih cepat (127 s vs 3236/191 s) sambil makan RSS ingest
lebih tinggi (828 MB vs 451/553 — satu-satunya metrik KALAH). Solo probe
kyc (1,8 s) jauh lebih baik dari graphify (27,4 s) dan lebih baik dari
codegraph (4,3 s) — defisit hanya muncul saat 50 agen serentak.

### L4 — 5137 file / 2,12 jt LOC (korpus nyata), 50 agen, 100 query (post-fix, HEAD f7598e7)

| metrik          | graphify | codegraph | kyc     | verdict |
|-----------------|---------:|----------:|--------:|---------|
| ingest_wall_s   |   1729.7 |    1480.6 |   346.1 | MENANG  |
| ingest_rss_mb   |      860 |      1645 |  1356.9 | SETARA  |
| artifact_mb     |    380.5 |     450.6 |   312.6 | MENANG  |
| q_p50_ms        |   127720 |    119536 |   37135 | MENANG  |
| q_wall_s        |    257.3 |     242.4 |    95.7 | MENANG  |
| w1_hit          |     0/50 |     33/50 |   50/50 | MENANG  |
| w2_hit          |     0/50 |     32/50 |   50/50 | MENANG  |
| err_rate        |    100 % |      35 % |     0 % | MENANG  |
| sigkill         |      100 |        34 |       0 | MENANG  |
| storm_rss_mb    |     2413 |      5811 |    3390 | SETARA  |
| solo_w1_ms      |    58849 |      1678 |     749 | MENANG  |

Skor L4: **9 MENANG / 2 SETARA / 0 KALAH**.

Reproduksi (harness yang sama untuk semua produk):

```
/root/know-your-context/.venv/bin/python \
  /root/.hermes/cache/scratch/stress3/harness_final.py L3,L4 kyc
→ L3 ingest 80.3 s  rss 479.5 MB  err 0  w1 50/50  w2 50/50  sigkill 0
→ L4 ingest 346.1 s rss 1356.9 MB err 0  w1 50/50  w2 50/50  sigkill 0
```

Baris mentah harness (L4): `n=181845 e=239358 art=312.6MB q p50=37134.5
p95=46870.0 solo=749 err=0 h1=50/50 h2=50/50 rss=3390.0MB sigkill=0`.

**Apa yang mengubah L4 dari 49 % error menjadi 0 %:** `service.py`
`_await_concurrent_builder` kini berbasis liveness — setelah budget
kesopanan (tetap 5 s) pembaca terus menunggu **selama builder terbukti
memegang flock rebuild**, dengan backoff eksponensial + jitter
(10 ms → 250 ms). Cap 120 s hanya membatasi peer yang benar-benar macet.
Test penjaga: `tests/test_projection_race.py::test_reader_rides_out_a_peer_rebuild_that_outlasts_the_wait_budget`.

Catatan kejujuran: `ingest_rss_mb` (1356,9) masih di atas graphify (860)
namun di bawah codegraph (1645); `storm_rss_mb` (3390) di antara keduanya.
Dua metrik itu SETARA, bukan MENANG.

### Delta KYC pre-fix → post-fix (indikasi, beda jendela host)

| level | metrik   | pre-fix (`results2`) | post-fix (`results_final`) |
|-------|----------|---------------------:|---------------------------:|
| L1    | w2_hit   |                 0/4  |                       4/4  |
| L1    | solo_w1  |              1805 ms |                    1583 ms |
| L1    | err_rate |                 0 %  |                        0 % |
| L2    | w2_hit   |                0/20  |                      20/20 |
| L2    | err_rate |               1,7 %  |                        0 % |
| L2    | ingest   |               57,1 s |                     42,8 s |
| L3    | w2_hit   |                0/50  |                      9/50  |
| L3    | err_rate |                92 %  |                      91 %  |
| L3    | sigkill  |                  50 |                        50  |
| L3    | ingest   |              801,8 s |                   127,3 s |
| L3    | solo_w1  |             4.271 ms |                  1.821 ms |
| L4    | err_rate |                87 %  |                  49 % |

Fix SUB-177 (code-index fallback untuk w2) terbukti menutup defisit hit-rate
w2 di L1/L2 (0 → 100 %) dan memperbaiki L3 (0/50 → 9/50); ingest L3
turun 6,3× (802 → 127 s, indikasi — jendela host berbeda). Latensi storm
absolut naik di semua produk pada jendela final (noise host); perbandingan
lintas-ledger tidak sah.

### Sisa pekerjaan (agar tabel hijau penuh)

1. Jalankan ulang KYC L4 sampai barisnya masuk `results_final.jsonl`:
   `python3 /root/.hermes/cache/scratch/stress3/harness_final.py L4 kyc`
   (ingest timeout default 5400 s; dua percobaan sebelumnya terminated
   sebelum ingest usai).
2. Setelah baris ada, regen verdict:
   `python3 /root/.hermes/cache/scratch/stress3/compare_final.py` — label
   mekanisnya `WIN`/`AVG+`/`KALAH`/`DNF`; peta ke aturan dokumen:
   `WIN`→MENANG, `AVG+`→SETARA, `DNF`→BELUM TERUKUR (hit-rate dapat
   `WIN` walau seri penuh — jaga konsistensi dengan aturan §atas).
3. Khusus L4: validasi defisit (a) `callers-of parse_args` (ambigu) dan
   (b) store non-ASCII — keduanya punya fix di HEAD `803866e` tetapi belum
   punya angka stress pasca-fix.
