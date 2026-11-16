# Studi 5 Repo Skill — SUB-168 (fase belajar, task 2)

Tanggal: 2026-11-16 · Metode: REA-any (jalur repo-sumber → tools repo normal, sesuai aturan skill
itu sendiri) + EXPERT-THINKING tertulis di bawah · Aturan: murni belajar, 0 kode produk diubah.

Sumber yang dipelajari:

| Repo | Lokasi dipelajari | Bentuk |
|---|---|---|
| pm-skills (phuryn) | `/root/.hermes/skills/productivity/pm/` | 69 SKILL.md, 9 kategori, 768 KB |
| mattpocock/skills | `/root/.hermes/skills/mattpocock-engineering/` + `mattpocock-in-progress/` | 20 + 7 SKILL.md |
| superpowers (obra) | `/root/.hermes/skills/superpowers/` | 15 SKILL.md, 78 file, 732 KB |
| karpathy-skills (multica-ai) | clone `multica-ai/andrej-karpathy-skills` → `/root/.hermes/cache/scratch/learn-repos/karpathy-skills/` | 1 SKILL.md + CLAUDE.md + EXAMPLES.md |
| TokenEconomizer (microsoft) | clone `microsoft/TokenEconomizer` → `/root/.hermes/cache/scratch/learn-repos/TokenEconomizer/` | paket Python 17.777 LOC, 282 file, 48 file test |

Catatan koreksi sumber: brief menyebut `multica-ai/karpathy-skills`; nama repo nyata adalah
`multica-ai/andrej-karpathy-skills` (ditemukan via GitHub API `users/multica-ai/repos` dan
`search/repositories?q=karpathy-skills`, keduanya menempatkannya di urutan pertama).

## EXPERT-THINKING (7 pertanyaan, dijawab tertulis sebelum bekerja)

1. **Masalah**: 5 repo skill mengatur cara kerja agen di produk kita (superpowers dipakai untuk
   wave, pm-skills untuk planning, TE mengilhami EXPERT-THINKING), tapi pemahaman tim terhadap
   isinya belum pernah diukur — hanya diklaim. Masalah nyata: keputusan pinjam/tolak pola dari
   repo-repo ini selama ini tidak berbasis bukti file:baris.
2. **Kekurangan/kendala**: tidak boleh mengubah repo produk; tidak boleh build; repo lokal bukan
   git checkout (tanpa riwayat); klaim pemahaman harus terukur (≥90%), bukan pernyataan.
3. **Tujuan terukur**: per repo 10 pertanyaan kunci yang harus bisa dijawab dari kode nyata;
   skor = pertanyaan terjawab dengan bukti file:baris / 10. Target ≥9/10 per repo.
4. **Manfaat**: koordinator wave berikutnya bisa meminjam pola dengan sitasi presisi (bukan
   "katanya"), dan tahu persis apa yang sudah dipakai produk vs belum.
