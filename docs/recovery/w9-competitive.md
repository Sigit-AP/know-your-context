# W9 — competitive benchmark harness (SUB-113)

Tanggal: 2026-11-01. Scope: `bench/competitive/` (baru), `tests/test_competitive_bench.py`,
dokumen ini, +1 baris suite di `bench/README.md` (doc-only, preseden SUB-94/SUB-108).
`src/`, `bench/codebase/`, dan kontrak beku TIDAK disentuh.

## Tujuan

Mengukur tiga sistem pada TUGAS IDENTIK atas korpus yang sama (dataset ter-pin
`bench/codebase`: fixture + requests v2.32.4 + chalk v5.6.2 + tree-sitter-python
v0.23.6, sha256 per file):

1. **know-your-soul** — produk in-repo (EventStore file-backed + IngestionPipeline +
   SymbolLookup, driver `python -c` per invokasi);
2. **codegraph** 1.6.2 — `@colbymchenry/codegraph` via npm (terinstal persisten di
   `/root/.hermes/tools/codegraph-env`, ditemukan via `$CODEGRAPH_BIN` atau PATH);
3. **graphify** 0.9.95 — `/usr/local/bin/graphify`.

## Enam tugas

| Tugas | Definisi identik per sistem |
|-------|------------------------------|
| `ingest` | Index tree yang sama: wall time proses penuh, exit code, counts (symbols/nodes, edges/links, refs) |
| `cold-start` | p50 wall per query, **proses baru per query** (tak ada daemon hangat), 16 query beku dari ground truth codebase bench |
| `concurrent` | 8 klien paralel × 5 query, satu proses baru per query (satu-satunya interface CLI ketiga sistem); throughput + error |
| `crash` | SIGKILL di 40% wall ingest bersih (target requests), lalu pulihkan; integrity + data loss vs counts bersih |
| `determinism` | Dua ingest fresh → byte-identical artifact? (kys: `canonical_ingestion_dump`; codegraph: `.codegraph/codegraph.db`; graphify: `graphify-out/graph.json`) |
| `tokens` | Estimator beku kontrak `ceil(utf8_bytes/4)` atas jawaban stdout untuk query yang sama |

Semantik status entry: `OK` = tugas tereksekusi dan terukur; `NOT_SUPPORTED` =
sistem tak punya kemampuan (bukti terlampir — absen itu sendiri skor); `FAILED` =
tugas gagal dieksekusi. Verdict terukur (byte-identik/tidak, data loss, error
rate) hidup di field entry — skor buruk kompetitor adalah pengukuran, bukan
kegagalan harness. `run` exit 0 bila semua tugas tereksekusi; `--strict` exit 1
bila ada NOT_SUPPORTED.

Kebijakan exit code query: graphify mendokumentasikan 0=jawaban, 3=ambigu,
4=tak ada match — ketiganya jawaban non-error (query negatif beku
`zzz_no_such_symbol` memang harus exit 4). codegraph/kys: 0.

## Hasil run nyata (ledger-20261101T105528Z, exit 0, 63/63 entry OK)

### Ingest (wall, counts)

| Target | kys | codegraph | graphify |
|--------|-----|-----------|----------|
| fixture | 948ms · 10 sym/18 edges | 1572ms · 15 nodes/21 edges | 750ms · 20 nodes/26 links |
| chalk | 1319ms · 252/321 | 1683ms · 138/348 | 3468ms · 241/279 |
| requests | 7389ms · 1804/2832 | 2958ms · 1169/2685 | 5551ms · 1347/2397 |
| tree-sitter-python | 5956ms · 1445/1557 | 3105ms · 924/1686 | 7609ms · 1312/1430 |

Counts kys cocok PERSIS dengan baseline ter-pin `bench/codebase/baseline.json`
(requests: proposed=50, failed=79 file non-kode, nodes 1804, edges 2832).
Counts identik di dua run penuh independen (ledger 102736Z dan 105528Z);
wall time bervariasi dengan beban mesin, seperti biasa.

### Cold-start (p50, proses baru per query)

| Target | kys | codegraph | graphify |
|--------|-----|-----------|----------|
| fixture | 530ms | 548ms | 420ms |
| chalk | 337ms | 527ms | 474ms |
| requests | 369ms | 552ms | 612ms |
| tree-sitter-python | 311ms | 587ms | 552ms |

### Concurrent (8 klien × 5 query, throughput q/s, error)

