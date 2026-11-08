# Cluster A Verdict — store + superpower server + serve flow + audit/journal

**Peneliti:** SUB-155 (deep researcher, cluster A) · **Tanggal:** 2026-11-08 · **HEAD:** `5952097`
**Scope (READ-ONLY, tanpa mengubah kode):** `store.py` (1917 LOC), `serve/superpower.py` (2848), `serve/{rpc,daemon_config,budgets,watchdog,proxy,flow_retry,pipeline_stages,middleware,simulation}.py`, `audit.py` (247), `events.py` (157), `leases.py` (386), plus titik sentuh `service.py`/`projection.py`.

## 0. EXPERT-THINKING (7 pertanyaan wajib)

1. **Masalah:** apakah 4 bagian cluster A berdiri di level profesional — diukur, bukan diklaim — pada 3 dimensi: algoritma, performa (termasuk overload + store multi-GB), level profesional.
2. **Kendala:** host riset 2 vCPU / 3 GiB RAM / SSD cloud (bukan mesin target besar); angka absolut = angka host ini, tren skala = yang dibuktikan. Knowledge path punya cap desain 64 KiB (F-3) — bukan bug.
3. **Tujuan terukur:** setiap klaim punya angka + perintah reproduksi (lampiran A).
4. **Manfaat:** verdict per bagian dengan % yang dapat diaudit + ledger bug nyata berperingkat.
5. **Pelajaran (dari pengukuran):** dua perilaku superlinear nyata ditemukan (COUNT(*) per insert; verify_chain O(N) memori); publish projection = replay penuh per batch tulis (O(K)); read ceiling daemon dibatasi audit lane (2 txn tulis per read).
6. **Prinsip yang dipegang:** fail-closed untuk data/security; fail-open untuk hal non-kritis; angka terukur > adjective; rasio terhadap nol = null (tidak dipakai di sini).
7. **Kill criteria:** riset berhenti saat setiap butir punya bukti angka; butir tanpa bukti ditandai eksplisit (tidak ada yang tersisa tanpa angka kecuali yang diberi label *ekstrapolasi*).

**Lingkungan pengukuran:** `nproc=2`, RAM 3 GiB, disk SSD (`/dev/sda2`), Linux 5.15, CPython 3.14.9, SQLite 3.53.1, WAL + `synchronous=FULL` + `mmap_size=1 GiB` + `cache≈320–380 MiB` (hardware plan probe, terverifikasi live). Config daemon efektif di host ini: `read_pool=8, write_q=256, read_q=512, heavy_q=16, audit_q=2048, max_agents=50, group_commit_batch=32 (8→32 oleh hardware plan), window 2 ms, agent_rate=100/s burst 200`.

---

## 1. STORE (`store.py`, schema v1→v5)

### 1.1 Algoritma (identifikasi: sendiri / berpasangan / dengan bagian lain)

| Algoritma | Jenis | Bukti |
|---|---|---|
| Hash chain append-only (sha256 over canonical JSON, `prev_event_hash`) + anchor `chain_head_hash`/`chain_event_count` di `system_state` (transaksi sama) | sendiri (pola rantai hash umum, implementasi + anchor sendiri) | `store.py:818-888, 912-959`; trigger `event_log_no_update/no_delete` (migrations/0001) |
| Versi node + OCC (`expected_version`), materialisasi `node_revision` per event | sendiri | `store.py:963-1032` |
| Idempotency: kunci unik parsial + **request fingerprint** (strip drift fields) + pembuktian retry via journal (`≥2 invocation, 1 request_hash`) | sendiri (tidak ada di codegraph/graphify) | `store.py:455-472, 1034-1062` |
| Skema graph v3: edge identity `UNIQUE(source,target,kind,IFNULL(line,-1),IFNULL(col,-1))`, indeks komposit left-prefix, partial indexes (pending/failed), expression index `lower(name)` | diadopsi dari codegraph v4 + ditulis ulang | `store.py:291-318` + komentar sitasi |
| SQL-shape cache adjacency (≤64 bentuk, prepared-statement effect) | diadopsi (codegraph edgeKindStmt) | `store.py:342-346, 1707-1734` |
| BFS subtree dengan visited-set, depth dihitung (tidak disimpan) | sendiri | `store.py:1751-1781` |
| Migrasi berversi fail-closed (ledger↔objek cross-check, rollback per versi) | sendiri | `store.py:655-781` |
| Storage tuning dari satu HardwarePlan (mmap/cache/page/synchronous), fail-open ke resep legacy | dengan `hardware/` | `store.py:148-202` |
| Group-commit seam: transaksi reentrant (SAVEPOINT) | berpasangan dengan `_WriteArbiter` | `store.py:571-603` |
| Estimator kapasitas publish (konservatif, terkalibrasi ~1.6×) | sendiri | `store.py:1204-1246` |

