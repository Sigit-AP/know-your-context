# W16 — Model-Based Testing + Power-of-Ten Static Gates (SUB-136)

## Tujuh pertanyaan pakar (dijawab SEBELUM menulis kode)

1. **Masalah.** Dua kelas regresi tidak terjangkau suite yang ada: (a) bug
   *stateful* — urutan operasi tak biasa (crash mid-write, rollback lalu
   reopen, replay idempoten lintas restart) yang hanya muncul pada kombinasi
   langkah, bukan pada contoh tunggal; (b) regresi *bentuk* kode — loop
   tak-terbatas tanpa exit, fungsi membesar diam-diam, rekursi baru, return
   status dibuang — masuk tanpa ada gate statis yang menolaknya.
2. **Kekurangan/kendala.** Kontrak beku tak disentuh; scope hanya
   `tests/` + `tools/` + `docs/recovery/w16-modelbased.md`; TIDAK commit;
   determinisme mutlak (seed tetap → trace tetap; dua run audit byte-identik);
   runtime tambahan suite wajar (<3 menit untuk ketiga file test).
3. **Tujuan terukur.** ≥100 test baru hijau; state machine mengecek invariant
   model-vs-sistem SETELAH SETIAP langkah; tool `tools/power_of_ten.py` exit 0
   pada repo dengan berkas justifikasi, exit 1 pada pelanggaran baru, exit 2
   pada konfigurasi rusak; ruff + pyright bersih; determinisme terbukti pada
   PYTHONHASHSEED 0/1/42.
4. **Manfaat.** Builder/auditor berikutnya: bug stateful yang pernah ditemukan
   mesin menjadi regression permanen (trace codified); CI mendapat gate statis
   <10 detik tanpa dependensi baru (stdlib AST saja); setiap pengecualian pada
   aturan Power-of-Ten adalah daftar nyata dengan alasan per kasus, bukan
   opini.
5. **Pelajaran dari pola kegagalan.** Kegagalan tersambunyi terlarang →
   invariant dicek setelah langkah, bukan di akhir; crash-kill harus NYATA
   (`os._exit(137)` / SIGKILL), bukan close-yang-sopan; justifikasi basi harus
   GAGAL (stale-entry check) supaya berkas pengecualian tidak bisa membusuk;
   baseline ratchet diukur, bukan dikarang.
6. **Prinsip desain.** (a) Model adalah orakel, sistem nyata adalah kebenaran
   — keduanya dibandingkan per langkah. (b) Deterministik di mana-mana:
   hypothesis `derandomize=True`, driver seeded `random.Random`, trace replay
   tanpa hypothesis sama sekali. (c) Gate read-only terhadap `src/`; tidak ada
   perbaikan tanpa kepemilikan — pelanggaran lama diratchet, bukan disentuh.
   (d) Fail closed pada gate: pelanggaran tanpa justifikasi = merah.
7. **Kill criteria.** Jika mesin daemon tidak stabil 3 run berturut → turun ke
   driver seeded deterministik saja. Jika aturan POT menghasilkan >100
   pelanggaran tak-terjustifikasi yang bukan false positive → persempit ke
   yang dapat dibuktikan statis. (Tidak terpicu: mesin stabil; aturan POT
   final 166 pelanggaran nyata, semua terjustifikasi per kasus.)

## Yang dibangun

### 1. Model-based testing (`tests/test_model_based_store.py`, `tests/test_model_based_session.py`)

Pola `RuleBasedStateMachine` hypothesis dengan SATU harness per sistem yang
dipakai tiga driver:

* **`ModelHarness` (store)** — model: versi per node, status lifecycle, jumlah
  event/knowledge/evidence. Rules: upsert / retire / evidence / relation /
  read / rollback-attempt (`VersionConflict` tak boleh menambah seq) /
  conflict-attempt (`IdempotencyConflict` tak boleh menulis) /
  replay-idempoten (substansi sama, identitas attempt baru → replay) /
  close / reopen / **crash-kill** (subproses nyata
  `tests/_model_store_crash_child.py`, mode `commit` = mati tanpa close,
  mode `uncommitted` = mati DI DALAM transaksi terbuka — baris parsial wajib
  lenyap). Invariant per langkah: `verify_chain().ok`, `event_count` ==
  model, `knowledge_high_watermark` == model, versi + lifecycle tiap node ==
  model.
