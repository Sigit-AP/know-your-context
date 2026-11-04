# INDEX — Stitch Design Program, Webview Know Your Context

Proyek Stitch: **Know Your Context — Webview Redesign**
(id `15376293374719786204`), design system "Obsidian Telemetry"
(`assets/7631f4c02d8e4033b7910b900aab3555`).

36 screen = 12 layar × 3 varian. Spesifikasi per layar: [PLAN.md](PLAN.md).
Riset + jawaban EXPERT-THINKING: [BRIEF.md](BRIEF.md).

Varian: **A** = dense-technical · **B** = clean-spacious · **C** = dashboard-cards.

Cara melihat di stitch.withgoogle.com:
1. Buka https://stitch.withgoogle.com/ → masuk dengan akun pemilik API key.
2. Pilih proyek **"Know Your Context — Webview Redesign"**.
3. Semua screen terdaftar di proyek; cari per judul layar. Screen id di tabel
   di bawah bisa dipakai untuk membedakan varian.
Secara lokal: buka file `.html` per baris di browser, atau lihat `.png`.

## Tabel artefak

| Layar | Varian | Screen id | HTML | PNG | Audit |
|---|---|---|---|---|---|
| 01 Overview / Dashboard | A dense-technical | 1921b1535f5643138063b9342ff06d41 | 01-overview-A.html | 01-overview-A.png | PASS (iterasi 2: regen dgn prompt spesifikasi) |
| 01 Overview / Dashboard | B clean-spacious | 0f2912a04605483f8028087fe08b669f | 01-overview-B.html | 01-overview-B.png | PASS |
| 01 Overview / Dashboard | C dashboard-cards | 881e41de379341b887d6c68e2e04d242 | 01-overview-C.html | 01-overview-C.png | PASS |
| 02 Agent Activity Log | A dense-technical | da7f5358ad4b4436bda113b9d8ed6991 | 02-activity-A.html | 02-activity-A.png | **FAIL** (2 iterasi habis — lihat catatan) |
| 02 Agent Activity Log | B clean-spacious | 741e3e195d9d4f3b9816ec88a4040301 | 02-activity-B.html | 02-activity-B.png | PASS |
| 02 Agent Activity Log | C dashboard-cards | 7a2755153a15482388e7c9720cb7da51 | 02-activity-C.html | 02-activity-C.png | PASS |
| 03 Graph Explorer | A dense-technical | c5e1694d7a624246a4b3c7812317ef83 | 03-graph-A.html | 03-graph-A.png | PASS |
| 03 Graph Explorer | B clean-spacious | 2bc6c07e0a2e4940afb18959cb67246e | 03-graph-B.html | 03-graph-B.png | PASS (iterasi 2: regen dgn prompt spesifikasi) |
| 03 Graph Explorer | C dashboard-cards | fa123afb05024b6ebcb009d90462f5ff | 03-graph-C.html | 03-graph-C.png | PASS |
| 04 Search + Query | A dense-technical | a0836701795f46bfa521630ebea89564 | 04-search-A.html | 04-search-A.png | PASS |
| 04 Search + Query | B clean-spacious | ca5501c46d5f45149e6160b949910d14 | 04-search-B.html | 04-search-B.png | PASS |
| 04 Search + Query | C dashboard-cards | 2347eb9f1172408ab8217ce4f8f7185f | 04-search-C.html | 04-search-C.png | PASS |
| 05 Metrics | A dense-technical | 7165bfeb967e4500bd50b776d174e65b | 05-metrics-A.html | 05-metrics-A.png | PASS (iterasi 2: bar chart diperbaiki) |
| 05 Metrics | B clean-spacious | 824be2e1dbeb4319bdf3091f3d24230f | 05-metrics-B.html | 05-metrics-B.png | PASS* (iterasi 2; tabel latency ada di HTML, di bawah fold screenshot) |
| 05 Metrics | C dashboard-cards | 7e37d27279bf4cd081c94b8e31977ade | 05-metrics-C.html | 05-metrics-C.png | PASS (iterasi 2: regen dgn prompt spesifikasi) |
| 06 Health + System | A dense-technical | f9cf0da1bbb84e80b8557af154fb182f | 06-health-A.html | 06-health-A.png | PASS |
| 06 Health + System | B clean-spacious | 562dd4c5c15e43e197c88fb9c4474f19 | 06-health-B.html | 06-health-B.png | PASS |
| 06 Health + System | C dashboard-cards | ea0c29b3ffb1440d91e540963672dd55 | 06-health-C.html | 06-health-C.png | PASS |
| 07 Analytics | A dense-technical | b88398e3b7fd4101847ffa6ea68b5320 | 07-analytics-A.html | 07-analytics-A.png | PASS |
| 07 Analytics | B clean-spacious | 579aeb0c8148447faa5aed07f6a0015f | 07-analytics-B.html | 07-analytics-B.png | PASS (transport retry ke-3: 2x connection reset) |
| 07 Analytics | C dashboard-cards | d8e95edd1e5947578a4c6f8fbf908796 | 07-analytics-C.html | 07-analytics-C.png | PASS |
| 08 Audit Ledger | A dense-technical | d02dc4e3047e41a7a2132023252cef13 | 08-audit-A.html | 08-audit-A.png | PASS (iterasi 2: regen dgn prompt spesifikasi) |
| 08 Audit Ledger | B clean-spacious | db80a4d7cf234ef09e983b274ff55dd7 | 08-audit-B.html | 08-audit-B.png | PASS |
| 08 Audit Ledger | C dashboard-cards | eb84b5d2d8da45b38d619f12dbaf0363 | 08-audit-C.html | 08-audit-C.png | PASS |
| 09 Proposals | A dense-technical | 345cc13a82894224ba9a41066c0e55fb | 09-proposals-A.html | 09-proposals-A.png | PASS |
| 09 Proposals | B clean-spacious | 76d1572ef8af48c18e5b82368b27a323 | 09-proposals-B.html | 09-proposals-B.png | PASS (iterasi 2: regen dgn prompt spesifikasi) |
| 09 Proposals | C dashboard-cards | 91083e73bee8447d9fc12c5123ca8ad0 | 09-proposals-C.html | 09-proposals-C.png | PASS |
| 10 Debt Queue | A dense-technical | 7da99bd6b366496c91550211724020be | 10-debt-A.html | 10-debt-A.png | PASS |
| 10 Debt Queue | B clean-spacious | fc4e550a936447c9a64700da35760f0d | 10-debt-B.html | 10-debt-B.png | PASS |
| 10 Debt Queue | C dashboard-cards | 0d15bb69da794db998f030ff0214ed4b | 10-debt-C.html | 10-debt-C.png | PASS |
| 11 System (Store+Switches+Config+Hardware) | A dense-technical | 1117f964672240d5af7888f746021446 | 11-system-A.html | 11-system-A.png | PASS (iterasi 2: sidebar 12 item diperbaiki) |
| 11 System (Store+Switches+Config+Hardware) | B clean-spacious | 6d84cc562e5a47b7a66df4fad369a5e9 | 11-system-B.html | 11-system-B.png | PASS |
| 11 System (Store+Switches+Config+Hardware) | C dashboard-cards | 7f3ca8d4eb49448c886d9fe184fdcc01 | 11-system-C.html | 11-system-C.png | **FAIL-minor** (2 iterasi habis — lihat catatan) |
| 12 Setup Lifecycle | A dense-technical | d1e10c9b2922470ea384e9ce68d347f9 | 12-setup-A.html | 12-setup-A.png | PASS |
| 12 Setup Lifecycle | B clean-spacious | 97cffd77d7fe481d8d64456286a83457 | 12-setup-B.html | 12-setup-B.png | PASS* (4 panel ada di HTML; 3 di antaranya di bawah fold screenshot) |
| 12 Setup Lifecycle | C dashboard-cards | 4bf2c3a21e12493cae5e8eae68f5ec54 | 12-setup-C.html | 12-setup-C.png | PASS |