**Efek positif (level ahli, terbukti):** integritas tamper-evident terverifikasi penuh (verify_chain hijau di semua run termasuk kill-9); idempotency conflict ditolak typed, bukan silent-drop; indeks adjacency terbukti seek (bukan scan) pada 5.2 GB (§1.3).

### 1.2 Efek negatif / bug nyata (terukur)

| # | Temuan | Bukti terukur | Severity |
|---|---|---|---|
| S1 | **`insert_event` menjalankan `SELECT COUNT(*) FROM event_log` pada SETIAP insert** (untuk anchor count). O(N) per tulis → O(N²) seumur store. | `store.py:874`. EXPLAIN: `SCAN event_log USING COVERING INDEX event_log_knowledge_seq`. Terukur: 2.2 ms warm / 73.4 ms cold @160k event; lane A throughput 645→290 ops/s (N=5k→160k). Fix jelas: baca anchor lama +1. | **Sedang** (benar secara fungsi; degradasi linier permanen) |
| S2 | **`verify_chain` memuat SELURUH event_log ke memori** (`fetchall()` + re-hash per row). | `store.py:921`. Terukur @160k event: **12.71 s, peak RSS 951 MiB** (delta ~920 MiB). Ekstrapolasi linier: ~1M event → ~6 GiB → OOM di host 3 GiB. | **Sedang-tinggi** (operasi audit besar tak bisa jalan di host kecil; perlu streaming/iterator) |
| S3 | `invocation_journal.effective_idempotency_key` **tanpa indeks** — dipakai di `_conflicting_reuse`. | `store.py:1057-1062`; EXPLAIN: `SCAN invocation_journal`. Hanya di jalur konflik (jarang). | Rendah |
| S4 | Head lookup `ORDER BY event_seq DESC LIMIT 1` tampil `SCAN event_log` di EXPLAIN (rowid traversal, lazy — terukur tidak degradasi). | EXPLAIN; lane A/B per-op tidak melonjak karena ini. | Bukan bug (diverifikasi) |

**Kompleksitas %:** 88 fungsi, rata-rata CC 3.39, maks 24 (`_apply_versioned_migrations`), 5 fungsi >10 → **94.3% fungsi CC≤10**. (Alat: AST walker CC, `cc_scan.py`, lampiran A.)

**Optimalitas saat overload %:** throughput tulis event-path pada 160k event = **45% dari throughput pada 5k** (290 vs 645 ops/s) — degradasi murni S1 + tekanan cache; bentuk batch (group commit) mempertahankan **~100%** (per-op 1.07 ms @160k vs 0.94 ms @1k — datar). Nilai overload store (batch path, yang dipakai daemon): **~95%** (datar + fsync FULL tiap batch; headroom ada di S1).

### 1.3 Performa terukur (host ini, FULL sync)

| Metrik | Angka | Metode |
|---|---|---|
| append_domain serial (1 txn/op), rata-rata 0→160k event | **303 ops/s** (1.55→3.45 ms/op) | B1 lane A |
| append_domain batched (100/txn, = group commit) | **872 ops/s** rata-rata; per-op datar ~1 ms | B1 lane B |
| `store.commit` penuh (journal+2×heartbeat+append+audit; 2 event + 4 txn per op) | **171 ops/s**; 251→152 ops/s (5k→20k) | B1 lane C |
| `current_node` @160k event | p50 **0.157 ms**, p95 0.384 ms | B1 |
| `verify_chain` @160k event | 12.71 s, RSS puncak 951 MiB | B1 |

