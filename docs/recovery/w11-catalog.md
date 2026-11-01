# W11 — Katalog kapabilitas 124 + bench diferensiator (SUB-119, 2026-11-01)

Eksekusi W3.0–W3.4 dari `.superpowers/sdd/2026-10-27-universal-knowledge-graph/WAVE3-PLAN.md`.
Tujuan: composite scorecard 242,52% → ≥400%, jujur dan terukur.

## W3.0 — Amandemen instrumen (suite v1.1.0)

- `bench/scorecard/run.py`: `MAX_CAPABILITIES` 80 → **160** (MIN tetap 60). Formula,
  `status_points`, rentang weight 1–3, 10 kategori, dan aturan pass **tidak berubah**.
- `capabilities.json`: versi 1.0.0 → **1.1.0**, `changelog` di dalam dokumen,
  `capability_limits: {min: 60, max: 160}` tercatat — validator gagal bila dokumen dan
  runner drift (`capability_limits ... drifted from the runner`).
- Dokumen lama dibekukan sebagai `capabilities-v1.0.0.json`; test
  `test_frozen_v1_document_reproduces_baseline` memin T=165, C=53,5, G=45,0 —
  ledger `20261101T105612Z` (composite 242,5236) tetap reproducible.

## W3.1–W3.3 — 52 kapabilitas baru (+107 weight → T=272)

Batch A (16 caps, +37), B (15, +30), C (21, +40 — tabel rencana menjumlah ke 107,
bukan 106; angka ledger yang berlaku). Semua proof test **sudah ada** di suite dan
diverifikasi AST (97 nodeid); `run.py run` mengeksekusi 220 proof unik.

**Penyimpangan jujur dari rencana** (evidence diverifikasi per-baris oleh builder,
bukan hanya menyalin rencana):

| Kapabilitas | Rencana | Kenyataan di repo lawan | Keputusan |
|---|---|---|---|
| `extensibility.protocol_version_handshake` | cg absent | `src/mcp/proxy.ts:418` — MCP initialize menjawab `PROTOCOL_VERSION` (fallback implisit ke versi server) | cg **partial** |
| `integrity.exporter_caps_and_roundtrip` | gf absent | `graphify/exporters/html.py:521` — `ValueError` fail-closed pada node-limit viz default (tapi degradasi aggregated bila eksplisit; tanpa byte-cap typed, tanpa determinisme permutasi) | gf **partial** |
| `operability.watch_cli_policy_gates` | cg absent | `src/bin/codegraph.ts:2243` — `--no-watch` + `CODEGRAPH_NO_WATCHDOG` ada; tanpa interval-floor usage-error gate | cg **partial** |

Tiga downgrade ini merugikan skor (C +1,5 poin lawan, G +1,0) dan diterima apa adanya.

## W3.4 — Bench diferensiator (11 task × 3 target)

`bench/competitive/diff_tasks.py` (driver nyata, exit 0 hanya bila kapabilitas
terukur bekerja) + fase diferensiator di `bench/competitive/run.py` (HARNESS_VERSION 2).

11 task: gated-extraction-submit, audit-chain-verify, rollback-byte-exact,
operator-gated-apply, watermark-ryw, group-commit-burst, retention-policy-audited,
contract-digest-verify, relearn-quarantine-flow, secret-quarantine-scan,
review-decision-policy. Target: fixture, requests, chalk. Lawan: NOT_SUPPORTED dengan
evidence path:line per task (diverifikasi ada di repo lawan).

**T12–T14 rencana (knowledge-debt, provenance-chain, experience-rerank) DICABUT** sesuai
aturan keras rencana §4(4): inovasi I1/I2/I4 belum ada di repo (tidak ada
`retrieval_v2/experience.py`, `extraction/provenance.py`, `analytics/debt.py`, tidak ada
test-nya) — itu scope W3.9 builder lain. Mencatat task yang produknya belum ada sebagai
OK untuk kita = penggelembungan. Konsekuensi: komponen bench 257,14% (33/33 entri baru
NS untuk lawan), bukan 300%; lantai 400% tetap terlampaui tanpanya.

**Keputusan implementasi driver** (semua tercatat di payload/docstring):
- T4/T5/T6/T7/T9/T10 memakai store task-local: kapabilitas yang diukur (gate pipeline,
  protokol watermark, write path, retention, relearn, validator) independen dari isi
  korpus, dan guard kapasitas publish 64 KiB (F-3) adalah batas produksi NYATA yang
  menolak burst pada store besar — terbukti saat pengembangan (PublishCapacityExceeded
  pada store fixture setelah ~70 tulisan). Guard tidak dilewati; driver disesuaikan.
- T6 memakai pool 20 node yang ditulis ulang (pola storm gate), bukan pertumbuhan
  tak terbatas — alasan yang sama.

## Angka (dari ledger mesin, bukan model)

- Bench kompetitif `ledger-20261101T174703Z` (exit 0): know_your_context **OK=54**
  (21 base + 33 diferensiator), FAILED=0; codegraph OK=21 + NOT_SUPPORTED=33;
  graphify OK=21 + NOT_SUPPORTED=33 → bench relative **257,1429%** vs kedua lawan.
- Scorecard `ledger-20261101T175636Z` dan `ledger-20261101T180010Z` (keduanya exit 0):
  proofs **220/220 hijau**; T=272, points cg=55,0 gf=46,0; relative 494,5452% /
  591,3031%; final 399,5843% / 457,6390%; **composite 428,6116%**.
- Determinisme: dua run berturut — semua angka skor identik.
- Baseline lama (v1.0.0, 72 caps): 242,5236% → **428,6116%** (+186 poin).

## Gates

- `python bench/scorecard/run.py check` → exit 0 (124 caps, 220 proofs, T=272)
- `python bench/scorecard/run.py run` → exit 0, semua proof hijau
- `pytest tests/test_scorecard.py tests/test_competitive_bench.py` → hijau
- `ruff check` + `ruff format --check` + `pyright` pada file scope → bersih
- `python bench/competitive/run.py run` → exit 0 (63 + 33×3 = 162 entri)
