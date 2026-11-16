# Install 3 Produk — Metode, Alasan, Bukti Jalan

SUB-169, 2026-11-16 (UTC). Host: 2 vCPU, 3.8 GB RAM, Python 3.14.9, uv 0.13.5, Node v26.12.1, npm 12.0.2.
Semua instalasi kompetitor di `/root/.hermes/cache/scratch/install/` — repo produk `/root/know-your-context` tidak diubah.

## Ringkasan

| Produk | Metode | Versi | Status |
|---|---|---|---|
| Graphify | `uv tool install 'graphifyy[mcp]'` (PyPI, resmi README) | 0.9.111 | ✅ CLI + graph build nyata + MCP server handshake |
| CodeGraph | `install.sh` resmi (self-contained bundle, resmi README) | 1.6.2 | ✅ CLI + init/query nyata |
| know-your-context | `uv sync --frozen` (venv repo, sudah ada) | 0.3.0 | ✅ CLI + write/read/health nyata |

## 1. Graphify

**Metode terpilih:** `uv tool install 'graphifyy[mcp]'` dengan `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` diarahkan ke scratch.

**Kenapa terbaik:** README resmi menyatakan "Needs Python 3.10+ and uv (or pipx): `uv tool install graphifyy`". `uv tool install` adalah metode resmi #1 (pipx = alternatif), terisolasi dari venv lain, dan bisa dipindah lokasi via env var standar uv. Extra `[mcp]` wajib — tanpa itu `graphify-mcp` gagal import `mcp.server.stdio` (ditemukan saat verifikasi, diperbaiki dengan reinstall `--force 'graphifyy[mcp]'`). Instalasi dari PyPI (bukan checkout lokal `/root/src/graphify` v0.9.91) supaya yang di-stress-test adalah release resmi terbaru (0.9.111).

**Lokasi:** `/root/.hermes/cache/scratch/install/graphify/{tools,bin}` → executables `graphify`, `graphify-mcp`.

**Bukti jalan (perintah nyata):**

```
$ graphify --version
graphify 0.9.111                              # exit=0

$ graphify extract pkg --code-only --out ./out     # pkg = graphify source, 97 file .py
[graphify extract] AST extraction on 97 code files...
[graphify extract] wrote .../graphify-out/graph.json: 3728 nodes, 8470 edges, 178 communities
real 0m30.945s                              # exit=0

$ graphify god-nodes --graph .../graph.json --top 3
  1. _read_text() - 214 edges
  2. _make_id() - 146 edges
  3. walk() - 79 edges                      # exit=0

$ graphify query "what handles extraction" --graph .../graph.json --budget 400
Graph: .../graph.json (3728 nodes) | Traversal: BFS depth=2 | ... | 63 nodes found
NODE Return the interpreter name from a shebang line...   # exit=0

# MCP server — JSON-RPC initialize + tools/list via stdio:
$ printf '{"jsonrpc":"2.0","id":1,"method":"initialize",...}\n{"jsonrpc":"2.0","id":2,"method":"tools/list",...}\n' | graphify-mcp
{"jsonrpc":"2.0","id":1,"result":{...,"serverInfo":{"name":"graphify","version":"0.9.111"}}}
{"jsonrpc":"2.0","id":2,"result":{"tools":[{"description":"Search the knowledge graph using BFS or DFS...
```

## 2. CodeGraph

**Metode terpilih:** installer resmi `install.sh` dari repo (`sh /root/src/codegraph/install.sh`) dengan `CODEGRAPH_INSTALL_DIR` + `CODEGRAPH_BIN_DIR` ke scratch.

**Kenapa terbaik:** README resmi menempatkan install.sh sebagai metode utama ("**No Node.js required** — one command grabs the right build for your OS"); npm (`npm i -g @colbymchenry/codegraph`) hanya alternatif "Already have Node?". Bundle self-contained = tidak ada build step (tsc + vite UI build di host 2vCPU/3GB berisiko OOM) dan byte-identik dengan release resmi v1.6.2. Installer natively mendukung custom dir via env var — tidak perlu patch apapun.

**Lokasi:** `/root/.hermes/cache/scratch/install/codegraph/{bundle/versions/v1.6.2,bin/codegraph}`.

**Bukti jalan:**

```
$ codegraph --version
1.6.2                                       # exit=0
$ codegraph --help                          # exit=0 (Usage + commands: init/index/sync/status/query/explore/context/node/files...)

$ codegraph init .          # proj = 55 file .py dari graphify source
◆  Indexed 55 files
●  2,321 nodes, 6,156 edges in 2.3s         # exit=0, real 0m14.562s

$ codegraph status .
  Files: 55   Nodes: 2,321   Edges: 6,156   DB Size: 9.92 MB
  Backend: node:sqlite — built-in (full WAL)  # exit=0

$ codegraph query "resolve import" -l 5
function    resolve_python_import_guided_calls   symbol_resolution.py:239
function    _resolve_kotlin_import_targets       extract.py:6394   # exit=0
```

Catatan: `codegraph query <search> <path>` ditolak ("too many arguments") — path lewat `-p/--path`, sesuai `query --help`.

## 3. know-your-context (produk kita)

**Metode:** instalasi uv yang sudah ada diverifikasi, bukan diinstall ulang — `uv sync --frozen` di `/root/know-your-context`.

**Bukti sehat:**

```
$ uv sync --frozen
Checked 101 packages in 26ms                # exit=0 — lockfile konsisten, 0 reinstall

$ .venv/bin/know-your-context --help        # exit=0 — 40+ subcommand tampil
$ .venv/bin/python -c "import know_your_context, graphify"   # OK (graphifyy[mcp]==0.9.95 pinned dep)

# Write nyata (store baru di scratch, repo tidak disentuh):
$ know-your-context --store $S log-event '{"node_id":"n1","kind":"root","domain":"P1","summary":"SUB-169 verify","source_version_id":"v1"}'
{"audit_event_id":"6c54d877...","event_seq":5,"knowledge_seq":1,"node_version":1,...}

# Read-back nyata:
$ know-your-context --store $S get-node '{"node_id":"n1"}'
{"node":{"domain":"P1","kind":"root","node_id":"n1","summary":"SUB-169 verify","version":1,...},"stale":false}

$ know-your-context --store $S health
health ok: True  watermark: 1  chain events: 7

$ echo '{"purpose":"verify install","token_budget":500}' | know-your-context --store $S query -
{"routing":"no_match","results":[],...,"stale":false}   # exit=0 (store baru → no_match, jujur)
```

Schema payload strict (pydantic `extra="forbid"`): `log-event` wajib `node_id/kind/domain(P1-P7)/summary/source_version_id`; `query` wajib `purpose/token_budget` (tanpa field teks — routing lewat index). Error validation mengembalikan typed `VALIDATION_ERROR` dengan audit_event_id — perilaku kontrak yang benar, bukan crash.

## Verdict

Ketiga produk **siap stress test**: ketiga CLI exit 0, dan ketiganya dibuktikan dengan kerja nyata (bukan `--help` saja): graphify membangun graph 3728 node + query + MCP handshake; codegraph mengindex 55 file → 2321 node + query; kyc menulis event → read-back → health ok. Nol kegagalan install; satu penyesuaian terdokumentasi (extra `[mcp]` untuk server graphify).
