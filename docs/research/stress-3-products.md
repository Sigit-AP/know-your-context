# Stress Test 3 Produk — 4 Level Beban, Metode Identik

SUB-172, 2026-11-16 (UTC). Retry ke-3 (dua percobaan sebelumnya mati saat model primer hilang dari marketplace; run final di model sekunder). Host: 2 vCPU, 3,9 GB RAM, 4 GB swap, Linux 5.15, Python 3.14.9, Node v26.12.1.

Produk & versi (dari `docs/research/install-3-products.md`, tidak diubah): **Graphify 0.9.111** (`uv tool install graphifyy[mcp]`), **CodeGraph 1.6.2** (`install.sh` bundle resmi), **know-your-context 0.3.0** (repo ini, `uv sync --frozen`). Repo produk tidak disentuh; semua state uji di `/root/.hermes/cache/scratch/stress3/`.

## 1. Metode (apple-to-apple)

**Korpus.** Korpus identik byte-untuk-byte untuk ketiga produk; tiap produk mendapat **copy pristine sendiri** per level. Ini wajib: ketiga produk menulis artefak di dalam tree korpus (`.codegraph/`, `graphify-out/`), dan kyc akan ikut memindai artefak itu bila berbagi direktori (terbukti di probe awal: kyc "meng-ingest" `codegraph.db` 4,3 MB dan gagal `FILE_TOO_LARGE`).

| Level | Sumber | File | LOC | Ukuran |
|---|---|---|---|---|
| L1 LOW | sintetik deterministik (seed 7) | 100 | 5.843 | 0,4 MB |
| L2 MEDIUM | sintetik deterministik (seed 7) | 1.000 | 58.295 | 4,0 MB |
| L3 HIGH | sintetik deterministik (seed 7) | 5.000 | 294.082 | 20 MB |
| L4 SUPER | korpus real terbesar yang bisa dirakit di host: union sha256-dedup `/root/src` + `/usr/lib/python3.10` + site-packages graphify + `src/tests` kyc (112 duplikat dibuang) | 5.137 | 2.117.598 | 76,8 MB |

Generator (`scratch/stress3/gen_corpus.py`) membuat modul Python dengan import lintas-modul, 2–4 fungsi per modul yang **saling memanggil** (jadi ada edge `calls`, bukan cuma `imports`), satu kelas + 3 method per file. Deterministik: level sama = bytes sama untuk ketiga produk.

**Beban agent.** Level = jumlah proses agent **konkuren** yang menembak query ke produk yang sama pada saat bersamaan; tiap agent menjalankan query-nya berurutan (`scratch/stress3/harness2.py`).

| Level | Agent konkuren | Query/agent | Total query | Komposisi |
|---|---|---|---|---|
| L1 | 1 | 10 | 10 | 6 W1 + 4 W2 |
| L2 | 10 | 6 | 60 | 4 W1 + 2 W2 |
| L3 | 50 | 2 | 100 | 1 W1 + 1 W2 |
| L4 | 50 | 2 | 100 | 1 W1 + 1 W2 |

**Dua beban query identik secara semantik**, dijalankan untuk ketiga produk dengan argumen yang sama:

- **W1 — "siapa yang memanggil simbol X" (traversal terbalik, depth 1)**: graphify `affected X --relation calls --depth 1`, codegraph `callers X` (semantik = callers langsung), kyc `codeintel callers-of X --depth 1`. Simbol: `fn_0_0` (L1–L3), `parse_args` (L4 — diverifikasi ada di ketiga indeks sebelum run).
- **W2 — "cari simbol/pengetahuan bernama T"**: graphify `query T --budget 400 --depth 1`, codegraph `query T -l 5`, kyc `query` (`{"purpose": T, "token_budget": 500}`). L1–L3: `Cls17`, `Cls3`, `fn_12_1`, `fn_0_0` (keempatnya diverifikasi **hit** di graphify+codegraph sebelum run). L4: `parse`, `cache`, `config`, `resolve`.

