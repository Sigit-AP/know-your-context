# V5 — console-dark

**Keluarga**: minimalis-profesional (fase 3, V3). Spec lengkap: [../STYLES-V3.md](../STYLES-V3.md).

## Prinsip hierarki
Minimal ops modern near-black. Hierarki via 4 TINGKAT KECERAHAN teks (100/85/65/40%) + posisi; data utama paling terang, label struktural paling redup. Detail = dock bawah horizontal 200px.

## Palet
Base #09090B · panel #111113 · raised #18181B · hairline #232328 · teks #F4F4F5/#D4D4D8/#A1A1AA/#52525B · aksen violet #8B5CF6 · ok #4ADE80 warn #FACC15 danger #F87171

## Tipografi & densitas
Inter (label UI) + JetBrains Mono (SEMUA data + micro label). Judul 15px/600 — data yang besar. Radius 6px. Tabel dense 36px.

## Struktur & komponen kompleks
Utility bar mono 44px (tab aktif = underline violet); faset kiri 220px; dock bawah 200px inspector horizontal; status strip E7. Graph: dot-grid, selected border violet 2px. Log: hover wash #18181B, selected bar kiri 2px + wash 8%.

## Sitasi pola (PATTERNS.md)
E7(ref-52), B1-dense, C1, D4, E6, B4, J1/J3, N3, O3, P5

## Layar uji (3 paling kompleks)
| Layar | PNG | HTML |
|---|---|---|
| Graph Explorer (~150 node, LOD 3 tingkat) | 01-graph.png | 01-graph.html |
| Agent Activity Log (50 agent, stream padat) | 02-activity.png | 02-activity.html |
| Analytics (multi-panel) | 03-analytics.png | 03-analytics.html |

Skor audit: lihat [../REPORT.md](../REPORT.md).
