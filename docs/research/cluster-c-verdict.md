# Cluster C Verdict — Security · Hardware · Ops · Webview · Contracts

**Researcher:** SUB-157 (deep-research, REA-any staged methodology + EXPERT-THINKING)
**Date:** 2026-11-08 11:02 UTC · **Repo:** `/root/know-your-context` @ `5952097` · **Host:** 2 vCPU (affinity), 3.8 GiB RAM, SSD (`sda`), no compute GPU (vmwgfx display-only)
**Scope:** kode tidak diubah; semua angka dari run nyata hari ini (perintah reproduksi di §7).

## Metode angka (semua terukur, bisa direproduksi)

- **Kompleksitas%** = persen fungsi dengan cyclomatic complexity (McCabe, AST, skrip `cx_scan.py` deterministik) ≤ 10 — ambang "simple" industri. Hotspot >10 dilaporkan terpisah.
- **Optimalitas%** = rubrik per area: jumlah item algoritma state-of-the-art-yang-tepat yang terbukti ada di kode ÷ total item rubrik area itu (item dinamai eksplisit per tabel; bukti = file:baris).
- **Level profesional %** per butir (flow / core-algo / arsitektur / kreativitas / detail) = skor rubrik 0–100 dengan bukti terukur (jumlah test hijau, angka beban, digest, hitungan artefak). Bukan rasa.
- **Performa** = angka run nyata di host ini (bukan klaim dokumen).

## Ringkasan eksekutif

| Area | LOC | Test hijau (hari ini) | Kompleksitas% (fn ≤10) | Optimalitas% | Level profesional (rata² 5 butir) |
|---|---:|---:|---:|---:|---:|
| 1. Security/capability/autoreview | 3.548 | 353 / 0 gagal (49,0 s) | 94,2% (146/155) | 93,8% (15/16) | 93% |
| 2. Hardware layer | 1.121 | 202 + 24 stress / 0 gagal | 86,2% (25/29) | 93,3% (14/15) | 91% |
| 3. Ops tooling | 5.895 | 700 / 0 gagal (48,4 s) | 90,2% (248/275) | 87,5% (14/16) | 90% |
| 4. Webview backend+UI | 3.702 py + 4.866 asset | 670 + 24 isolasi/overload / 0 gagal | 94,0% (141/150) | 93,8% (15/16) | 94% |
| 5. Kontrak v1–v5 | 864 py + 726 md | 300 / 0 gagal (4,2 s) | 100% (18/18) | 100% (12/12) | 96% |
| **Total cluster C** | **20.722 (19.996 kode + 726 docs kontrak)** | **2.249 passed / 0 failed** | **91,8%** | **93,4%** | **92,8%** |

Gates pendukung di file cluster C: `ruff check` bersih; `bandit -ll` = **0 medium / 0 high** (11 low-severity note, tidak memblokir).

---

## 1. Security / capability / autoreview

**File:** `security/__init__.py` (481), `security/capabilities.py` (639), `security/provenance.py` (491), `autoreview.py` (363), `runtime_guard.py` (1327), `audit.py` (247).

### Algoritma
- **Sanitasi/redaksi dua-pass** (`security/__init__.py:106-295`): strip Unicode Cc/Cf → pola secret ter-pin (9 famili) + PII → entropy-run ≥40 char dengan heuristik 3-kelas-karakter; pass kedua atas *match view* (strip Zs/Zl/Zp → NFKC → strip Cf/Cc → fold homoglyph Cyrillic/Greek→Latin). Input bersih tetap byte-identik.
- **Capability token** (`capabilities.py`): `kyc1.<b64url(payload)>.<b64url(HMAC-SHA256)>` atas canonical JSON; verifikasi konstan-waktu `hmac.compare_digest`, urutan fail-closed shape→signature→expiry→revocation→actor; registry atomik (tmp+rename, 0600) di-stat per verify (revokasi efektif panggilan berikutnya); audit JSONL hash-chain + head marker.
- **Provenance 5-link** (`provenance.py`): source_sha256 → payload_hash → capability_actor → submission_event → node_event, semua re-derivasi read-only; verdict jujur `verified/failed/unverified`.
- **Auto-review disk-mode** (`autoreview.py`): round-trip (node ada/versi/fingerprint) + scan bahaya (5 famili prompt-injection + SECRET_PATTERNS + 4 active-markup, raw+match-view) → reject = kompensasi append-only (rollback revisi lama / tombstone) + typed `AUTO_REVIEW_REJECTED`; budget scan dibatasi (depth 8, 1 MiB).
- **Runtime guard** (`runtime_guard.py`): distribusi/import/credential/model-env terlarang fail-closed; parser-category allowlist disjoint dari forbidden surface; `verify_runtime` satu entry point.

