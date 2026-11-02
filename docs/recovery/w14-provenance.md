# W14 — Verifiable Provenance Chain (I2) — SUB-127

Laporan build inovasi I2 dari WAVE3-PLAN: rantai provenance 5 tautan yang dapat
diverifikasi mesin untuk setiap node hasil ekstraksi agent (kontrak v4).

## 7 pertanyaan pakar (dijawab SEBELUM kode, sesuai EXPERT-THINKING)

1. **Masalah.** Node hasil `knowledge.submit_extraction` memakai confidence cap
   lebih rendah (tier `agent-extracted`) — tapi tidak ada cara menjawab "node ini
   ada KARENA apa?" secara end-to-end. Bukti asal tercecer di empat artefak
   (node payload, queue sidecar, audit event, domain event) tanpa verifikator
   yang me-re-derive seluruh mata rantai. Jika satu tautan dipalsukan — hash
   sumber di queue diubah, payload diganti, actor diklaim ulang, event audit
   diedit, atau event apply dimanipulasi — tidak ada yang berbunyi.
2. **Kekurangan/kendala.** (a) Kontrak v4 beku: tidak ada kolom/tabel baru di
   store beku; verifikasi murni derivasi ulang dari artefak yang ADA. (b)
   Read-only total: nol tulisan ke store/queue (dibuktikan test byte-level).
   (c) Offline: tidak memerlukan file sumber maupun daemon hidup. (d) Additive
   only di `store.py` (satu helper baca publik) dan `cli.py` (satu subcommand).
   (e) Node lama tanpa provenance tidak boleh berpura-pura valid.
3. **Tujuan terukur.** (a) `verify_provenance(store, node_id)` me-re-derive 5
   tautan: source sha256 → payload hash → capability actor → submission event
   hash → node event hash, dan melaporkan per-tautan ok/broken. (b) Tamper drill
   di SETIAP tautan → status `failed` dengan `broken_link` menunjuk indeks
   tautan yang tepat + typed error per tautan. (c) Verifikasi terbukti nol
   tulisan (byte file db/wal/shm identik). (d) Node legacy → status
   `unverified` jujur (bukan `verified`, bukan crash). (e) CLI
   `provenance verify <node>`: exit 0 verified / 1 failed / 3 unverified.
   (f) ≥100 test hijau; full suite hijau; ruff/pyright/bandit bersih.
4. **Manfaat.** Auditor/operator: klaim provenance berubah dari kepercayaan pada
   pipeline menjadi fakta yang bisa diperiksa mesin, per node, kapan pun,
   tanpa membangun ulang store.
5. **Pelajaran dari masalah.** Integritas parsial (hash di satu titik) memberi
   rasa aman palsu — rantai hanya sekuat tautan terlemah, jadi SEMUA tautan
   di-hash dan di-re-derive. Pola kegagalan yang harus dicegah desain: laporan
   "valid" untuk sesuatu yang tidak diverifikasi → status `unverified`
   eksplisit; verifikator yang menulis → gate read-only; error generik → typed
   error per tautan dengan indeks.
6. **Prinsip desain.** (a) Derivasi ulang deterministik dari artefak yang ada,
   tanpa state baru. (b) Fail closed: tautan yang tak bisa dievaluasi = broken,
   bukan skip. (c) Read-only murni. (d) Jujur tentang ketiadaan: UNVERIFIED ≠
   VERIFIED ≠ FAILED.
7. **Kill criteria.** Jika node historis v4 ternyata tak punya artefak lengkap
   (mis. >5% queue row hilang) → verifikasi tetap benar melaporkan `failed`
   per tautan yang hilang (tidak backfill sembarangan); alternatif degradasi:
   verifikasi 3 tautan (receipt↔audit↔event) tetap tersedia karena tiap tautan
   dievaluasi independen.

## Desain (kontrak beku tak disentuh — semua derivasi)

Lima tautan, masing-masing me-re-derive dari artefak berbeda lalu membandingkan
silang:

