# V4 — modular-gray

**Keluarga**: minimalis-profesional (fase 3, V3). Spec lengkap: [../STYLES-V3.md](../STYLES-V3.md).

## Prinsip hierarki
Minimal modular terang. Hierarki via UKURAN MODUL pada grid 12 kolom: SATU modul primer 8 kolom penuh-tinggi; sekunder 4 kolom; tersier 4 kolom setengah-tinggi.

## Palet
Kanvas #EEF1F5 · modul #FFF · hairline #DCE2EA · teks #101828 · sekunder #667085 · aksen teal #0D9488 · ok #16A34A warn #D97706 danger #DC2626

## Tipografi & densitas
Inter + JetBrains Mono. Judul modul 13px/600, KPI 24px/650. Radius modul 12, kontrol 8, chip 6. Tabel dense 38px.

## Struktur & komponen kompleks
Topbar putih 52px, tab tengah (aktif = pill tint teal). Graph: modul kanvas 8 kolom + stack inspector/evidence 4 kolom; filter strip 12 kolom. Log: modul stream 8 kolom + roster agent (50) + level mix.

## Sitasi pola (PATTERNS.md)
A3/A4, B1-dense, B3–B5, C1–C3, D1–D4, E1, E6, H2/H3, J1/J3, K5, N2/N3, P5

## Layar uji (3 paling kompleks)
| Layar | PNG | HTML |
|---|---|---|
| Graph Explorer (~150 node, LOD 3 tingkat) | 01-graph.png | 01-graph.html |
| Agent Activity Log (50 agent, stream padat) | 02-activity.png | 02-activity.html |
| Analytics (multi-panel) | 03-analytics.png | 03-analytics.html |

Skor audit: lihat [../REPORT.md](../REPORT.md).
