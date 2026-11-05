# V3 — slate-calm

**Keluarga**: minimalis-profesional (fase 3, V3). Spec lengkap: [../STYLES-V3.md](../STYLES-V3.md).

## Prinsip hierarki
Minimal gelap tenang. Hierarki via LANGKAH PERMUKAAN + RUANG: panel utama di surface raised paling terang + padding terluas; tersier opacity 60%.

## Palet
Base #101216 · surface #16181D · raised #1C1F26 · hairline rgba(255,255,255,.06) · teks #E8EAEE · sekunder #9BA1AC · aksen sky #38BDF8 · ok #34D399 warn #FBBF24 danger #F87171

## Tipografi & densitas
Inter + JetBrains Mono. Judul 18px/600, KPI 26px/650. Radius kartu 12, kontrol 8, chip 6. Tabel standard 44px.

## Struktur & komponen kompleks
Sidebar ringkas 220px (pill aktif tint aksen); inspector = panel melayang di kanvas; status bar mono. Graph: selected ring sky 2px + halo tint 8%; satu node hover. Log: group header waktu, hover +8%, selected tint 10%.

## Sitasi pola (PATTERNS.md)
A1/A2(ref-04/34), D2(ref-04), E1, E6, B1-standard, C1–C3, D3/D4, J1/J3, N2, O1, Q2/Q4

## Layar uji (3 paling kompleks)
| Layar | PNG | HTML |
|---|---|---|
| Graph Explorer (~150 node, LOD 3 tingkat) | 01-graph.png | 01-graph.html |
| Agent Activity Log (50 agent, stream padat) | 02-activity.png | 02-activity.html |
| Analytics (multi-panel) | 03-analytics.png | 03-analytics.html |

Skor audit: lihat [../REPORT.md](../REPORT.md).
