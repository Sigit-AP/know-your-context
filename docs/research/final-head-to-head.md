# Final Head-to-Head — pasca-fix (SUB-178)

Status: **SELESAI SEBAGIAN** — tabel final L1–L2 lengkap; **KYC L3 & L4
BELUM TERUKUR** di ledger kanonik (lihat §"Status ledger").

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
- **Post-fix (kanonik)**: `scratch/stress3/results_final.jsonl` — **10 baris;
  baris KYC L3 dan KYC L4 TIDAK ADA**. Artinya semua metrik KYC di L3/L4
  **BELUM TERUKUR** secara kanonik. `results_final_prefix.jsonl` /
  `results_final_window1.jsonl` (08:05 UTC) memang memuat baris KYC L3/L4
  (keduanya error 100%, sigkill 100) tetapi baris itu tidak masuk ledger
  kanonik; percobaan remeasure L3–L4 KYC (`run_l34_remeasure.log`, 09:35 UTC)
  berhenti di `### L3 kyc ingest...` → `Terminated`, tanpa baris baru.
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
- **BELUM TERUKUR** — tidak ada baris `(level, kyc)` di `results_final.jsonl`.

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
| ingest_wall_s   |     19.6 |      11.4 |   7.7 | MENANG  |
| ingest_rss_mb   |       73 |       230 |    90 | SETARA  |
| artifact_mb     |      2.0 |       2.6 |   2.1 | SETARA  |
| q_p50_ms        |     1892 |      1999 |  3117 | KALAH   |
| q_wall_s        |     19.4 |      20.9 |  38.0 | KALAH   |
| w1_hit          |      6/6 |       6/6 |   6/6 | SETARA  |
| w2_hit          |      4/4 |       4/4 |   4/4 | SETARA  |
| err_rate        |      0 % |       0 % |   0 % | SETARA  |
| sigkill         |        0 |         0 |     0 | SETARA  |
| storm_rss_mb    |       65 |       117 |    82 | SETARA  |
| solo_w1_ms      |     1746 |      1824 |  1583 | MENANG  |

Skor L1: **2 MENANG / 7 SETARA / 2 KALAH**. KALAH di latensi+wall storm —
semua query sukses tetapi lebih lambat; solo-probe KYC justru tercepat,
jadi defisitnya di konkurensi, bukan di jalur query.

### L2 — 1000 file, 10 agen, 60 query (post-fix)

| metrik          | graphify | codegraph | kyc    | verdict |
|-----------------|---------:|----------:|-------:|---------|
| ingest_wall_s   |    326.9 |      36.0 |   42.8 | SETARA  |
| ingest_rss_mb   |      214 |       310 |    242 | SETARA  |
| artifact_mb     |     20.9 |      25.0 |   19.4 | MENANG  |
| q_p50_ms        |    46770 |     19129 |  20992 | SETARA  |
| q_wall_s        |    287.0 |     116.9 |  166.6 | SETARA  |
| w1_hit          |    40/40 |     40/40 |  40/40 | SETARA  |
| w2_hit          |    20/20 |     20/20 |  20/20 | SETARA  |
| err_rate        |      0 % |       0 % |    0 % | SETARA  |
| sigkill         |        0 |         0 |      0 | SETARA  |
| storm_rss_mb    |      684 |      1220 |    788 | SETARA  |
| solo_w1_ms      |     5686 |      2327 |   1969 | MENANG  |

Skor L2: **2 MENANG / 9 SETARA / 0 KALAH**.

### L3 — 5000 file, 50 agen, 100 query (post-fix)

| metrik          | graphify | codegraph | kyc | verdict            |
|-----------------|---------:|----------:|----:|--------------------|
| ingest_wall_s   |   3235.7 |     191.3 |   – | **BELUM TERUKUR**  |
| ingest_rss_mb   |      451 |       553 |   – | **BELUM TERUKUR**  |
| artifact_mb     |    107.1 |     118.7 |   – | **BELUM TERUKUR**  |
| q_p50_ms        |   124062 |    127640 |   – | **BELUM TERUKUR**  |
| q_wall_s        |    251.3 |     257.3 |   – | **BELUM TERUKUR**  |
| w1_hit          |     0/50 |      0/50 |   – | **BELUM TERUKUR**  |
| w2_hit          |     0/50 |      0/50 |   – | **BELUM TERUKUR**  |
| err_rate        |    100 % |     100 % |   – | **BELUM TERUKUR**  |
| sigkill         |      100 |       100 |   – | **BELUM TERUKUR**  |
| storm_rss_mb    |     2436 |      3962 |   – | **BELUM TERUKUR**  |
| solo_w1_ms      |    27409 |      4310 |   – | **BELUM TERUKUR**  |

