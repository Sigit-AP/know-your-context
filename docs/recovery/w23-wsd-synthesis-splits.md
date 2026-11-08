# W23 / WS-D — Synthesis N+1 batch + propose_primary_splits (SUB-162)

TASK2-PLAN WS-D: item **B4** (`propose_primary_splits` = 92% wall ingest) dan
**S1B** (synthesis 12 pass 114,4s @15k simbol). Builder: SUB-162. TDD
(RED→GREEN), tanpa commit. Semua angka dari run nyata di host ini; skrip ukur
di `/root/.hermes/cache/scratch/sub162-wsd/` (perintah persis di §Ledger).

## EXPERT-THINKING — 7 pertanyaan

1. **Masalah**: ingest korpus nyata membuang ~92% wall di
   `propose_primary_splits` (rekonstruksi containment O(N×E) per batch: 56,8M
   akses enum `.value` — terukur cProfile), dan `resolve_and_synthesize`
   membuang 114–200s @15k simbol dengan hasil 0 edge.
2. **Kendala keras**: output edge/proposal byte-identik (dipin test + kill
   criteria plan); kontrak beku v1–v5 tidak disentuh; tidak ada skema baru di
   WS-D (v6 milik WS-B); fail-closed cap name-match tidak boleh berubah
   semantik; PoT ratchet hanya boleh shrink.
3. **Tujuan terukur**: (B4) `_primary_suggestions` ≤10% wall ingest korpus 1×
   (kill: >30% = gagal), proposal diff kosong; (S1B) 12 pass ≤3s @15k, edge
   inserted identik; name_match wall turun ≥10×.
4. **Manfaat**: operator — ingest 145s → puluhan detik; serve flow ingest
   direktori besar tidak lagi didominasi saran proposal.
5. **Pelajaran dari masalah**: atribusi verdict S1B keliru — 12 pass hanya
   3,7s; biaya 144–200s ada di **tier name_match** (`resolve_unmatched`:
   61.816 query SQL = 103,6s + 346.786 `score_name_pair` = 73,7s, 13.426 ref
   → 0 resolved). Klaim "N+1 di module_member_calls" benar secara pola tapi
   salah sebagai sumber wall; dibuktikan dengan timing per-pass
   (`s1b-before.json`) + cProfile name_match (`nm-profile-before.json`).
   Pola kegagalan yang dicegah desain: akumulasi kandidat dengan LIMIT
   tergantung state (`cap+1-len(found)`) harus di-replay byte-identik — pool
   global menyimpan prefix `cap+1` dan replay memotong prefix yang sama.
6. **Prinsip desain**: (a) indeks dipelihara saat insert, bukan dihitung
   ulang saat query (edge append-only → indeks tak bisa drift); (b) replay
   deterministik — data dibatch, logika keputusan per-ref tidak berubah satu
   baris pun; (c) query SQL yang sama persis dipakai ulang untuk pool
   (semantik LIKE/lower tetap di SQLite, bukan di Python); (d) fail-closed
   tetap fail-closed — cap trip menghasilkan keputusan yang sama.
7. **Kill criteria + status**: proposal semantik berubah → STOP (status:
   diff kosong ✓); cProfile after >30% di `_primary_suggestions` → STOP
   (status: ≤ target, lihat ledger ✓); edge synthesis berubah → STOP
   (status: 6/6 skenario byte-identik ✓).

## Fix

### B4 — `graph_model.py`: containment dihitung sekali saat insert
- `UniversalGraph` memelihara `_contains_children` / `_contains_parent`
  (diupdate hanya pada insert edge `contains` yang benar-benar baru; graph
  append-only, tanpa API hapus edge → indeks konsisten oleh konstruksi).
- `contains_children` O(deg) (tetap `sorted`), `contains_parent` O(1),
  `subtree_ids` BFS `deque` (urutan byte-identik), `_check_contains` ikut
  O(depth) karena memakai `contains_parent`.
- `propose_primary_splits`: satu `get_node` per descendant (dulu 3 loop),
  subtree per direct-child dihitung sekali (dulu 2×); jalur size-split
  diekstrak ke `_size_split_proposals` (PoT shrink, nol perubahan perilaku).

### S1B — `synthesis.py`: `module_member_calls` dibatch (pola SUB-135)
- Helper batched join: `_symbols_indexed_by_node`,
  `_outgoing_indexed_by_source`, `_symbols_indexed_by_path` — `IN (?)`
  chunked 500, ORDER BY identik dengan `_adjacency`/`symbols_in_path`.
- Loop kandidat per-ref tidak berubah; hanya sumber datanya (map hasil join).

### S1B (koreksi atribusi) — `name_matcher.py`: tier name-match dibatch
- `_source_languages`: 2 chunked read (symbols by node, files by path)
  menggantikan `symbol_by_node`+`get_file` per ref.
- `_load_candidate_pools`: per language-family, SATU query exact per distinct
  lookup (chunked) + SATU query LIKE per distinct segment (`LIMIT cap+1` —
  maksimum yang pernah bisa diambil query lama, prefix identik).
- `_candidates_from_pools`: replay persis akumulasi lama (exact dulu, lalu
  segmen berurutan, cap fail-closed, prefix `cap+1-len(found)`).
- Decision cache per `(kind, target_name, name_tail, family)` — 13.426 ref →
  ratusan keputusan unik; write path (`retry_failed_ref`,
  `record_resolution_attempt`, edge payloads, urutan outcomes) TIDAK berubah.
- `_ascii_lower` = semantik SQLite `lower()` (ASCII-only) untuk key pool;
  kunci pool exact memakai kolom `lower(s.name)` dari SQL itu sendiri.

