# Design Brief — Know Your Context Webview (Stitch program)

Sub-agent: SUB-139 (DESIGNER). Scope: 12 layar kunci × 3 varian desain via Stitch
(project id 15376293374719786204). Desktop 1440px, dark profesional, tanpa emoji,
tanpa gradasi. Output: HTML + PNG per screen + INDEX.md.

## EXPERT-THINKING (7 pertanyaan, wajib)

1. **Masalah**: Operator memilih arah desain webview Know Your Context tanpa
   bisa membandingkan alternatif nyata — satu mockup tunggal memaksa keputusan
   buta. Masalahnya: "bagaimana 19 tab / 30 fitur berpayload nyata ini terlihat
   dalam tiga bahasa desain berbeda agar operator memilih berdasarkan bukti
   visual, bukan imajinasi".
2. **Kekurangan/kendala**: Data yang digambar harus persis payload API nyata
   (field, tipe, nilai plausible) — mockup yang mengarang field akan
   mengkhianati implementasi. Dark profesional, tanpa emoji/gradasi, desktop
   1440px. Stitch rate/quota: 1 retry per layar, gagal dicatat jujur. Tidak ada
   commit.
3. **Tujuan terukur**: 36 screen (12 layar × 3 varian) terunduh lengkap
   (HTML + PNG) di docs/design/stitch/ + INDEX.md dengan tabel lengkap;
   setiap kegagalan tercatat eksplisit (bukan disembunyikan).
4. **Manfaat**: Operator/user memilih 1 dari 3 arah desain per layar dengan
   bukti visual; implementor berikutnya menerjemahkan HTML Stitch menjadi
   index.html webview tanpa menebak struktur data.
5. **Pelajaran dari masalah**: Kegagalan umum program desain: prompt generik
   ("dashboard bagus") menghasilkan layar yang tidak mencerminkan data nyata;
   dan kegagalan generate yang disembunyikan membuat indeks bohong. Dicegah
   dengan prompt per-fitur spesifik payload + pencatatan gagal-jujur.
6. **Prinsip desain (tidak bisa dilanggar)**:
   - Data nyata: setiap prompt menyebut field payload aktual dari webview.py.
   - Satu arah per varian: A=dense-technical, B=clean-spacious, C=dashboard-card;
     tidak mencampur arah dalam satu screen.
   - Konsistensi: satu design system Stitch untuk semua screen.
   - Gagal-jujur: retry 1x, lalu catat; tidak pernah mengarang file.
7. **Kill criteria**: Jika >30% generate gagal setelah retry (≈11 screen),
   berhenti, laporkan ke koordinator dengan daftar pasti — jangan bakar kuota.
   Alternatif: kurangi jadi 1 varian per layar (12 screen) sebagai fallback.

## RISET ringkas per fitur (dari webview.py + docs/api.md)

Sumber: `src/know_your_context/serve/webview.py` (WEBVIEW_FEATURES 30 baris,
payload functions), `docs/api.md`. Ringkasan data nyata per layar:

1. **Overview/Dashboard** (sintesis): health_payload {ok, error_count,
   warning_count, worst_severity, exit_code, checked[11], findings[]};
   store_stats {tables→row counts, page_count, page_size_bytes,
   file_size_bytes, freelist_pages, journal_mode, schema_versions[]};
   graph totals {total_nodes, total_edges, truncated}; activity agents[];
   relearn state. UX: jawaban 5 detik "sehat? berapa besar? ada yang pending?"
2. **Agent Activity Log** (/api/activity): entries {seq, kind, event_type,
   agent, action, description, nodes[], sha8, level, occurred_at}, agents[];
   leases {node, holder, expires_at} + typed NODE_IN_USE. UX: log streaming
   dengan filter level/agent, grouping, autoscroll, hash chain sha8 terlihat.
3. **Graph Explorer** (/api/graph): nodes {id,label,domain,kind,
   lifecycle_status,version,scores{p,m,e,uncertainty,confidence},provenance[],
   x,y}, edges {source,target,kind}; node detail {relations, incident_edges,
   evidence_ids, symbol{name,kind,path,line,col}, professional_level};
   provenance 5-link verdict {verified, links[]}. UX: canvas pan/zoom dominan,
   panel detail kanan, legend domain P1-P7.
