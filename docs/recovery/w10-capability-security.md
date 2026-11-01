# W10 — Capability-Based Security untuk Agent (SUB-117)

Status: IN-PROGRESS
Scope: `src/know_your_context/security/capabilities.py` (baru), `security.py` →
`security/__init__.py` (move murni, import path identik), integrasi additif
`serve/rpc.py` + `serve/daemon_config.py` + `serve/superpower.py` + `cli.py`,
`tests/test_capability_security.py`, dokumen ini.

Catatan referensi: `DEEP-SOTA.md` yang disebut di brief TIDAK ada di repo
(verified: `find` + ripgrep 0 hit). Pola yang dipakai adalah capability
security SOTA umum: bearer capability token HMAC-signed (garis keturunan
macaroons/Biscuit), disesuaikan dengan house rules repo ini (fail closed,
typed errors, determinisme, audit).

## 7 Pertanyaan Pakar (EXPERT-THINKING.md) — dijawab SEBELUM menulis kode

1. **Masalah**: setiap agent yang terhubung ke superpower daemon punya hak
   yang sama — satu agent jahat/salah-prompt bisa memanggil verb tulis apa
   pun (`knowledge_apply_improvement`, `knowledge_rollback`, ingest) atas
   nama siapa pun; yang ada hanya `actor` self-asserted (audit, bukan
   otorisasi). Tidak ada mekanisme untuk membatasi agent ke subset verb,
   mencabut aksesnya, atau memberi masa berlaku.
2. **Kekurangan/kendala**: (a) kontrak beku (14+2+1 tool surface, skema
   store, envelope JSON-RPC) tidak boleh berubah; (b) mode default HARUS
   perilaku sekarang byte-identik (backward compatible); (c) tidak ada
   dependensi baru; (d) revokasi harus efektif seketika pada daemon yang
   sedang berjalan; (e) verifikasi tidak boleh membuka timing side-channel
   (konstan-waktu); (f) tidak ada jaringan — semua lokal.
3. **Tujuan terukur**: 120+ test hijau yang membuktikan: forgery ditolak,
   expiry ditolak, revokasi langsung efektif (tanpa restart daemon), verb di
   luar allowlist ditolak, scope read tidak bisa tulis, setiap penolakan
   teraudit di hash-chained log, penerbitan deterministik. Gate: fokus hijau,
   full suite hijau, ruff/pyright/bandit bersih.
4. **Manfaat**: operator bisa menjalankan campuran agent dengan kepercayaan
   berbeda (agent tak dikenal = read-only atau tanpa token sama sekali;
   agent terpercaya = write scoped + berakhir otomatis; admin hanya untuk
   operator). Satu agent terkompromi tidak lagi berarti seluruh knowledge
   base bisa ditulis-ulang.
5. **Pelajaran dari masalah**: pola kegagalan yang harus dicegah desain —
   (a) otorisasi berbasis identitas self-asserted (actor string) → token
   mengikat actor ke tanda tangan; (b) denylist yang basi → revokasi
   berbasis file dengan reload-on-change (mtime_ns+size), efektif pada
   verifikasi berikutnya; (c) fail-open saat komponen hilang → secret
   hilang/korup = semua non-publik ditolak; (d) audit terpisah dari store
   domain agar penolakan tercatat walau store sibuk.
6. **Prinsip desain**:
   - Fail closed di semua jalur keamanan; fail open TIDAK ADA di mode
     enforce. Mode off = tepat perilaku lama (kill switch total).
   - Token self-contained: HMAC-SHA256 atas payload kanonik
     {actor, verbs, scope, expires_at, nonce, issued_at}; verifikasi
     `hmac.compare_digest`; tidak ada lookup jaringan.
   - Root of trust = file secret 0600 di runtime dir daemon; pemegang file
     (operator, same-uid) bisa issue/revoke. Yang dipelajari tetap lokal.
   - Audit security terpisah (JSONL hash-chained), bukan store domain:
     penolakan capability harus tercatat bahkan saat writer lane penuh.
7. **Kill criteria**: jika revokasi-seketika tidak bisa dibuktikan tanpa
   restart daemon, atau integrasi memaksa perubahan kontrak beku, berhenti
   dan laporkan — alternatif: otorisasi di proxy stdio saja (lebih lemah,
   tidak menutup klien socket langsung).

## Desain

- **Token**: `kyc1.<b64url(payload-kanonik)>.<b64url(hmac-sha256)>`.
  Payload kanonik (sort_keys, separators rapat) → penerbitan deterministik.
- **Scope → lane**: `read` ⊇ {read}; `write` ⊇ {read, write, heavy};
  `admin` ⊇ semuanya + verb `daemon.*`. Allowlist `verbs` di token membatasi
  lebih lanjut (intersection semantics).
