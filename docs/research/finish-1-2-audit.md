# Finish #1–#2 — Planning dan Audit Matrix

**Status:** audit dokumenter, tanpa perubahan `src/`. Angka di bawah hanya angka yang sudah ada di ledger/dokumen sumber; sel tanpa bukti diberi **BELUM TERUKUR**. Dokumen ini memisahkan hasil yang terbukti dari target planning.

**Ruang lingkup:**
- **Finish #1:** push-200 per dimensi (algoritma, performa, profesionalisme), perbaikan gap yang dapat dibuktikan, dan verdict `bench/verdict`.
- **Finish #2:** audit independen klaim wave, stress head-to-head 3 produk pada L1–L4, serta penutupan gap yang tersisa.

## 1. Peta seluruh repo referensi

| Repo/produk | Identitas dan lokasi bukti | Peran dalam Finish #1–#2 | Status bukti |
|---|---|---|---|
| `Sigit-AP/know-your-context` | repo kerja `/root/know-your-context`; versi produk yang dipakai stress: 0.3.0 | Sistem yang diaudit; baseline, patch, kontrak, dan harness | Terukur dan dapat direproduksi dari repo + artefak scratch |
| Graphify | release PyPI `graphify 0.9.111`; instalasi `/root/.hermes/cache/scratch/install/graphify`; sumber analisis `/root/src/graphify` | Pembanding knowledge/graph graph; ingest, query, MCP, dan failure-mode comparison | Terpasang dan dijalankan nyata; bukti instalasi di `docs/research/install-3-products.md` |
| CodeGraph | release bundle `codegraph 1.6.2`; instalasi `/root/.hermes/cache/scratch/install/codegraph`; sumber `/root/src/codegraph` | Pembanding code intelligence; indeks SQLite, query, traversal, dan pola batching/indexing | Terpasang dan dijalankan nyata; bukti instalasi di `docs/research/install-3-products.md` |
| `phuryn/pm-skills` | skill lokal `/root/.hermes/skills/productivity/pm/` | Kerangka planning/ICE dan acceptance criteria Finish #1 | Dibaca; 69 skill/9 kategori dicatat di `skill-repos-study.md` |
| `obra/superpowers` | `/root/.hermes/skills/superpowers/` | TDD, verification-before-completion, parallel work, dan review discipline | Dibaca; 15 skill/78 file dicatat di `skill-repos-study.md` |
| `mattpocock/skills` | `/root/.hermes/skills/mattpocock-engineering/` dan `mattpocock-in-progress/` | Alur grill → spec → tickets → implement → review/retro | Dibaca; 20 + 7 skill dicatat di `skill-repos-study.md` |
| `multica-ai/andrej-karpathy-skills` | clone `/root/.hermes/cache/scratch/learn-repos/karpathy-skills/` | Prinsip problem-first dan contoh skill; bukan benchmark produk | Dibaca; nama repo dikoreksi dari brief awal di `skill-repos-study.md` |
| `microsoft/TokenEconomizer` | clone `/root/.hermes/cache/scratch/learn-repos/TokenEconomizer/` | Pola lifecycle fakta, cost ledger, fail-open hook, dan hard backstop sebagai referensi desain | Dibaca; mekanisme plugin tidak diklaim terpasang di produk |

**Batas penting:** repo referensi adalah input pola dan pembanding, bukan sumber angka yang boleh dicampur. Angka kompetitor hanya sah bila berasal dari satu run/harness yang sama; angka dari analisis source dipakai untuk capability mapping, bukan sebagai hasil stress.

## 2. Planning dan acceptance criteria

| Finish | Deliverable wajib | Acceptance criteria (harus terbukti) | Status pada audit ini |
|---|---|---|---|
| #1 | Matriks sub-skor dan target per dimensi | Semua sub-skor <90% diinventarisasi; setiap intervensi memiliki before/after atau proof test; target 200% tidak dicapai lewat inflasi volume; metrik tanpa bukti ditandai | **TERBUKTI sebagian:** plan/verdict mencatat I1–I8, P1–P4; Finish #1 tercapai untuk algoritma/performa dan komposit terhadap codegraph/graphify, tetapi profesionalisme vs codegraph tidak |
| #1 | Perbaikan mekanisme | P1 adaptive batch, P2 incremental verify, P3 watchtower parallel, P4 changelog memiliki test/gate dan angka reproduksi | **TERBUKTI** di `task1-push200-verdict.md`; angka mikro WS-C tertentu tetap tidak diverifikasi |
| #1 | Verdict instrumen | `bench/verdict/run.py` menghasilkan ledger dengan command, raw value, dan determinisme; l1/l5 vs codegraph dilaporkan tanpa inflasi | **TERBUKTI pada ledger yang dirujuk**; re-run terbaru tidak dilakukan dalam audit dokumenter ini |
| #2 | Audit klaim wave | Klaim SUB-159–162 diuji ulang; status TERBUKTI/PARSIAL/TIDAK DIVERIFIKASI dicatat dengan bukti | **TERBUKTI** di `task2-audit-wave1.md`: 31 klaim diperiksa, 27 terbukti, 2 parsial, 2 minor tidak diverifikasi |
| #2 | Head-to-head L1–L4 | Ketiga produk dijalankan dengan korpus, workload, timeout, dan harness identik; tiap level memiliki ingest/query/error/hit/RSS | **TERUKUR**, tetapi sinkronisasi L1 belum selesai dan L4 storm RSS tetap KALAH terhadap Graphify pada band stabil |
| #2 | Closure gap | Setiap KALAH punya akar masalah dan rencana; tidak ada KALAH yang diubah menjadi MENANG karena sampel tunggal atau angka lintas-ledger | **BELUM SELESAI:** L4 `storm_rss_mb` masih KALAH; L1 perlu regen; beberapa gap khusus belum punya stress pasca-fix |