4. **Search + Query** (/api/search, /api/query): hasil {node_id,name,kind,
   path,line,col,score,source}; filter kind/lang; query neighborhood
   {node_id,direction in/out/both,kind,edges[]}. UX: search bar utama,
   tabel hasil padat, panel neighborhood.
5. **Metrics** (/api/metrics*): ops metrics ter-redact; series harian
   {day,events}; health_series {recorded_at,score,max_score,event_count};
   buckets day/kind/actor; bench per-operasi {operation,count,min,max,mean,
   p50,p95 ms, malformed_outcomes}. UX: chart series + tabel statistik.
6. **Health + System** (/api/health*): 11 checks fail-closed, findings
   {code,severity,message,details}, worst_severity, exit_code; store ringkas,
   timing, switches. UX: status banner + daftar checks + findings berseverity.
7. **Analytics** (/api/analytics*): centrality {global top ranking},
   communities {id,members[]}, bridges {candidates,emitted,by_kind},
   node_count/total/partial. UX: 3 kolom leaderboard + graf mini.
8. **Audit Ledger** (/api/audit*, /api/ledger/*): events {event_seq,
   event_type,actor,occurred_at,payload}; verify {ok,checked,first_bad_seq};
   ledger health snapshots; debt ledger entries; experience journal
   {seq,prev_hash,entry_hash,payload} + verification. UX: tabel hash-chain,
   badge verifikasi, prev/entry hash monospace.
9. **Proposals** (/api/proposals, /api/improvements*): review signals
   {signal_counts,items[]}; proposals {proposal_id,node_id,impact,status
   proposed/applied,proposed_at,applied_at}; detail trail events;
   extraction queue (contract v4) {receipt_id,status,actor,source_sha256,
   nodes,edges,confidence_reported/effective,corroborators,contradictions,
   agent,decided_by}. UX: antrian review dua bagian + status lifecycle.
10. **Debt Queue** (/api/debt*): build_debt_queue items berprioritas
    berbudget, count, burndown dari ledger hash-chained, kill switch
    KYS_DEBT_QUEUE. UX: antrian prioritas + grafik burn-down.
11. **Store + Switches + Config + Hardware** (/api/store/*, /api/switches,
    /api/config, /api/hardware, /api/routes, /api/relearn): store stats
    per-tabel + pragma; switches {name,env,state,affects,fail_safe}; knobs
    {name,value,provenance}; hardware {cpu_count,memory,disk,gpu,plan
    base_workers/queue_scale/io_buffer_bytes/query_cache_entries}; routes
    {path,method,access,route_class,summary}; relearn state machine.
    UX: 4 panel introspeksi teknis.
12. **Setup Lifecycle** (/api/setup, /api/contracts, /api/export/*):
    progress {state,steps[]}, prefs {enabled,port,updated_at},
    live_refresh_seconds; contracts {14 knowledge ops frozen + ops, notes};
    export subgraph manifest {artifacts[]{name,sha256,size,version}}.
    UX: langkah instalasi vertikal + status webview on/off + kontrak beku.

## PLANNING (pm): pengelompokan 12 layar — sesuai daftar operator di atas.

Prioritas (MoSCoW): semua 12 = Must; penomoran = urutan generate.
Varian per layar (arah berbeda, konsisten lintas layar):
- **A — Dense Technical**: mono-space, tabel padat, panel sempit, densitas
  informasi maksimal (gaya IDE/terminal pro).
- **B — Clean Spacious**: whitespace lega, hierarki tipografi besar, satu
  fokus per section, navigasi tenang.
- **C — Dashboard Cards**: grid kartu KPI, ringkasan visual, chart kecil,
  status pill.

Risiko (pre-mortem singkat): (1) Stitch mengarang field → prompt menyebut
field eksplisit; (2) kuota/rate limit → retry 1x + jeda; (3) screenshot
tidak tersedia per screen → download HTML tetap disimpan, PNG dicatat absent;
(4) design system asset id salah → fallback tanpa designSystem pada retry.