* **`SessionHarness` (daemon)** — daemon in-process nyata di socket Unix
  nyata. Rules: open / ping / read (`knowledge_query`) / write
  (`knowledge_log_event`) / read-back (`knowledge_get_node`) / review /
  **approve** (`propose_improvement` + `apply_improvement` approved) /
  **rollback** (`knowledge_rollback` ke v1) / close session / **crash-kill
  client** (frame parsial + RST via SO_LINGER=0) / restart daemon. Invariant
  per langkah: chain valid lewat handle store independen (WAL multi-reader),
  jawaban daemon == bacaan store langsung untuk setiap node (cross-check dua
  sisi), `requests_total` >= model (baseline per inkarnasi daemon),
  `sessions_active` <= kapasitas.
* **Driver**: (a) hypothesis state machine (`derandomize=True` → shrinker
  deterministik; trace gagal → codified permanen), (b) `TestSeededTraces`
  seeded `random.Random` (20 seed store, 3 seed daemon — trace = f(seed)),
  (c) `TestDeterministicRegression` — 12 trace handcrafted untuk interleaving
  paling jahat (crash-antara-write-dan-close, replay lintas restart, rollback
  lalu reopen, retire→revive, ...).
* **Crash-kill daemon nyata** (`tests/_model_daemon_crash_child.py` +
  `TestDaemonCrashKill`): daemon subproses di-SIGKILL; persis write yang
  ter-ack yang selamat; daemon baru recover di path yang sama lewat jalur
  produksi `spawn_daemon` (stale lock pid mati termasuk).
* **Mutation proof** (`test_invariant_catches_tampering`,
  `test_invariant_catches_truncated_tail`): invariant DIBUKTIKAN menangkap
  korupsi (tamper payload row; truncate tail) — dengan kontrol positif
  (model ter-rekonstruksi pada store sehat = hijau), jadi bukan false-pass.

### 2. Power-of-Ten static gates (`tools/power_of_ten.py`)

Lima aturan NASA/JPL yang dapat dibuktikan statis di Python, stdlib AST saja:

* **POT1 loop terbatas** — `while True`/konstanta-truthy dan `for` atas
  `itertools.count/repeat/cycle` tanpa `break` MILIK loop itu (break di for
  bersarang tidak dihitung) atau `return`/`raise` yang reachable = pelanggaran.
  Hasil nyata di repo: **0**.
* **POT2 fungsi ≤ 60 baris** (satu halaman JPL). Kode lama di-*ratchet*:
  setiap fungsi >60 dicatat dengan panjang terukurnya; boleh menyusut, TIDAK
  BOLEH membesar (membesar = gate merah; menyusut ≤60 = entry basi = merah —
  ratchet mengetat sendiri).
