# PLAN — Spesifikasi Layar Webview Know Your Context (Stitch)

Status: AKTIF — tidak ada generate tanpa spesifikasi di dokumen ini.
Sumber data: `src/know_your_context/serve/webview.py` (WEBVIEW_FEATURES + payload
functions) dan `docs/api.md`. Semua nilai contoh di bawah plausible terhadap
payload nyata.

## Konvensi global (berlaku semua layar)

- Chrome: sidebar kiri "Know Your Context" berisi 12 nav item (Overview,
  Activity, Graph, Search, Metrics, Health, Analytics, Audit, Proposals, Debt,
  System, Setup); item aktif ter-highlight. Topbar: judul layar + status store.
- Tema: dark profesional (#0B0D11 base, hairline #2A313E), Inter untuk UI,
  JetBrains Mono untuk data/hash/timestamp. Tanpa emoji, tanpa gradasi.
- Desktop 1440px.
- Setiap layar menampilkan state utama (terisi data); empty/error/loading
  dirancang sebagai variasi kecil di dalam spesifikasi (Stitch cukup menggambar
  state utama, tetapi hierarki harus menyisakan tempat yang jelas untuk pesan
  error typed `CODE — message` dan state kosong yang jujur).

## Arah varian (wajib berbeda nyata)

- **A — Dense Technical**: densitas maksimal; multi-panel split; tabel 28px;
  label mono uppercase kecil; semua metrik terlihat tanpa scroll; nuansa IDE/
  observability console.
- **B — Clean Spacious**: whitespace lega; hierarki tipografi besar; satu fokus
  per section; margin lebar; section bertumpuk dengan ruang napas; chrome
  minimal; terbaca dalam sekali lihat.
- **C — Dashboard Cards**: grid kartu KPI mandiri; tiap kartu satu nilai bold +
  sparkline/status pill; ringkasan per topik; scannable dalam 5 detik.

## Checklist audit per screen (0/1 per butir; lolos = ≥8/10)

1. Semua komponen yang dispesifikasikan hadir.
2. Data yang tampil sesuai field payload nyata (nama field/nilai plausible).
3. Hierarki visual benar (primer vs sekunder jelas).
4. Arah varian terlihat jelas dan konsisten dengan definisi A/B/C.
5. Dark profesional; tanpa emoji; tanpa gradasi.
6. Sidebar 12 item + highlight tab aktif benar.
7. Mono font untuk hash/timestamp/data teknis.
8. Status/severity/chip terbaca benar (bukan dekorasi acak).
9. Tidak ada teks lorem ipsum / placeholder generik yang menggantikan data.
10. Layak pakai: operator bisa membayangkan memakainya tanpa penjelasan.

Iterasi: audit gagal → perbaiki prompt → regenerate (maks 2 iterasi per varian;
alasan dicatat di INDEX.md).

---

## 01 Overview / Dashboard

**Data nyata**: health_payload {ok, exit_code, worst_severity, error_count,
warning_count, checked[11]}; graph totals {total_nodes, total_edges}; store
{file_size_bytes, journal_mode, schema_version}; metrics series harian;
attention: proposals count, debt count, refs failed; activity agents[];
relearn state.

**Komponen & hierarki**: (1) banner kesehatan full-width (ok=true, exit_code 0,
11/11 checks, worst_severity INFO, error 0, warning 2). (2) KPI strip 6 tile:
total_nodes 1,284 · total_edges 3,917 · store 46.2 MB · journal WAL · schema v5
· events today 214. (3) sparkline events/hari 14 hari. (4) panel Attention:
3 proposals pending, 12 debt items, 2 failed refs. (5) panel Agents:
agent-2, sub-139, operator. (6) relearn state machine IDLE → SCAN → DIFF →
SYNC, posisi IDLE.

**Interaksi**: klik tile → tab terkait; banner → Health.
**States**: kosong = store baru (0 nodes, banner "no data yet"); error =
health merah dengan worst_severity ERROR; loading = skeleton KPI.

## 02 Agent Activity Log

**Data nyata**: entries {seq, kind, event_type, agent, action, description,
nodes[], sha8, level, occurred_at}; agents[]; leases {node, holder, expires};
typed conflict NODE_IN_USE.

**Komponen**: tabel log streaming (kolom seq, level chip INFO/WARN/ERROR,
agent, action, description, nodes, sha8 mono 9f2c1ab4, occurred_at ISO);
filter bar (level chips, agent chips, search, autoscroll ON, group-by-day);
panel kanan Node Leases (node id, holder, ttl countdown 03m 12s, badge
NODE_IN_USE pada satu baris).

**Interaksi**: filter live; klik entry → expand payload; toggle autoscroll.
**States**: kosong = "no events yet"; error = payload tak terbaca → baris
WARN "unreadable payload"; loading = baris skeleton di atas (tail).

## 03 Graph Explorer

**Data nyata**: nodes {id, label, domain, kind, lifecycle_status, version,
scores{p,m,e,uncertainty,confidence}, x, y}; edges {source, target, kind};
node detail {relations, incident_edges, evidence_ids, symbol{name,kind,path,
line,col}, professional_level}; provenance verdict 5-link VERIFIED.

**Komponen**: canvas ~40 node card berarah, tint per domain P1–P7 + legend;
toolbar (zoom in/out/reset, domain filter chips, search-to-focus); inspector
kanan node 'heuristics-catalog': summary, domain P2, kind leaf, version 3,
scores P=0.828 M=0.341 E=0.898 u=0.050 c=0.912, professional_level 2,
relations (depends_on → retrieval-core; supersedes → heuristics-v1),
evidence_ids ev-0081/0082, symbol (function, src/…/retrieval.py:84),
provenance 5-link VERIFIED 5/5 dengan 5 baris hash mono.

**Interaksi**: pan/zoom; klik node → inspector; chip domain → filter.
**States**: kosong = graph 0 node (pesan + CTA ingest); error = layout gagal
→ fallback grid + banner LayoutError; loading = canvas skeleton.

## 04 Search + Query

**Data nyata**: search results {node_id, name, kind, path, line, col, score,
source}; filters kind/lang/limit; query neighborhood {node_id, direction
in/out/both, kind, edges[] source→relation→target, count}.

**Komponen**: search bar + dropdown kind (function/class/method) + lang
(py/ts) + limit; tabel hasil (name mono, kind chip, path:line:col, score 0.87,
source); panel Graph Neighborhood Query: input node_id, segmented in/out/both,
filter relation (depends_on/contradicts/supersedes/parent_of), tabel edges,
count 18.

**Interaksi**: ketik → autocomplete prefix; submit → hasil; ganti direction →
re-query.
**States**: kosong = "no symbols match"; error = VALIDATION_ERROR (limit di
luar rentang) → banner typed; loading = baris skeleton.

## 05 Metrics

**Data nyata**: ops metrics; event_series {day, events}; health_series
{recorded_at, score, max_score, event_count}; buckets day/kind/actor; bench
per-operasi {operation, count, min_ms, mean_ms, p50_ms, p95_ms, max_ms};
malformed_outcomes.

**Komponen**: KPI tiles (ops total 1,521, errors 0, malformed_outcomes 0,
busiest actor agent-2); bar chart events/hari 14 hari; line chart health
score vs max_score; panel buckets dengan toggle day/kind/actor (bar
horizontal); tabel bench (operation mono, count, min, mean, p50 6.43,
p95 7.43, max).

**Interaksi**: toggle bucket; klik operasi → detail.
**States**: kosong = "no invocations journaled"; error = health history
corrupt → banner "corrupt: …" jujur; loading = chart skeleton.

## 06 Health + System Diagnostics

**Data nyata**: {ok, error_count, warning_count, worst_severity, exit_code,
checked[11], findings[{code,severity,message,details}]}; store summary
{tables, schema_versions, file_size_bytes, freelist_pages}; timing per-route.

**Komponen**: banner HEALTHY exit_code 0; daftar 11 checks (store_readable,
chain_integrity, projection_fresh, leases_consistent, schema_version,
journal_mode, freelist_bounds, kill_switches, hardware_probe, sidecars,
contracts_frozen) masing-masing PASS; tabel findings (code, severity chip,
message, details mono); kartu store summary; panel timing.

**Interaksi**: klik check → detail; filter findings per severity.
**States**: kosong = n/a (checks selalu ada); error = banner UNHEALTHY merah
exit_code 1 dengan finding ERROR; loading = spinner per-check.

## 07 Analytics

**Data nyata**: centrality top ranking (skor 12-desimal); communities
{id, members[]}; bridges {candidates, emitted, by_kind}; node_count/total/
partial.

**Komponen**: header stats (1,284 / 1,284, partial=false); kolom Centrality
leaderboard (rank, node id mono, skor 0.828000000000, domain chip); kolom
Communities (kartu C1..C6, member count, member chips); kolom Bridges (tabel
edge antar komunitas, counts candidates 41 / emitted 12, by_kind depends_on 7,
contradicts 2).

**Interaksi**: klik node → Graph; klik komunitas → highlight.
**States**: kosong = "no nodes"; error = analytics gagal → banner typed;
loading = tiga kolom skeleton.

## 08 Audit Ledger

**Data nyata**: audit events {event_seq, event_type, actor, occurred_at,
payload}; verify {ok, checked, first_bad_seq}; ledger health snapshots;
debt ledger entries; experience journal {seq, prev_hash, entry_hash, payload}
+ verification {ok, checked}.

**Komponen**: banner verifikasi chain ok=true, checked 1,521, first_bad_seq
none, badge VERIFIED; tabel audit (event_seq mono, event_type, actor,
occurred_at, payload preview mono terpotong); stack tiga sub-ledger: health
snapshots (recorded_at, score), debt entries, experience journal dengan rantai
prev_hash → entry_hash (hex mono terpotong) + badge ok per ledger.

**Interaksi**: paging; klik event → payload penuh; tombol re-verify.
**States**: kosong = "ledger absent" (jujur, bukan error); error = chain
putus → banner merah dengan first_bad_seq; loading = skeleton tabel.

## 09 Proposals

**Data nyata**: review signals {signal_counts, items[]}; improvements
{proposal_id, node_id, impact, status proposed/applied, proposed_at,
applied_at}; detail trail events; extraction queue {receipt_id, status,
confidence_reported/effective, corroborators, contradictions, agent,
decided_by}.

**Komponen**: kiri Review signals (chips signal_counts low_confidence 4,
stale 3, orphan 2 + list baris node/signal/detected_at); tengah tabel
proposals (proposal_id mono prop-9f2c1a, node_id, impact chip low/medium/
high, timeline status proposed → applied, timestamps); satu baris expanded
dengan event trail (proposed/reviewed/applied + actor + seq); kanan
Extraction review queue v4 (receipt_id, status chip pending/approved/
rejected, confidence 0.92 → 0.71 bar, corroborators 2, contradictions 0,
agent, decided_by).

**Interaksi**: klik proposal → trail; approve/reject extraction (read-only
display).
**States**: kosong = "no open proposals"; error = proposal_id invalid →
typed 404; loading = skeleton.

## 10 Debt Queue

**Data nyata**: build_debt_queue items (node, reason, priority, cost, age),
count; burndown dari ledger hash-chained; kill switch KYS_DEBT_QUEUE.

**Komponen**: header 12 items + chip kill switch ON; tabel antrian prioritas
(rank, node_id mono, reason chip stale/low-confidence/orphan/unresolved-ref,
priority score, budget cost, age days); line chart burn-down open items dari
ledger (state ok); panel ringkasan by-reason + aksi "propose improvement"
per baris (proposal-gated).

**Interaksi**: sort prioritas; klik → proposal draft.
**States**: kosong = "queue empty — no debt detected"; error = ledger corrupt
→ banner jujur; loading = skeleton.

## 11 System (Store + Switches + Config + Hardware)

**Data nyata**: store stats {tables→counts, page_count, page_size_bytes,
freelist_pages, journal_mode, schema_versions}; switches {name, env, state,
affects, fail_safe}; config knobs {name, value, provenance}; hardware
{cpu_count, memory{total,available}, disk{kind,rotational}, gpu{kind}, plan
{base_workers, queue_scale, io_buffer_bytes, query_cache_entries}}; routes
{path, method, access, route_class}; relearn state.

**Komponen**: empat kuadran: (1) Store — tabel row counts (event_log 1,521,
node_revision 1,284, node_relation 3,917, evidence 860, symbols 2,204),
page_count 11,280, page_size 4096, freelist 12, WAL, schema 1→5; (2) Kill
switches — tabel name/env/state/affects/fail_safe; (3) Config — tabel knob,
value, provenance chip default/env/file; (4) Hardware — cpu 16, mem 64/41 GB,
disk nvme non-rotational, gpu none, plan workers 8, queue_scale 2, io 1 MiB,
cache 512. Panel kecil relearn state + ringkasan routes (45: path, method,
access, class read/heavy/export).

**Interaksi**: klik knob → detail; klik switch → detail.
**States**: kosong = n/a; error = hardware probe gagal → kartu
"available: false" + typed error (data, bukan 500); loading = skeleton.

## 12 Setup Lifecycle

**Data nyata**: setup progress {state, steps[]}; prefs {enabled, port,
updated_at}; live_refresh_seconds; contracts {14 knowledge ops frozen + ops,
notes}; export manifest artifacts {name, sha256, size, version}.

**Komponen**: stepper vertikal install (init store ✓, hardware probe ✓, chain
verification ✓, projection build ✓, webview start ▶ running) dengan durasi
mono; kartu preferensi webview (enabled true, port 8741, updated_at,
live_refresh_seconds 2.0, tombol on/off/status); panel Frozen Contracts: 14
operasi knowledge dengan chip v1/v2 + ops operations + catatan "new
capability lands in new addendum contracts, never in the frozen set"; panel
Export manifest: baris artifact name / sha256 mono terpotong / size /
version.

**Interaksi**: tombol on/off webview; klik step → log step.
**States**: kosong = pre-install (step 1 running); error = progress corrupt
→ banner "corrupt: …" jujur; loading = step berjalan dengan spinner.

---

## Log eksekusi (diisi selama program berjalan)

- Prompt gelombang 1 (kompak, sebelum koreksi operator) menghasilkan 15 screen
  (01-B/C, 02-A/B/C, 03-A/C, 04-A/B/C, 05-A/B, 06-A/B) + 3 gagal transport
  (01-A, 03-B, 05-C) + 06-C dihentikan mid-flight. Semua screen WAJIB lolos
  audit checklist di atas; yang gagal audit di-regenerate dengan prompt =
  spesifikasi lengkap dokumen ini.

## Hasil eksekusi (final)

- 36/36 screen ter-generate dan terunduh (HTML + PNG) — 0 hilang permanen.
- 4 kegagalan transport (SSE timeout / connection reset) berhasil di-retry.
- Audit mandiri per screen (checklist 10 butir, via screenshot + verifikasi HTML):
  34 PASS (2 di antaranya PASS* — komponen lengkap di HTML, sebagian di bawah
  fold screenshot), 2 FAIL dicatat jujur: 02-activity-A (kolom sha8/timestamp
  hilang + overlap, 2 iterasi habis) dan 11-system-C (ellipsis pada deskripsi
  kill switch, 2 iterasi habis; semua panel wajib hadir).
- 6 varian melalui iterasi desain ke-2 dengan prompt yang diperkuat dan berhasil
  PASS (01-A, 03-B, 05-A, 05-B, 05-C, 11-A); alasan tiap regen tercatat di
  INDEX.md dan stitch_state.json.
- Tabel lengkap: INDEX.md. Tidak ada commit (aturan).