## 3. Coverage matrix L1–L4: metrik per produk

Sumber utama: `docs/research/final-head-to-head.md` dan ledger `/root/.hermes/cache/scratch/stress3/results_final.jsonl`. **L1 ditandai provisional** karena dokumen sumber sendiri menyatakan tabelnya belum disinkron ke `tail -1`; L3/L4 memakai angka kanonik yang ditulis di dokumen sumber. `—` berarti tidak ada angka yang dapat dipakai dari sumber yang diaudit, bukan nol.

### 3.1 Metrik ingest

| Level | Korpus/beban | Graphify | CodeGraph | KYC | Status |
|---|---|---:|---:|---:|---|
| L1 | 100 file, 1 agent | 7.23 s / 169.9 MB / 2.0 MB artefak | 4.11 s / 312.8 MB / 2.7 MB | 2.61 s / 75.4 MB / 2.1 MB | Terukur; baris L1 provisional |
| L2 | 1,000 file, 10 agent | 112.35 s / 358.0 MB / 20.2 MB | 16.69 s / 350.5 MB / 25.4 MB | 15.72 s / 148.5 MB / 18.8 MB | Terukur pada dokumen final |
| L3 | 5,000 file, 50 agent | 3235.7 s / 451 MB / 107.1 MB | 191.3 s / 553 MB / 118.7 MB | 84.64 s / 285.6 MB / 95.9 MB | Terukur; run kanonik |
| L4 | 5,137 file / 2.12M LOC, 50 agent | 1729.7 s / 860 MB / 380.5 MB | 1480.6 s / 1645 MB / 450.6 MB | 305.235 s / 721.4 MB / 312.6 MB | Terukur; run kanonik |

Format sel: `ingest_wall_s / ingest_rss_mb / artifact_mb`.

### 3.2 Metrik query/storm

| Level | Produk | q_p50_ms | q_wall_s | W1 | W2 | error | sigkill | storm RSS | verdict sumber |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| L1 | Graphify / CodeGraph / KYC | 630 / 949 / 514–613 | 7.0 / 8.6 / 6.4 | 6/6 / 6/6 / 6/6 | 4/4 / 4/4 / 4/4 | 0% / 0% / 0% | 0 / 0 / 0 | 72.4 / 175.5 / 80.4 MB | Provisional; dokumen sumber memiliki inkonsistensi skor L1 |
| L2 | Graphify / CodeGraph / KYC | 11733 / 9503 / 5578 | 106.2 / 66.9 / 45.9 | 40/40 / 40/40 / 40/40 | 20/20 / 20/20 / 20/20 | 0% / 0% / 0% | 0 / 0 / 0 | 1021.9 / 1838.5 / 477.3 MB | Terukur; KYC 7 menang, 4 setara |
| L3 | Graphify / CodeGraph / KYC | 124062 / 127640 / 35793.5 | 251.3 / 257.3 / 72.25 | 0/50 / 0/50 / 50/50 | 0/50 / 0/50 / 50/50 | 100% / 100% / 0% | 100 / 100 / 0 | 2436 / 3962 / 2490.6 MB | Terukur; KYC 10 menang, 1 setara |
| L4 | Graphify / CodeGraph / KYC | 127720 / 119536 / 29954.0 | 257.3 / 242.4 / 62.21 | 0/50 / 33/50 / 50/50 | 0/50 / 32/50 / 50/50 | 100% / 35% / 0% | 100 / 34 / 0 | 2413 / 5811 / 2583.6 MB | Terukur; KYC 10 menang, 0 setara, 1 kalah |

Format sel tiga produk mengikuti urutan Graphify / CodeGraph / KYC. Untuk hit-rate, angka adalah hit/query, bukan relevance ground truth.

## 4. Matriks coverage capability dan bukti

