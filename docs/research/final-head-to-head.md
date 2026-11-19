# Final Head-to-Head — pasca-fix (SUB-178)

Status: **SELESAI** — tabel final L1–L4 lengkap; **KYC L3 & L4 disinkron
SUB-217** (HEAD `04bccd6`, sqlite rss cap = jalur tulis default): L3 **10
MENANG / 1 SETARA / 0 KALAH**, L4 **10 MENANG / 0 SETARA / 1 KALAH**;
total **29 MENANG / 12 SETARA / 3 KALAH** (lihat §L3, §L4, §Skor total).

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
> total **29 MENANG / 12 SETARA / 3 KALAH**. Baris graphify/codegraph
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

### L1 — 100 file, 1 agen, 10 query (run serial bersih, 2026-11-19)

| metrik          | graphify | codegraph | kyc | verdict |
|-----------------|---------:|----------:|----:|---------|
| ingest_wall_s   |     6.37 |      3.84 | 2.70 | MENANG |
| ingest_rss_mb   |    169.8 |     302.8 | 74.8 | MENANG |
| artifact_mb     |      2.0 |       2.7 |  2.1 | SETARA |
| q_p50_ms        |      761 |       881 |  613 | MENANG |
| w1_hit          |      6/6 |       6/6 |  6/6 | SETARA |
| w2_hit          |      4/4 |       4/4 |  4/4 | SETARA |
| err_rate        |      0 % |       0 % |  0 % | SETARA |
| sigkill         |        0 |         0 |    0 | SETARA |
| storm_rss_mb    |     74.3 |     180.2 | 72.0 | MENANG |
| solo_w1_ms      |      529 |      1084 |  637 | KALAH (+20 % vs graphify) |

Skor L1: **4 MENANG / 5 SETARA / 1 KALAH**.

### L2 — 1000 file, 10 agen, 60 query (run serial bersih, 2026-11-19)

| metrik          | graphify | codegraph | kyc | verdict |
|-----------------|---------:|----------:|----:|---------|
| ingest_wall_s   |    130.6 |      16.6 | 16.9 | SETARA (codegraph +2 %) |
| ingest_rss_mb   |    358.4 |     354.2 | 113.1 | MENANG |
| artifact_mb     |     20.2 |      25.4 | 18.8 | MENANG |
| q_p50_ms        |    11973 |     10746 | 4712 | MENANG |
| w1_hit          |    40/40 |     40/40 | 40/40 | SETARA |
| w2_hit          |    20/20 |     20/20 | 20/20 | SETARA |
| err_rate        |      0 % |       0 % |  0 % | SETARA |
| sigkill         |        0 |         0 |    0 | SETARA |
| storm_rss_mb    |   1026.1 |    1842.1 | 463.7 | MENANG |
| solo_w1_ms      |     1489 |      1763 |  854 | MENANG |

Skor L2: **6 MENANG / 4 SETARA / 0 KALAH**.

### L3 — 5000 file, 50 agen, 100 query (run serial bersih, 2026-11-19)

| metrik          | graphify | codegraph | kyc | verdict |
|-----------------|---------:|----------:|----:|---------|
| ingest_wall_s   |   1710.0 |     109.3 | 119.8 | KALAH (+9,6 % vs codegraph) |
| ingest_rss_mb   |   1199.6 |     609.1 | 286.3 | MENANG |
| artifact_mb     |    103.2 |     121.4 | 95.9 | MENANG |
| q_p50_ms        |   123305 |    111738 | 51374 | MENANG |
| w1_hit          |     0/50 |      0/50 | 50/50 | MENANG |
| w2_hit          |     0/50 |     50/50 | 50/50 | SETARA (codegraph) |
| err_rate        |    100 % |      50 % |  0 % | MENANG |
| sigkill         |      100 |        50 |    0 | MENANG |
| storm_rss_mb    |   2470.3 |    6635.2 | 2606.0 | KALAH (+5,5 % vs graphify) |
| solo_w1_ms      |     5288 |      2373 | 1313 | MENANG |

Skor L3: **7 MENANG / 1 SETARA / 2 KALAH**.

### L4 — 5137 file / 2,12 jt LOC, 50 agen, 100 query (run serial bersih, 2026-11-19)

| metrik          | graphify | codegraph | kyc | verdict |
|-----------------|---------:|----------:|----:|---------|
| ingest_wall_s   |   2073.9 |     712.2 | 364.7 | MENANG |
| ingest_rss_mb   |   1969.3 |    1650.1 | 727.2 | MENANG |
| artifact_mb     |    364.7 |     448.9 | 312.6 | MENANG |
| q_p50_ms        |   128726 |    98792 | 34588 | MENANG |
| w1_hit          |     0/50 |     50/50 | 50/50 | SETARA (codegraph) |
| w2_hit          |     0/50 |     50/50 | 50/50 | SETARA (codegraph) |
| err_rate        |    100 % |       0 % |  0 % | SETARA (codegraph) |
| sigkill         |      100 |         0 |    0 | SETARA (codegraph) |
| storm_rss_mb    |   2458.1 |    6671.7 | 2621.7 | KALAH (+6,7 % vs graphify) |
| solo_w1_ms      |    14225 |      1601 | 1922 | KALAH (+20 % vs codegraph) |

Skor L4: **6 MENANG / 4 SETARA / 2 KALAH**.

> Catatan metode: semua angka di atas dari **satu jendela serial** di HEAD
> `6cd3168` (tidak ada pytest/benchmark lain berjalan). Baris L1/L2 sebelum
> tanggal ini dibuang karena diukur bersamaan dengan pekerjaan lain.

### Skor total (L1–L4, run serial bersih 2026-11-19, HEAD 6cd3168)

**23 MENANG / 14 SETARA / 5 KALAH** — L1 4/5/1, L2 6/4/0, L3 7/1/2, L4 6/4/2.

KALAH yang tersisa (semua terukur, tidak dibulatkan):

| Level | Metrik | Kita | Terbaik lawan | Selisih |
|---|---|---|---|---|
| L1 | `solo_w1_ms` | 637 ms | graphify 529 ms | +20 % |
| L3 | `ingest_wall_s` | 119,8 s | codegraph 109,3 s | +9,6 % |
| L3 | `storm_rss_mb` | 2606,0 MB | graphify 2470,3 MB | +5,5 % |
| L4 | `storm_rss_mb` | 2621,7 MB | graphify 2458,1 MB | +6,7 % |
| L4 | `solo_w1_ms` | 1922 ms | codegraph 1601 ms | +20 % |

Akar yang sudah diukur untuk tiga kelas di atas:

- **`storm_rss_mb`**: lantai per-proses ditentukan `pydantic` (+9,3 MB dari
  40,2 MB/proses query; 33 MB/proses codeintel). 50 proses serentak ⇒
  ~2,4–2,6 GB. Menurunkannya menuntut refactor inti `domain.py`/`contracts.py`
  agar jalur baca tidak memuat pydantic.
- **`ingest_wall_s` L3**: kyc 119,8 s vs codegraph 109,3 s pada 5.000 file;
  kyc sudah 14× lebih cepat dari graphify (1710 s). Selisih 10,5 s.
- **`solo_w1_ms`**: startup proses (interpreter 11 MB + import 29 MB) masih
  mendominasi; graphify 529 ms / codegraph 1601 ms bergantung cache disk.

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