- **Enforce mode** (`DaemonConfig.capability_mode`, default `"off"`):
  tanpa token → hanya verb READ publik; token invalid/forged/expired/
  revoked/actor-mismatch/verb-di-luar-allowlist → typed
  `CAPABILITY_DENIED` (-32009) + audit event. `daemon.shutdown` butuh scope
  admin; `daemon.status` tetap publik (monitoring).
- **CLI**: `capability issue|revoke|list` (ops surface; memakai
  `--store` untuk menemukan runtime dir yang sama dengan daemon).
  `daemon stop` menyematkan token admin yang diterbitkan lokal bila secret
  ada, agar graceful stop tetap jalan di mode enforce.
- **Audit**: `capability-audit.jsonl` hash-chained (seq, prev sha256,
  event) — issue, revoke, dan setiap denial.

## Bukti

- **RED nyata**: collection error (`ImportError: CapabilityDenied`,
  `ModuleNotFoundError: security.capabilities`) sebelum implementasi —
  pytest exit 2.
- **GREEN**: `pytest tests/test_capability_security.py` → **157 passed**
  (target 120+), exit 0, ~35 s. Tiga kegagalan TDD nyata ditemukan dan
  diperbaiki: (1) `float()` melaundry `True` → expiry bool lolos (divalidasi
  sebelum koersi); (2) `verbs="string"` ter-iterasi per karakter (ditolak
  eksplisit); (3) `zip()` tanpa strict.
- **Suite tetangga**: test_serve_daemon/protocol/config/cli + test_cli =
  139 passed exit 0.
- **Ruff**: `ruff check` + `ruff format --check` bersih pada semua file
  scope-ku (security/, rpc.py, daemon_config.py, superpower.py, cli.py,
  test_capability_security.py).
- **Pyright**: 0 error / 0 warning pada semua file scope-ku.
- **Bandit** (CI gate: `bandit -ll -r src`): exit 0 — 3 false positive B105
  (nama konstanta mengandung kata "secret") di-`# nosec` dengan alasan,
  mengikuti preseden repo.
- **Live CLI E2E** (subprocess nyata, `/tmp/cap-e2e`): `capability issue`
  exit 0 (token + record JSON), `capability list` menampilkan issued,
  `capability revoke --nonce` exit 0 + langsung tercatat,
  `revoke --nonce nope` exit 1 "unknown capability nonce",
  `issue --scope bogus` ditolak argparse; artifact `capability-secret` 0600,
  `capabilities.json` 0600, audit JSONL + head marker.
- **Determinisme**: `test_deterministic_issuance` +
  `test_verb_order_irrelevant` mem-pin penerbitan byte-identik untuk input
  identik (payload kanonik sort_keys).
- **Full suite**: run#2 (pohon final) **7159 passed, 1 skipped, 0 failed,
  0 errors, exit 0** (827 s). Run#1 menunjuk 9 failed/24 error yang
  dibuktikan milik sibling (lihat Temuan #2) + 1 milikku
  (test_no_ai_runtime surface scan) yang kemudian diperbaiki — scan kini
  me-resolve package lewat `importlib.util.find_spec` dan
  `security.capabilities` masuk `RUNTIME_SURFACE_MODULES`.
- **Post-format re-check**: ruff check+format, pyright 0/0/0, bandit -ll,
  206 passed (capability + serve_daemon) pada pohon final.

## Temuan / yang mengejutkan

1. **`stop_daemon(force=True)` di proses yang sama berbahaya**: leg
   eskalasi SIGKILL mengenai pid pidfile = proses pytest itu sendiri saat
   daemon in-process (membunuh test runner — "Killed" di tengah run). Test
   untuk jalur tanpa token dipin ke *refusal* (daemon tetap hidup), bukan ke
   eskalasi; eskalasi tetap ter-cover oleh suite subprocess sibling.
2. **Sibling SUB-??? (tiger-dst) mengedit superpower.py bersamaan**
   (`serve/budgets.py`, `assert_static_budgets`): 4 kegagalan di
   test_serve_arbiter/gates/concurrency dibuktikan milik sibling
   (`AssertionError` di budgets.py:253 pada config queue kecil) — 0 milikku.
   File-ku tidak menyentuh perubahan mereka; patch-ku berbasis konten sehingga
   koeksis.
3. **`security.py` → `security/__init__.py`** (git mv): import path
   `know_your_context.security` identik, semua import lama (20+ file) hijau
   tanpa perubahan — dibuktikan oleh suite tetangga + full suite.
4. **Keputusan desain**: audit security sengaja TERPISAH dari audit domain
   (store SQLite) — denial harus tercatat bahkan saat writer lane penuh atau
   token milik actor tanpa hak tulis; hash-chain + head marker membuat
   tamper DAN truncation terdeteksi. `daemon.status` tetap publik di mode
   enforce (monitoring); `daemon.shutdown` butuh admin.