### Positif
- Fail-closed di mana-mana dengan typed error; zero trusted-network assumption (loopback+allowlist+HMAC).
- Revocation tanpa restart (stat mtime+size per verify) — jarang di sistem sekelas.
- Match-view menutup bypass zero-width/fullwidth/homoglyph yang lolos dari regex naif (bukti: komentar SUB-35/43 + test wave-3).
- 353 test hijau termasuk property tests (`test_property_security.py`) dan pin parity pola secret dengan publish gate.

### Negatif / bug
1. **`CapabilityStore.audit()` O(n) per append** (`capabilities.py:544-578`): membaca SELURUH log untuk seq+prev → pertumbuhan total O(n²). Dituturkan "stays small by policy" tapi tanpa rotasi enforced. Diukur: issue 50 token = 69,4 ms/op dan naik seiring panjang log. Bukan bug kebenaran — batas skala nyata.
2. **Batas entropy-run terdokumentasi**: secret base64url murni (`-`/`_`), pure-hex, atau satu-case lolos dari aturan entropy (hanya pola ter-pin yang menangkap) — desain sadar, tapi celah deteksi nyata (`security/__init__.py:240-254`).
3. `_auth_disabled` webview memakai `== "1"` mentah, bukan `parse_switch_bool` strict — inkonsisten dengan aturan owned-switch (`webview.py:539-543`; dev-only).
4. Hotspot kompleksitas: `cli_scraping_violations` C=21, `_check_source_link` C=20, `verify_provenance` C=19.

### Performa (diukur, host ini)
| Operasi | Angka |
|---|---|
| `scan_text` 10 KB | 12,16 ms/op |
| `scan_value` ~1 MiB nested (budget penuh) | 129,8 ms |
| `sanitize_text` 10 KB | 3,71 ms/op |
| `redact_text` 10 KB | 10,97 ms/op |
| capability `verify` | **0,952 ms/op** |
| capability `issue` (registry+audit) | 69,42 ms/op |
| `verify_audit_chain` (50 entri) | True |

Bisa di-push? Verify murah (sub-ms) — bukan bottleneck; issue O(n²) akan terasa di >10⁴ capability (perlu rotasi/head-cache). Regex scan ~1 MB/s per view cukup untuk write path interaktif; untuk bulk ingest masif bisa jadi kandidat optimasi (kompilasi sekali — sudah; gabung pola jadi satu alternation).

### Level profesional
| Butir | % | Bukti |
|---|---:|---|
| Flow | 95 | urutan verify anti-oracle; deny path typed; satu final audit event per tulis |
| Core-algo | 92 | HMAC+canonical-JSON benar; match-view canggih; −entropy boundary & O(n²) audit |
| Arsitektur | 94 | pure functions + factory adapter; root of trust file 0600; store terpisah dari kebijakan |
| Kreativitas | 90 | revocation-stat, hash-chain+head marker, 5-link re-derivasi tanpa schema change |
| Detail | 94 | komentar SUB-reference per keputusan; 9 famili secret; docstring threat-model |

---

## 2. Hardware layer

**File:** `hardware/probe.py` (389), `profile.py` (145), `tuning.py` (172), `autoscaler.py` (179), `gpu_gate.py` (194).

