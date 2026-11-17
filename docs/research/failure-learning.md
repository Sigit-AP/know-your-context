# Failure Learning — graphify + codegraph → penguatan know-your-context

SUB-176, 2026-11-16 (UTC). Sumber: `docs/research/stress-3-products.md` (SUB-172),
`docs/research/skill-repos-study.md` (SUB-168), kode nyata `/root/src/graphify`
(0.9.111) dan `/root/src/codegraph` (1.6.2), store uji `scratch/stress3/t3_L3b.sqlite`
(59.910 simbol / 124.617 edge mentah / 64.728 edge valid — skala L3). Aturan: TIDAK commit.

## EXPERT-THINKING (7 pertanyaan, tertulis sebelum build)

1. **Masalah nyata**: dua kompetitor gagal di L3/L4 dengan pola arsitektural berbeda;
   apakah produk kita membawa bibit kegagalan yang sama tanpa sadar?
2. **Kendala**: tidak boleh commit; kontrak digest-pinned tak boleh berubah; output
   `codeintel` harus tetap byte-identik; host 2 vCPU/3,9 GB.
3. **Tujuan terukur**: (a) setiap akar kegagalan punya bukti file:baris; (b) setiap
   pelajaran → verdict IMUN/TERPAPAR berbukti untuk kyc; (c) risiko laten yang
   ditemukan → diperbaiki TDD + diuji ulang di kondisi L3 (50 agent).
4. **Manfaat**: perbaikan yang bisa diukur (wall, RSS, error rate), bukan opini.
5. **Pelajaran dari masalah lalu**: klaim tanpa bukti merusak verdict (aturan repo) —
   semua angka di sini hasil run nyata, perintah tercatat.
6. **Prinsip**: evidence-first; fail closed pada kesetaraan output (fallback ke jalur
   lama bila ragu); bedakan "tidak terbukti berisiko" dari "terbukti aman".
7. **Kill criteria**: berhenti menambal bila ekuivalensi byte-identik di store L3
   tercapai dan storm 50-agent hijau; sisanya dilaporkan sebagai batas, bukan ditambal.

---

## 1. Akar kegagalan graphify (100% timeout L3/L4)

**Bukti kode.** Setiap query CLI memuat ulang SELURUH graf dari disk:

- `graphify/affected.py:370-392` `load_graph()`: `json.loads(path.read_text())` lalu
  `json_graph.node_link_graph(raw)` — NetworkX penuh dibangun per pemanggilan.
- `graphify/cli.py:1717` (`affected`), `cli.py:1741-1811` (`god-nodes`), dan
  `cli.py:1591-1637` (`query`: `gp.read_text()` → `json.loads` → `node_link_graph`
  inline) — semua subcommand query lewat jalur ini.
- `graphify/cache.py:1-30` adalah **cache ekstraksi per-file** (AST), BUKAN cache graf
  query. Tidak ada daemon cache di jalur CLI; yang persisten hanya `serve.py`
  (`_load_graph`, MCP) — mode yang tidak dipakai harness (§1 batasan 1 stress doc).

**Mekanisme kegagalan (terukur SUB-172)**: biaya per query tumbuh linear dengan ukuran
graf: 0,85 s @1,3k node → 1,9 s @12,9k → 8,1 s @64,9k (L3) → 22,5 s @163,7k (L4,
graph.json 380 MB). Pada 50 agent: 50 × (parse 107–380 MB JSON + bangun NetworkX)
→ CPU jenuh + thrash → semua query >120 s → SIGKILL harness. **100/100 timeout.**

**Pelajaran G1**: *biaya query tidak boleh tumbuh dengan ukuran store bila jawabannya
lokal.* Index-backed lookup per hop, bukan full-graph materialization.