## Catatan audit jujur

- **02-activity-A — FAIL (iterasi habis).** Iterasi 1: chip aksi menimpa teks
  deskripsi + search field absen. Iterasi 2 (prompt diperkuat): kolom sha8 +
  timestamp hilang, masih ada overlap judul/breadcrumb, search input tetap
  absen. Batas 2 iterasi tercapai → dicatat gagal, tidak dipaksakan. Layar
  Activity tetap punya 2 varian layak (B dan C, keduanya PASS).
- **11-system-C — FAIL-minor (iterasi habis).** Iterasi 2 sudah memuat semua
  panel wajib (store, kill switches, config, hardware, relearn, routes) —
  satu-satunya temuan: teks deskripsi "affects" pada kartu kill switch
  terpotong ellipsis (pola single-line-truncation yang memang dipakai design
  system). Dicatat apa adanya.
- **PASS\*** = komponen terverifikasi ada di HTML (grep) tetapi berada di bawah
  fold screenshot; desain itu sendiri lengkap.
- Transport: 4 kegagalan sementara (SSE/connection reset) berhasil di-retry;
  0 layar hilang permanen. 36/36 screen punya HTML + PNG.

## Ringkasan

| | A dense | B spacious | C cards |
|---|---|---|---|
| PASS (bersih) | 11 | 10 | 11 |
| PASS* (lengkap di HTML, sebagian di bawah fold screenshot) | 0 | 2 | 0 |
| FAIL (dicatat jujur, iterasi habis) | 1 (02-A) | 0 | 1 (11-C minor) |

Total: 36/36 screen terunduh (HTML + PNG); 34 PASS/PASS*; 2 FAIL tercatat
jujur tanpa disembunyikan.