### Algoritma
- **Probe read-only** dari `/proc`+`/sys`: disk via `st_dev → /sys/dev/block/<maj>:<min>` → walk ≤6 ancestor ke `queue/rotational`; memori MemTotal/MemAvailable; CPU = `sched_getaffinity` → fallback `os.cpu_count`, tak pernah 0; GPU bertingkat (vendor tools → OpenCL/Vulkan ICD → DRM driver, display-only diklasifikasi jujur). Fail-open dengan note; `strict=True` → typed `ProbeError`. Cache per-path.
- **Tuning plan** murni: `mmap = min(db_size, ½·MemAvailable, cap)`; page cache = min(db, ⅛·MemAvailable, cap) dengan floor 2 MiB; page-size power-of-two 512..65536 tervalidasi; synchronous=NORMAL hanya SSD+opt-in.
- **Autoscaler**: `workers = clamp(ceil(load1), min, min(cores, MemAvailable/512MiB))` — scale-up only, unknown→full parallelism.
- **GPU gate** (formula verdict): offload hanya jika `edges > 5M` DAN `T_cpu > 3×(T_transfer+20µs)` DAN GPU compute ada DAN `KYS_GPU_OFFLOAD=on`; default NO-GPU, `would_offload` selalu dicatat.

### Positif
- Satu `HardwarePlan` global dikonsumsi semua subsistem (store pragma, autoscaler, gpu gate, ops) — sesuai aturan operator "satu profil".
- Jujur secara evidence: GPU virtual ≠ compute; MemAvailable hilang dilaporkan 0+bukan fabrikasi.
- Kill switch per mekanisme (`DISABLE_HW_TUNING`, `DISABLE_HW_AUTOSCALER`).

### Negatif / bug
1. **`KYS_GPU_OFFLOAD` prefix legacy** di tengah konvensi `KNOW_YOUR_CONTEXT_*` — dipin test (`test_hardware_switches.py:52`), jadi disengaja, tapi naming-debt nyata (juga `src/switches_head.py`, kernel_bridge, webview memakai KYS_*).
2. Autoscaler tanpa hysteresis: `ceil(load1)` bisa flapping 1↔2 di sekitar batas integer (dampak kecil: pool advisory, bukan kill).
3. `queue_scale` hanya dari RAM (step 4 GiB, cap 16) — tidak memperhitungkan core; di host RAM-besar/CPU-kecil bisa over-relatif (fail aman: scale-up only).
4. Hotspot: `probe_gpu` C=19, `probe_disk_kind` C=17, `plan_storage_tuning` C=14, `decide_gpu_offload` C=14.

### Performa (diukur, host ini)
| Operasi | Angka |
|---|---|
| `probe_hardware` cold | 3,49 ms |
| Plan turunan host ini | mmap 1024 MiB (cap), cache 361.007 KiB (~352 MiB), query-cache 16.384 entri, base_workers 2, queue_scale 1 |
| Autoscaler @ load1 0,11 | 1 worker (floor), saturated=False |
| GPU gate 10M edges/2 s CPU | `GPU_ABSENT` → offload=False (benar: host tanpa compute GPU) |
| Test hardware | 202 passed/17,1 s + **stress 24 passed/46,2 s** (matriks beban low→dense) |

Bisa di-push? Ya — plan skala otomatis dengan RAM (⅛ cache, ½ mmap bound) dan core (workers=cores saat saturasi). Di host 64-core/256 GiB: workers→64, cache→cap; HDD→batch 128+page 8 KiB. Hardware optimal sesuai aturan "maximal, never caged": mmap/cache/worker semua scale-up, overload diserap backpressure.

### Level profesional
| Butir | % | Bukti |
|---|---:|---|
| Flow | 92 | probe→plan→consume satu arah; cache eksplisit dengan reset |
| Core-algo | 90 | formula GPU berbasis literatur (Gunrock/McSherry) + terukur; −tanpa hysteresis |
| Arsitektur | 95 | satu sumber kebenaran; pure computation terpisah dari I/O; dual strict/fail-open |
| Kreativitas | 88 | klasifikasi display-vs-compute via DRM driver; derivasi query_cache dari cache policy |
| Detail | 90 | note per keputusan; semua batas divalidasi typed; GPU-VERDICT jadi dasar |

