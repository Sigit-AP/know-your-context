# V2 — ink-mono

**Keluarga**: minimalis-profesional (fase 3, V3). Spec lengkap: [../STYLES-V3.md](../STYLES-V3.md).

## Prinsip hierarki
Minimal putih Swiss. Hierarki via SKALA TIPE MURNI — tanpa kartu/panel; zona dipisah hairline. Tingkat-1 = display 40–48px/700; tingkat-4 = micro 10px uppercase. Aksen biru HANYA untuk interaktif.

## Palet
Base #FFFFFF · hairline #E5E5E5 · rule #111111 · tinta #0A0A0A · sekunder #525252 · aksen biru #1D4ED8 (interaktif saja) · status = kata+glyph (INFO ·, WARN ▲, ERROR ■)

## Tipografi & densitas
Inter agresif (tracking −2% display) + JetBrains Mono 12px data. Radius 0–2px. Whitespace sebagai konten.

## Struktur & komponen kompleks
Header 48px text-link (aktif = underline hitam 2px); kolom faset kiri 220px; kolom inspector kanan 340px (rule hitam 1px). Graph: node outline circle, selected = isi tinta solid + ring biru. Log: hover #F5F5F5, selected bar hitam 3px.

## Sitasi pola (PATTERNS.md)
Q1–Q5(ref-94/95/96), C1, D1, B2, E6, H1, J1/J3, M1, N2/N3, P5

## Layar uji (3 paling kompleks)
| Layar | PNG | HTML |
|---|---|---|
| Graph Explorer (~150 node, LOD 3 tingkat) | 01-graph.png | 01-graph.html |
| Agent Activity Log (50 agent, stream padat) | 02-activity.png | 02-activity.html |
| Analytics (multi-panel) | 03-analytics.png | 03-analytics.html |

Skor audit: lihat [../REPORT.md](../REPORT.md).
