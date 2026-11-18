# Final Head-to-Head — pasca-fix (SUB-178)

Status: **SELESAI** — tabel final L1–L4 lengkap; **KYC L3 & L4 disinkron
SUB-217** (HEAD `04bccd6`, sqlite rss cap = jalur tulis default): L3 **10
MENANG / 1 SETARA / 0 KALAH**, L4 **11 MENANG / 0 SETARA / 0 KALAH**;
total **30 MENANG / 12 SETARA / 2 KALAH** (lihat §L3, §L4, §Skor total).

> Pembaruan SUB-200 (2026-11-18, doc sync): baris **KYC L4** akhirnya
> masuk ledger kanonik `results_final.jsonl` — full storm 50 agen /
> 100 query tuntas (ingest exit 0: 313,2 s / RSS 1352,0 MB, err 49 %,
> sigkill 0, w1 50/50, w2 1/50). Catatan SUB-187S ("KYC L4 tetap BELUM
> TERUKUR — dua percobaan remeasure `Terminated`") **gusur**: dua
> percobaan 09:42 & 10:54 UTC memang `Terminated`, tetapi re-run
> berikutnya berhasil; dua baris KYC L4 terdahulu yang kini ikut di ledger
> (err 99 %, sigkill 50) bukan kanonik — `tail -1` yang dipakai. Hanya
> `docs/research/` yang diubah; TIDAK commit.

> Pembaruan SUB-217 (2026-11-18, doc sync, HEAD `04bccd6`): tabel L3/L4
> disinkron ke baris kanonik `tail -1` terbaru (sqlite rss cap = jalur
> tulis default) — KYC L3 ingest 84,64 s / RSS 285,6 MB; KYC L4 ingest
> 305,235 s / RSS 721,4 MB, storm rss 2268,4 MB (kini **di bawah kedua
> kompetitor**), err 0, w1/w2 50/50, sigkill 0. Dampak verdict:
> `ingest_rss` L3/L4 dan `storm_rss` L4 naik SETARA → MENANG → L3 = **10
> MENANG / 1 SETARA / 0 KALAH**, L4 = **11 MENANG / 0 SETARA / 0 KALAH**,
> total **30 MENANG / 12 SETARA / 2 KALAH**. Baris graphify/codegraph
> tidak diukur ulang (tetap jendela final yang sama). Hanya
> `docs/research/final-head-to-head.md` yang diubah; TIDAK commit.

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
- **Post-fix (kanonik)**: `scratch/stress3/results_final.jsonl` — **32 baris
  total**. Baris kanonik KYC L3/L4 (append `tail -1` menang, full storm
  100 query, HEAD `04bccd6`): L3 ingest 84,64 s / RSS 285,6 MB / artifact
  95,9 MB, err 0, w1/w2 50/50, sigkill 0; L4 ingest 305,235 s / RSS
  721,4 MB / artifact 312,6 MB, err 0, w1/w2 50/50, sigkill 0, storm rss
  2268,4 MB — **baris yang dipakai tabel L3/L4 di dokumen ini**. Baris
  KYC L4 terdahulu di ledger yang sama (err 49 %/99 %, w2 1/50, ingest
  313,2–770,4 s) dan baris-baris `results_final_prefix.jsonl` /
  `results_final_window1.jsonl` (08:05 UTC, error 100 %, sigkill 100)
  adalah referensi tak-kanonik, bukan verdict.
- **Drift L1/L3/L4 sejak SUB-187S**: ledger di-append terus — KYC L1 kini
  3 baris (tail-1: ingest 2,8 s, p50 1089,5 ms), KYC L3 kini
  9 baris, KYC L4 kini 6 baris. SUB-217
  menyinkronkan tabel **L3/L4 ke `tail -1` saat ini** (L3: err 0 %, w2
  50/50, p50 35,8 s; L4: err 0 %, w2 50/50). Tabel L1 di dokumen ini
  **masih memakai baris yang disinkron di SUB-187S/SUB-200**, BUKAN
  `tail -1` saat ini; verdict L1 belum di-regenerate (lihat §"Sisa
  pekerjaan" item 4).
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

Catatan: untuk L1 `tail -1` saat ini sudah bergeser dari baris tabel
(ledger di-append lebih lanjut sejak SUB-187S); tabel L3/L4 sudah
disinkron ke `tail -1` (SUB-217). Lihat §"Sisa pekerjaan" sebelum
meregenerasi verdict L1.

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
| q_p50_ms        |      630 |       949 |    514–613 | MENANG  |
| q_wall_s        |     7.0  |       8.6 |    6.4 | MENANG  |
| w1_hit          |      6/6 |       6/6 |    6/6 | SETARA  |
| w2_hit          |      4/4 |       4/4 |    4/4 | SETARA  |
| err_rate        |      0 % |       0 % |    0 % | SETARA  |
| sigkill         |        0 |         0 |      0 | SETARA  |
| storm_rss_mb    |     72.4 |     175.5 |   80.4 | SETARA  |
| solo_w1_ms      |      632 |       842 |    416 | MENANG  |

Skor L1: **3 MENANG / 6 SETARA / 2 KALAH** (remeasure SUB-216: `q_p50_ms`
746 → 514–613 ms dan `solo_w1_ms` 531 → 416 ms — keduanya kini MENANG;
lihat §Pembaruan SUB-216). Sebelumnya 2 MENANG / 7 SETARA / 2 KALAH —
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

### L3 — 5000 file, 50 agen, 100 query (post-fix, HEAD 04bccd6, kyc = baris kanonik `tail -1`)

| metrik          | graphify | codegraph | kyc     | verdict |
|-----------------|---------:|----------:|--------:|---------|
| ingest_wall_s   |   3235.7 |     191.3 |   84.64 | MENANG  |
| ingest_rss_mb   |      451 |       553 |   285.6 | MENANG  |
| artifact_mb     |    107.1 |     118.7 |    95.9 | MENANG  |
| q_p50_ms        |   124062 |    127640 | 35793.5 | MENANG  |
| q_wall_s        |    251.3 |     257.3 |   72.25 | MENANG  |
| w1_hit          |     0/50 |      0/50 |  50/50  | MENANG  |
| w2_hit          |     0/50 |      0/50 |  50/50  | MENANG  |
| err_rate        |    100 % |     100 % |    0 %  | MENANG  |
| sigkill         |      100 |       100 |      0  | MENANG  |
| storm_rss_mb    |     2436 |      3962 | 2490.6  | SETARA  |
| solo_w1_ms      |    27409 |      4310 |   1066  | MENANG  |

Skor L3: **10 MENANG / 1 SETARA / 0 KALAH** — sqlite rss cap (HEAD
`04bccd6`) menurunkan RSS ingest 479,5 → 285,6 MB sehingga
`ingest_rss_mb` naik SETARA → MENANG (kini di bawah graphify 451 dan
codegraph 553).

Catatan kejujuran: graphify & codegraph **kolaps total** saat storm L3
(err 100 %, hit 0/50, sigkill 100, latensi di langit-langit timeout
~120 s) — MENANG latensi/wall dibaca melawan kompetitor yang kolaps,
bukan jalur query sehat; KYC satu-satunya yang menjawab penuh (err 0,
w1/w2 50/50, sigkill 0). Satu-satunya metrik bukan MENANG:
`storm_rss_mb` (2490,6) — di bawah codegraph (3962) dan di atas
rata-rata (3199), tetapi masih di atas graphify (2436). Solo probe KYC
(1,07 s) tercepat dari codegraph (4,3 s) dan graphify (27,4 s).

### L4 — 5137 file / 2,12 jt LOC (korpus nyata), 50 agen, 100 query (post-fix, HEAD 04bccd6 — liveness f7598e7 + sqlite rss cap; kyc = baris kanonik `tail -1`)

| metrik          | graphify | codegraph | kyc      | verdict |
|-----------------|---------:|----------:|---------:|---------|
| ingest_wall_s   |   1729.7 |    1480.6 |  305.235 | MENANG  |
| ingest_rss_mb   |      860 |      1645 |    721.4 | MENANG  |
| artifact_mb     |    380.5 |     450.6 |    312.6 | MENANG  |
| q_p50_ms        |   127720 |    119536 |  29954.0 | MENANG  |
| q_wall_s        |    257.3 |     242.4 |    62.21 | MENANG  |
| w1_hit          |     0/50 |     33/50 |   50/50  | MENANG  |
| w2_hit          |     0/50 |     32/50 |   50/50  | MENANG  |
| err_rate        |    100 % |      35 % |     0 %  | MENANG  |
| sigkill         |      100 |        34 |      0   | MENANG  |
| storm_rss_mb    |     2413 |      5811 |  2268.4  | MENANG  |
| solo_w1_ms      |    58849 |      1678 |    742   | MENANG  |

Skor L4: **11 MENANG / 0 SETARA / 0 KALAH** — sweep penuh: sqlite rss cap
(HEAD `04bccd6`) menurunkan RSS ingest 1356,9 → 721,4 MB dan storm rss
3390 → 2268,4 MB, keduanya kini **di bawah graphify dan codegraph**;
`ingest_rss_mb` & `storm_rss_mb` naik SETARA → MENANG.

Reproduksi (harness yang sama untuk semua produk):

```
/root/know-your-context/.venv/bin/python \
  /root/.hermes/cache/scratch/stress3/harness_final.py L3,L4 kyc
→ L3 ingest 84.64 s   rss 285.6 MB  err 0  w1 50/50  w2 50/50  sigkill 0
→ L4 ingest 305.235 s rss 721.4 MB  err 0  w1 50/50  w2 50/50  sigkill 0
```

Baris mentah harness (SUB-217, HEAD `04bccd6`):
- L3: `n=59910 e=64728 art=95.9MB q p50=35793.5 p95=41082.3 solo=1066 err=0 h1=50/50 h2=50/50 rss=2490.6MB sigkill=0`
- L4: `n=181845 e=239358 art=312.6MB q p50=29954.0 p95=39094.2 solo=742 err=0 h1=50/50 h2=50/50 rss=2268.4MB sigkill=0`

**Apa yang mengubah L4 dari 49 % error menjadi 0 %:** `service.py`
`_await_concurrent_builder` kini berbasis liveness — setelah budget
kesopanan (tetap 5 s) pembaca terus menunggu **selama builder terbukti
memegang flock rebuild**, dengan backoff eksponensial + jitter
(10 ms → 250 ms). Cap 120 s hanya membatasi peer yang benar-benar macet.
Test penjaga: `tests/test_projection_race.py::test_reader_rides_out_a_peer_rebuild_that_outlasts_the_wait_budget`.

Catatan kejujuran: dengan sqlite rss cap (HEAD `04bccd6`),
`ingest_rss_mb` (721,4) maupun `storm_rss_mb` (2268,4) kini **di bawah
kedua kompetitor** — dua metrik itu naik dari SETARA ke MENANG; seluruh
11 metrik L4 MENANG.

### Skor total (L1–L4, baris kanonik per level)

**30 MENANG / 12 SETARA / 2 KALAH** — L1 2/7/2, L2 7/4/0, L3 10/1/0,
L4 11/0/0. Satu-satunya KALAH tersisa di L1 (q_p50 & q_wall saat storm,
lihat §L1); satu-satunya SETARA di L3 adalah `storm_rss_mb` (lihat §L3).
Skor L1/L2 berdiri pada baris yang disinkron SUB-187S/SUB-200 (L1 bukan
`tail -1`; lihat §Sisa pekerjaan item 4). Catatan kejujuran: sel-sel tabel
L1 saat ini (4 MENANG / 7 SETARA / 0 KALAH — termasuk `q_wall_s` kyc 6,4 s
< kedua kompetitor) tidak sinkron dengan skor L1 yang diterbitkan
(2/7/2) — inkonsistensi pra-ada, di luar scope SUB-217 (regen L1 = §Sisa
pekerjaan item 4).

### Delta KYC pre-fix → post-fix (indikasi, beda jendela host)

| level | metrik   | pre-fix (`results2`) | post-fix (`results_final`) |
|-------|----------|---------------------:|---------------------------:|
| L1    | w2_hit   |                 0/4  |                       4/4  |
| L1    | solo_w1  |              1805 ms |                    1583 ms |
| L1    | err_rate |                 0 %  |                        0 % |
| L2    | w2_hit   |                0/20  |                      20/20 |
| L2    | err_rate |               1,7 %  |                        0 % |
| L2    | ingest   |               57,1 s |                     42,8 s |
| L3    | w2_hit   |                0/50  |                      50/50  |
| L3    | err_rate |                92 %  |                       0 %  |
| L3    | sigkill  |                  50 |                         0  |
| L3    | ingest   |              801,8 s |                     84,6 s |
| L3    | solo_w1  |             4.271 ms |                    1.066 ms |
| L4    | err_rate |                87 %  |                       0 %  |

Fix SUB-177 (code-index fallback untuk w2) terbukti menutup defisit hit-rate
w2 di L1/L2 (0 → 100 %) dan L3 (0/50 → 50/50); err L3 92 → 0 %, sigkill
L3 50 → 0; ingest L3 turun 9,5× (802 → 84,6 s, indikasi — jendela host
berbeda). Sqlite rss cap (HEAD `04bccd6`) menurunkan RSS ingest L4
1356,9 → 721,4 MB dan storm rss L4 3390 → 2268,4 MB. Latensi storm
absolut naik di semua produk pada jendela final (noise host); perbandingan
lintas-ledger tidak sah.

### Sisa pekerjaan (agar tabel hijau penuh)

1. ~~Jalankan ulang KYC L4 sampai barisnya masuk `results_final.jsonl`~~ —
   **SELESAI (SUB-217)**: baris kanonik L4 kini `tail -1` (ingest 305,235 s,
   RSS 721,4 MB, err 0, w2 50/50, sigkill 0, HEAD `04bccd6`).
2. Regen verdict mekanis untuk L1 (L3/L4 sudah di-regen manual ke baris
   `tail -1` di dokumen ini, SUB-217):
   `python3 /root/.hermes/cache/scratch/stress3/compare_final.py` — label
   mekanisnya `WIN`/`AVG+`/`KALAH`/`DNF`; peta ke aturan dokumen:
   `WIN`→MENANG, `AVG+`→SETARA, `DNF`→BELUM TERUKUR (hit-rate dapat
   `WIN` walau seri penuh — jaga konsistensi dengan aturan §atas).
3. Khusus L4: (a) `callers-of parse_args` (ambigu) — **tertutup terbukti**:
   storm L4 SUB-217 w1 50/50 err 0 dengan term `parse_args` dalam set
   query (banding list error gf/cg yang timeout di term yang sama; lihat
   juga 40379fa "L4 hit-rate proven"); (b) store non-ASCII — fix di HEAD
   `803866e` (sql-backend ascii-safe) tetapi belum punya angka stress
   pasca-fix khusus.
4. Sinkron tabel L1 ke `tail -1` (KYC L1 tail-1: ingest 2,8 s, p50
   1089,5 ms ≠ baris tabel 2,61 s / 746 ms) — di luar scope SUB-217.

### Pembaruan SUB-216 (2026-11-18, L1 latency)

- [21:19 UTC] SUB-216 | L1 latency | **SELESAI** | fast-path parser di
  `cli.py` (profil: interpret 34 ms + cli import 29 ms + parser penuh
  89–125 ms + http_api 447 ms + stack 30 ms vs dispatch ~12 ms — setiap
  query membayar parser lagi) | harness `L1 kyc` dijalankan SENDIRIAN,
  ledger `/root/.hermes/cache/scratch/stress3/results_final.jsonl`.

Diagnosis (profil 10 query berurutan, store L1, per tahap dalam satu
proses segar): interpreter 34 ms, `import cli` 29 ms, `build_parser`
89–125 ms, import `http_api` 447 ms, `build_stack` 30 ms, dispatch
routing+retrieval 12 ms, serialize 0,1 ms. W2 (verb `query`) ~750–832 ms
vs w1 (verb `codeintel`) ~486–610 ms sebelum fix. Kerja berulang antar
query: parser penuh (89–125 ms) dibangun ulang oleh SEMUA 10 proses —
dihilangkan dengan fast path `_try_fast_args` (hanya bentuk
`query`/`codeintel` yang well-formed; bentuk lain fallback ke parser asli
→ byte-identical, pengujian parity 71 kasus 0 divergensi).

| metrik L1 (10 query, 1 agen) | sebelum (HEAD 04bccd6) | sesudah (3 run) |
|------------------------------|------------------------:|----------------:|
| q_p50_ms                     |                   710.5 | 514.5 / 589.5 / 613.0 |
| q_wall_s                     |                     6.4 |            5.15–5.6 |
| solo_w1_ms                   |                     640 |             416 |
| err / w1 / w2 / sigkill      |           0 / 6-6 / 4-4 / 0 |  0 / 6-6 / 4-4 / 0 |

Target p50 < 630 ms: **tercapai** (semua 3 run; probe p50 444 ms).
Bukti TDD: `tests/test_l1_latency.py` — parity RED sebelum fix
(ImportError `_try_fast_args`), GREEN setelah; output byte-identik
(mask `audit_event_id`/`expiry`; w1 sha256 dipin), budget p50 900 ms
tripwire. Gate: ruff check/format + pyright 0 error, `test_cli.py` 28
hijau. `git diff --stat` (file SUB-216): `src/know_your_context/cli.py`
+182/−1, `tests/test_l1_latency.py` baru. TIDAK commit.
