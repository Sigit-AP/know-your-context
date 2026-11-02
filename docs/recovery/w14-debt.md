# W14 — Knowledge Debt Queue berbudget (I4, SUB-128, 2026-11-02)

Inovasi I4 dari WAVE3-PLAN §3: antrian kerja terstruktur untuk node berutang
(skor rendah / kontradiksi ter-flag / stale / unresolved refs lama), dengan
budget per siklus, kill switch, proposal aksi nyata lewat jalur
proposal-gated, laporan di webview + CLI, dan metrik burn-down antar siklus.

## 7 pertanyaan pakar (terjawab sebelum build)

1. **Masalah:** sinyal review (stale/drift/low) ada tapi datar — operator tak
   bisa menjawab "node mana yang PALING mahal dibiarkan?", sehingga utang
   menumpuk tanpa antrian kerja dan kuota review terbuang ke node berdampak
   kecil.
2. **Kendala:** skor komposit harus deterministik (Decimal terkuantisasi,
   bobot beku); murni read-only terhadap store; kontrak beku v1–v4 tak
   disentuh; tidak ada perubahan konten tanpa persetujuan operator (jalur
   improvement.propose yang sudah ada); kill switch wajib mengumumkan aksinya.
3. **Tujuan terukur:** (a) skor = Σ wᵢ·sinyalᵢ × dampak, bobot dipin dan
   fixture recompute persis (test `TestScoring`); (b) urutan antrian stabil
   di bawah permutasi input (`test_order_is_stable_under_input_permutation`);
   (c) cap top-K ditegakkan typed (`DebtBudgetError` untuk budget di luar
   [1,100]); (d) nol tulisan ke store untuk list/report (test byte-identik);
   (e) burn-down menghasilkan trend improving/worsening/flat deterministik.
4. **Manfaat:** operator menghabiskan siklus review untuk utang termahal
   dulu; produk — kualitas korpus membaik terukur per siklus lewat ledger
   burn-down.
5. **Pelajaran dari masalah:** skor komposit tanpa bobot beku = angka
   politik; antrian tanpa budget = daftar yang tak pernah selesai; antrian
   yang menulis diam-diam = kegagalan tersambunyi.
6. **Prinsip desain:** (a) bobot beku + fixture recompute; (b) deterministik
   penuh (now selalu diinjeksikan di lapisan murni); (c) read-only terhadap
   graf — ledger burn-down adalah file sidecar, bukan store; (d) degradasi
   graceful: skor/timestamp tak terparse membungkam satu sinyal untuk satu
   node, bukan error; tanpa sinyal → antrian kosong typed.
7. **Kill criteria:** jika korelasi debt↔hasil review nyata tak terbukti
   setelah 2 siklus pemakaian → kembalikan ke katalog sinyal datar
   (review_signals tetap ada); alternatif: debt hanya untuk primary.

## Yang dibangun

- `src/know_your_context/analytics/debt.py` (baru, murni):
  - 4 detektor dengan katalog beku: `contradiction_flagged` (w 0.5),
    `low_score` (0.3, confidence < 0.25), `stale_access` (0.1,
    last_used_at → updated_at → created_at, > 30 hari), `unresolved_ref_old`
    (0.1, pending/failed > 7 hari).
  - severity = Σ bobot sinyal (terkuantisasi 12dp); impact =
    (1+degree)/(1+10); priority = severity × impact (12dp).
  - Urutan: `-priority, -severity, node_id`; budget default 10, maks 100.
  - Aksi deterministik: stale+low → `retire` (set_status deprecate,
    high-impact); kontradiksi → `merge` (set_status superseded, target =
    counterpart terkuat, tie → id terkecil); lainnya → `improve`
    (add_context_tag debt-review, low-impact).
  - Kill switch `KYS_DEBT_QUEUE`: 0/false/no/off/atau tak terparse → fail
    safe disabled dengan envelope typed + alasan.
  - `queue_snapshot` + `burndown` (deltas per pasangan siklus, burned_total,
    net delta, trend) + `build_debt_report` + `render_debt_markdown`.
- `src/know_your_context/ops/debt.py` (baru):
  - `load_debt_inputs` — adapter SQL read-only (pola atm-wave-c2): revisi
    terbaru non-superseded + node_relation + parent_of + unresolved_refs.
  - Ledger burn-down hash-chained JSONL (`knowledge-debt.jsonl` di samping
    store): tamper/rantai putus → `DebtLedgerError` (fail-closed).
  - `run_debt_command` — `debt list|report|propose`; report `--record`
    menambah snapshot siklus; `propose` memanggil `improvement.propose`
    (jalur gated yang sudah ada: draft high-impact tetap butuh approval
    operator sebelum apply — dibuktikan `test_retire_gate`).
- Wiring (di luar scope utama, minimal dan aditif):
  - `cli.py`: subcommand `debt` (OPS-only; tidak masuk KNOWLEDGE/OPS
    OPERATIONS — dibuktikan test).
  - `serve/webview.py`: route `/api/debt` (GET, authenticated, read-only) +
    `WebviewApp.debt_payload()`.
  - `serve/webview_assets/index.html` + `app.js`: tab Debt.
  - `ops/switches.py`: entri registry `debt_queue` (muncul di `/api/switches`
    dan `config`).

## Bukti

- `uv run pytest tests/test_knowledge_debt.py tests/test_debt_cli.py
  tests/test_debt_webview.py -q` → **141 passed** (104 + 26 + 11).
- Tetangga (webview lama, api v2, extraction webview, cli, w12 tools,
  improvement, switches, review_signals, health): **557 passed**.
- Determinisme: `PYTHONHASHSEED=1` dan `=42` → 141 passed identik; test
  permutasi input byte-identik via canonical_json.
- `ruff check` + `ruff format --check` scope: bersih. `pyright` analytics/ +
  ops/ + cli.py + serve/webview.py: 0 errors.
- Read-only dibuktikan dua lapis: byte store identik sebelum/sesudah
  `debt list` (CLI) dan `debt_payload` (webview).

## Yang tidak dilakukan / jujur

- `debt propose` hanya membuat proposal; apply tetap di tangan operator
  (by design — kontrak improvement beku).
- Sinyal `stale_access` bergantung pada `last_used_at`/revisi; node yang tak
  punya timestamp valid tak pernah diflag stale (graceful, terdokumentasi).
- Tidak ada commit (aturan kolam renang).
