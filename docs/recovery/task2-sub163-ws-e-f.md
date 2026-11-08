# TASK2 WS-E + WS-F — SUB-163 (builder)

Scope: WS-E (D1 writer supervision, D2 bucket LRU eviction, D3 publish incremental) +
WS-F sebagian (F1C capability audit O(n²), F2C hotspot _make_handler C=68 /
run_diagnostics C=47 / run_doctor C=38). F5C/F6C/A2/A3 = WS-F ringan, di luar brief.

## Pola pikir pakar (7 pertanyaan, dijawab sebelum kode)

1. **Masalah**: (D1) writer thread — satu-satunya penulis store — mati tanpa
   supervisor: daemon tampak hidup tapi semua tulisan menggantung selamanya.
   (D3) setiap publish me-replay SEMUA knowledge event O(K) (79% dari 240–540 ms
   @K=2010 adalah `model_validate`+`json.loads` atas 2010 event), padahal delta
   antar-publish biasanya beberapa event. (D2) aktor ke-513 me-reset budget rate
   SEMUA aktor. (F1C) `CapabilityStore.audit()` membaca seluruh log per append →
   O(n²) total, 69,4 ms/op @50 token dan naik. (F2C) tiga fungsi C=68/47/38
   melampaui ambang PoT dengan justifikasi "terlalu besar untuk diekstrak".
2. **Kendala keras**: kontrak beku v1–v5 tak disentuh; semantik publish
   byte-identik (content_hash sama antara incremental dan full rebuild — dipin
   property test); single-writer tak berubah (restart hanya untuk thread MATI);
   hash-chain audit tetap verify True dari genesis; zero behavior change pada
   refactor F2C (suite webview/ops tetap hijau); kill-9 zero-loss gate hijau.
3. **Tujuan terukur** (dari TASK2-PLAN): D1 = 5 thread teregistrasi + drill
   restart hijau; D3 = p50 publish ≤100 ms @K=2010 (≥3×), konstan thd K untuk
   delta kecil; D2 = budget aktor eksisting bertahan saat aktor baru masuk;
   F1C = ms/op issue datar di 50/500/5000 token, chain True; F2C = ketiga fungsi
   ≤15 di cx_scan, justifikasi PoT ter-update, ruff/pyright/bandit bersih.
4. **Manfaat**: operator — daemon tak pernah "setengah hidup"; tulisan sustained
   tak meluruh seiring K; rate limiter adil; capability issue O(1) amortized;
   maintainer — tiga hotspot bisa dipahami/diuji per-cabang.
5. **Pelajaran dari masalah**: stateful loop tanpa supervisor = liveness palsu;
   biaya O(K) per batch tersembunyi di balik "publish berhasil"; cache global
   dengan `clear()` menghukum aktor tak bersalah; log append-only harus punya
   head anchor (pola yang sama dengan chain_event_count store, WS-A S1).
6. **Prinsip desain**: (a) delta replay hanya maju, regression apa pun → reset +
   full replay (fail-closed ke kebenaran, fail open ke biaya); (b) restart
   writer hanya lewat jalur SupervisedThread yang menolak thread hidup;
   (c) head-cache audit mengikuti LOG sebagai sumber kebenaran, head hanya
   jalan pintas yang harus cocok dengan ekor log, keraguan apa pun → full scan;
   (d) refactor F2C murni pemindahan — data-driven dispatch, nol logika baru.
7. **Kill criteria**: D1 — drill kill-writer tak membuktikan restart, atau
   semantik single-writer berubah → rollback. D3 — validasi pasca-publish gagal
   di satu kasus, atau p50 tulis tak membaik ≥30% → rollback. D2 — spammer
   mendapat burst penuh setelah eviction → rollback. F1C — `verify_audit_chain`
   False di satu kasus → rollback. F2C — satu test suite area terkait merah atau
   ruff/pyright/bandit tak bersih → rollback.

## Baseline before (terukur, perintah di bawah)

| Item | Perintah | Angka before |
|---|---|---|
| D3 | `.venv/bin/python /root/.hermes/cache/scratch/sub163/d3_publish_baseline.py 5` | rebuild @seq=2010: min 222.4 / median 239.7 / max 539.2 ms (nodes=37, edges=36, hash 8dae985f…); 79% di `_knowledge_events` |
| D1 | grep `watchdog.register` superpower.py | 4 register, tanpa writer |
| F1C | (skrip sub163/f1c_baseline.py) | tbd |
| F2C | `python3 /root/.hermes/cache/scratch/cx_scan.py …` | _make_handler C=68, run_diagnostics C=47, run_doctor C=38 |

Store D3 = salinan byte-identik `/root/.hermes/cache/scratch/sub155/m2c_True/soul.sqlite3`
(watermark persis 2010, titik ukur verdict).

## After + bukti