| Target | kys | codegraph | graphify |
|--------|-----|-----------|----------|
| fixture | 4.25 q/s, 0 err | 2.98 q/s, 0 err | 3.59 q/s, 0 err |
| chalk | 4.19 q/s, 0 err | 2.94 q/s, 0 err | 3.28 q/s, 0 err |
| requests | 3.18 q/s, 0 err | 2.91 q/s, 0 err | 2.51 q/s, 0 err |
| tree-sitter-python | 5.04 q/s, 0 err | 2.15 q/s, 0 err | 2.87 q/s, 0 err |

Catatan kejujuran: ketiganya CLI-per-query — angka ini terikat biaya spawn
proses, diukur identik untuk ketiga sistem. graphify tak punya daemon/server;
codegraph punya daemon tapi MCP stdio (single-client per server) — interface
paralel-CLI adalah permukaan konkuren yang keduanya dukung, dicatat di field
`interface` tiap entry.

### Crash recovery (SIGKILL tengah ingest requests)

| Sistem | Kill | Post-kill | Recovery | Data loss |
|--------|------|-----------|----------|-----------|
| kys | -9 | `PRAGMA integrity_check`=ok, 1804 symbols ter-commit transaksional, edges parsial 1804/2832 | re-ingest exit 0 (incremental 1.7s) | **none** |
| codegraph | -9 | `status` MENDETEKSI: "last index run never finished… index is truncated", counts 0/0/0 | `init` menolak ("Already initialized… use index", exit 0), `index` exit 0 | **none** |
| graphify | -9 | tak ada graph.json parsial (atomik); tersisa `.rebuild.lock` + `cache/` | re-`update` exit 0, nodes/links identik run bersih | **none** |

### Determinisme (2 ingest fresh)

| Sistem | Artifact | Byte-identical |
|--------|----------|----------------|
| kys | canonical_ingestion_dump | **YES** (4/4 target) |
| codegraph | .codegraph/codegraph.db | **NO** (4/4; counts stabil — byte menyimpan `updatedAt` wall-clock per node) |
| graphify | graphify-out/graph.json | **YES** (4/4 target) |

### Token per jawaban (estimator beku, total per target)

| Target | kys | codegraph | graphify |
|--------|-----|-----------|----------|
| fixture | 124 | 2126 | 2253 |
| chalk | 53 | 582 | 4863 |
| requests | 749 | 9098 | 6719 |
| tree-sitter-python | 60 | 290 | 2437 |

Perbedaan bentuk jawaban dicatat apa adanya: kys mengembalikan hit simbol
terskeletonisasi (name/kind/path/line/source), codegraph JSON node penuh
(docstring/signature/updatedAt), graphify dump traversal BFS bertoken-budget.

## Temuan jujur

1. **codegraph tidak byte-deterministik** pada artifact index-nya (4/4 target;
   counts stabil, sha berbeda — `updatedAt` epoch-ms per node di dalam db).
   Diagnostik crash-nya justru paling baik (mendeteksi run terinterupsi).
2. **graphify menulis graph.json secara atomik** — kill tengah update tak
   meninggalkan graph parsial untuk dibaca; determinisme byte penuh.
3. **kys unggul di cold-start, throughput konkuren, dan keringkasan jawaban**;
   paling lambat di wall ingest (harga resolusi dua-fase + refs; 1804 simbol
   vs 1169 codegraph pada requests — ekstraksi lebih dalam).
4. Exit 4 graphify pada no-match adalah kontrak terdokumentasi, bukan error —
   tanpa kebijakan exit per-sistem, harness salah-menuduh query negatif.

## Reproduksi

```bash
npm install -g @colbymchenry/codegraph   # atau CODEGRAPH_BIN=<path>
uv run python bench/competitive/run.py check
uv run python bench/competitive/run.py run        # ledger + report di results/
uv run pytest tests/test_competitive_bench.py -q  # 20 test, skip jujur bila CLI absen
```

Setiap angka di ledger membawa `entries[].command` (argv persis) dan
`entries[].exit_code`. Tanpa jaringan: dataset vendored, kedua CLI kompetitor
berjalan offline (`CODEGRAPH_TELEMETRY=0`).

## Gates

- run nyata tiga sistem: exit 0, 63/63 entry OK (0 NOT_SUPPORTED — ketiga CLI
  terinstal dan mendukung keenam tugas yang diukur);
- `pytest tests/test_competitive_bench.py`: 20 passed exit 0 (skip jujur
  bila dataset/CLI absen — terverifikasi via marker `skipif`);
- counts kys cocok persis baseline ter-pin bench/codebase.

TIDAK commit (aturan). Rename sibling `know_your_soul`→`know_your_context`
sedang in-flight di worktree: harness me-resolve nama modul produk secara
dinamis (`product_module()`), driver diparameterkan — keduanya didukung.
