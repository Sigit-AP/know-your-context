# Verdict (Unified)

## Hasil #1 (skala/hardware) ↔ Hasil #2 (verdict vs kompetitor)

Angka skala **L1–L4** (jumlah file/agen) menjadi dasar metrik‑metrik pada instrumen verdict. Tier hardware (host 2 vCPU / 3.9 GB RAM, sqlite RSS cap, dll.) menjelaskan perubahan nilai: contoh, penurunan *RSS ingest* pada L4 dari ~1.36 GB ke 0.72 GB mengubah skor `ingest_rss_mb` dari KALAH menjadi MENANG.

### Tabel status akhir per level

| Level | MENANG | SETARA | KALAH | Sumber & reproduksi |
|------|--------|--------|-------|---------------------|
| L1 | 4 | 6 | 1* | L1 baris di `final-head-to-head.md`; reproduksi: `uv run python bench/verdict/run.py metric <id> --repo kyc` |
| L2 | 7 | 4 | 0 | L2 baris di `final-head-to-head.md`; reproduksi per‑metrik via perintah di atas |
| L3 | 10 | 1 | 0 | L3 baris di `final-head-to-head.md`; reproduksi per‑metrik via perintah di atas |
| L4 | 11 | 0 | 0 | L4 baris di `final-head-to-head.md`; reproduksi per‑metrik via perintah di atas |

\* Pada L1, metrik latency `q_wall_s` dan `q_p50_ms` masih sedikit di atas graphify, sehingga **L1 belum sepenuhnya menang**.

### Pencapaian vs yang belum selesai
- **Sudah tercapai:** Skor MENANG/SETARA untuk L2–L4, termasuk perbaikan RSS ingest dan storm RSS yang kini berada di bawah kompetitor.
- **Belum tercapai:** Pada L1, metrik latency (`q_wall_s`, `q_p50_ms`) masih lebih tinggi daripada graphify; ini berarti **L1 belum sepenuhnya menang**.

---
*Bukti*: baris‑baris ledger di `docs/research/final-head-to-head.md` (mis. L3 menang 10/1/0 pada baris 215‑230), serta perintah reproduksi tercantum di bagian “How to run” dokumen tersebut.