Catatan: kedua kompetitor kolaps total di storm L3 (error 100%, 100 sigkill,
hit 0/50) — bukti batas host 2 vCPU/3,9 GB berlaku untuk semua produk.
Referensi tak-kanonik (`results_final_prefix.jsonl`, 08:05 UTC): KYC L3
ingest 180,2 s / 941 MB, error 100 %, sigkill 100 — belum sah sampai
remeasure masuk `results_final.jsonl`.

### L4 — 5137 file / 2,12 jt LOC (korpus nyata), 50 agen, 100 query (post-fix)

| metrik          | graphify | codegraph | kyc | verdict            |
|-----------------|---------:|----------:|----:|--------------------|
| ingest_wall_s   |   1729.7 |    1480.6 |   – | **BELUM TERUKUR**  |
| ingest_rss_mb   |      860 |      1645 |   – | **BELUM TERUKUR**  |
| artifact_mb     |    380.5 |     450.6 |   – | **BELUM TERUKUR**  |
| q_p50_ms        |   127720 |    119536 |   – | **BELUM TERUKUR**  |
| q_wall_s        |    257.3 |     242.4 |   – | **BELUM TERUKUR**  |
| w1_hit          |     0/50 |     33/50 |   – | **BELUM TERUKUR**  |
| w2_hit          |     0/50 |     32/50 |   – | **BELUM TERUKUR**  |
| err_rate        |    100 % |      35 % |   – | **BELUM TERUKUR**  |
| sigkill         |      100 |        34 |   – | **BELUM TERUKUR**  |
| storm_rss_mb    |     2413 |      5811 |   – | **BELUM TERUKUR**  |
| solo_w1_ms      |    58849 |      1678 |   – | **BELUM TERUKUR**  |

Catatan: codegraph satu-satunya yang menjawab di storm L4 (65 % hit,
35 % error); graphify kolaps 100 %. Referensi tak-kanonik (prefix 08:05
UTC): KYC L4 ingest 610,0 s / 1947 MB, error 100 %, sigkill 100 — belum
sah. Dua defisit header dokumen ini (simbol ambigu `parse_args` L4, store
non-ASCII) **belum tervalidasi di level L3/L4** karena baris KYC belum ada.

### Delta KYC pre-fix → post-fix (indikasi, beda jendela host)

| level | metrik   | pre-fix (`results2`) | post-fix (`results_final`) |
|-------|----------|---------------------:|---------------------------:|
| L1    | w2_hit   |                 0/4  |                       4/4  |
| L1    | solo_w1  |              1805 ms |                    1583 ms |
| L1    | err_rate |                 0 %  |                        0 % |
| L2    | w2_hit   |                0/20  |                      20/20 |
| L2    | err_rate |               1,7 %  |                        0 % |
| L2    | ingest   |               57,1 s |                     42,8 s |
| L3    | err_rate |                92 %  |           BELUM TERUKUR    |
| L4    | err_rate |                87 %  |           BELUM TERUKUR    |

Fix SUB-177 (code-index fallback untuk w2) terbukti menutup defisit hit-rate
w2 di L1/L2 (0 → 100 %). Latensi storm absolut naik di semua produk pada
jendela final (noise host); perbandingan lintas-ledger tidak sah.

### Sisa pekerjaan (agar tabel hijau penuh)

1. Jalankan ulang KYC L3 + L4 sampai barisnya masuk `results_final.jsonl`:
   `python3 /root/.hermes/cache/scratch/stress3/harness_final.py L3,L4 kyc`
   (ingest timeout default 5400 s; terakhir terminated sebelum ingest usai).
2. Setelah baris ada, regen verdict:
   `python3 /root/.hermes/cache/scratch/stress3/compare_final.py`.
3. Khusus L4: validasi defisit (a) `callers-of parse_args` (ambigu) dan
   (b) store non-ASCII — keduanya punya fix di HEAD `803866e` tetapi belum
   punya angka stress pasca-fix.