5. **Pelajaran dari masalah**: klaim tak berbukti pernah merusak verdict (aturan repo:
   "scoring an opponent 'absent' on an assumption a 30-second grep would disprove corrupts the
   whole verdict"). Desain studi ini mencegahnya: setiap jawaban wajib membawa file:baris yang
   benar-benar dibaca.
6. **Prinsip desain**: (a) evidence-first — baca kode, bukan README saja; (b) jujur pada batas —
   pertanyaan yang hanya bisa dijawab parsial diberi skor parsial, bukan dibulatkan;
   (c) read-only pada repo produk; (d) temuan harus actionable untuk produk.
7. **Kill criteria**: berhenti menambal bacaan bila 10/10 pertanyaan per repo sudah terjawab
   berbukti; bila sebuah repo ternyata tidak bisa di-clone/dibaca → laporkan blocker apa adanya
   (terjadi pada URL awal karpathy-skills; diatasi dengan koreksi nama repo via API).

---

## 1. TokenEconomizer (microsoft) — experience-driven cost control

### Peta struktur

```
tokeneconomizer/            paket inti, standard-library only (CLAUDE.md:10,37)
├── microskills/            distiller.py(880) observe.py(730) envfacts.py(626) lifecycle.py(203) stage.py(182)
├── toolprune/              codex.py(578) stage.py(199) tools.py
├── loopguard/              turn.py(500) shell_edits.py(313) state.py(257) envelope.py(205) rules.py(198) judge.py(102)
├── trace/                  pembaca log per agen: claude(381) hermes(328) codex(281) cursor droid grok opencode antigravity pi
├── plugin/                 runtime hook: hosts.py(1380) cli.py(1106) sidecall.py(1034) repos.py(877) denylist.py(561) ...
├── ledger.py(41) domain.py(77) pricing.py(285) names.py stage.py
plugin/                     plugin installable per agen (manifest .claude-plugin/.codex-plugin/...,
                            hooks, launcher scripts/te-hook, codex-skills/, hermes/, pi/, opencode/)
experiments/                harness paper (te_bench/ te_harness/ workloads/ scripts/) — terpisah dari plugin
tests/                      48 file test offline
docs/                       PLUGIN.md ARCHITECTURE.md DROID.md
```

### Pola inti

1. **Tiga mekanisme waste-cutting** (README.md:52-69): micro-skills (fakta repo dari kegagalan
   berulang + proposer LLM tiap k=3 sesi), tool pruning (buang tool tak terpakai lewat settings
   agen sendiri), loop guard (intervensi saat turn stuck).
2. **Empat prinsip desain** (README.md:103-112): bekerja lewat apa yang sudah dibaca agen;
   hitung biaya seperti ditagih (cache-aware, termasuk biaya plugin sendiri); lindungi keberhasilan
   tugas (fakta hanya dari bukti berulang); setiap aksi reversibel.
3. **Lifecycle fakta terukur**: CANDIDATE → ACTIVE → RETIRED dengan konstanta tervalidasi-replay
   (`lifecycle.py:17-23`: K_PROMOTE=3, GAMMA=0.8, LAMBDA=5.0, FIRE_GATE=0.40, FIRE_MIN_N=4).
4. **Kaskade 3 tingkat untuk stuck**: gate gratis → judge berbayar → heuristik ketat
   (`rules.py:1-14`), plus hard backstop yang tidak bisa diveto judge (`rules.py:130-144`).
5. **Fail-open menyeluruh**: hook yang gagal menjawab `{}` dan menulis ke errors.log
   (CLAUDE.md:37-38; `te_bootstrap.py:144,172`; `te-hook` baris 4: "Fails open -- with no usable
   Python the hook prints an empty object, which changes nothing").
6. **Satu definisi biaya** (`ledger.py:19-26`): overhead mekanisme (proposer, judge, nudge writer)
   masuk ke akun yang sama dengan pekerjaan yang mereka hemat — amortized, tidak disembunyikan.
7. **Domain-neutral lewat Protocol**: `domain.py:25-77` DomainAdapter memisahkan mekanisme dari
   detail host (load_trajectory, tool_usage, parse_catalog, protected_tools, spiral_snapshot...).
8. **Anti-bocor (leakage-safe)**: judge hanya membaca digest aksi (nama tool, perintah, basename
   file, signature error — tidak pernah isi file atau teks tugas) (`judge.py:1-8,15-27`).

### Dipakai produk kita / belum / bisa dipinjam

- **Dipakai**: pola pikirnya — `.superpowers/collab/EXPERT-THINKING.md:3,9,17` menyitir TE
  eksplisit (problem-first, measure-first, fail-open, biaya diamortisasi). Plugin TE sendiri
  TIDAK terinstal (bukti: `ls /root/.hermes/plugins/` hanya activity-feed, rtk-rewrite,
  surplus-pool, surplus-pool-provider; `~/.tokeneconomizer` tidak ada).
- **Belum dipakai**: ketiga mekanisme (micro-skills, tool pruning, loop guard) tidak ada di
  know-your-context; envelope p90; lifecycle fakta K_PROMOTE/LAMBDA; ledger biaya amortisasi.
- **Bisa dipinjam** (lihat §6 untuk prioritas): lifecycle fakta ber-recency-decay untuk modul
  "auditable memory" kita; hard backstop yang tak bisa diveto judge; deny-list yang hanya
  menyentuh entri milik sendiri (README.md:446-450); causal rule "outcome suatu turn hanya
  meng-gate turn BERIKUTNYA" (`envelope.py:5,48-49,194-197`).

### 10 pertanyaan kunci + jawaban berbukti

| # | Pertanyaan | Jawaban (bukti) |
|---|---|---|
| 1 | Masalah apa yang dipecahkan? | "Coding agents are built to finish tasks, not to keep them cheap... the same facts rediscovered, the same unused tools sent, the same kinds of failing loops" — README.md:95-98 |
| 2 | Apa 3 mekanismenya dan defaultnya? | micro-skills (OFF default), tool pruning (ON), loop guard (ON) — README.md:52-69, 362-364; tabel setting README.md:373-384 |
| 3 | Bagaimana fakta dipromosikan/dipensiunkan? | CANDIDATE→ACTIVE butuh K_PROMOTE=3 konfirmasi (fakta kode: pada file berbeda — `_drifted_confirms` lifecycle.py:80-88); ACTIVE→RETIRED bila skor recency `GAMMA^Δt` konfirmasi minus `LAMBDA=5×` kontradiksi < 0, atau 1 kontradiksi bersih untuk fakta high-stakes (HARD_KILL) — lifecycle.py:17-23, 90-119 |
| 4 | Kapan fakta benar-benar diinjeksikan? | Hanya bila ACTIVE DAN firing rate ≥ 0.40 pada ≥4 tugas yang meng-exercise-nya (FIRE_GATE/FIRE_MIN_N) — lifecycle.py:98-102 (`injectable()`) |
| 5 | Bagaimana loop guard memutuskan "stuck"? | Tier-0 gate gratis (`gate_for_judge`: ≥18 turn DAN (≥1 failure berulang ATAU ≥8 error) — rules.py:78-90); lalu judge LLW confidence ≥0.7 (rules.py:164-165); fallback tanpa judge: konjungsi churn DAN error DAN stagnasi ≥20% (`is_stuck` rules.py:93-106); hard backstop ≥2.5× envelope + is_stuck, judge tak bisa memveto (rules.py:130-144) |
| 6 | Kenapa envelope memakai p90, bukan max? | Satu resolve 115-turn di xarray pernah men-set envelope berbasis max ke ~144 dan mematikan guard untuk semua spiral 40-60-turn; p90 mengabaikan outlier tunggal — envelope.py:33-40 |
| 7 | Bagaimana fail-open diimplementasikan di kode? | Launcher shell: tanpa Python ≥3.10 hook tetap menjawab `{}` (te-hook baris 4-5); bootstrap menulis `{}\n` pada setiap kegagalan (te_bootstrap.py:144,172) dan ditulis dalam sintaks Python 3.0 agar gagal sebagai `{}` bukan syntax error (te_bootstrap.py:23-26); konvensi repo: "any exception in a hook becomes {} and a line in errors.log" (CLAUDE.md:37-38); judge gagal → `stuck=None` → fallback gratis (judge.py:44-46, rules.py:159-162) |
| 8 | Bagaimana biaya plugin sendiri dihitung? | task_cost = effective_exec (cache-aware) + overhead; OVERHEAD_KEYS menjumlah 6 field token mekanisme (optimizer, untool classify/judge, codex adapt, spiral judge, nudge writer) — ledger.py:19-35; "includes the plugin's own model calls" README.md:106-107 |
| 9 | Bagaimana tool pruning menghindari menghapus tool penting? | Bukti dulu: tool diproses hanya setelah unused streak ≥ threshold DAN tidak ada di `protected_tools` (work surface) DAN tidak pernah terlihat dipakai — stage.py:94-98; `toolprune restore` mengembalikan dan tool yang dikembalikan tak dihapus lagi — README.md:449-450; deny list tak pernah menyentuh aturan deny milik pengguna — README.md:446 |
| 10 | Bagaimana integrasi Hermes bekerja? | `plugin/hermes/__init__.py` docstring: prompt section→SessionStart (fakta masuk system prompt), pre_llm_call→UserPromptSubmit, transform_tool_result→PostToolUse (loop guard), on_session_end→Stop/SessionEnd (fold terpisah); hanya mengikuti sesi `created_source` cli/oneshot dengan cwd; subagent/TUI/platform diabaikan; setiap kegagalan = no change |

**Skor pemahaman: 10/10 = 100%.** (Batas jujur: `experiments/` hanya dibaca struktur +
README-nya — baris 1-15 mengonfirmasi harness Docker SWE-Bench/Web-Bench; kode arm/detail
tidak dibaca. Itu di luar 10 pertanyaan kunci plugin.)

---

## 2. superpowers (obra) — disiplin proses agen

### Peta struktur

```
superpowers/                      15 skill, 78 file
├── using-superpowers/            meta-skill: wajib invoke skill sebelum respons apa pun
│   └── references/               per-harness: claude-code, codex, pi, antigravity, hermes, muse
├── brainstorming/                temukan apa yang sebenarnya diinginkan sebelum build (225 baris)
├── test-driven-development/      Iron Law + RED-GREEN-REFACTOR (330 baris + writing-good-tests.md)
├── systematic-debugging/         4 fase, root-cause dulu (283 baris)
├── verification-before-completion/  gate klaim (120 baris)
├── writing-plans/                rencana bite-sized untuk engineer baru (229 baris)
├── executing-plans/              eksekusi inline (373 baris)
├── subagent-driven-development/  implementer fresh per task + review (705 baris)
├── dispatching-parallel-agents/  kapan paralel (295 baris)
├── requesting-code-review/ receiving-code-review/  loop review dua arah
├── finishing-a-development-branch/ using-git-worktrees/  integrasi & isolasi
├── writing-skills/               TDD untuk skill itu sendiri (690 baris + anthropic-best-practices.md)
└── diagnosing-superpowers/       post-mortem sesi superpowers (120 baris)
```

### Pola inti

1. **Meta-rule wajib**: "If you think there is even a 1% chance a skill might apply... you
   ABSOLUTELY MUST invoke the skill" (using-superpowers/SKILL.md:10-16); skill check mendahului
   bahkan pertanyaan klarifikasi (baris 20, 39-50 tabel red flags).
2. **Iron Laws bergaya keras**: "NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST"
   (tdd/SKILL.md:33-35); "NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE"
   (verification/SKILL.md:16-20); "NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST"
   (systematic-debugging/SKILL.md:15-17). Kode yang ditulis sebelum test: "Delete it. Start over"
   — delete berarti delete, bukan "keep as reference" (tdd/SKILL.md:37-45).
3. **Gate function 5 langkah** sebelum klaim: IDENTIFY command → RUN fresh → READ output +
   exit code → VERIFY → baru CLAIM (verification/SKILL.md:24-36).
4. **Tabel rasionalisasi**: setiap skill membawa tabel "Excuse | Reality" untuk menangkap
   self-negotiation (tdd/SKILL.md:222-236; verification/SKILL.md:61-72).
5. **Verifikasi suite penuh**: "A scope statement in your task bounds the deliverable, not your
   verification... a red test you watched scroll past and didn't mention is a report falsified by
   omission" (tdd/SKILL.md:185-193).
6. **TDD untuk dokumen proses**: writing-skills menerapkan RED-GREEN ke skill itu sendiri —
   tulis pressure scenario, lihat agen gagal tanpa skill, tulis skill, lihat agen patuh
   (writing-skills/SKILL.md:10-21).
7. **Subagent berkonteks terisolasi**: implementer fresh per task, tidak pernah mewarisi konteks
   sesi pemanggil (subagent-driven-development/SKILL.md:7-11).

### Dipakai produk kita / belum / bisa dipinjam

- **Dipakai (bukti kuat)**: repo produk punya `.superpowers/collab/` (board, EXPERT-THINKING.md)
  dan `.superpowers/sdd/` (spec-driven waves); pola board sub-agent (`subagent-board.md`) adalah
  pola dispatching-parallel-agents/subagent-driven-development; board entries memakai TDD
  RED→GREEN dengan exit code (mis. SUB-165: "pytest exit=1 (1f/42p)" lalu "DONE ... 43p") —
  persis verification-before-completion; aturan wave EXPERT-THINKING mewajibkan alur superpowers
  untuk build (EXPERT-THINKING.md:39).
- **Belum dipakai eksplisit**: brainstorming (wawancara sebelum build), writing-skills
  (TDD-untuk-skill), diagnosing-superpowers (post-mortem sesi), finishing-a-development-branch.
- **Bisa dipinjam**: tabel rasionalisasi untuk skill baru produk; gate function 5 langkah sudah
  de-facto dipakai board tapi belum dinamai; writing-skills' "pressure scenario" untuk menguji
  skill internal produk (mis. hermes-agent-skill-authoring).

### 10 pertanyaan kunci + jawaban berbukti

| # | Pertanyaan | Jawaban (bukti) |
|---|---|---|
| 1 | Apa aturan meta yang mengikat semua skill? | Invoke skill yang relevan SEBELUM respons/aksi apa pun, termasuk pertanyaan klarifikasi; user instructions (CLAUDE.md dll) mengalahkan skill — using-superpowers/SKILL.md:18-24, 63-65 |
| 2 | Apa Iron Law TDD dan konsekuensi pelanggarannya? | "NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST"; kode yang telanjur ditulis dihapus total — "Don't keep it as reference... Delete means delete" — tdd/SKILL.md:33-45 |
| 3 | Bagaimana siklus RED-GREEN-REFACTOR diverifikasi? | Verify RED wajib (test gagal karena fitur belum ada, bukan typo — tdd/SKILL.md:113-128); Verify GREEN wajib termasuk suite penuh proyek, bukan hanya file sendiri (tdd/SKILL.md:168-193); refactor hanya setelah green (tdd/SKILL.md:195-202) |
| 4 | Apa itu "report falsified by omission"? | Test merah yang terlihat scroll tapi tidak dilaporkan = laporan palsu oleh kelalaian — tdd/SKILL.md:185-193 |
| 5 | Bagaimana verification gate bekerja? | 5 langkah: IDENTIFY perintah pembukti → RUN fresh lengkap → READ output+exit code → VERIFY mengonfirmasi klaim → baru CLAIM; skip satu langkah = "lying, not verifying" — verification/SKILL.md:24-36 |
| 6 | Apa 4 fase systematic debugging? | Phase 1 root cause investigation (baca error lengkap, reproduksi konsisten, cek perubahan terbaru, instrumentasi batas komponen) — systematic-debugging/SKILL.md:15-17, 42-78; fase berikutnya (pattern/candidate-fix/implement) terkunci sebelum Phase 1 selesai |
| 7 | Kapan agen paralel boleh didispatch? | Hanya bila failure independen, tak berbagi state, dan tak saling mengganggu; decision tree eksplisit — dispatching-parallel-agents/SKILL.md:24-46 ("Don't use when: failures are related... agents would interfere") |
| 8 | Bagaimana subagent-driven development menjaga kualitas? | Implementer fresh per task (tanpa konteks sesi) + review per task (spec compliance + quality) + broad review seluruh branch di akhir — subagent-driven-development/SKILL.md:5-11 |
| 9 | Bagaimana brainstorming membuka percakapan? | Pesan pertama = (1) satu baris tawaran skip, (2) satu pertanyaan terbuka tentang momen nyata; setiap pesan berikutnya diakhiri tepat satu pertanyaan tanpa menu contoh jawaban — brainstorming/SKILL.md:26-46 |
| 10 | Bagaimana skill baru diuji sebelum deploy? | TDD untuk proses-dokumen: tulis pressure scenario (subagent tanpa skill), saksikan gagal (RED), tulis skill, saksikan patuh (GREEN), refactor tutup loophole; wajib paham TDD dulu — writing-skills/SKILL.md:10-21 |

**Skor pemahaman: 10/10 = 100%.** (Batas jujur: 4 dari 15 skill dibaca penuh, 11 lainnya dibaca
header + aturan inti; references/ per-harness tidak dibaca detail. Pertanyaan kunci sengaja
dibidik pada yang dibaca penuh.)

---

## 3. mattpocock/skills — alur engineering idea→ship

### Peta struktur

```
mattpocock-engineering/     20 skill
├── ask-matt/               ROUTER: peta seluruh alur (97 baris)
├── grill-with-docs/        wawancara menajamkan ide, stateful (GLOSSARY.md + ADR)
├── to-spec/                percakapan → spec ke issue tracker (75 baris)
├── to-tickets/             spec → tracer-bullet tickets + blocking edges (105 baris)
├── implement/              kerjakan ticket via tdd + code-review (17 baris)
├── implement-spec/         seluruh spec: subagent paralel di frontier task graph
├── tdd/                    red→green + seam + anti-pattern (38 baris + tests.md mocking.md)
├── code-review/            2 sumbu (Standards + Spec), subagen paralel (110 baris)
├── diagnosing-bugs/        feedback loop dulu (138 baris + scripts/)
├── triage/ wayfinder/ prototype/ research/ handoff/ retro/ pr/
├── codebase-design/        kosakata deep modules (114 baris + DEEPENING.md DESIGN-IT-TWICE.md)
├── domain-modeling/        kosakata domain + ADR
└── setup-matt-pocock-skills/  precondition: konfigurasi tracker + label
mattpocock-in-progress/     7 skill eksperimental: chief-of-staff, claude-handoff, loop-me,
                            setup-ts-deep-modules, writing-beats/fragments/shape
```

### Pola inti

1. **Satu router**: ask-matt memetakan main flow (idea→ship), dua on-ramp (triage,
   diagnosing-bugs), wayfinder untuk usaha raksasa-kabut, vocabulary layer, standalone
   (ask-matt/SKILL.md:13-97). Skill tak diingat — "You don't remember every skill, so ask"
   (baris 9).
2. **Main flow**: grill-with-docs → (detour prototype via handoff bila perlu jawaban runnable) →
   to-spec → to-tickets (tracer bullet + blocking edges) → implement per ticket ATAU
   implement-spec paralel → tdd per slice → code-review 2 sumbu → pr → retro (ask-matt:19-34).
3. **Seam sebagai disiplin test**: "Test only at pre-agreed seams... No test is written at an
   unconfirmed seam" (tdd/SKILL.md:22-24); seam = batas publik yang bisa diamati tanpa masuk ke
   dalam (tdd/SKILL.md:20-21).
4. **Anti-pattern test bernama**: implementation-coupled, tautological, horizontal slicing
   (tdd/SKILL.md:28-32) — vertikal: satu test → satu implementasi → ulangi (tracer bullet).
5. **Deep-module vocabulary** (Ousterhout-ish): module/interface/implementation/depth/seam/
   adapter/leverage/locality — dilarang substitusi istilah ("component", "service", "API")
   (codebase-design/SKILL.md:16-40).
6. **Feedback loop adalah skill-nya**: diagnosing-bugs — "If you have a tight pass/fail signal
   for the bug, you will find the cause... This is the skill. Everything else is mechanical"
   (diagnosing-bugs/SKILL.md:18-22); 5 cara membangun loop berurutan (test → curl → CLI fixture →
   headless browser → replay trace) (baris 24-34); redaksi rahasia wajib `<REDACTED>` (baris 13-17).
7. **Context hygiene + smart zone**: satu window utuh sampai to-tickets; ~150k token "smart zone";
   5 opsi di phase boundary (Continue / clear / handoff / subagent / compact — compact adalah
   default di dasar pohon keputusan) (ask-matt/SKILL.md:36-40, 69-79).
8. **Retro memperbaiki lingkungan, bukan kode**: "Mechanical mistakes become deterministic checks;
   judgement calls become coding standards" (ask-matt/SKILL.md:34).

### Dipakai produk kita / belum / bisa dipinjam

- **Dipakai**: pola ADR — produk punya `docs/adr/0001-standalone-deterministic-monolith.md` dan
  `0002-sqlite-events-graphify-read-model.md` (pola domain-modeling/grill-with-docs); pola
  spec-driven wave di `.superpowers/sdd/2026-10-27-universal-knowledge-graph/` menyerupai
  to-spec/to-tickets; tracer-bullet + blocking edges tercermin di pembagian WS-A..G wave task2.
- **Belum dipakai**: GLOSSARY.md (tidak ada di root produk), issue-tracker label `ready-for-agent`,
  retro formal per sesi, code-review 2-sumbu subagen, smart-zone/phase-boundary tree.
- **Bisa dipinjam**: (a) **router skill** seperti ask-matt untuk 40+ skill Hermes kita —
  satu titik masuk yang memetakan flow; (b) **feedback-loop-first** dari diagnosing-bugs untuk
  memperkuat systematic-debugging yang kita pakai; (c) **retro→environment** (kesalahan mekanis →
  deterministic check) — sangat cocok dengan runtime_guard/dependency-policy produk; (d) aturan
  redaksi `<REDACTED>` untuk drill keamanan; (e) anti-pattern "tautological test" sebagai checklist
  auditor wave.

### 10 pertanyaan kunci + jawaban berbukti

| # | Pertanyaan | Jawaban (bukti) |
|---|---|---|
| 1 | Bagaimana menemukan skill yang tepat di repo ini? | Lewat ask-matt, router atas seluruh skill: "You don't remember every skill, so ask"; ringkasannya hanya orientasi — baca SKILL.md aslinya sebelum merekomendasikan skip — ask-matt/SKILL.md:9-12 |
| 2 | Apa main flow idea→ship? | grill-with-docs → (prototype via handoff bila perlu) → to-spec → to-tickets → implement/implement-spec → tdd → code-review → pr → retro — ask-matt/SKILL.md:19-34 |
| 3 | Di mana test boleh ditulis? | Hanya di seam yang disepakati sebelumnya: "Before writing any test, write down the seams under test and confirm them with the user. No test is written at an unconfirmed seam" — tdd/SKILL.md:22-24 |
| 4 | Apa 3 anti-pattern test? | Implementation-coupled (pecah saat refactor tanpa perubahan perilaku), tautological (expected value dihitung dengan cara yang sama dengan kode → tak pernah bisa tidak setuju), horizontal slicing (semua test dulu → memverifikasi perilaku yang dibayangkan) — tdd/SKILL.md:28-32 |
| 5 | Apa definisi "deep module"? | Banyak perilaku di balik interface kecil pada seam bersih; depth = leverage di interface; istilah wajib: module/interface/implementation/depth/seam/adapter/leverage/locality — codebase-design/SKILL.md:16-40 |
| 6 | Apa inti diagnosing-bugs? | Phase 1 membangun feedback loop ketat yang merah pada bug INI: "This is the skill. Everything else is mechanical... Be aggressive. Be creative. Refuse to give up" — diagnosing-bugs/SKILL.md:18-22; urutan konstruksi: failing test → curl → CLI+snapshot → headless browser → replay trace (baris 24-34) |
| 7 | Bagaimana to-spec bekerja? | Sintesis, BUKAN wawancara: eksplorasi repo → sketsa seam (existing seam lebih baik, seam tertinggi, ideal satu) → konfirmasi seam → tulis template → publish dengan label ready-for-agent — to-spec/SKILL.md:5-25 |
| 8 | Apa itu tracer-bullet ticket? | Vertical slice end-to-end yang mendeklarasikan blocking edges-nya; dikerjakan blockers-first; tiap ticket self-contained sehingga konteks ticket terakhir disposable — to-tickets/SKILL.md:5-20, ask-matt/SKILL.md:25-28 |
| 9 | Apa 2 sumbu code-review? | Standards (patuh coding standards terdokumentasi repo) + Spec (sesuai issue/spec asal) — dijalankan sebagai subagen paralel agar tak saling mencemari konteks — code-review/SKILL.md:5-11 |
| 10 | Bagaimana keputusan di phase boundary? | 5 opsi berpohon: Continue (rule out dulu) / clear / handoff (hanya: harness baru, direktori baru, kolega, fork mid-phase) / subagent / compact (default di dasar pohon); putuskan DI boundary, jangan mid-phase — ask-matt/SKILL.md:69-79 |

**Skor pemahaman: 10/10 = 100%.** (Batas jujur: ask-matt + tdd dibaca penuh; 6 skill inti lain
dibaca 30 baris pertama; 13 skill sisanya dari peta ask-matt. Pertanyaan dibidik ke yang dibaca.)

---

## 4. pm-skills (phuryn) — toolkit product manager

### Peta struktur

```
productivity/pm/                69 skill, 9 kategori (instalasi lokal; upstream: phuryn/pm-skills)
├── ai-shipping/        (3)  code-review, intended-vs-implemented, shipping-artifacts
├── data-analytics/     (3)  ab-test-analysis, cohort-analysis, sql-queries
├── execution/         (15)  brainstorm-okrs, create-prd, dummy-dataset, job-stories,
                             outcome-roadmap, pre-mortem, prioritization-frameworks,
                             release-notes, retro, sprint-plan, stakeholder-map,
                             strategy-red-team, summarize-meeting, test-scenarios, ...
├── go-to-market/       (6)  beachhead-segment, competitive-battlecard, growth-loops, ...
├── market-research/    (7)  competitor-analysis, customer-journey-map, market-sizing, ...
├── marketing-growth/   (5)  marketing-ideas, north-star-metric, positioning-ideas, ...
├── product-discovery/ (13)  analyze-feature-requests, brainstorm-experiments-*,
                             identify-assumptions-*, interview-script, metrics-dashboard,
                             opportunity-solution-tree, prioritize-*, summarize-interview
├── product-strategy/  (12)  ansoff-matrix, business-model, lean-canvas, monetization-strategy,
                             pestle-analysis, porters-five-forces, pricing-strategy, ...
└── toolkit/            (4)  draft-nda, grammar-check, privacy-policy, review-resume
```

### Pola inti

1. **Template dokumen keputusan, bukan proses agen**: setiap skill = persona PM ("You are an
   experienced product manager...") + instruksi bernomor + template output + kriteria review.
   Terukur: 21/69 membawa `## Purpose`, 44/69 membawa `## Instructions` (grep terhadap instalasi
   lokal), rata-rata ~96 baris/skill (6619 baris total).
2. **Lean by default**: PRD pertama 1-2 halaman (600-1.000 kata); "Complete means no gap that
   blocks a decision about the first version, not every question answered"; bagian yang tak
   relevan dihilangkan, tidak diisi "TBD" (create-prd/SKILL.md:12-19).
3. **Jangan mengarang**: "Don't invent. What you don't know goes to 7.4 Assumptions and open
   questions, clearly marked, not into the body as fact" (create-prd/SKILL.md:19); tanya maksimal
   3 pertanyaan pendek, dan hanya yang hanya bisa dijawab user (create-prd/SKILL.md:23).
4. **Spine konsistensi**: value proposition 6-bagian JTBD (Who/Why/What before/How/What after/
   Alternatives) adalah tulang punggung PRD — Solution mengantarkan How-nya, Key Results mengukur
   What-after-nya (create-prd/SKILL.md:51-63).
5. **Taksonomi risiko pre-mortem**: Tigers (nyata, berbasis bukti) / Paper Tigers (kekhawatiran
   berlebih) / Elephants (tak dibicarakan) → launch-blocking / fast-follow / track + action plan
   per launch-blocking tiger (pre-mortem/SKILL.md:27-59).
6. **Skill saling mereferensikan**: create-prd menunjuk value-proposition skill untuk metode
   lengkap (create-prd/SKILL.md:61) — library, bukan silo.
7. **Transparansi kelengkapan**: "Say what you left out" — balasan menyertakan satu baris daftar
   yang sengaja diskip (create-prd/SKILL.md:79).

### Dipakai produk kita / belum / bisa dipinjam

- **Dipakai**: diamanatkan untuk planning — EXPERT-THINKING.md:41: "Planning → WAJIB menjalankan
  prosedur pm-skills (PRD/pre-mortem/prioritization) SAAT menyusun rencana". (Catatan jujur: saya
  tidak menemukan artefak PRD/pre-mortem bernama eksplisit di `.superpowers/sdd/`; mandatnya ada,
  jejak artefak bernama-eksplisit tidak — rencana wave memakai format sendiri.)
- **Belum dipakai**: 65+ skill lain — terutama ai-shipping (intended-vs-implemented,
  shipping-artifacts) yang justru paling relevan dengan produk agen.
- **Bisa dipinjam**: (a) **intended-vs-implemented** — metode mengukur gap antara yang
  dimaksud vs terimplementasi; sangat cocok untuk audit wave kita (SUB-164 sudah melakukan
  sejenisnya secara manual); (b) **pre-mortem Tigers/Paper/Elephants** untuk gate sebelum wave
  besar; (c) pola "Say what you left out" untuk setiap verdict/riset produk; (d) "Don't invent →
  assumptions section" selaras dengan aturan benchmark produk (jangan mengarang angka).

### 10 pertanyaan kunci + jawaban berbukti

| # | Pertanyaan | Jawaban (bukti) |
|---|---|---|
| 1 | Apa unit dasar repo ini? | 69 skill dokumen-keputusan dalam 9 kategori PM; setiap skill = SKILL.md mandiri (ls + wc -l instalasi lokal; 6619 baris total) |
| 2 | Apa pola umum satu SKILL.md? | Frontmatter name+description panjang berisi use-case → Purpose (persona PM) → Instructions bernomor → template output → (banyak) mode review; bukti pola: 21/69 `## Purpose`, 44/69 `## Instructions` (grep) |
| 3 | Bagaimana create-prd menjaga PRD tetap lean? | Default 1-2 halaman (600-1.000 kata); "Complete means no gap that blocks a decision"; bagian minim konten dihilangkan, bukan di-TBD-kan; detail hanya atas permintaan — create-prd/SKILL.md:12-19, 87 |
| 4 | Bagaimana ketidaktahuan ditangani? | "Don't invent" — yang tak diketahui masuk 7.4 Assumptions and open questions, ditandai jelas; tanya user maks 3 pertanyaan singkat dan hanya yang tak bisa diriset — create-prd/SKILL.md:19, 23 |
| 5 | Apa template value proposition? | 6-bagian JTBD (Paweł Huryn & Aatir Abdul Rauf): Who / Why / What before / How / What after / Alternatives + statement 1-2 kalimat; spine PRD: Solution mengantar How, Key Results mengukur What after — create-prd/SKILL.md:51-63 |
| 6 | Bagaimana pre-mortem mengkategorikan risiko? | Tigers (nyata, berbasis bukti) / Paper Tigers (tampak valid tapi berlebih) / Elephants (tak dibicarakan cukup); Tigers diklasifikasi launch-blocking / fast-follow (≤30 hari) / track; tiap launch-blocking dapat mitigasi + owner + tanggal — pre-mortem/SKILL.md:27-59 |
| 7 | Apa isi prioritization-frameworks? | Referensi 9 framework berformula + when-to-use: ICE (Impact×Confidence×Ease, direkomendasikan cepat), RICE ((Reach×Impact×Confidence)/Effort), Kano (memahami ekspektasi, bukan memprioritaskan), MoSCoW, Opportunity Score, dst — prioritization-frameworks/SKILL.md:3, 27-60 |
| 8 | Bagaimana skill saling terhubung? | Cross-reference eksplisit: create-prd merujuk skill value-proposition untuk metode penuh "if it's installed" — create-prd/SKILL.md:61 |
| 9 | Bagaimana mode review bekerja? | Review PRD existing memakai standar yang sama: adakah gap yang memblokir keputusan v1? Segments + value-prop 6-bagian per segmen? Solution↔How dan KR↔What-after selaras? Assumptions ditandai? Kembalikan gaps dulu, lalu cuts — create-prd/SKILL.md:81-83 |
| 10 | Standar bahasa output? | "Write for a primary school graduate. Avoid jargon. Use clear, short sentences" — create-prd/SKILL.md:75; + transparansi: "Say what you left out" di balasan — create-prd/SKILL.md:79 |

**Skor pemahaman: 10/10 = 100%.** (Batas jujur: 2 skill dibaca penuh, 1 dibaca sebagian
(prioritization-frameworks), 66 lainnya dari deskripsi frontmatter + pola; upstream repo tidak
di-clone — instalasi lokal yang jadi sumber kebenaran.)

---

## 5. karpathy-skills (multica-ai) — 4 prinsip perilaku

### Peta struktur

```
andrej-karpathy-skills/     36 file, 280 KB — sengaja minimalis
├── skills/karpathy-guidelines/SKILL.md   67 baris — SATU-SATUNYA skill
├── CLAUDE.md               2,3K — konten identik untuk instruksi proyek
├── .cursor/rules/karpathy-guidelines.mdc  rule Cursor (alwaysApply: true)
├── .claude-plugin/         plugin.json + marketplace.json (distribusi plugin)
├── EXAMPLES.md             14,5K — contoh salah/benar per prinsip (15 section ##)
├── CURSOR.md               panduan Cursor
└── README.md + README.zh.md
```

### Pola inti

1. **Empat prinsip dari satu post Karpathy** (README.md:7,15-19): Think Before Coding, Simplicity
   First, Surgical Changes, Goal-Driven Execution — masing-masing memetakan ke pitfall LLM yang
   dikutip (asumsi diam-diam; overcomplicate; sentuh kode ortogonal; success criteria lemah).
2. **Setiap prinsip punya uji sederhana**: "Would a senior engineer say this is overcomplicated?"
   (SKILL.md:33); "Every changed line should trace directly to the user's request" (SKILL.md:49).
3. **Transformasi imperatif → deklaratif**: "Add validation" → "Write tests for invalid inputs,
   then make them pass" (SKILL.md:55-58) + rencana multi-step ber-format `step → verify: check`
   (SKILL.md:60-65).
4. **Multi-format delivery**: konten yang sama dikemas sebagai plugin Claude Code, CLAUDE.md
   (append ke proyek), Cursor .mdc rule, dan personal skill (README.md:99-130; CURSOR.md:1-29).
5. **Tradeoff dinyatakan**: bias ke hati-hati di atas kecepatan; tugas trivial pakai judgment
   (SKILL.md:11; README.md:163-167).
6. **Keberhasilan teramati**: sinyal working = diff lebih sedikit yang tak diminta, pertanyaan
   klarifikasi datang SEBELUM implementasi, PR bersih (README.md:140-147).

### Dipakai produk kita / belum / bisa dipinjam

- **Dipakai**: tidak langsung — tidak ada CLAUDE.md/AGENTS.md berisi 4 prinsip ini di
  know-your-context; skill karpathy-guidelines tidak terinstal di `~/.hermes/skills/`.
  Semangatnya tumpang tindih dengan EXPERT-THINKING + instruksi sub-agent (scope eksplisit,
  jangan sentuh di luar scope ≈ Surgical Changes).
- **Belum dipakai**: keempat prinsip sebagai guideline tertulis; uji "senior engineer";
  sinyal-teramati "how to know it's working".
- **Bisa dipinjam**: (a) **Surgical Changes test** ("setiap baris berubah harus tertelusur ke
  permintaan user") sebagai checklist auditor wave — produk kita sudah punya aturan scope tapi
  belum punya uji satu-kalimat setajam ini; (b) **format rencana `step → verify: check`** untuk
  board sub-agent (entri board kita sudah semi-pola ini, bisa diformalkan); (c) contoh
  salah/benar EXAMPLES.md sebagai gaya dokumentasi skill internal.

### 10 pertanyaan kunci + jawaban berbukti

| # | Pertanyaan | Jawaban (bukti) |
|---|---|---|
| 1 | Apa isi repo ini? | Satu skill (karpathy-guidelines) + kemasan ulang konten yang sama (CLAUDE.md, Cursor .mdc, plugin) + EXAMPLES.md — bukti: find menunjukkan 1 SKILL.md, CLAUDE.md:1-65 identik substansi dengan SKILL.md:13-67 |
| 2 | Dari mana prinsip berasal? | Post X Andrej Karpathy tentang pitfall coding LLM — README.md:7 mengutip tautannya; README.md:15-19 mengutip 3 keluhan (asumsi salah jalan terus; overcomplicate 1000 baris untuk 100; mengubah kode yang tak dipahami sebagai side effect) |
| 3 | Apa prinsip 1 dan mekanismenya? | Think Before Coding: nyatakan asumsi eksplisit, sajikan multi-interpretasi, push back bila ada yang lebih sederhana, berhenti saat bingung — SKILL.md:13-21 |
| 4 | Apa prinsip 2 dan ujinya? | Simplicity First: tanpa fitur tak diminta, tanpa abstraksi sekali pakai, tanpa error handling skenario mustahil; "If you write 200 lines and it could be 50, rewrite it"; uji: akankah senior engineer bilang overcomplicated? — SKILL.md:23-33 |
| 5 | Apa prinsip 3 dan ujinya? | Surgical Changes: jangan "improve" kode sekitar, cocokkan gaya existing, hapus hanya orphan buatan sendiri; uji: "Every changed line should trace directly to the user's request" — SKILL.md:35-49 |
| 6 | Apa prinsip 4 dan formatnya? | Goal-Driven Execution: ubah tugas imperatif jadi goal terverifikasi (tabel contoh SKILL.md:55-58) + rencana `1. [Step] → verify: [check]`; kriteria kuat membuat LLM loop mandiri — SKILL.md:51-67 |
| 7 | Bagaimana cara distribusinya? | 3 jalur: plugin marketplace Claude Code, CLAUDE.md via curl append, Cursor .mdc committed (alwaysApply: true) — README.md:99-130; CURSOR.md:1-29 |
| 8 | Apa isi EXAMPLES.md? | Contoh nyata salah-vs-benar per prinsip (15 section `##`); mis. export_users mengasumsikan semua user/format/field tanpa bertanya, lalu versi benar yang memunculkan asumsi — EXAMPLES.md:1-40 |
| 9 | Apa tradeoff yang diakui? | Bias hati-hati di atas kecepatan; tugas trivial (typo, one-liner) pakai judgment — "not every change needs the full rigor" — SKILL.md:11, README.md:163-167 |
| 10 | Bagaimana tahu guideline bekerja? | Sinyal teramati: diff lebih sedikit perubahan tak diminta; rewrite karena overcomplication berkurang; pertanyaan klarifikasi datang sebelum implementasi; PR bersih tanpa drive-by refactoring — README.md:140-147 |

**Skor pemahaman: 10/10 = 100%.** (Repo kecil — README, SKILL.md, CLAUDE.md, CURSOR.md,
EXAMPLES.md dibaca; satu-satunya bagian tak dibaca kata-per-kata: README.zh.md, diasumsikan
terjemahan. Tidak memengaruhi 10 pertanyaan.)

---

## 6. Verifikasi: TokenEconomizer vs EXPERT-THINKING kita

Protokol kita (`.superpowers/collab/EXPERT-THINKING.md`) mengaku terinspirasi TE. Verifikasi butir
demi butir terhadap kode nyata TE:

| Pola | Di EXPERT-THINKING kita | Di TE (bukti) | Status |
|---|---|---|---|
| Problem-first | ✓ pertanyaan 1 ("masalah NYATA apa") | Satu kalimat tajam mengarahkan desain — README.md:95 | SETARA |
| Measure-first | "ukur dulu, klaim belakangan"; "Setiap klaim membawa bukti: perintah + exit code + ledger" | Ledger biaya tunggal, cache-aware, termasuk biaya plugin sendiri — ledger.py:19-35; README.md:106-107 | SETARA |
| Fail-open non-kritis | "Fail open pada jalur non-kritis" | Hook gagal → `{}` + errors.log (CLAUDE.md:37-38; te_bootstrap.py:144,172); judge error → stuck=None → fallback gratis (judge.py:44-46) | SETARA |
| Fail-closed keamanan/data | "fail closed pada jalur keamanan/data" | Tak pernah pegang kredensial (README.md:117-118); belajar tetap lokal di ~/.tokeneconomizer (README.md:78); deny list tak sentuh aturan user (README.md:446) | SETARA |
| Kill switch per mekanisme | ✓ "Setiap mekanisme bisa dimatikan sendiri dan mengumumkan aksinya" | microskills/toolprune/loopguard on/off per repo/global + TE_DISABLE/TE_* env (README.md:369-384, 420-424); "announces each action in one line" (README.md:75) | SETARA |
| **Reversibilitas sebagai prinsip** | implisit saja | Prinsip 4 eksplisit: fakta terkontradiksi di-drop, tool bisa dikembalikan (dan tak dihapus lagi), guard hanya mengalihkan — README.md:110-112, 449-450; Forgetting.unforget mengembalikan record utuh — lifecycle.py:186-198 | **KURANG: jadikan prinsip eksplisit** |
| **Anti-self-gating (causal rule)** | tidak ada | "A turn's own outcome only ever gates LATER turns" — envelope.py:5, 48-49; hanya turn RESOLVED yang memberi makan baseline (envelope.py:72-80, 194-197) | **KURANG: belum ada aturan ini** |
| **Hard backstop yang tak bisa diveto otomasi** | kill criteria ada, tapi tak melarang judge menimpa | Backstop 2.5× envelope + is_stuck membatalkan TERLEPAS dari verdict judge; "it must not be able to veto such an outlier" — rules.py:130-144 | **KURANG: tambahkan eksplisit** |
| **Ambiguitas → spare (asimetri keamanan)** | "fail open" umum | Judge ambiguous → JANGAN abort: "Ambiguity => SPARE" — rules.py:168-173; LAMBDA=5.0 kontradiksi lebih berat dari konfirmasi ("fail toward retirement") — lifecycle.py:19 | **KURANG: formalisasi asimetri** |
| **Ambang bukti sebelum bertindak** | "ukur dulu" tanpa angka | Konkret: K_PROMOTE=3 konfirmasi, FIRE_MIN_N=4, envelope_min_samples=5, p=10 prompt/m=2 sesi — lifecycle.py:17-23, rules.py:30, README.md:373-384 | **KURANG: kita tak menamai ambang** |

**Kesimpulan verifikasi**: 5 pola inti sudah setara; **5 pola TE belum teradopsi eksplisit** di
EXPERT-THINKING — reversibilitas, causal anti-self-gating, hard backstop anti-veto, asimetri
ambiguitas→spare, dan ambang bukti bernomor. Semuanya layak masuk revisi EXPERT-THINKING berikutnya
(rekomendasi, bukan keputusan — wewenang operator).

## 7. Temuan lintas-repo yang bisa dipakai produk (prioritas)

1. **[TE] Lifecycle fakta terukur untuk pilar "auditable memory"**: produk kita sudah punya scored
   learning + rollback; pola CANDIDATE→ACTIVE→RETIRED dengan GAMMA decay + LAMBDA asimetri +
   FIRE_GATE (lifecycle.py:17-23, 90-119) adalah model konkret yang bisa ditiru untuk menilai
   fakta hasil extraction — dengan provenance `soul://` kita di atasnya.
2. **[TE] Hard backstop anti-veto untuk autoreview**: kompensasi otomatis kita (autoreview.py)
   bisa memakai pola "judge tidak bisa memveto kill keras" (rules.py:130-144) untuk jalur keamanan.
3. **[mattpocock] Router skill (ask-matt)**: Hermes punya 40+ skill; satu router yang memetakan
   flow + precondition akan memangkas salah-pilih skill. Murah: satu SKILL.md.
4. **[mattpocock] Retro→environment**: "mechanical mistakes become deterministic checks" —
   terjemahan langsung ke runtime_guard/license_guard produk; tiap insiden wave menelurkan satu
   gate baru (preseden sudah ada: PoT ratchet, nosec B608).
5. **[superpowers] writing-skills TDD-untuk-skill**: uji skill internal produk (mis.
   hermes-agent-skill-authoring) dengan pressure scenario RED→GREEN, bukan hanya membaca ulang.
6. **[pm-skills] intended-vs-implemented + "say what you left out"**: formalisasi audit wave —
   audit SUB-164 sudah melakukan spirit-nya; menjadikannya prosedur bernama meningkatkan
   konsistensi auditor berikutnya.
7. **[karpathy] Uji satu-kalimat untuk auditor**: "setiap baris berubah tertelusur ke permintaan"
   + "akankah senior engineer bilang ini overcomplicated?" — checklist murah untuk review wave.

## 8. Skor akhir (terukur: pertanyaan terjawab berbukti / 10)

| Repo | Skor | Catatan batas jujur |
|---|---|---|
| TokenEconomizer | **10/10 = 100%** | experiments/ hanya struktur+README; di luar 10 pertanyaan plugin |
| superpowers | **10/10 = 100%** | 4/15 skill dibaca penuh, 11 header+aturan inti; pertanyaan dibidik ke yang dibaca |
| mattpocock | **10/10 = 100%** | 2 skill penuh + 6 header + peta router; 13 skill dari peta saja |
| pm-skills | **10/10 = 100%** | 2 skill penuh + 1 sebagian + 66 dari frontmatter/pola; upstream tidak di-clone |
| karpathy-skills | **10/10 = 100%** | Repo kecil, hampir seluruhnya terbaca |
| **Total** | **50/50 = 100%** | Target ≥90% per repo terlampaui; batas dibaca dicatat per repo |

Skor 100% berarti "10 pertanyaan kunci yang dipilih terjawab dengan bukti file:baris", BUKAN
"setiap baris kelima repo telah dibaca" — batas cakupan bacaan tercatat eksplisit di tiap bagian.