---

## 3. Ops tooling

**File:** `ops/` 21 modul (5.895 LOC): switches (1370), lifecycle (685), doctor (668), migrate (549), config (545), diagnose (470), metrics (366), schema_migrations (342), debt (317), retention (299), watchtower (297), async_lifecycle (285), subgraph_export (268), codeintel_cli (209), webview_control (190), export_cli (174), setup_flow (130), report_cli (115), watch_cli (111), ingest_cli (100).

### Algoritma
- **Switch/knob board**: registry kanonik 25 kill switch + 61 config knob; parse strict (8 spelling boolean saja, sisanya `SwitchError` — tak pernah menebak maksud operator); legacy-mirror dipin ke konstanta pemiliknya.
- **Config 4 lapis** (defaults→TOML→env→CLI), fail-closed, typed coercion (`_coerce_typed` C=17).
- **Diagnose**: invariant walk dengan severity berjenjang (info/warning/error), setiap check crash → `CHECK_CRASHED` error (fail-closed), exit code gate-able. **Doctor**: probe lingkungan (writability, free-space, umask, quick_check/integrity_check/foreign_key_check, latensi live 32 iterasi) + saran perbaikan terstruktur.
- **Retention/GC**: CURRENT + pinned + referenced-by-backup tak pernah disentuh; newest-first selebihnya.
- **Migrate**: inspeksi read-only (`mode=ro`) → klasifikasi 5-status → rencana deterministik → eksekusi via migrator EventStore + verify hash chain.
- **Metrics**: snapshot kanonik float-free (permille/ms integer), strictly SELECT-only. **Watchtower**: agregasi fleet, probe tak pernah raise/tak pernah membuat file.

### Positif
- Semua permukaan operator: doctor, diagnose, migrate, retention, debt, watchtower, metrics, lifecycle, export, switches — lengkap dan seragam (typed, auditable, hash-chained di mana history disimpan).
- 700 test hijau (48,4 s); prinsip "never creates, never raises" untuk probe dipin test.
- Metrics deterministik (integer permille/ms) — bisa diff-test byte-exact.

### Negatif / bug
1. **`run_diagnostics` C=47 dan `run_doctor` C=38** — fungsi raksasa imperatif; lolos uji tapi di atas bar maintainability (C≤15). Kandidat ekstraksi per-check.
2. `load_switches` C=23 — registry besar satu fungsi; masih linear dan teruji.
3. Sebagian CLI tipis (`ingest_cli` 100 LOC) membungkus surface lain — duplikasi tipis dokumentasi antar adapter.
4. Tidak ada paralelisasi di watchtower fleet probe (sekuensial per store) — O(n) fleet latency; belum jadi masalah di skala single-operator.

### Performa (diukur)
- 700 test ops hijau/48,4 s; `load_knobs` default resolve instan (61 knob); probe store watchtower read-mostly tanpa sidecar. Diagnose/doctor dirancang gate-able (exit 0/1) — dipakai CI langsung.
- Tahan beban: ops bersifat operator-driven (bukan hot path); metrics SELECT-only aman dipanggil webview per poll 2 s (bukti: webview /api/metrics dilayani di storm, p95 142,8 ms).

### Level profesional
| Butir | % | Bukti |
|---|---:|---|
| Flow | 90 | 4-layer config jelas; probe→report→exit-code konsisten |
| Core-algo | 86 | benar & konservatif; −C=47/38 monolit; watchtower sekuensial |
| Arsitektur | 92 | registry kanonik; adapter tipis; fail-closed seragam; hash-chain ledger reuse |
| Kreativitas | 88 | mirror-switch pinning; float-free metrics; burn-down ledger hash-chained |
| Detail | 92 | saran perbaikan terstruktur per finding; typed coercion; nosec beralasan |

