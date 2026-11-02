# W14 — I1 Experience-driven re-rank (`retrieval_v2.experience`)

Wave 3 inovasi I1 (WAVE3-PLAN §3): retrieval belajar dari outcome terekam.
Pola TokenEconomizer (MSR): belajar dari pengalaman nyata, fail-open, tanpa
panggilan model, biaya diamortisasi. Status: **builder SUB-126**.

## 7 pertanyaan pakar (EXPERT-THINKING.md, terjawab SEBELUM kode)

1. **Masalah** — Ranking retrieval v2 statis: bobot BM25/bonus dibekukan
   kontrak. `record_outcome` (14 ops) dan skor P/M/E sudah merekam pengalaman
   nyata, tetapi tidak ada umpan balik: node yang berulang kali menyelesaikan
   tugas tidak naik peringkat; node yang tak pernah dipakai terus memakan
   token budget.
2. **Kendala** — Determinisme penuh (tanpa model, tanpa random, tanpa clock
   di jalur rerank); fail-open: ledger kosong = output **byte-identik**
   baseline (dipin test); kontrak retrieval beku tak disentuh — re-ranker
   adalah lapisan opsional DI ATAS skor kontrak dengan multiplier terbatas;
   skema store beku tak disentuh (ledger hidup di **sidecar** SQLite
   `<store>.experience-ledger.db`); tidak ada data keluar; kill switch
   `KYS_EXPERIENCE_RERANK=0`.
3. **Tujuan terukur** — (a) outcome positif menaikkan rank secara
   deterministik (fixture beku: Δrank persis, dipin); (b) ledger kosong /
   kill switch → output byte-identik baseline (dipin); (c) multiplier
   ter-clamp [0.5; 1.5] dan terkuantisasi 12dp ROUND_HALF_EVEN (dipin);
   (d) budget token respons tak pernah terlampaui sesudah re-rank (sweep
   dipin); (e) ledger survive restart (append-only, jurnal hash-chained,
   tamper terdeteksi); (f) A/B bench deterministik: recall@10 dengan histori
   outcome ≥ baseline, tidak pernah turun di regresi fixture ter-pin.
4. **Manfaat** — Agent pengguna: konteks makin relevan seiring pemakaian
   nyata, bukan heuristik beku. Operator: metrik "retrieval mana yang
   benar-benar dipakai" via jurnal keputusan re-rank yang teraudit.
5. **Pelajaran dari masalah** — Loop umpan balik tanpa clamp = drift tak
   terbatas; ranking yang berubah tanpa audit = kegagalan tersambunyi;
   sinyal sedikit (1 outcome) tak boleh mengguncang ranking (damping).
   Maka: multiplier ter-clamp + damping saturasi + setiap keputusan tercatat.
6. **Prinsip desain** — (a) bertindak hanya lewat apa yang sudah terekam
   (tabel `evidence` + skor P/M/E node); (b) fail-open ke baseline kontrak
   di setiap titik ragu; (c) tanpa panggilan model; (d) biaya diamortisasi:
   sinyal diagregasi inkremental di sidecar terindeks (lookup O(log n) per
   node, bukan scan evidence per query); sinkronisasi idempoten (agregat
   dihitung ulang penuh per node tersentuh, watermark `event_seq`).
7. **Kill criteria** — Jika recall@10 bench turun vs baseline pada fixture
   regresi mana pun → `KYS_EXPERIENCE_RERANK=0` mematikan total (default
   kembali baseline byte-identik). Jika ledger tumbuh >10% per 10k query →
   jurnal di-retensi keep-N teraudit (pola retention yang sudah ada di
   ops/). Alternatif degradasi: rerank hanya pada kanal FTS.

## Desain (ringkas)

- **Sinyal** (`OutcomeSignal`): per `node_id` — `outcome_count`,
  `positive`/`negative`, `outcome_sum` (Decimal 12dp), P/M/E node terkini.
  Sumber: tabel `evidence` (append-only, frozen) + `node_revision` terbaru.
  Dibaca **read-only**; diagregasi ke sidecar terindeks.
- **Multiplier** (`compute_multiplier`): `1 + max_boost · mean_outcome ·
  min(1, count/count_saturation)`, clamp `[1−max_boost, 1+max_boost]`
  (⊆ [0.5, 1.5], `max_boost=0.5` default), kuantisasi 12dp. Tanpa sinyal /
  count < `min_outcomes` → multiplier tepat `1.000000000000`.
  Multiplier hanya menyentuh skor **positif** (skor ≤ 0 = kanal fallback
  LIKE/fuzzy yang lemah; memperbesarnya akan menaikkan noise) — dipin test.