## Bukti determinisme & kill criteria

Differential harness (`diff_harness.py`): 5 skenario sudut (resolves,
ambiguity margin, cap-trip 600>500, batas fold non-ASCII `Äpfel`/`äpfel`,
multi-family python/js, unknown kind/source/language) + snapshot nyata
14.896 simbol/13.426 ref — kode HEAD vs kode baru, masing-masing 2×:

| Skenario | old≡new | deterministik | nm resolved | edges |
|---|---|---|---|---|
| resolves | ✓ | ✓ | 25 | 25 |
| ambiguity | ✓ | ✓ | 1 | 1 |
| cap_trip | ✓ | ✓ | 0 (cap) | 0 |
| fold_boundary | ✓ | ✓ | 1 | 1 |
| multifamily | ✓ | ✓ | 2 | 2 |
| snapshot 15k | ✓ | ✓ | 0 | 15.232 |

`diff-report.json`: **ALL IDENTICAL** — diff edge inserted = 0, outcomes
name-match (termasuk skor Fraction) identik, ref states identik.

## Ledger before/after (perintah reproduksi)

Baseline tersimpan: `b4-before.json` (cProfile HEAD, 1× korpus),
`s1b-before.json` (per-pass timing, store 14.896 simbol — ingest 20×
scale-tree cluster-b), `nm-profile-before.json` (cProfile name_match HEAD).

| Metrik | Before | After | Perintah |
|---|---|---|---|
| `_primary_suggestions` wall (1× korpus 238 file) | 137,1s / 154,9s (88,5%); clean-room HEAD: 90,5% dari 111,97s | **0,231s = 1,92% wall** (target ≤10%; kill >30%) | `python after_measure.py` |
| Wall ingest 1× korpus | 111,97s (clean-room HEAD) | **12,18s = 9,2×** (≤15s ✓; basis verdict 145,4s ≈ 13×) | idem |
| enum `__get__` calls di jalur containment | 56.848.770 (41,6s tottime) | 0 (index, `contains_children` 7.240 calls = 0,019s) | pstats di json |
| Proposal output (kill criterion) | 8 suggestions | **byte-identik** (`b4-head` vs `b4-after`) | diff json |
| name_match wall @15k (13.426 ref) | 199,94s (61.816 query) | **3,663s = 54,6×** | `python after_measure.py` |
| synthesis 12 pass @15k | 3,71s (bukan 114,4s — koreksi atribusi) | **1,581s** | idem |
| `resolve_and_synthesize` wall @15k | 144,17s | **≈5,3s ≈ 27×** | `s1b-before.json` vs `s1b-after.json` |
| PoT gate (file scope WS-D) | 3 pelanggaran (fungsi tumbuh) | 0 (shrink via ekstraksi, bukan ratchet-up) | `pytest tests/test_power_of_ten.py` |

Catatan target plan: "12 pass ≤3s @15k" → **1,58s ✓**; "ingest ≤15s (~13×)"
→ **12,18s ✓ (9,2× dari baseline bersih host ini)**; angka 114,4s verdict
ternyata name_match (koreksi atribusi, bukti §EXPERT-THINKING.5) → 54,6×.

## S2B — keputusan keep-ON

Synthesis tetap ON (keputusan plan: tidak mematikan pass tanpa persetujuan
operator). Urgensi biaya hilang: 12 pass ≤ ~1–4s setelah batching; yield 0
pada korpus kode murni didokumentasikan apa adanya (pass bernilai untuk graf
pengetahuan; name_match kini juga murah).

## Gates

- RED→GREEN: `tests/test_graph_model_containment_index.py` (RED: 270.600
  iterasi edge / 300 edge → GREEN: 0), `tests/test_synthesis_query_batching.py`
  (RED: 30× `symbol_by_node` → GREEN: 0), `tests/test_name_match_batching.py`
  (RED: 126 read-SELECT → GREEN: 13; RED diverifikasi ulang terhadap kode HEAD
  via `git worktree` — insiden stash awal tercatat di board).
- Suite area (594p): graph_model 160+, synthesis (4 file), name_matcher 54+1,
  fixture synthesis, batching pins — hijau pasca-shrink.
- ruff check + format + pyright 0/0/0 pada 6 file scope; bandit `-ll` 0;
  `uv lock --check` OK; license_guard OK; kontrak v3/v4/v5 166p hijau.
- PoT: 88/88 — dua entri ratchet yang menjadi stale akibat shrink
  (`resolve_unmatched` 68, `_run_module_member_calls` 64) DIHAPUS dari
  `tools/power_of_ten_justifications.json` (fungsi kini <60 baris).
- Full suite: 16.287 passed / 3 failed — atribusi: (1) PoT stale entries
  milikku → DIPERBAIKI, hijau; (2) `test_verdict.py::test_real_measure_deterministic`
  self-skip \"sibling agent modified src/ mid-measurement\" (by design, wave
  paralel); (3) `test_watcher_async` flake kontensi terdokumentasi — hijau
  standalone (8,26s).
- Determinisme: harness 2-run per skenario × 6 (HEAD-vs-baru) + snapshot 15k,
  byte-identik (`diff-report.json`: ALL IDENTICAL).

## Reproduksi

```bash
# baseline + after (butuh corpus cluster-b; head-tree = worktree HEAD)
git worktree add /root/.hermes/cache/scratch/sub162-wsd/head-tree HEAD
cd /root/.hermes/cache/scratch/sub162-wsd
/root/know-your-context/.venv/bin/python after_measure.py   # b4-head/after + s1b-after
/root/know-your-context/.venv/bin/python diff_harness.py    # kill-criteria diff
```