**Diukur per produk per level**: wall-clock ingest (perintah ingest resmi di tree bersih), **peak RSS seluruh process tree** (sampler 100 ms per-pgid), CPU time anak, ukuran artefak indeks, node/edge dari status resmi produk; query p50/p95/p99, error rate, hit rate terpisah, peak RSS agregat, CPU utilization (cpu_s/wall_s; 2 core = maks 2,0), plus **solo probe** 3 query W1 berurutan tanpa beban lain untuk memisahkan "biaya korpus" dari "biaya antrean"; timeout 120 s/query (hang di-SIGKILL lalu dicatat).

**Batasan yang diakui di depan:**

1. Ketiga produk diukur lewat **CLI per query** (proses baru tiap query) — satu-satunya antarmuka yang identik di ketiganya, dan itu memang jalur "agent memanggil tool". Konsekuensinya startup proses ikut terukur (§6 B4). Mode persisten (graphify-mcp, kyc `serve`/daemon, codegraph daemon) **tidak** diukur; angka di sini bukan angka server.
2. p50/p95 fase konkuren = wall-clock yang **melihat antrean CPU**. Karena itu solo probe wajib dibaca berpasangan.
3. Korpus sintetik L1–L3 punya topologi hub-and-spoke ekstrem: setiap modul memanggil `fn_0_0` modul 0, jadi `fn_0_0` punya ~376 caller di L1 dan ~5.000 di L3. Ini **menguntungkan produk yang membatasi output** dan **menghukum produk yang mengenumerasi semua edge**; L4 (real code) adalah uji skala yang lebih netral dan hasilnya berbeda (lihat §2). Ini dilaporkan, bukan disembunyikan.
4. Hit rate bukan ukuran kualitas retrieval (tidak ada ground truth relevance); ia hanya memisahkan "produk menjawab" dari "produk tidak menemukan apa pun".

## 2. Hasil ingest

| Level | Produk | File | Ingest (s) | LOC/s | Peak RSS (MB) | Node | Edge | Artefak (MB) |
|---|---|---|---|---|---|---|---|---|
| L1 | graphify | 100 | 6,70 | 872 | 73 | 1.296 | 3.175 | 2,0 |
| L1 | codegraph | 100 | **2,95** | **1.978** | 230 | 1.279 | 2.684 | 2,6 |
| L1 | kyc | 100 | 4,57 | 1.279 | 99 | 1.179 | 1.241 | 2,1 |
| L2 | graphify | 1.000 | 97,27 | 599 | 214 | 12.898 | 32.565 | 20,9 |
| L2 | codegraph | 1.000 | **11,14** | **5.232** | 322 | 12.919 | 27.625 | 25,0 |
| L2 | kyc | 1.000 | 57,07 | 1.021 | 225 | 11.919 | 12.764 | 19,4 |
| L3 | graphify | 5.000 | 1.459,46 | 202 | 442 | 64.864 | 164.519 | 107,1 |
| L3 | codegraph | 5.000 | **67,34** | **4.367** | 550 | 64.910 | 139.596 | 118,7 |
| L3 | kyc | 5.000 | 801,82 | 367 | 794 | 59.910 | 64.728 | 98,9 |
| L4 | graphify | 5.137 | **637,11** | **3.324** | 860 | 163.760 | 324.776 | 380,5 |
| L4 | codegraph | 5.137 | 724,02 | 2.925 | **1.632** | 160.848 | 407.133 | 450,6 |
| L4 | kyc | 5.137 | 760,26 | 2.785 | **1.937** | 181.845 | 191.373 | 295,2 |

Semua ingest exit 0, tidak ada crash, tidak ada timeout. Node/edge = angka status resmi produk (graph.json `nodes`/`links`; `codegraph status`; kyc: `node_count`/`edge_count` dari `codeintel` — untuk L4 harus lewat `--file` karena nama umum ambigu, §6 B6).

**Yang terbaca dari tabel:**