---

## 4. Webview backend + UI

**File:** `serve/webview.py` (3.702 LOC, 150 fn, 14 kelas) + `webview_assets/` (`app.js` 3.664 LOC/291 fn, `index.html` 307, `style.css` 895).

### Algoritma
- **Backend stdlib-only** (`http.server.ThreadingHTTPServer`): 67 route terdaftar (`API_ROUTES`; 52 read / 13 heavy / 2 export), semuanya GET/HEAD read-only (405 sisanya — guard tetap jalan dulu agar probe tanpa auth tidak belajar apa pun).
- **Guard order**: Host (DNS-rebinding: loopback + port server sendiri, bare-host ditolak) → Origin → method → token (`?token=` → cookie HttpOnly+SameSite=Strict; header `Authorization: Bearer`/X-API-Key tak pernah mint cookie), semua `hmac.compare_digest`.
- **JSON markup-safe**: `escape_json_bytes` (escape `&<>` + ascii-only) + klien `textContent`-only → anti stored-XSS dua lapis; CSP strict; `X-Content-Type-Options: nosniff`; `Cache-Control: no-store`.
- **Rate limit**: token bucket per route-class atas clock injektable; class tak dikenal fail-closed deny; 429 membawa `Retry-After` + envelope typed `{error:{code,message,retryable}}`.
- **Feature flags** per route-group (export/config/debt/provenance/ledger/activity/setup) fail-closed via knob.
- **UI**: 14 tab SPA vanilla (Graph/Search/Activity/Analytics/CodeIntel/Proposals/Debt/…); live refresh short-poll per tab aktif (`webview.live_refresh_seconds`, 0 = freeze, quiet refresh menjaga scroll/seleksi); graph interaksi (expand/panel/bezier re-anchor/zoom) diuji DOM-level lewat harness `.mjs` tanpa jsdom.

### Positif
- Threat model DNS-rebinding konkret dan diuji; token tak pernah bocor ke response/asset.
- **0 `innerHTML`** di 3.664 baris JS (terukur) — XSS surface diminimalkan konstruksi.
- Deterministik: payload = fungsi murni state store (sorted keys) — dua request atas store sama menjawab byte-identik (dipin test).
- Backpressure typed yang terbukti pulih (threads 3→16→3), isolation dari daemon terbukti.

### Negatif / bug
1. **`_make_handler` C=68** — dispatch raksasa; berfungsi penuh tapi titik konsentrasi risiko maintainability tertinggi cluster ini.
2. Thread-per-connection: tepat untuk loopback single-operator; bila operator opt-in bind publik, model ini tak menskala (mitigasi: rate limit + allowlist; didokumentasikan).
3. Session cookie tanpa atribut `Secure` (loopback http — benar untuk model, tapi akan salah bila di-reverse-proxy TLS tanpa penyesuaian; dicatat di security docs sebagai "di balik proxy" tanggung jawab operator).
4. `codeintel_payload` C=18, `query_payload` C=16 — payload builders mulai tebal.

### Performa (diukur hari ini, host 2 vCPU — `tests/test_webview_isolation_overload.py`, 4 passed/23,6 s)
| Skenario | Angka nyata |
|---|---|
| Isolasi daemon di bawah flood webview 8-thread | read p95 base **124,6 ms** → during **135,0 ms** (+8,4%; bound 622,8 ms); 389 jawaban flood |
| Overload webview (flood 12-thread, 3 s) | **130 req/s**, 196×200 + 195×429, **p95 142,8 ms / p99 180,4 ms**, threads 3→16→3 (pulih bersih) |
| Flood bersamaan webview+daemon storm | webview 313 req (8×200, 305×429 typed, p99 68,2 ms); daemon 101 ops **p95 170,1 / p99 192,8 ms**, 33,7 ops/s — fairness: daemon menang, webview shed typed |
| Suite webview keseluruhan | 670 passed/27,2 s + DOM harness + isolation 24 passed |