| # | Tautan | Artefak yang dicek silang |
|---|--------|---------------------------|
| 0 | `source_sha256` | `node.payload.certainty.source_sha256` ↔ entri `sha256:…` di `node.provenance` ↔ `queue.source_sha256` ↔ `receipt.source_sha256`; opsional re-hash file bila `--root` diberi |
| 1 | `payload_hash` | `sha256(canonical_json(queue.payload))` di-re-derive ↔ `queue.payload_sha256` ↔ `node.payload.extraction.payload_sha256` ↔ `receipt.payload_sha256` |
| `capability_actor` | `method_fingerprint` di-re-derive dari `method_json` ↔ `idem_key` di-re-derive (`source|actor|fingerprint`) ↔ `receipt_id` di-re-derive (`ext-`+sha256(idem_key|payload_sha256)) ↔ entri `agent:…` di `node.provenance` |
| 3 | `submission_event` | event audit `knowledge.submit_extraction` dengan `result_references.receipt_id` = receipt: hash event di-re-derive (`store.fetch_event_chain_state`) ↔ `event_hash` tersimpan; `prev_event_hash` ↔ hash event pendahulu; actor event ↔ `queue.actor`; result_references ↔ queue |
| 4 | `node_event` | event domain apply (`node_id` + `source_version_id = extraction:<receipt>`): hash + prev di-re-derive; payload event memuat blok `certainty.receipt_id` yang sama; actor event = operator `decided_by` |

Typed error per tautan: `SourceHashLinkError`, `PayloadHashLinkError`,
`CapabilityActorLinkError`, `SubmissionEventLinkError`, `NodeEventLinkError`
(semua `ProvenanceChainError` → `SecurityError`, membawa `link_index`/`link_name`).

## Hasil build (terbukti, 2026-11-02)

**File:**
- `src/know_your_context/security/provenance.py` (BARU) — `verify_provenance` /
  `verify_provenance_strict`, `ProvenanceReport`/`LinkReport` (frozen), typed
  error per tautan (`SourceHashLinkError` … `NodeEventLinkError`, semua
  `SecurityError`, membawa `link_index`/`link_name`).
- `src/know_your_context/store.py` (ADDITIVE) — `fetch_event_chain_state`:
  helper baca publik yang me-re-derive hash satu event + prev-nya.
- `src/know_your_context/cli.py` (ADDITIVE) — `provenance verify <node>`
  (`--json`, `--root`); exit 0 verified / 1 failed+unknown / 3 unverified.
- `tests/test_provenance_chain.py` (BARU) — **109 test**.
- `docs/api.md` — baris `provenance` di tabel CLI (gate
  `test_api_documents_every_cli_command`).

**Bukti:**
- TDD: RED nyata (collection error `ModuleNotFoundError:
  know_your_context.security.provenance`) → GREEN 109 passed (17–21s), juga
  hijau pada `PYTHONHASHSEED=42`.
- Tamper drill hidup (CLI nyata, bukan mock): ubah `payload_json` sidecar →
  exit 1, `broken link 1 (payload_hash)` + `PayloadHashLinkError`; node legacy
  (log-event biasa) → exit 3 `unverified`; node hantu → exit 1
  `PROVENANCE_NODE_NOT_FOUND`; chain bersih → exit 0 dengan 5 tautan ok.
- Read-only terbukti byte-level: sha256 file db+wal (dan sidecar) identik
  sebelum/sesudah verify — termasuk saat chain sedang FAILED.
- Tamper matrix ter-parametri: 5 tautan × (terdeteksi dengan indeks tepat,
  tautan sebelumnya tetap ok, strict menaikkan typed error yang tepat).
- 4 proof wajib rencana ada persis: `test_full_chain_rederives_and_matches`,
  `test_tamper_at_each_link_detected_with_index`,
  `test_verification_is_read_only`, `test_missing_link_is_typed_error`.
- Gates: ruff check+format 0 pada file-ku; pyright 0/0/0 (provenance.py,
  store.py, cli.py, test); bandit -ll exit 0.
- Kontrak beku tak disentuh: nol tabel/kolom baru; store.py hanya +1 metode
  baca; semua derivasi dari artefak yang ada.

**Yang mengejutkan / pelajaran:**
- `PRAGMA wal_checkpoint` tidak bisa jalan di dalam `store.transaction()`
  ("database table is locked") — bukti read-only dialihkan ke fingerprint
  file db+wal apa adanya (lebih kuat: tanpa mutasi apapun).
- `-shm` dikecualikan dari bukti byte: ia reader-mark bookkeeping transient,
  bukan konten durable — koneksi pembaca kedua (CLI) sah mengubahnya.
- Celah evasi ditemukan saat penulisan test: dua blok provenance node
  (`certainty.receipt_id` vs `extraction.receipt_id`) bisa berbeda tanpa
  terdeteksi → ditambahkan cek konsistensi antar-blok di tautan 0.
- Sisa kegagalan tetangga di `test_api_documents_every_cli_command` hanyalah
  `debt`/`propose` milik sibling yang belum didokumentasikan pemiliknya —
  `provenance` sudah terdokumentasi.