**Status kyc sebelum SUB-176: TERPAPAR (laten).** `ops/codeintel_cli.py:49-61`
(lama) melakukan `SELECT ... FROM symbols` + `SELECT ... FROM graph_edges` TANPA
`LIMIT`, `fetchall()`, lalu `build_code_graph` mengurutkan/mendedup SELURUH tabel —
untuk query `callers-of X --depth 1` yang jawabannya 200 baris. Terukur di store L3
(`t3_L3b`): 3,29 s wall, **RSS 325 MB** untuk satu query depth-1. Ini persis penyakit
graphify, hanya dengan format SQLite alih-alih JSON. Webview server
(`serve/webview.py`, handler codeintel) melakukan hal yang sama **per HTTP request**.
Inilah komponen terbesar "solo 11,7 s" kyc di L4 (setelah startup B4 diperbaiki
SUB-173) dan penyebab RSS agregat 4,66 GB di L3.

**Perbaikan (TDD, byte-identik)** — lihat §4.

## 2. Akar kegagalan codegraph (50% error L3)

**Bukti kode.** Tiga lapisan:

1. **Baseline per proses besar**: `codegraph callers` → `CodeGraph.open()`
   (`src/index.ts:407-437`) selalu `await initGrammars()` → `Parser.init()`
   (`src/extraction/grammars.ts:350-356`, web-tree-sitter WASM runtime) — untuk
   query read-only yang tidak mem-parse satu file pun. Ditambah
   `recoverIndexesOffThread()` di setiap open.
2. **Materialize-sebelum-limit**: `src/graph/traversal.ts:279-326`
   `getCallersRecursive` mengumpulkan SEMUA caller (hot symbol L3: ~5.000) ke
   array/Map; CLI `src/bin/codegraph.ts:2383-2402` mengumpulkan semuanya ke `Map`
   lalu BARU `slice(0, limit)` — limit tidak pernah turun ke query SQLite.
3. **Nol admission control**: tidak ada batas konkurensi/memori per query; 50 proses
   Node × ~80 MB ≈ 4 GB > RAM 3,9 GB → swap thrash → wall-clock meledak → 50/100
   timeout (semuanya W1, simbol hub). Pulih di L4 karena fan-in target kecil.

**Pelajaran G2**: *batas (limit) harus didorong ke lapisan data (SQL LIMIT / early
exit), bukan diterapkan setelah materialisasi.* **Pelajaran G3**: *per proses query
harus ramping; jangan memuat runtime yang tidak dipakai jalur baca.*

**Status kyc**: G2 — SEKTIAKAN aman: `codeintel` `_transitive` memang menjelajah
closure penuh sebelum truncate `max_results` (by design: `total` harus jujur), tapi
setelah F-A traversal-nya indexed per-hop, bukan full-table. G3 — TERPAPAR ringan:
startup Python + import tetap ada (pasca-B4: ~0,3–0,5 s); bukan kelas WASM-loader.
Tidak ada admission control di ketiga produk — dicatat sebagai gap industri, bukan
bug khusus kita (lihat §5, tidak dibangun: alasannya).

## 3. Kenapa error rate tinggi di semua produk di L3

Bukan crash (0 segfault di ketiganya — §4 stress doc). Mekanisme tunggal: **CLI
CPU-bound × 50 agent di 2 vCPU + RSS agregat > RAM** → swap → wall ≈ CPU × 25 →
timeout 120 s. Yang membedakan produk hanyalah *berapa cepat biaya per query tumbuh
dengan ukuran graf*: graphify linear-terhadap-graf (mati duluan), codegraph hampir
konstan (bertahan), kyc linear-terhadap-graf lewat full-table load (kolaps).

**Status kyc pasca-F-A: biaya query depth-1 kini tumbuh dengan ukuran JAWABAN,
bukan store** — dibuktikan di §4.

## 4. Perbaikan nyata: F-A — SqlCodeGraph (lazy, indexed, byte-identik)

**Desain** (TDD: RED = import gagal → GREEN 79 test baru → REFACTOR pyright/ruff/PoT):