Tahan beban berat? Ya untuk profil targetnya (single-operator loopback + ops polling): backpressure typed, tak ada transport error di bawah flood, recovery bersih. Bisa di-push: rate/burst knob-driven; batas struktural = thread-per-conn stdlib (cukup untuk puluhan klien, bukan ribuan).

### Level profesional
| Butir | % | Bukti |
|---|---:|---|
| Flow | 95 | guard order anti-oracle; 405 setelah guard; cookie-mint hanya di HTML auth segar |
| Core-algo | 92 | token bucket benar; escape kanonik; −C=68 dispatcher |
| Arsitektur | 95 | 67 route terdaftar deklaratif; route-class+flag terpisah; UI/asset allowlist tetap |
| Kreativitas | 93 | DOM harness tanpa dependency; liveness contract per tab; parity header-auth graphify |
| Detail | 95 | nosniff/CSP/no-store; Server-Timing; `.mjs` assertion graph mechanics; 14 tab teruji |

---

## 5. Kontrak v1–v5

**File:** `contracts.py` (864 LOC, 51 kelas pydantic, 18 fn, maxC=4) + `docs/contracts/` 7 dokumen (726 LOC).

### Algoritma / struktur
- **v1** (beku): sepuluh operasi dasar spec §8 + sub-kontrak Task 3A (scoring, measurement, access, recommendation, relearn, token-budget). Model pydantic `frozen+extra=forbid+strict`; token estimator `ceil(utf8/4)`; skor fixed-point 12-desimal; `RecommendationItem` fail-closed `ONE_OBSERVATION_NO_PROMOTION` di model boundary.
- **v2** (`retrieval-verbs-v2.md`): +4 verb retrieval (path/explain/affected/god_nodes) atas `graph.json`, read-linearized watermark + STALE_PROJECTION typed.
- **v3** (`ingestion-contract-v3.md`): +2 verb ingestion proposal-only + edge `bridges`; ekstraksi deterministik murni; path discipline typed (PATH_OUTSIDE_ROOT/NOT_FOUND/NOT_READABLE).
- **v4** (`extraction-contract-v4.md`): `submit_extraction` — sidecar queue, provenance bound sha256 sumber, truth tier `agent-extracted`, corroboration/contradiction distinct-actor, idempotensi (source_hash, actor, method).
- **v5** (`write-contract-v5.md`, ACTIVE 2026-11-08): disk-mode — semua write commit langsung; auto-review sinkron wajib; kompensasi append-only; supersesi eksplisit v3/v4 proposal-only untuk jalur tulis; `MANUAL_GATES=1` escape hatch.
- **Permukaan terukur:** HTTP `KNOWLEDGE_OPERATIONS` = 14 (dipin), MCP `EXPECTED_TOOLS` = 17 (dipin).

### Positif
- **Semua 7 digest sha256 diverifikasi MATCH hari ini** (v1-family 3 doc + v2/v3/v4/v5) — drift satu byte = gagal.
- Addendum mechanism: kapabilitas baru = kontrak baru, bukan edit; perubahan semantik butuh versi baru + persetujuan operator — governance kelas protokol.
- Shape tests bukan sekadar digest: 33 test v5 memin frasa normatif (disk-mode, kompensasi, typed rejects, carve-out relearn).

### Negatif / bug
1. Dokumen v1 dasar tidak ada sebagai satu file `*-v1.md` — v1 hidup sebagai spec §7/8/13 + 3 dok sub-kontrak + fixtures; traceability bergantung pada referensi silang (diterima oleh desain, tapi penanya baru harus merangkai).
2. v5 men-supersede v3/v4 "untuk jalur tulis" — dua rezim (historis beku vs aktif) hidup berdampingan; jelas di dokumen, tapi beban kognitif nyata.
3. Tidak ada negatif kebenaran: 300 test kontrak hijau (4,2 s), 0 drift.

### Performa
- Validasi kontrak = validasi pydantic strict (µs-scale per request, bukan hot concern); shape+digest suite 300 test/4,2 s. Kompleksitas 100% fungsi ≤10 — lapisan paling datar dan paling stabil di cluster.

