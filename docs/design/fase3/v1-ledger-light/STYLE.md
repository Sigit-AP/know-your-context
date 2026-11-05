# V1 — ledger-light

**Keluarga**: minimalis-profesional (fase 3, V3). Spec lengkap: [../STYLES-V3.md](../STYLES-V3.md).

## Prinsip hierarki
Minimal profesional terang hangat. Hierarki via POSISI + SKALA: zona utama kiri-atas terluas (tingkat-1), rail kanan 320px lebih tenang (tingkat-3), status bar mono 11px paling minim (tingkat-4). Label seksi dinomori.

## Palet
Base #FAFAF8 · kartu #FFF · hairline #E7E5E0 · tinta #1C1917 · sekunder #78716C · aksen indigo #4338CA · ok #15803D warn #B45309 danger #B91C1C

## Tipografi & densitas
Inter + JetBrains Mono (data). Skala: 11 micro / 12.5 body / 14 nav / 16 panel / 28 halaman / 24 KPI. Radius kartu 10, kontrol 6, chip 4. Tabel dense 36px.

## Struktur & komponen kompleks
Topbar 56px + nav pill; rail kanan inspector; status bar mono bawah. Graph: LOD 3 tingkat, selected = ring aksen 2px, non-tetangga dim 40%. Log: hover wash, selected bar kiri 3px, fokus ring 2px.

## Sitasi pola (PATTERNS.md)
A1(ref-45), A3, B1-dense, B2–B5, C1–C3, D1–D4, E1, E6, E7, H1–H5, J1/J3/J5, N2/N3, P5, Q2–Q4, R2

## Layar uji (3 paling kompleks)
| Layar | PNG | HTML |
|---|---|---|
| Graph Explorer (~150 node, LOD 3 tingkat) | 01-graph.png | 01-graph.html |
| Agent Activity Log (50 agent, stream padat) | 02-activity.png | 02-activity.html |
| Analytics (multi-panel) | 03-analytics.png | 03-analytics.html |

Skor audit: lihat [../REPORT.md](../REPORT.md).