| Area | KYC | Graphify | CodeGraph | Coverage L1–L4 | Acceptance evidence |
|---|---|---|---|---|---|
| Ingest/index | AST/code index + knowledge write path | graph extraction + graph JSON | SQLite code index | Terukur semua level | exit 0, wall, RSS, nodes/edges, artefact |
| Exact callers/traversal (W1) | `codeintel callers-of` | `affected --relation calls` | `callers` | Terukur semua level | W1 hit, latency, error, timeout |
| Text/knowledge query (W2) | `query` | `query` | `query` | Terukur semua level; quality tidak | hit/no-hit saja; tidak ada relevance ground truth |
| Persistence/audit | hash chain, journal, contracts | graph artefact | SQLite/WAL | Capability source-audited; bukan semua stress metric | proof tests + read-back; durability stress khusus BELUM TERUKUR |
| MCP/server mode | KYC contracts/serve; Graphify MCP handshake; CodeGraph CLI | Graphify MCP terverifikasi | CodeGraph daemon/server mode tidak diukur | L1–L4 stress hanya CLI baru | server-mode latency/storm = BELUM TERUKUR |
| Ambiguous symbol | union traversal dan `ambiguous` payload di fix wave | output semantics teramati | all-definitions behavior teramati | L4 W1 50/50 terbukti pasca-fix | dedicated ambiguous post-fix stress = BELUM TERUKUR |
| Non-ASCII SQL | fallback ASCII-CI + Python CI | tidak dibandingkan | tidak dibandingkan | unit/area proof, bukan L1–L4 | dedicated corpus stress = BELUM TERUKUR |
| Overload/liveness | liveness-based projection wait; RSS cap | timeout/reload behavior | timeout/traversal behavior | L3/L4 stress terukur | error=0/sigkill=0 pada KYC final; RSS band wajib multi-run |
| Quality/relevance | — | — | — | Tidak tercakup | ground-truth relevance benchmark = BELUM TERUKUR |

## 5. Gap yang belum terbukti

1. **L1 canonical re-verdict belum selesai.** `final-head-to-head.md` menyatakan tabel L1 bukan `tail -1` ledger terbaru; acceptance: regenerate from current ledger and reconcile score/table.
2. **L4 `storm_rss_mb` belum menang stabil.** Tiga run stabilitas KYC tercatat 2556.4 / 2583.6 / 2630.3 MB, sementara Graphify 2413 MB; sampel 2268.4 MB tidak boleh menjadi headline tunggal. Gap: root cause dan pengukuran after-fix belum tertutup.
3. **Stress khusus non-ASCII pasca-fix belum ada.** SQL fallback punya proof test, tetapi tidak ada angka L1–L4 yang mengisolasi corpus non-ASCII.
4. **Server/daemon mode belum dibandingkan apple-to-apple.** Stress memakai CLI process-per-query; latency dan RSS daemon Graphify MCP, CodeGraph daemon, dan KYC serve masih BELUM TERUKUR.
5. **Retrieval quality belum terbukti.** Hit-rate hanya menunjukkan jawaban ditemukan/tidak; tidak ada relevance labels atau ground truth. Jangan menyebutnya accuracy.
6. **Mikro-benchmark kernel WS-C belum diverifikasi ulang.** Mekanisme parity 36/36 dan routing proof test terbukti, tetapi angka pajak/latency spesifik tidak.
7. **Before-run name-match 199.94 s tidak dapat direproduksi** karena snapshot sudah v6; after 2.689 s terbukti, baseline magnitudo tetap tidak terbukti.
8. **Finish #1 official ledger perlu rerun bila angka dijadikan current headline.** Verdict task1 merujuk ledger `20261109T192827Z`; audit ini tidak mengarang pengganti.
9. **Klaim lintas-host/lintas-ledger tidak sah sebagai before/after.** Semua delta tersebut harus dilabeli indikasi; acceptance membutuhkan satu ledger/run yang sama atau before/after host yang dikontrol.
10. **Coverage capability tidak sama dengan proof capability.** Fitur yang hanya tercantum di plan/source mapping tidak boleh diberi skor sampai proof test atau run nyata lulus.

## 6. Urutan kerja penutupan

1. Regenerate L1 dari `results_final.jsonl`; simpan command, raw row, dan reconciliation terhadap tabel.
2. Jalankan tiga-run L4 `storm_rss_mb` serial pada host yang sama; simpan band, process breakdown, dan error/hit parity.
3. Jika L4 masih KALAH, buat root-cause plan terpisah; jangan mengubah verdict dengan threshold baru.
4. Jalankan corpus non-ASCII terisolasi dan server-mode matrix hanya bila harness identik dapat dipenuhi; jika tidak, tetap BELUM TERUKUR.
5. Rerun `bench/verdict/run.py run` untuk Finish #1 dan pin ledger baru; bandingkan per opponent, bukan gabungan.
6. Re-run gates sesuai repo playbook setelah perubahan docs/bench: `pytest`, `ruff`, `pyright`, `uv lock --check`, license guard, benchmark check, dan bandit.

## 7. Sumber bukti

- `docs/research/task1-push200-plan.md`
- `docs/research/task1-push200-verdict.md`
- `docs/research/task2-audit-wave1.md`
- `docs/research/stress-3-products.md`
- `docs/research/final-head-to-head.md`
- `docs/research/install-3-products.md`
- `docs/research/skill-repos-study.md`
- Ledger: `/root/.hermes/cache/scratch/stress3/results_final.jsonl`
- Artefak benchmark Finish #1: `/root/.hermes/cache/scratch/` paths referenced by task1 verdict