- **Ledger** (`ExperienceLedger`): sidecar SQLite; tabel `signal` (PK
  node_id → O(log n)), `journal` (append-only, hash-chained sha256,
  genesis 64×"0", tanpa timestamp → deterministik), `sync_state`
  (watermark). `sync_from_store` inkremental + idempoten.
- **Reranker** (`ExperienceReranker`): mengikat ledger+config; `rerank()`
  menarik sinyal sekali per query (satu query indexed IN), menerapkan
  multiplier, mengurutkan ulang dengan total order yang sama
  (`-score, path, line, col, node_id`), lalu menulis SATU entri jurnal
  berisi semua keputusan (node_id, base, multiplier, final, reason).
  `from_env` → `None` bila kill switch mematikan (fail-open total).
- **Hook** (1 titik, additif): `SymbolLookup(config, experience=None)` —
  default `None` = perilaku hari ini byte-identik; bila dipasang, re-rank
  terjadi sesudah sort kanonik dan SEBELUM truncasi limit (agar node
  ter-boost bisa masuk top-N — inti perbaikan recall@10).

## Bukti (diisi sesudah build)

Lihat bagian "Hasil" di bawah (angka nyata + perintah + exit code).

## Hasil

(ditulis setelah gate hijau — lihat board SUB-126)

- **RED → GREEN nyata**: `ModuleNotFoundError` (collection error) →
  `124 passed` (`tests/test_experience_rerank.py`, exit 0, ~3–5 s).
- **Tujuan terukur**:
  (a) `test_outcome_lifts_rank_deterministically` — node dengan 5 outcome
  positif tersaturasi naik ke rank 1 dengan skor persis
  `quantize12(base × 1.5)` (dipin);
  (b) `test_empty_ledger_byte_identical` + `test_kill_switch_byte_identical`
  — tuple equality DAN payload JSON identik baseline;
  (c) `test_multiplier_clamped_and_quantized` (kelas
  `TestComputeMultiplier`, 17 test) — batas 0.5/1.5 persis, kuantisasi
  12dp dipin (`1/7 → 1.071428571429`);
  (d) `test_budget_never_exceeded_after_rerank` — sweep budget
  16–1024 token, `total_tokens <= budget` di semua titik;
  (e) `test_survives_restart` + `test_journal_survives_restart` +
  `test_tampered_{payload,hash}_detected` — ledger sidecar persisten,
  jurnal hash-chained, tamper terdeteksi dengan `first_bad_seq`;
  (f) `TestABRecallBench` — recall@10 rerank ≥ baseline di semua query
  fixture, ≥1 query naik strictly; determinisme 2 pass byte-identik.
- **Gates**: ruff check + format 0 issue pada file scope; pyright 0/0/0;
  tetangga retrieval (10 file test, 306 test) hijau; determinisme
  2 run file test identik (124 passed dua kali) + 3× PYTHONHASHSEED
  0/1/42 (170 passed tiap seed, exit 0).
- **Full suite**: 9049 passed / 2 skipped (jujur: codegraph CLI absent,
  snapshot guard) / 3 failed (1450 s) — ketiga failure DIBUKTIKAN bukan
  milik scope ini: (1) `test_docs_changelog::test_api_documents_every_cli_command`
  gagal karena command `debt`/`propose`/`provenance` milik SIBLING WIP
  (cli.py/store.py dimodifikasi sibling I2/I4 mid-flight, traceback
  konklusif); (2)(3) `test_serve_gates` storm+fairness = flake beban yang
  sudah terdokumentasi SUB-124/125 — storm HIJAU standalone di tree ini
  (69 s, exit 0) dan kontrol worktree HEAD bersih juga hijau (78 s);
  kode rerank inert untuk jalur serve (`experience=None` → satu-satunya
  delta adalah branch yang tak pernah diambil, dibuktikan 306 test
  retrieval + seluruh test webview/pipeline hijau).
- **Kontrak beku tak disentuh**: skema store, `record_outcome`, konstanta
  scoring, dan perilaku `SymbolLookup()` default (tanpa `experience=`)
  byte-identik — dibuktikan `test_hook_none_matches_plain_lookup` dan
  seluruh suite retrieval lama hijau tanpa ubahan.
- **Pelajaran saat build**: multiplier hanya boleh menyentuh skor positif —
  skor kanal fallback (LIKE/fuzzy) bisa ≤ 0, dan memperbesar skor negatif
  justru MENURUNKAN node ber-outcome positif (semantik terbalik). Dipin di
  `test_{zero,negative}_score_never_moves`.