**Tahan 5–10 GB?** Store graph dibangun nyata sampai **5.17 GB konsolidasi** (6.70M edges, 1.38M symbols, 13.8k files; working set 6.3 GB selama build). Hasil baca/tulis di ukuran itu:

| Operasi @5.17 GB | p50 | p95 | max |
|---|---|---|---|
| `symbols_named` exact (300 sampel) | 0.245 ms | 0.557 ms | 5.8 ms |
| `symbols_named` case-insensitive | 0.040 ms | 0.050 ms | 1.5 ms |
| `outgoing_edges` (500 sampel) | 1.477 ms | 4.654 ms | 21.6 ms |
| `incoming_edges` | 0.828 ms | 3.224 ms | 6.7 ms |
| `outgoing_edges(kinds=2)` | 0.677 ms | 1.855 ms | — |
| `list_files` (13.8k rows) | 143 ms (scan, by design) | — | — |
| `add_graph_edge` tunggal | 0.894 ms | 1.611 ms | — |
| `add_graph_edges` batch 10k | — | **2915 edges/s** | — |

Build menunjukkan degradasi waktu blok 10 s→85 s/blok (pertumbuhan indeks B-tree + page-cache pressure di 3 GiB) — wajar, tidak patologis. **Bisa di-push ke 10 GB?** Ya secara mekanis (indeks seek terbukti); batas praktis host ini = RAM 3 GiB (cache 380 MiB ≈ 7% db → cold reads naik). Yang perlu untuk >10 GB: streaming verify_chain (S2), penghapusan COUNT(*) (S1), dan host dengan RAM ≥ db×10% untuk menjaga p95 adjacency <5 ms. **Pengaruh hardware:** besar pada fsync (FULL sync per txn) dan page cache; tuning hardware-aware sudah aktif (mmap 1 GiB, cache adaptif, batch 32). Sudah optimal untuk kebijakan durability FULL; opsi `synchronous=NORMAL` ada di plan tapi host ini memilih FULL (SSD profile) — trade-off disengaja.

### 1.4 Level profesional — STORE