- **Sintetik (L1→L3)**: codegraph menang telak dan **linear** (3→11→67 s untuk 10x→50x file; 1.978→5.232→4.367 LOC/s). kyc 12x lebih lambat dari codegraph di L3 (802 vs 67 s) dan **superlinear**: 4,6→57→802 s untuk 50x file (175x waktu). graphify paling lambat di L3 (1.459 s, 22x codegraph) dan juga superlinear.
- **Real code (L4)**: urutannya berbalik dan **ketiganya setara** — graphify 637 s, codegraph 724 s, kyc 760 s (selisih ≤20%). Artinya keunggulan 12–22x codegraph di L3 adalah **artefak topologi sintetik** (fan-in ekstrem → 164k edge dari 5.000 file, dan kyc memarkir 33k–600k unresolved ref). Jangan generalisasi "codegraph 12x lebih cepat" ke repo nyata; di repo nyata 2,1 juta LOC, ketiganya satu kelas.
- **Efisiensi memori ingest**: graphify paling hemat (860 MB di 2,1M LOC), codegraph 1,63 GB, kyc 1,94 GB — kyc & codegraph mendekati/melewati separuh RAM host pada korpus terbesar.
- **Kepadatan graf berbeda** (bukan bug): untuk korpus yang sama, kyc menghasilkan edge paling sedikit di semua level (L3: 64.728 vs codegraph 139.596 vs graphify 164.519). graphify paling agresif membuat edge (termasuk edge non-`calls`), jadi "nodes/edges" bukan ukuran kualitas — tapi penting untuk biaya query. Perlu dicatat: angka kyc = `node_count`/`edge_count` indeks kode (`symbols`/`graph_edges`), **bukan** node knowledge — lihat B1; pada produk ini dua dunia itu terpisah.
- **Anomali yang harus dibaca apa adanya**: graphify L3 (1.459 s) **lebih lambat** dari L4 (637 s) padahal L4 7,2x LOC. Sebabnya biaya graphify di L3 didominasi resolusi cross-file pada hub sintetik (5.000 modul meng-import `mod0`), bukan ukuran korpus. Konsekuensi metodologis: L3 mengukur "topologi buruk", L4 mengukur "skala nyata" — dan untuk pertanyaan skala, L4 yang berlaku.

## 3. Hasil query

