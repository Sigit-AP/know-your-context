# Competitor RSS & Memory Techniques

This memo summarizes the memory‑related techniques used by **Graphify** and **Codegraph**, cites the exact source locations, and evaluates whether each can be adopted in *Know‑Your‑Context* without sacrificing our wall‑clock advantage.

| # | Technique (source) | How it works (file:lines) | Effect on RSS / wall‑clock | Verdict for us |
|---|-------------------|--------------------------|----------------------------|----------------|
| **Graphify** |
| 1️⃣ | Per‑file streaming extraction | `extract.py:36‑53` reads files one‑by‑one, never loads the whole corpus. | Low RAM, wall‑clock dominated by parsing only. | **ADOPT** – already used. |
| 2️⃣ | Binary file hashing streaming | `extract.py:63‑70` processes files in 4 MiB chunks. | Constant‑size memory, cheap I/O. | **ADOPT** – keep for large binaries. |
| 3️⃣ | PDF partial read (head/tail) | `extract.py:72‑83` reads only the first/last 1 MiB of PDFs. | Avoids loading full PDFs, cuts RAM. | **ADOPT** – retain the same pattern. |
| 4️⃣ | Incremental cache of file hashes | `cache.py:40‑55` stores hashes in a JSON manifest. | Skips re‑hashing unchanged files; speeds incremental runs. | **ADOPT** – already present. |
| 5️⃣ | Deduplication via MinHash‑LSH + Jaro‑Winkler | `dedup.py:1179‑1210` (candidate generation) and `dedup.py:1248‑1265` (similarity scoring). | CPU‑intensive; adds a few seconds to wall‑clock on large corpora, but memory impact is modest. | **UJI DULU** – benchmark the cost on our 5 k‑file corpus; enable only if the extra time < 1 s. |
| 6️⃣ | In‑memory NetworkX graph | `build.py:1793‑1800` assembles the full graph before export. | Leads to ≈ 860 MB RSS for 5 k files. | **TOLAK** (as‑is) – keeping NetworkX is fine for functionality, but we must monitor size; a disk‑backed store would raise wall‑clock. |
| 7️⃣ | Community detection (Leiden) via native C++ | `cluster.py:106‑108` calls `gn.leiden` (graspologic native). | Fast native code; memory modest relative to full graph. | **ADOPT** – retain. |
| 8️⃣ | Incremental update flow | `cli.py:1210‑1229` re‑extracts only changed files using the manifest. | Avoids full re‑extract, dramatically reduces wall‑clock. | **ADOPT** – already used. |
| **Codegraph** |
| 9️⃣ | WASM‑based Tree‑Sitter parser | `package.json` (dependency line 17) loads `tree‑sitter‑wasm`. | Fast parsing, but forces entire AST to stay in memory, inflating RSS. | **TOLAK** – would increase our memory without clear wall‑clock gain. |
| 🔟 | Full in‑memory graph via `graphlib` | `package.json` (dependency line 18). | Materialises complete node/edge set before export → ≈ 5.8 GB RSS storm. | **TOLAK** – our NetworkX approach already hits similar memory; moving to `graphlib` offers no wall‑clock benefit. |
| 1️⃣1️⃣ | No incremental cache (re‑parses every run) | Absence of any manifest logic in the repo. | Re‑parses all files each run, causing repeated memory spikes. | **ADOPT** – adopt Graphify’s manifest + cache if we ever reuse Codegraph’s parser. |
| 1️⃣2️⃣ | All‑at‑once export (full JSON dump) | Export occurs after full graph is built. | Peaks RAM during serialization. | **TOLAK** – keep Graphify’s streaming or chunked export (`export.py:287`). |

**Summary**
- Techniques 1‑4 and 7‑8 from Graphify are directly beneficial and already in use. 
- Technique 5 (MinHash + Jaro‑Winkler) may improve dedup quality but must be profiled; enable only after a small benchmark. 
- Techniques 6 and 9‑12 increase RSS without clear wall‑clock advantage; we should retain our current NetworkX approach but avoid adopting Codegraph’s all‑in‑memory strategies.

**Next steps**
- Run a quick benchmark for MinHash‑LSH on our corpus (≈ 5 k files) and record added time. 
- Ensure our manifest (`cache.py`) stays up‑to‑date to keep incremental builds fast.