Semua di host ini, venv repo, atas HEAD e46fc89 + diff workstream ini.

### D1 — writer thread masuk supervisi watchdog

- `superpower.py`: `_WriteArbiter` kini punya `heartbeat` (beat per wakeup loop),
  `_respawn()` (assert thread lama MATI — single-writer dijaga oleh guard, bukan
  harapan; reset `_executing`/`_batch_transaction_open`; pending audit receipt dari
  batch yang rolled back gagal typed), dan registrasi `writer` di
  `_start_worker_watchdog` (registrasi diekstrak ke `_lane_workers`).
- Test: `tests/test_serve_writer_supervision.py` (4 test): registrasi + beat saat
  idle; drill crash → watchdog restart → tulisan yang disubmit MELINTASI crash
  selesai (antrian tak menggantung) + alert "restart" tercatat; respawn menolak
  thread hidup (AssertionError); budget exhaustion → terminal dead + alert "dead".
- Pin terukur yang disesuaikan: `test_flow_integration.py::test_stats_section_covers_every_worker`
  kini menuntut "writer" di roster (pin lama mengkodekan bug setengah-hidup).
- Bukti: `pytest tests/test_serve_writer_supervision.py tests/test_flow_integration.py -q`
  → 30 passed; `tests/test_serve_gates.py` (storm + kill-9 zero-loss) → 11 passed.

### D2 — eviction LRU per aktor

- `_admit`: `self._buckets.clear()` (reset SEMUA aktor saat aktor ke-513 masuk) →
  `OrderedDict` + `move_to_end` saat hit + `popitem(last=False)` saat cap.
- Test: `tests/test_serve_admission_eviction.py` (2 test): budget aktor eksisting
  bertahan saat aktor baru masuk; spammer yang baru saja menghabiskan burst TETAP
  tertolak setelah eviction gelombang aktor baru (kill criterion). RED terbukti
  pada kode lama (clear() me-reset korban tak bersalah).

### D3 — publish incremental (delta replay)

- `projection.py`: `_ReplayState` (nodes/versions/relations/last_event_seq/watermark)
  di-fold per publish; `_knowledge_events(after_event_seq, upto)` membawa event_seq;
  `_replay_upto` — delta maju, regression/first-build/fold-tak-maju → full replay;
  `rebuild` retry sekali sebagai full replay bila delta publish gagal validasi
  (fallback sesuai plan). Correctness delta bersandar pada: event log append-only +
  knowledge_seq = MAX+1 di bawah single writer → knowledge_seq monoton thd event_seq.
- Semantik dipin byte-identik: `tests/test_projection_incremental.py` (5 test):
  delta publish hanya me-validasi d event (bukan K); incremental multi-round ≡
  full rebuild (content_hash + projection_version + SEMUA byte artifact);
  regression ke watermark lebih awal ≡ full rebuild fixed-mark (dengan
  `activate=False` — CAS store menolak watermark mundur, proteksi tak disentuh);
  fallback validation → full replay; watermark maju terus.
- Angka (store verdict byte-identik, watermark 2010, salinan di scratch/sub163):
  - Before: `d3_publish_baseline.py 5` → min 222.4 / median 239.7 / max 539.2 ms.
  - After (5-event delta per round, K≈2010→2050): p50 62.6 / 53.0 / 54.4 ms
    (3 run) — target ≤100 ms TERLAMPAUI (≥3.8×). First publish tetap full replay
    (533 ms, by design); republish empty-delta 56–62 ms dengan content hash
    IDENTIK (8dae985fcc496371) ke hasil full rebuild.
  - M2 daemon (skrip verdict `b3_daemon.py m2`): gc=on p50 tulis 131.6 ms
    (before verdict: 240 ms) → membaik 45% (kill criterion ≥30% TERLAMPAUI),
    120 ops/s, chain ok=True.
- Pin terukur disesuaikan: `test_property_projection.py::test_knowledge_events_respect_the_watermark`
  memakai signature baru `_knowledge_events(0, watermark)` (API internal).

### F1C — audit capability head-cache + registry copy

- `capabilities.py`: `audit()` memakai `_audit_head()` — head marker + tail read
  64 KiB (O(1)); log di depan head (crash window) → ikuti log; keraguan apa pun
  (head hilang/garbage, mismatch, baris > window, tail korup) → full-scan fallback
  dengan semantik pra-fix IDENTIK (log = sumber kebenaran; typed failure tetap dari
  full scan). `verify_audit_chain` tak disentuh.
- Residual registry: `_load_registry` deep copy json-round-trip (45% dari issue
  @5000) → `_copy_registry` dua-level (pointer copy; isolasi dipin test).
- Test: `tests/test_capability_audit_scaling.py` (9 test): append memory-bounded
  (tracemalloc ≤384 KiB atas log 4000 event — pra-fix 1867 KiB), crash-window,
  head tampered, bootstrap tanpa head, tail korup → CapabilityStoreError, baris
  >window, chain verify per step, nama file kontrak, isolasi cache registry.