| Level | Produk | Agent | Query | p50 (ms) | p95 (ms) | solo p50 (ms) | konkuren/solo | qps | CPU util | Error | Hit W1 | Hit W2 | Peak agg RSS (MB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L1 | graphify | 1 | 10 | **756** | 899 | 852 | 0,9x | 1,31 | 0,88 | 0/10 | 6/6 | 4/4 | 42 |
| L1 | codegraph | 1 | 10 | **762** | 923 | 750 | 1,0x | 1,31 | 0,94 | 0/10 | 6/6 | 4/4 | 93 |
| L1 | kyc | 1 | 10 | 1.570 | 1.667 | 1.805 | 0,9x | 0,64 | 0,92 | 0/10 | 6/6 | **0/4** | 97 |
| L2 | graphify | 10 | 60 | 13.315 | 15.745 | 1.915 | 7,0x | 0,74 | 1,53 | 0/60 | 40/40 | 20/20 | 647 |
| L2 | codegraph | 10 | 60 | **5.016** | 6.521 | **746** | 6,7x | 1,90 | 1,55 | 0/60 | 40/40 | 20/20 | 1.218 |
| L2 | kyc | 10 | 60 | 13.795 | 14.938 | 1.888 | 7,3x | 0,76 | 1,51 | **1/60** | 40/40 | **0/20** | 1.285 |
| L3 | graphify | 50 | 100 | 122.109 | 123.583 | 8.145 | 15,0x | 0,41 | 1,40 | **100/100** | **0/50** | 0/50 | 2.423 |
| L3 | codegraph | 50 | 100 | **98.827** | 124.145 | **1.898** | 52,1x | 0,50 | 1,44 | **50/100** | 0/50 | **50/50** | 3.974 |
| L3 | kyc | 50 | 100 | 117.367 | 122.992 | 4.271 | 27,5x | 0,42 | 1,39 | **92/100** | 0/50 | 0/50 | 4.656 |
| L4 | graphify | 50 | 100 | 138.547 | 166.191 | 22.460 | 6,2x | 0,33 | 1,52 | **100/100** | 0/50 | 0/50 | 2.369 |
| L4 | codegraph | 50 | 100 | **77.391** | **83.701** | **840** | 92,1x | 0,64 | 1,48 | **0/100** | **50/50** | **50/50** | 4.092 |
| L4 | kyc | 50 | 100 | 121.489 | 122.910 | 11.683 | 10,4x | 0,41 | 1,39 | **87/100** | 0/50 | 0/50 | 4.217 |

Error = timeout 120 s (SIGKILL) atau exit non-zero di luar kode "no match" yang bertipe. Hit dipisah dari error: query bisa exit 0 tapi tidak menemukan apa pun.

**Yang terbaca:**

- **L1 (1 agent)**: graphify dan codegraph **seri** (756 vs 762 ms, selisih <1%). kyc **2x lebih lambat** (1.570 ms) — hampir seluruhnya startup: `--version` kyc 1,30–1,80 s vs graphify/codegraph 0,15 s (§6 B4). Di level ini biaya proses mendominasi, bukan graf.
- **L2 (10 agent)**: ketiganya masih sehat (0 error, kecuali 1 error kyc). codegraph 2,7x lebih cepat dari graphify/kyc (p50 5,0 s vs 13,3/13,8 s). Solo: codegraph 746 ms, graphify 1.915 ms, kyc 1.888 ms → selisih nyata ada di **biaya per query**, bukan cuma antrean.
- **L3 (50 agent)**: **ketiganya kolaps**. p50 98–122 s, dan CPU util 1,39–1,44 dari 2,0 → host jenuh. Error 100% (graphify), 50% (codegraph, hanya W1), 92% (kyc). codegraph satu-satunya yang masih menjawab W2 penuh (50/50) di L3; graphify & kyc 0/50 (timeout).
- **L4 (50 agent, 2,1M LOC)**: **codegraph satu-satunya yang tidak menghasilkan error** (0/100, semua hit, p50 77 s) dan solo-nya tetap **840 ms** — indeks SQLite-nya membuat biaya query nyaris tidak tergantung ukuran korpus. graphify 100% timeout dengan solo 22,5 s (**setiap query memuat ulang graph.json 380 MB**). kyc 87% error, solo 11,7 s.
- **Hit rate W2 kyc = 0 di semua level** (0/4, 0/20, 0/50, 0/50) — bukan karena lambat, tapi struktural: `query` tidak bisa melihat korpus hasil `ingest` (§6 B1). Ini temuan terpenting laporan ini dan satu-satunya tempat kyc kalah bukan karena performa.

## 4. Perilaku overload, crash, hang

**Tidak ada crash.** Nol segfault, nol panic, nol korupsi store/DB, nol proses yang mati sendiri. Semua kegagalan berbentuk (a) timeout 120 s yang kami SIGKILL, atau (b) typed error yang dikembalikan produk.

**Hang nyata (bukan crash):**

| Level | Produk | Query hang >120 s | Pola |
|---|---|---|---|
| L3 | graphify | 100/100 | semua query, termasuk W2; solo 8,1 s → 50 proses × muat 107 MB JSON = thrash |
| L3 | codegraph | 50/100 | hanya W1 (hot symbol ~5.000 caller); W2 100% sukses |
| L3 | kyc | 92/100 | W1 & W2; disertai typed error STALE_PROJECTION di sebagian run |
| L4 | graphify | 100/100 | solo 22,5 s (graph.json 380 MB) → 50 agent mustahil |
| L4 | codegraph | 0/100 | W1 & W2 semua selesai <120 s (p50 77 s) |
| L4 | kyc | 87/100 | W1 100% timeout (simbol ambigu + beban), W2 74% timeout |

**Mekanisme overload (terukur, bukan dugaan):**

- Ketiga produk adalah **proses CLI CPU-bound**; pada 50 agent, CPU util 1,39–1,52 dari 2,0 core → host jenuh, jadi wall-clock ≈ CPU-time × ~25. Itulah sebabnya p50 melonjak 15–92x dari solo.
- **RSS agregat melewati RAM fisik** di L3/L4: codegraph 3,97/4,09 GB, kyc 4,66/4,22 GB, graphify 2,42/2,37 GB pada host 3,9 GB → swap (4 GB) dipakai, terlihat dari p95 yang menempel di ~123 s (batas timeout) dan I/O wait.
- **Pola kegagalan berbeda per arsitektur**: graphify mati karena **memuat ulang seluruh graf tiap query** (biaya per query naik linear dengan ukuran graf: 0,85 s @1,3k node → 1,9 s @12,9k → 8,1 s @64,9k → 22,5 s @163,7k); codegraph hanya tersandung pada query hasil-besar (enumerasi ~5.000 caller) dan **pulih** di L4 saat simbol target ber-fan-in kecil; kyc tersandung di startup + traversal + guard konsistensi yang bisa salah positif.
- **Tidak ada produk yang "recover" di tengah badai** — semuanya butuh badai selesai dulu. Setelah fase konkuren berakhir, solo probe ketiganya kembali normal (codegraph 840 ms di L4), jadi tidak ada kebocoran state/daemon yang tertinggal.

## 5. Verdict jujur

| Dimensi | Menang | Setara | Kalah | Dasar |
|---|---|---|---|---|
| Ingest L1 (100 file) | codegraph (2,95 s) | graphify↔kyc (6,7 vs 4,6 s, satu kelas) | — | §2 |
| Ingest L2 (1.000) | codegraph (11,1 s) | — | graphify (97 s), kyc (57 s) | §2 |
| Ingest L3 (5.000 sintetik) | codegraph (67 s) | — | kyc (802 s, 12x), graphify (1.459 s, 22x) | §2 |
| Ingest L4 (2,1M LOC real) | **setara** | graphify 637 / codegraph 724 / kyc 760 s (≤20%) | — | §2 |
| Query L1 (1 agent) | graphify & codegraph seri (756/762 ms) | — | kyc (1.570 ms, 2x) | §3 |
| Query L2 (10 agent) | codegraph (5,0 s; solo 746 ms) | — | graphify (13,3 s), kyc (13,8 s) | §3 |
| Query L3 (50 agent) | codegraph paling tidak buruk (50% error) | — | graphify (100% hang), kyc (92% error) | §3/§4 |
| Query L4 (50 agent) | **codegraph** (0 error, solo 840 ms) | — | graphify (100% hang), kyc (87% error) | §3/§4 |
| Retrieval atas korpus hasil ingest | graphify & codegraph (W2 100% hit) | — | **kyc 0% hit** (B1) | §6 |
| Memori ingest | graphify (860 MB @2,1M LOC) | codegraph 1,63 GB ↔ kyc 1,94 GB | — | §2 |
| Stabilitas (crash/hang) | codegraph (0 crash, 0 hang di L4) | — | graphify & kyc (hang masif ≥L3) | §4 |

**Kesimpulan tiga kalimat.** Ingest: codegraph menang jelas di korpus kecil–menengah dan **seri** dengan dua lainnya di korpus real 2,1 juta LOC — jadi keunggulan ingest codegraph nyata tapi tidak universal. Query: **codegraph menang di semua level beban** (satu-satunya yang 0 error di L4, dan satu-satunya yang biaya query-nya tidak meledak bersama ukuran graf), graphify kalah karena memuat ulang seluruh graf per query, kyc kalah karena startup 2x + traversal mahal. Stabilitas: tidak ada produk yang crash, tapi pada 50 agent di host 2 vCPU/3,9 GB **ketiganya di luar kapasitas** — yang membedakan adalah apakah kegagalannya berupa timeout senyap (graphify), timeout sebagian (codegraph), atau error bertipe + timeout (kyc).

**Yang tidak bisa diklaim dari data ini:** kualitas retrieval (tidak ada ground truth), latensi mode server/daemon (tidak diukur), dan perilaku pada host ≥4 core (antrean CPU adalah penyebab dominan di sini).

## 6. Bug nyata produk kita (know-your-context 0.3.0)

Semua di bawah ini ditemukan dari run nyata, dengan perintah reproduksi. Tidak ada yang diperbaiki di sesi ini (scope SUB-172 = ukur, bukan patch).

### B1 — `ingest` tidak pernah menghasilkan knowledge node: `query` buta terhadap korpus hasil ingest (BERAT)

Setelah `ingest` 5.137 file (exit 0), store berisi indeks kode tapi **nol** knowledge:

```
files 5137 | symbols 181845 | graph_edges 293814 (edge_count codeintel 191373) | unresolved_refs 602907
node_revision 0 | node_relation 0 | evidence 0 | source_version 0 | event_log 51 (audit saja)
```

Akibatnya seluruh beban W2 (`query`) mengembalikan `routing: no_match`, `results: []` — **0/4, 0/20, 0/50, 0/50 hit di empat level**, padahal `codeintel callers-of` pada store yang sama bekerja (node_count 181.845). `god-nodes` juga `nodes: []`, `store_watermark: 0`.

Bukan sekadar "belum di-apply": tidak ada jalur resmi untuk meng-apply. `extraction review` → `queue_size 0`; `extraction approve` butuh `receipt_id` `ext-…` yang tidak pernah dibuat; `ingest_cli.py` docstring menyatakan output "proposal-only" sementara `write-contract-v5.md` menyatakan disk-mode menulis langsung — dua kontrak ini bertabrakan dan hasil nyatanya: **proposal ditulis ke tabel kode, tidak pernah menjadi knowledge node, dan tidak masuk antrean review**.

Repro:
```bash
S=/tmp/b1.sqlite; rm -f $S
know-your-context --store $S ingest /path/to/corpus --max-files 65536   # exit 0
echo '{"purpose":"cache","token_budget":500}' | know-your-context --store $S query -
# -> {"routing":"no_match","results":[],...}   padahal store sudah penuh indeks kode:
know-your-context --store $S codeintel callers-of fn_0_0 --depth 1      # -> entries terisi
sqlite3 $S 'select count(*) from node_revision; select count(*) from node_relation'   # -> 0 | 0
```

### B2 — `STALE_PROJECTION` false positive saat banyak pembaca pertama (BERAT)

Segera setelah ingest, pembacaan paralel gagal dengan error bertipe yang **secara logika mustahil**:

```
{"error":{"code":"STALE_PROJECTION","message":"the active projection does not cover knowledge watermark 0","retryable":true}}
```

Watermark 0 tidak mungkin "tidak tercakup". Terukur: **9 dari 40 operasi (22,5%) gagal** saat 20 agent membaca store yang baru selesai di-ingest (`scratch/stress3/repro_stale2.py`), dan 1/60 pada L2 di run utama. Setelah projection terbangun, 30 query paralel berikutnya 30/30 sukses — jadi ini balapan "pembangun projection pertama" vs pembaca, bukan korupsi.

Dampak nyata: pola kerja agent (ingest → langsung query) kena error acak 20%+; di harness ini juga menyebabkan probe `codeintel` pasca-ingest L4 tidak mengembalikan angka (n/e harus dibaca dari SQLite).

Repro:
```bash
python3 scratch/stress3/repro_stale2.py   # ingest 300 file lalu 20 agent x 2 op paralel
# -> {'STALE_PROJECTION': 9, 'ok': 31}
```

### B3 — `--max-files` default 1024 memotong korpus **diam-diam** dengan exit 0 (BERAT)

Ingest 1.100 file dengan default:

```
ingest: files 1024 (proposed 1024, skipped 0, failed 0, deleted 0) | nodes 13292 | ... | guards_hit max_files | ... 
EXIT=0
```

76 file tidak masuk, exit 0, dan satu-satunya jejak adalah `guards_hit max_files` di ujung baris ringkasan yang panjang. Untuk repo 5.000 file (L3) atau 5.137 file (L4), operator yang tidak menaikkan cap akan mendapat indeks 20% dari korpus dan **tidak ada pesan "TRUNCATED"**. Pembanding: graphify & codegraph mengindeks seluruh korpus tanpa cap semacam ini.

### B4 — Biaya startup CLI 1,3–1,8 s per query (SEDANG)

`--version` 5x: graphify 0,15–0,17 s, codegraph 0,14–0,39 s, **kyc 1,30–1,80 s**. Itu lantai latency setiap query CLI: di L1 (graf 1,3k node) kyc 1.570 ms vs graphify 756 ms / codegraph 762 ms — **2x lebih lambat tanpa beban apa pun**, dan selisih itu murni import + buka store + tulis audit, bukan kerja graf.

### B5 — Default `codeintel callers-of` = kedalaman 8 + limit 200 (SEDANG)

Default `--depth 8` berarti satu panggilan "siapa pemanggil X" melakukan closure 8 level; pembanding melakukan 1 level. Jujur: di L1 **tidak terbukti lebih lambat** (1,4 s depth-8 vs 1,57 s depth-1 — startup mendominasi, selisihnya noise), jadi ini bukan temuan performa terukur melainkan **default semantik yang berisiko**: biaya closure tumbuh dengan kedalaman × derajat, dan pada L3/L4 query ini ikut timeout. Default yang aman untuk agent: depth 1–2, kedalaman besar opt-in.

### B6 — Simbol ambigu: tidak ada kebijakan disambiguasi yang selesai (SEDANG–BERAT)

Di korpus L4 hampir semua nama umum ambigu: `parse_args` **14 definisi**, `read_text` **16 definisi**. codegraph menjawab normal (50/50 hit, melaporkan semua definisi). kyc mengembalikan `CODEINTEL_QUERY: ambiguous symbol 'parse_args': 14 candidates — …` — cepat saat sepi (~11 s wall, hampir semua startup), tapi **di bawah 50 agent semua 50 query W1 timeout >120 s**. Jadi jalur "ambigu" bukan jalan keluar yang murah: ia mengembalikan error yang memaksa operator menambah `--file`, dan di bawah beban ia berubah menjadi hang. Konsekuensi praktis yang saya alami sendiri: probe `codeintel` pasca-ingest L4 gagal menghitung node/edge sampai saya tambahkan `--file 00017_fsutil.py`. Perlu kebijakan: pilih kandidat terbaik + tandai `ambiguous: true` di output (seperti codegraph), atau tolak seketika (<100 ms) tanpa enumerasi.

### B7 — Ingest superlinear pada topologi fan-in tinggi (SEDANG, performa)

L1→L3 (50x file): kyc 4,6 → 802 s (**175x** waktu), graphify 6,7 → 1.459 s (218x), codegraph 2,95 → 67 s (23x, linear). Pada uji cap 1.024 file, kyc memarkir 39.772 unresolved ref (33.086 gagal di-resolve); di L4 ada 602.907 — konsisten dengan temuan cluster B (`docs/research/cluster-b-verdict.md`: resolver tail `LIKE` + scan). Di real code (L4) kyc tertolong karena ref-nya lebih sedikit per file, tapi laju 2.785 LOC/s masih 1,2x lebih lambat dari graphify dan 1,05x dari codegraph — jadi bukan bencana, tapi bukan keunggulan.

### Catatan kecil (bukan bug, tapi jebakan UX)

`ingest-directory <path>` me-resolve path terhadap **cwd**, bukan terhadap root korpus: memanggilnya dari direktori lain menghasilkan `PATH_OUTSIDE_ROOT: path '…' escapes the ingestion root`. Pesannya benar, tapi operator wajar mengira path absolut diterima.

## 7. Reproduksi & artefak

Semua di `/root/.hermes/cache/scratch/stress3/` (tidak di repo produk):

| File | Isi |
|---|---|
| `gen_corpus.py` | generator korpus sintetik deterministik (L1–L3) |
| `build_l4.py` | perakit korpus L4 (union sha256-dedup, 112 duplikat dibuang) |
| `measure.py` | runner tunggal: wall, peak RSS per-pgid (psutil 100 ms), timeout+SIGKILL, stdin |
| `harness2.py` | harness 3 produk x 4 level (ingest → query konkuren → solo probe), tulis `results2.jsonl` |
| `results2.jsonl` | 12 record mentah (satu per produk per level) — sumber semua tabel di atas |
| `repro_stale2.py` | repro B2 (9/40 STALE_PROJECTION) |
| `tables.py` | regenerasi tabel markdown dari `results2.jsonl` |

Menjalankan ulang: `python3 harness2.py L1,L2,L3,L4` (L4 ±35 menit; `INGEST_TIMEOUT` env untuk membatasi ingest).