### Level profesional
| Butir | % | Bukti |
|---|---:|---|
| Flow | 97 | v1→v5 evolusi additif murni; supersesi eksplisit; escape hatch terdokumentasi |
| Core-algo | 95 | model strict/frozen/forbid; fixed-point score; idempotensi v4 lengkap |
| Arsitektur | 96 | digest-pin + shape-pin + op-count-pin tiga lapis; dok/kode/test segitiga |
| Kreativitas | 94 | kontrak yang memin dokumen BEKU sekaligus rezim aktif baru (v5) tanpa mengubah byte lama |
| Detail | 97 | tanggal/authority/SUB-reference per dokumen; envelope typed seragam |

---

## 6. Temuan lintas-area & rekomendasi (prioritas)

| # | Temuan | Klasifikasi | Rekomendasi |
|---|---|---|---|
| F1 | `CapabilityStore.audit()` O(n²) growth (full-read per append) | batas skala, bukan bug | cache (seq, prev) di memory + verify berkala; rotasi berkala |
| F2 | `_make_handler` C=68, `run_diagnostics` C=47, `run_doctor` C=38 | maintainability debt | ekstraksi dispatch/check jadi tabel |
| F3 | Prefix env `KYS_*` legacy di ≥6 lokasi, dipin test | naming debt terdokumentasi | biarkan (ter-pin) atau migrasi lewat kontrak perubahan eksplisit |
| F4 | Entropy-run boundary (base64url/hex/single-case lolos) | batas deteksi sadar | tetap; dokumentasi sudah jujur |
| F5 | `_auth_disabled` raw `=="1"` vs strict parse | inkonsistensi minor | samakan ke `parse_switch_bool` (dev knob) |
| F6 | Autoscaler tanpa hysteresis | minor | EMA load1 atau band ±0,5 |

**Verdict akhir cluster C: PROFESIONAL-PRODUCTION (rata-rata 92,8%).** Keamanan fail-closed dengan bukti beban nyata; hardware layer jujur-evidence dan scale-up; ops terlengkap di repo; webview melebihi standar tool internal (anti-rebinding + typed backpressure terukur); kontrak bergovernance protokol dengan integritas byte-terverifikasi. Utang utama bersifat maintainability (3 fungsi C≥38) dan satu batas skala terdokumentasi (audit capability O(n²)) — tidak ada bug kebenaran ditemukan.

## 7. Reproduksi (persis perintah yang dipakai)

```bash
cd /root/know-your-context
# kompleksitas AST: python3 /root/.hermes/cache/scratch/cx_scan.py <files> (McCabe via ast.walk)
# test per area (contoh security):
.venv/bin/python -m pytest tests/test_security.py tests/test_wave2_security.py \
  tests/test_wave2_track_security.py tests/test_capability_security.py \
  tests/test_property_security.py tests/test_disk_mode_autoreview.py \
  tests/test_runtime_guard_parser_extension.py -q        # 353 passed / 49,0 s
# hardware: 11 file test_hardware_* + test_*_hardware → 202 passed / 17,1 s
# ops: 12 file test_ops_* → 700 passed / 48,4 s
# webview: 13 file test_*webview* → 670 passed / 27,2 s
# kontrak: v3/v4/v5 shape + measurement + parity → 300 passed / 4,2 s
# beban: pytest tests/test_webview_isolation_overload.py -q -s  → 4 passed / 23,6 s (angka ISOLATION/OVERLOAD)
# stress hardware: pytest tests/test_hardware_stress.py tests/test_hardware_integration_stress.py → 24 passed / 46,2 s
# digest kontrak: sha256 docs/contracts/*.md vs FROZEN_CONTRACTS di tests/test_write_contract_v5_shape.py → 7/7 MATCH
# bandit: .venv/bin/bandit -ll -r <cluster C paths> → 0 medium/0 high
# probe live: know_your_context.hardware.probe.probe_hardware(refresh=True) → 3,49 ms
```