- Angka (`f1c_bench.py`, median of 30): issue @50/500/5000 event audit:
  before 2.79 / 8.90 / 98.09 ms/op → after 1.60–1.94 / 5.78–7.86 / 37.2–39.2 ms/op.
  Append audit kini O(1) (dibuktikan memory bound, bukan timing). SISA pertumbuhan
  = `_write_registry` menulis ulang SELURUH registry JSON per issue (format-bound,
  cross-process revocation contract) — di luar temuan F1C (audit); kandidat
  workstream kompaksi registry terpisah (keputusan operator).

### F2C — hotspot kompleksitas

- `webview.py`: `_Handler` diangkat ke module level (factory kini
  `type("_Handler", (_Handler,), {"server_ref": self})` — binding per-instance
  identik dengan closure class); tabel dispatch diekstrak ke `_exact_read_table` +
  `_exact_ops_table` + merger `_exact_api_table`; param helpers `_int_param` /
  `_str_param` / `_query_route` / `_codeintel_route` module-level.
- `diagnose.py` / `doctor.py`: setiap closure check jadi fungsi module-level;
  `run_diagnostics`/`run_doctor` kini dispatch table murni.
- cx_scan after: `_make_handler` C=2 (dari 68), `run_diagnostics` C≤11 (dari 47),
  `run_doctor` C≤11 (dari 38). `_dispatch_api` ≤10 (dari 16).
- PoT: 4 entri justifikasi stale DIHAPUS (bukan dipindah) — violasi lenyap oleh
  refactor; `tools/power_of_ten.py` → "162 violation(s), 162 justified … PASS"
  (0 entri baru ditambahkan workstream ini; `_replay_upto` memakai loop, bukan
  rekursi, agar POT4 tak terpicu; `_start_worker_watchdog`/`_exact_api_table`
  di-split agar tetap di bawah ceiling 60 baris POT2).
- Suite: 674 test webview + 95 test ops + 119 test projection hijau.

### Gates akhir workstream

| Gate | Hasil |
|---|---|
| pytest scope tersentuh (1085 test) | 1085 passed (1 flake timing di bawah beban host → hijau standalone 3× + batch sedang; timeout drill diperkeras) |
| tests/test_serve_gates.py (storm/kill-9) | 11 passed |
| ruff check . + format --check . | PASS |
| pyright | 0 errors |
| bandit -ll -r src | exit 0 |
| tools/power_of_ten.py | PASS (162/162) |
| bench/run.py check | OK |
| bench/codebase/run.py check | OK |
| bench/institutional/run.py --strict | 8/8 PASS (2 SKIP terdokumentasi, sama seperti baseline) |
| uv lock --check + license_guard | OK |
| pytest suite penuh | 16310 passed, 1 skipped (codegraph CLI tak terpasang — environmental), 0 failed (1596s) |
| bench/codebase/run.py run (strict) | PASS — 1561 edges (pin SUB-159), determinism OK, ledger 20261108T202452Z |

Catatan determinisme: jalur yang disentuh (publish projection) dipin byte-identik
oleh `test_incremental_publish_is_byte_identical_to_full_rebuild`; bench/codebase
2-run in-run determinism OK pada gate di atas.

## Yang terbukti / tidak / mengejutkan (EXPERT-THINKING penutup)

- **Terbukti**: D1 (restart drill nyata, antrian tak menggantung), D2 (LRU), D3
  (publish O(delta), ≥3.8× @K≈2050, semantik byte-identik, M2 p50 −45%), F1C
  (audit append O(1), chain True di semua jalur), F2C (3 hotspot ≤11, PoT PASS
  dengan 4 entri stale dihapus).
- **Tidak sepenuhnya**: F1C target "ms/op issue datar" — append AUDIT sudah O(1),
  tapi `issue` masih tumbuh linear oleh full-rewrite registry JSON per panggilan
  (37–39 ms @5000). Ini mekanisme berbeda dari temuan (yang menunjuk audit);
  keputusan kompaksi/format registry = workstream terpisah.
- **Mengejutkan**: (1) delta replay mengekspos bahwa `_extraction` lama menghitung
  `version` dari fold — state yang tidak membawa event_seq menggandakan versi;
  tertangkap oleh test watermark-stability, diperbaiki dengan membawa event_seq di
  kedua jalur query. (2) PoT ternyata mengawasi PANJANG fungsi (POT2, 60 baris)
  selain kompleksitas — dua struktur baru harus di-split agar gate tetap PASS
  tanpa menambah justifikasi baru. (3) cx_scan menghitung closure bersarang ke
  fungsi induk — itulah mengapa C=68 menempel pada `_make_handler`.