| Sumbu | % | Bukti (bukan klaim) |
|---|---|---|
| Flow engineering | 88 | Transaksi reentrant savepoint sebagai seam group commit; migrasi fail-closed berlapis (ledger↔objek); −6 untuk S1/S2 yang lolos ke produksi |
| Core algoritma | 90 | Rantai hash + anchor + trigger append-only; fingerprint idempotency dengan pembuktian retry via journal (tidak ada di referensi); −10 untuk COUNT(*) O(N) per insert |
| Arsitektur | 92 | Satu penulis, registry live-store lemah untuk koordinasi restore (F-1), tuning perangkat keras tunggal, schema versioning berlapis v1→v5 |
| Kreativitas | 85 | Fingerprint drift-stripping; estimator kapasitas terkalibrasi; heal "lying watermark" (F-2) |
| Attention to detail | 90 | Komentar sitasi pola (codegraph #1034), nosec beralasan, invariant assert, nosec/partial index disiplin; −10 untuk S2 (fetchall tanpa paginasi) |
| **Total store** | **88.6** | rata-rata 5 sumbu |

---

## 2. SUPERPOWER SERVER (`serve/superpower.py`, 2848 LOC)

### 2.1 Algoritma

| Algoritma | Jenis | Bukti |
|---|---|---|
| `_FairQueue`: lane FIFO per aktor + round-robin + skip-excluded saat batch-gather (anti spammer-phasing) + invariant accounting di-assert tiap mutasi | sendiri (varian WFQ yang disederhanakan + tambahan skip-excluded) | `superpower.py:502-639` |
| `_WriteArbiter`: single-writer, batch group-commit (cap 32 / window 2 ms), audit lane didrain DULU tiap loop, ack hanya pasca-COMMIT (durability), dedup in-flight `(verb, actor, idem-key)` | sendiri, berpasangan dengan `EventStore.transaction` | `superpower.py:792-1229` |
| `_TokenBucket` admission per aktor (rate+burst, envelope di-assert) | pola umum, implementasi + invariant sendiri | `superpower.py:685-713` |
| `_SessionToken` lease idleness yang mengecualikan in-flight (fix flake SUB-132) | sendiri | `superpower.py:716-761` |
| Supervisi berjenjang: LivenessWatchdog global (exit 70) + worker watchdog suspect→restart→dead berbujet | sendiri (kebijakan #850 diadopsi sebagai last-resort) | `superpower.py:398-444, 1683-1742` + `watchdog.py` |
| Read-only pool dengan koneksi `mode=ro` (read-only dipaksa mesin, bukan disiplin) + schema cross-check vs writer | sendiri | `superpower.py:338-392` |
| Freshness read: nudge writer + tunggu watermark + typed STALE_PROJECTION | sendiri | `superpower.py:1370-1395` |
| Spawn/stop: O_EXCL lockfile+nonce, double-fork, stale-lock recovery, drain graceful | sendiri | `superpower.py:1769-1795, 2555-2830` |
| Static budgets Tiger Style (satu tabel kebenaran → render docs dipin test) | sendiri | `budgets.py` |

### 2.2 Efek negatif / bug nyata

| # | Temuan | Bukti | Severity |
|---|---|---|---|
| D1 | **Writer thread (`_WriteArbiter._thread`) TIDAK terdaftar di worker watchdog** — hanya read×N, heavy, acceptor, reaper (4 `SupervisedThread`). Pengecualian tak terduga di `_run` (mis. assert watermark regresi pasca-restore) membunuh writer tanpa restart: tulisan menggantung di antrian selamanya, read tetap jalan → daemon "setengah hidup". | `superpower.py:1683-1742` (4 register, tak ada writer); `_run` tanpa try/except terluar (`:979-1033`) | **Sedang-tinggi** (celah liveness; butuh pengecualian tak terduga untuk memicu) |
| D2 | Token-bucket cache: saat >512 aktor, `clear()` me-reset budget SEMUA aktor (flood aktor-distinct me-reset rate limiter global). Didokumentasikan sebagai "cheap rebuild", tapi efeknya vektor gaming ringan. | `superpower.py:2262-2268` | Rendah (terdokumentasi) |
| D3 | Publish per dirty-loop = **full projection rebuild** (replay SEMUA knowledge event + tulis ulang graph + validasi). Terukur: rebuild @knowledge_seq=2010 (37 node live) = **300–540 ms per publish**; inilah p50 tulis M2 (240 ms, gc on). Biaya O(K) per batch → throughput tulis sustained meluruh seiring K. | `projection.py:817-825, 1184-1236`; `superpower.py:1031-1032, 1213-1229`; pengukuran rebuild 3× | **Sedang** (termitigasi cap 64 KiB F-3 yang membatasi K; tapi desainnya kuadratik terhadap K di dalam cap) |

**Kompleksitas %:** 144 fungsi, rata-rata CC 4.44, maks 23 (`stop_daemon`), 10 fungsi >10 → **93.1% fungsi CC≤10**.

**Optimalitas saat overload % (terukur, repo gates + storm sendiri di host ini):**

| Kriteria overload | Target | Terukur | Nilai |
|---|---|---|---|
| Fairness di bawah spammer 10× | ≥80% throughput sopan | **82.8%** (16.7→13.9 ops/s; spammer 189 attempt/25 landed) | 100% (gate lolos) |
| Kehilangan tulisan di bawah kill -9 | 0 | **0 hilang, chain ok, 0 journal tak terrekonsiliasi** | 100% |
| Penolakan typed saat penuh (queue/session/rate) | 100% typed | 100% (SERVER_BUSY + retry_after; gate deadline/session/drain hijau) | 100% |
| Deadline propagation | typed DEADLINE_EXCEEDED | hijau (gate) | 100% |
| Latency storm 50 agen (dengan faktor kecepatan host 2.35) | read p95 <14.1 s; write p95 <42.3 s; heavy p95 <70.5 s | read p95 **7.66 s**, write p95 **9.64 s**, heavy p95 **8.19 s** | 100% |
| **Optimalitas overload superpower** | | | **100% terhadap SLO tertulis; catatan: SLO absolut longgar karena single-writer + FULL sync + publish O(K)** |

### 2.3 Performa terukur (host ini)

| Metrik | Angka | Metode |
|---|---|---|
| Storm 50 agen × 16 verb (800 ops) | 53.1 s; read p50 918 ms / write p50 4407 ms / heavy p50 4274 ms | repo gate `test_serve_gates.py` (exit 0, 11/11) |
| Group commit A/B repo (240 tulis/arm) | off **15/s** vs on **125/s** → **8.13×** | repo gate |
| Group commit A/B saya (2000 tulis, 16 aktor, 0 error) | off **6.2/s** p50 2387 ms vs on **65.4/s** p50 240 ms → **10.5×** | B3-M2 |
| Read ceiling (24 pembaca `knowledge_get_node`) | **72.7 reads/s**, p50 333 ms, 0 error | B3-M3 — **penyebab: setiap read = 2 transaksi audit di writer lane** (begin+complete via `_ForwardingAuditLog`); ceiling read ≈ setengah laju txn writer |
| Isolasi heavy lane | read p95 **117.9 ms** selama ingest berjalan | repo gate |
| Crash safety | kill -9 di tengah burst → 0 tulisan ter-ack hilang | repo gate |

### 2.4 Level profesional — SUPERPOWER SERVER

| Sumbu | % | Bukti |
|---|---|---|
| Flow engineering | 93 | Drain-first audit lane (deadlock-free by construction), ack-pasca-commit (assert anti-SUB-104), batch-fairness skip-excluded, graceful drain menunggu in-flight; 58 invariant test + DST simulation harness |
| Core algoritma | 88 | Fair-queue + group commit + watermark publish tersusun benar; −12 untuk D3 (publish O(K) per loop) dan D1 (writer tak disupervisi) |
| Arsitektur | 94 | Lane read/write/heavy/sys terpisah, read-only dipaksa mesin, single-writer untuk chain, capability gate opsional fail-closed, budget statis satu sumber kebenaran |
| Kreativitas | 90 | Session-lease yang paham in-flight; audit lane internal; dedup in-flight dengan analisis deadlock (resolve tanpa lock); DST virtual-time untuk replay deterministik |
| Attention to detail | 93 | 39 assert di superpower.py; komentar keputusan dengan ID temuan (W10/W13/W16/SUB-xxx); kill switch untuk tiap optimasi; −7 untuk D1 |
| **Total superpower** | **91.6** | |

---

## 3. SERVE FLOW (tulis/baca/konkurensi/daemon: `rpc.py`, `proxy.py`, `flow_retry.py`, `pipeline_stages.py`, `middleware.py`, `daemon_config.py`, `watchdog.py`, jalur dispatch)

### 3.1 Algoritma

| Algoritma | Jenis | Bukti |
|---|---|---|
| Framing NDJSON JSON-RPC 2.0 fail-closed (unknown keys ditolak, cap frame 1 MiB) | mengikuti codegraph transport, ditulis ulang strict | `rpc.py:105-204` |
| Negosiasi versi handshake = aturan pinned SDK (set tertutup → fallback latest) | replikasi aturan eksternal, konstan sendiri | `rpc.py:247-268` |
| Proxy stdio: handshake lokal byte-identik, forward selebihnya, PPID watchdog + early-ppid capture (#1185) | pola codegraph + hardening sendiri | `proxy.py:34-105` |
| Retry/backoff deterministik (rng eksplisit, seed → replay DST; cancellation sakral) | sendiri | `flow_retry.py:1-120` |
| Pipeline stages dengan checkpoint ber-digest + invalidasi suffix | sendiri | `pipeline_stages.py` |
| Middleware stack fail-closed (RequestId→Timing→CapabilityGate→TypedError) | sendiri | `middleware.py` |
| DaemonConfig: HARDWARE_AUTO sentinel + scale-up-only dari HardwarePlan + validasi fail-closed menyeluruh | sendiri | `daemon_config.py:227-433` |
| Dispatch: lane routing by WRITE SEMANTICS (bukan nama), aktor wajib, deadline propagation, stats reservoir berbatas | sendiri | `superpower.py:2036-2301` |

### 3.2 Efek negatif / bug nyata

| # | Temuan | Bukti | Severity |
|---|---|---|---|
| F1 | **FrameReader off-by-one di cap:** `readline(max+1)` lalu `len(line) > max_bytes` → frame dengan konten tepat `max_frame_bytes` (1 MiB) + `\n` = max+1 byte → **ditolak** meski tepat di batas. Batas efektif = max−1. | `rpc.py:144-152` (analisis; perilaku `readline(limit)` Python) | Rendah (edge 1-byte di 1 MiB) |
| F2 | Read path daemon membayar **2 transaksi audit per read** (contract, bukan bug) → ceiling read 72.7/s di host ini. Trade-off "auditable memory" yang disengaja, tapi perlu diketahui sebagai batas arsitektur. | `service.py:806-839` + `_ForwardingAuditLog`; terukur B3-M3 | Catatan arsitektur |
| F3 | Notifikasi JSON-RPC apa pun (id=None) ditelan diam-diam termasuk method tak dikenal — sesuai spek MCP, tapi menutupi kesalahan klien. | `superpower.py:2038-2041` | Rendah (by design) |

**Kompleksitas % (agregat 9 modul flow):** 184 fungsi, 166 dengan CC≤10 → **90.2%**. Outlier: `daemon_config._validate` CC=42 (rantai validasi linier — pola wajar), `simulation.run` CC=74 (harness DST), `watchdog.check_once` CC=17.

**Optimalitas saat overload %:** dispatch menolak typed di semua titik jenuh (session penuh, queue penuh, rate, drain, deadline) — diverifikasi 6 gate hijau + isolasi webview (SUB-152: 1410 req flood → 0 5xx). Overhead flow per request: framing+parse+dispatch ≈ sub-ms (solo probe gate p50 ≈ 96 ms di host ini dengan faktor 2.35 — termasuk audit). **Nilai: 92%** (−8 untuk ceiling read F2 yang merupakan harga kontrak audit, bukan defect; dan F1).

### 3.3 Performa terukur

Sudah tercakup di §2.3 (storm, read ceiling, A/B). Tambahan: handshake proxy lokal = 0 round-trip daemon (by construction, dipin test byte-identik).

### 3.4 Level profesional — SERVE FLOW

| Sumbu | % | Bukti |
|---|---|---|
| Flow engineering | 93 | Envelope strict fail-closed; deadline menembus queue; drain dua fase; proxy tak pernah hang di startup (try/except dengan fallback) |
| Core algoritma | 85 | Retry deterministik dengan seed (DST-replayable) jarang ada di produk sekelas; pipeline checkpoint digest; −15 karena tidak ada adaptive batching di flow (batch cap statis dari plan) |
| Arsitektur | 92 | Satu config fail-closed; budget→dok terpin; middleware eksplisit; pemisahan lane by-semantics |
| Kreativitas | 88 | Early-ppid capture; heartbeat-lease transparan di proxy; skip-excluded batch gather |
| Attention to detail | 90 | Validasi bool-vs-int di semua knob; batas AF_UNIX 100 byte dengan fallback path deterministik; −10 untuk F1 |
| **Total serve flow** | **89.6** | |

---

## 4. AUDIT / JOURNAL (`audit.py`, `events.py`, + `leases.py` sebagai koordinasi tulis teraudit)

### 4.1 Algoritma

| Algoritma | Jenis | Bukti |
|---|---|---|
| Journal durable + exactly-once final audit (idempotent complete; status complete → return stored) | sendiri (spec §7) | `audit.py:104-166` |
| Crash recovery: heartbeat-staleness + rekonsiliasi via effective idempotency key + repair projection sekali + satu audit per journal | sendiri | `audit.py:191-247` |
| `store.commit`: heartbeat mengelilingi append; post_write auto-review di dalam batas audit; failure audit tepat satu; `AuditOutcomeMismatch` typed | sendiri, berpasangan store↔audit | `store.py:1417-1502` |
| Lease registry: transisi tercatat di chain (`lease.*`), reap expired per acquire, konflik dicatat di transaksi terpisah (rollback tak menyembunyikan konflik) | sendiri (W17; tidak ada di referensi) | `leases.py:154-260` |
| Chain cross-check: anchor + journal→audit-event resolvability | sendiri | `store.py:912-959` |

### 4.2 Efek negatif / bug nyata

| # | Temuan | Bukti | Severity |
|---|---|---|---|
| A1 | `verify_chain` O(N) memori (= S2, bagian ini pemilik beban audit terbesar). | terukur §1.2 | Sedang-tinggi |
| A2 | `recover_incomplete` menutup journal berurutan dengan `complete` per journal — O(j) transaksi; ribuan journal crash → recovery lambat tapi benar (batching audit lane W16 tidak dipakai di sini). | `audit.py:214-246` | Rendah |
| A3 | Double-`complete` konkuren pada journal sama: UNIQUE(event_id) → `sqlite3.IntegrityError` mentah (bukan typed) bocor ke caller. Jalur membutuhkan dua writer konkuren untuk invocation sama — kontrak mengatakan recovery sebelum traffic, jadi praktis terlindungi. | `audit.py:128-166` (analisis; tidak ada lock idempotensi selain DB) | Rendah |

**Kompleksitas %:** audit.py 9/9, events.py 1/1, leases.py 20/21 CC≤10 → **96.8%**.

**Optimalitas saat overload %:** audit lane terbatas (2048) dengan blocking backpressure terukur aman di storm (0 jurnal tak lengkap dari 734 jurnal storm — gate memverifikasi `total==complete` per aktor untuk 50 aktor). W16 audit batching: satu transaksi untuk drain penuh (receipt defer-pasca-commit). Biaya: tiap op = 2 event (domain+audit) + 4 txn di lane C (terukur 171 ops/s). **Nilai: 95%** (−5 untuk A1 pada audit besar).

### 4.3 Performa terukur

| Metrik | Angka |
|---|---|
| Full commit path (journal+audit) | 171 ops/s avg; 251→152 ops/s (5k→20k) |
| Events per op | 2 (domain+audit), terverifikasi `GROUP BY kind` = 20000/20000 |
| Chain verify @160k | 12.71 s / 951 MiB RSS (S2/A1) |
| Kill -9 recovery | 0 journal tak terrekonsiliasi, chain ok (gate) |
| Storm audit completeness | 734/734 jurnal complete (50 aktor × 16/13 ops) |

### 4.4 Level profesional — AUDIT/JOURNAL

| Sumbu | % | Bukti |
|---|---|---|
| Flow engineering | 95 | Exactly-once final audit benar-benar terbukti (storm + kill-9 gates); failure-audit tepat satu bahkan saat domain gagal; heartbeat anti false-positive recovery |
| Core algoritma | 92 | Rekonsiliasi by effective-key; konflik lease tercatat di luar transaksi gagal; −8 untuk A1 (memori) |
| Arsitektur | 94 | Journal (operasional) vs event audit (immutable) dipisah tegas; anchor chain; leases additive-only di atas kontrak beku |
| Kreativitas | 90 | Audit-of-denial (capability_denied tercatat); recovery yang membuktikan retry via request_hash ganda |
| Attention to detail | 93 | Typed outcomes; `AuditOutcomeMismatch`; liveness contract tertulis; −7 untuk A3 |
| **Total audit/journal** | **92.8** | |

---

## 5. Ringkasan verdict

| Bagian | Kompleksitas (CC≤10) | Optimalitas overload | Algoritma | Performa (host ini) | Level profesional |
|---|---|---|---|---|---|
| Store | 94.3% | ~95% (batch path datar; serial path 45% @160k karena S1) | kuat, S1/S2 nyata | 872 ops/s batch event; 2915 edges/s @5.2 GB; reads p95 <5 ms @5.2 GB | **88.6** |
| Superpower server | 93.1% | 100% vs SLO tertulis (fairness 82.8% ≥ 80; 0 lost; storm dalam target) | kuat, D1/D3 nyata | storm 800 ops/53 s; group commit 8.13–10.5×; read ceiling 72.7/s (harga audit) | **91.6** |
| Serve flow | 90.2% | 92% (semua refusal typed; F1 off-by-one) | kuat | overhead sub-ms; handshake lokal | **89.6** |
| Audit/journal | 96.8% | 95% (734/734 jurnal complete; A1 beban memori) | sangat kuat | 171 ops/s full-commit; verify 12.7 s @160k | **92.8** |

**Bug ledger gabungan (semua nyata, semua terukur/terbukti):** S1 COUNT(*) per insert (O(N)/tulis) · S2/A1 verify_chain O(N) memori (951 MiB @160k) · S3 effective_idempotency_key tanpa indeks · D1 writer thread tak disupervisi watchdog · D2 reset bucket global saat >512 aktor · D3 publish = full rebuild O(K) per batch (300–540 ms @K=2010) · F1 frame off-by-one di cap · A2 recovery tak ter-batch · A3 IntegrityError mentah di double-complete. Tidak ada bug korupsi/kehilangan data — semua gate integrity hijau termasuk kill -9.

**Kapasitas knowledge path (temuan arsitektur, by design):** `ARTIFACT_MAX_BYTES = 64 KiB` (F-3) membatasi graph knowledge terpublikasi (~36–80 node kecil); kelebihan ditolak typed `PublishCapacityExceeded` dan teraudit (teramati langsung saat M2 run pertama: 1962 refusal typed, chain tetap ok). Store graph code-intel (files/symbols/edges) TIDAK dibatasi cap ini — terbukti 5.17 GB / 6.7M edges dengan read p95 <5 ms.

## Lampiran A — reproduksi (semua perintah di host ini, venv repo)

```bash
# B1 event-path scaling (lane A/B/C + verify_chain + EXPLAIN)
.venv/bin/python /root/.hermes/cache/scratch/sub155/b1_event_scaling.py   # lane A (log: b1_out.txt)
.venv/bin/python /root/.hermes/cache/scratch/sub155/b1b_rest.py           # verify + lane B/C (log: b1b_out.txt)
# B2 multi-GB build + read matrix @5.17 GB (resumable; log: b2_out.txt)
.venv/bin/python /root/.hermes/cache/scratch/sub155/b2_big_store.py 6300000000
# B4 WAL concurrency @5.17 GB (log: b4_out.txt)
.venv/bin/python /root/.hermes/cache/scratch/sub155/b4_wal_concurrency.py
# Gates repo (storm/fairness/group-commit/crash-safety; log: gates_out.txt)
.venv/bin/pytest tests/test_serve_gates.py -s -q
# M2 group-commit A/B 2000 tulis (log: b3m2c_out.txt); M3 read ceiling (log: b3m3_out.txt)
.venv/bin/python /root/.hermes/cache/scratch/sub155/b3_daemon.py m2
.venv/bin/python /root/.hermes/cache/scratch/sub155/b3_daemon.py m3
# Kompleksitas (AST CC walker): /root/.hermes/cache/scratch/sub155/cc_scan.py
```

Catatan metode: semua latensi client-side `time.monotonic`; persentil = nearest-rank; corpus B2 deterministik (`random.Random(42)`); store dibuka dengan hardware plan nyata (bukan pinned legacy) agar angka merepresentasikan produksi; dua run M2 pertama dibuang dari klaim (kontaminasi F-3 karena reuse dir — dicatat sebagai bukti guard bekerja, bukan sebagai throughput).