* **POT3 densitas assert** di 16 modul kritis (store, audit, backup,
  projection, service, importer, events, domain, contracts, mcp_server,
  serve/*, ops/switches, security). Idiom repo: typed fail-closed exception +
  pydantic, bukan `assert` (hilang di `-O`) — maka ratchet: densitas boleh
  naik, TIDAK BOLEH turun; target absolut JPL (2/fungsi) terdokumentasi.
* **POT4 tanpa rekursi tak-terdokumentasi** — call graph statis per modul
  (self-call langsung + mutual; call ke nama yang di-import BUKAN rekursi —
  regression test untuk false positive `service.apply_improvement` →
  `improvement.apply_improvement`). 13 situs nyata, semua recursion atas
  pohon terbatas (AST file, payload JSON, scope chain), terjustifikasi per
  kasus dengan argumen bound.
* **POT5 return status dicek** — call bare-statement ke fungsi `-> bool`
  (nama unik per modul atau per paket) = pelanggaran. 11 situs nyata,
  semuanya best-effort-yang-dibuktikan-oleh-guard (mis.
  `heal_lying_projection_watermark` dipanggil SETELAH lie terbukti, jadi bool
  redundan), terjustifikasi per kasus.

Berkas justifikasi: `tools/power_of_ten_justifications.json` (164 entry,
version 1, skema ketat: field wajib, rule dikenal, tanpa duplikat, POT2 wajib
`lines`, POT3 wajib `density`, `count` untuk multiplicity). Entry basi
(tak cocok apapun / count berbeda / ratchet terlampaui) = gate GAGAL.

**Masuk CI**: gate berjalan sebagai test (`TestRepoGate` di
`tests/test_power_of_ten.py`) — CI menjalankan pytest penuh, jadi gate aktif
di CI tanpa menyentuh `.github/` (di luar scope). Bentuk CLI mandiri tersedia:
`python tools/power_of_ten.py` (exit 0/1/2, `--json` untuk mesin). Perawatan
baseline yang DISENGAJA: `python tools/power_of_ten.py --rebaseline` mem-pin
ulang entry POT2/POT3 ke tree saat ini (alasan lama dipertahankan; setiap
kenaikan baseline dicetak sebagai `RAISED`; entry yang terlampaui `DROPPED`)
— 4 test khusus membuktikan perilakunya.

## Bukti

* **Test baru**: 133 hijau (88 `test_power_of_ten` + 39 `test_model_based_store`
  + 6 `test_model_based_session`), deterministik 3× pada PYTHONHASHSEED 0/1/42
  (identik tiap seed).
* **TDD**: RED nyata — `test_power_of_ten` gagal koleksi (FileNotFoundError
  pada tool yang belum ada); `--rebaseline` RED (4 gagal: arg tidak dikenal)
  sebelum diimplementasi. Mutation check pada invariant (tamper → FAIL →
  kontrol positif PASS).
* **Gate nyata menangkap perubahan nyata, dua kali**: (1) `validate`
  206→207 baris oleh sibling w16-perf → MERAH "grew beyond the recorded
  baseline"; (2) refactor sibling berikutnya MEMECAH `validate` di bawah 60
  baris → entry menjadi basi → MERAH ("stale") sampai `--rebaseline`
  menjatuhkannya (DROPPED) dan mem-pin 142 entry ke tree final. Ratchet
  bekerja di dua arah.
* **Tool final**: `python tools/power_of_ten.py` → `166 violation(s), 166
  justified; PASS`, exit 0; tanpa justifikasi exit 1; justifikasi basi exit 1.
* **Ruff**: `ruff check` + `ruff format --check` bersih pada keenam file.
  **Pyright**: 0 error pada keenam file (2 stub hypothesis
  `suppress_health_check` di-ignore dengan komentar alasan).
* **Full suite** (4 chunk, tree konkuren): 2504p + 9011p + 2305p/0f +
  1943p/1f. 1f = `test_serve_gates.py::TestStormAllVerbs::
  test_sixteen_ops_fifty_agents_with_latency_per_lane` — kelas flake latensi
  storm yang SUDAH terdokumentasi pre-existing (SUB-132/134/135, A/B di HEAD
  murni gagal identik) DAN jalur yang sedang diedit SUB-137 (audit batching,
  `serve/superpower.py` in-flight saat run). Bukan milikku: perubahanku aditif
  murni (tests/+tools/+docs), 0 baris src.

## Yang TIDAK terbukti / batasan jujur

* POT4/POT5 statis tanpa inferensi tipe: call lewat atribut yang ambigu
  (nama sama di 2+ tempat) TIDAK diflag — dokumentasi batasan, teruji.
* POT1 hanya memflag loop yang TERBUKTI tak-terbatas (`while True` dsb.);
  `while kondisi` kompleks tidak bisa dibuktikan statis → di luar aturan.
* Mesin daemon: 2 example hypothesis × 12 langkah + 3 seed × 18 langkah —
  cakupan ruang-state nyata tapi tidak lengkap; crash-kill daemon penuh
  (SIGKILL proses) diuji terpisah dan deterministik, bukan di dalam mesin.
* Tidak ada bug stateful BARU yang ditemukan mesin pada 129 run ini — klaim
  yang jujur adalah "invariant terbukti menangkap korupsi yang disuntik",
  bukan "tidak ada bug".

## File

* `tools/power_of_ten.py` (baru) — gate; `tools/power_of_ten_justifications.json`
  (baru) — 164 entry.
* `tests/test_power_of_ten.py` (baru, 88) — unit per aturan + justifikasi +
  rebaseline + CLI + gate repo.
* `tests/test_model_based_store.py` (baru, 39) + `tests/_model_store_crash_child.py`.
* `tests/test_model_based_session.py` (baru, 6) + `tests/_model_daemon_crash_child.py`.
* Kontrak beku tak disentuh; `src/` tak disentuh; TIDAK commit.
