# Final Head-to-Head — pasca-fix (SUB-178)

Status: **IN PROGRESS** — bagian hasil diisi setelah pengukuran ulang selesai.

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

_(diisi setelah pengukuran)_