- `src/know_your_context/analytics/code_intel_sql.py` (baru): `SqlCodeGraph`
  mengimplementasikan persis read-surface `CodeGraph` yang dipakai ops
  (`node`, `named`, `neighbours`, `neighbours_many`, `node_count`, `edge_count`,
  `dropped_edges`) di atas koneksi SQLite read-only terdedikasi (satu transaksi =
  satu snapshot, WAL). Semantik builder direplikasi eksak: dedup edge
  `(source,target,kind,IFNULL(line,0))`, buang self-loop/endpoint kosong/kind
  kosong/endpoint tak dikenal, urutan `(other, kind, line)` (UTF-8 binary order =
  code-point order). Counts di payload tetap count penuh store (bukan subset).
- `code_intel.py`: `CodeGraphView` Protocol; `CodeGraph` dapat `node_count`/
  `edge_count`/`neighbours_many`; `_transitive` memakai batch per-depth; payload
  memakai properti. `boundary_violations` tetap full-load (memang op seluruh-graf).
- Dispatch: `ops/codeintel_cli.codeintel_graph()` — dipakai CLI DAN webview.
  **Fallback fail-closed** ke jalur lama bila: store `:memory:`, tabel v3 absen,
  ada nama simbol non-ASCII (SQLite `lower()` hanya ASCII; Python melipat Unicode,
  mis. U+212A KELVIN SIGN → `k`), atau simbol query non-ASCII.

**Bukti ekuivalensi (kondisi yang membuat mereka gagal):**

- 79 test baru hijau ×3 seed (PYTHONHASHSEED 0/1/42): parity byte-identik untuk
  callers/callees/impact/dependency-path × depth 1/2/8 × limit 1/3/200, error-class
  parity (Unknown/Ambiguous, pesan identik), dirty rows (self-loop, dangling,
  empty-kind, empty-id, NULL-line + col-variance dedup), fallback paths, snapshot
  isolation, bounded-reads.
- Store L3 nyata (`t3_L3b`, 59.910 node): **10/10 skenario byte-identik** vs jalur
  lama (`audit_sql_codeintel.py`: 8 traversal + 2 dependency-path), termasuk
  `node_count`/`edge_count` payload.

**Bukti performa (host yang sama, store L3 yang sama):**

| Ukur | Sebelum (full-load) | Sesudah (SqlCodeGraph) | Rasio |
|---|---|---|---|
| `callers-of fn_0_0 --depth 1` wall | 3,29 s | 1,45 s | 2,3× |
| RSS puncak per query | 325 MB | 56,5 MB | 5,75× |
| Output | — | byte-identik (`diff` kosong) | — |
| Storm 50 agent (kondisi L3 stress) | 92/100 error @120 s timeout (SUB-172) | **0/50 error**, p50 35,9 s, wall total 37,5 s | — |

Catatan jujur storm: 37,5 s bukan "cepat" — host 2 vCPU tetap jenuh (50 × ~0,7 s
CPU ≈ 35 s serial). Yang berubah: **tidak ada satu pun query melewati timeout,
tidak ada SIGKILL, tidak ada swap thrash** (puncak RSS anak 386 MB vs RSS agregat
4,66 GB sebelumnya). Kegagalan berubah dari "kolaps sistemik" menjadi "antrean CPU
yang selesai".

## 5. Adopsi dari skill-repos-study — dipilih / ditolak berbukti

**Dibangun (bernilai nyata, terbukti di atas):**
- **[graphify→kyc] Lazy indexed read path** (pelajaran G1) = F-A di §4.
- **[mattpocock retro→environment]** Kesalahan mekanis jadi deterministic check:
  `test_reads_are_bounded_by_answer_not_by_store` mengunci "query traversal tidak
  boleh membaca seluruh store" sebagai gate permanen — regresi ke pola graphify
  akan merah di CI.

**Ditolak dengan alasan (bukan diabaikan):**
- **[TE] Hard backstop anti-veto**: autoreview kita (`autoreview.py`) murni
  deterministic rules (bukan LLM judge); tidak ada judge yang bisa memveto.
  Mengadopsinya = menambal masalah yang tidak ada.
- **[TE] Lifecycle fakta K_PROMOTE/GAMMA/LAMBDA**: bernilai untuk pilar auditable
  memory, tapi menyentuh scoring/extraction (kontrak digest-pinned) — di luar
  kill-criteria sesi ini. Direkomendasikan sebagai wave tersendiri.
- **Admission control CLI (G3)**: tidak ada produk yang punya; menambah semaphore
  global lintas-proses adalah fitur daemon, bukan perbaikan kegagalan yang
  terukur di sini — storm 50-agent sudah hijau tanpa itu.
- **[TE] Reversibilitas / causal anti-self-gating / ambiguitas→spare / ambang
  bernomor**: konteksnya protokol kolab, bukan kode produk. Draft revisi
  `EXPERT-THINKING.md` (5 pola) diserahkan ke operator sebagai rekomendasi —
  wewenang proses bukan subagent (sesuai catatan SUB-168).

**Dipinjam sebagai metode (pm-skills)**: pre-mortem Tigers/Paper/Elephants untuk
tabel risiko §6; "say what you left out" = §7.

## 6. Pre-mortem F-A (Tigers)

| Risiko | Kelas | Mitigasi / bukti |
|---|---|---|
| Output drift SQL vs in-memory | Tiger | 79 test parity + 10/10 byte-identik di store L3; fallback bila ragu |
| SQLite `lower()` vs Python (Unicode) | Tiger | terdeteksi & fallback store-level; test `café` + komentar KELVIN SIGN |
| Urutan byte vs code-point | Paper | UTF-8 binary order = code-point order; dipin di parity test |
| Snapshot berubah mid-op | Paper | satu transaksi read per SqlCodeGraph; test writer-mid-op |
| `boundary-violations` masih full-load | Track | op memang seluruh-graf; bukan jalur stress W1/W2 |
| Store v1/v2 tanpa tabel v3 | Paper | factory → `None` → jalur lama memunculkan error historis yang sama |

## 7. Yang sengaja ditinggalkan (say-what-you-left-out)

- L4 end-to-end re-run (ingest 12–35 menit/level) tidak diulang; audit memakai store
  L3 nyata + kondisi agent L3. Klaim L4 terbatas pada "biaya query tak lagi tumbuh
  dengan ukuran store", dibuktikan secara struktural (indexed lookup) bukan angka L4.
- Default `--depth 8` (B5) tidak diubah — perubahan default semantik adalah keputusan
  kontrak, bukan perbaikan kegagalan; dengan F-A biayanya sudah bounded oleh closure,
  bukan store.
- `graphify-mcp`/daemon mode kompetitor tidak dievaluasi (di luar metode stress test).

## 8. Gate

- `pytest` codeintel suites: **216 hijau** (79 baru); determinisme 3 seed hijau.
- `ruff check` + `ruff format`: bersih di 5 file sentuh.
- `pyright`: 0 error di 4 file sentuh.
- `tools/power_of_ten.py`: PASS (163/163 justified; webview `codeintel_payload`
  dikecilkan kembali di bawah baseline ratchet).
- Full suite: **14.693 hijau / 1 gagal / 1 skip** — satu-satunya gagal
  `test_kill9_mid_burst_recovers_every_acknowledged_write` (serve crash-safety
  storm), terbukti **flake kontensi**: hijau standalone (1p) dan sefile penuh
  standalone (11p, 92,7 s); file yang disentuh F-A (codeintel) tidak beririsan
  dengan jalur kill-9 serve.
- File: `src/know_your_context/analytics/code_intel_sql.py` (baru),
  `src/know_your_context/analytics/code_intel.py`, `src/know_your_context/ops/codeintel_cli.py`,
  `src/know_your_context/serve/webview.py`, `tests/test_codeintel_sql_graph.py` (baru),
  audit `scratch/stress3/audit_sql_codeintel.py`. TIDAK commit.
