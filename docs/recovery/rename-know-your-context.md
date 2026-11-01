# Rename: Know Your Soul → Know Your Context (SUB-115)

- Tanggal: 2026-11-01 ~10:00 UTC
- Repo: `/root/know-your-soul`, branch `main`, base HEAD `370d4d5`
- Sifat: rename brand menyeluruh atas perintah operator. **Tidak ada commit** — commit adalah langkah coordinator. Direktori repo, repo GitHub, dan PyPI **tidak** di-rename di sini (langkah coordinator).
- Paralel: SUB-113 (`bench/competitive/`) dan SUB-114 (`bench/scorecard/`) sedang menulis di direktori mereka — file-file itu SENGAJA tidak disentuh sweep ini; referensi nama di sana disesuaikan coordinator.

## Alasan

Produk sudah meluas dari korpus SOUL.md ke konteks untuk agent secara umum —
tiga pilar: **Knowledge** (multi-modal: markdown, PDF, teks, gambar/scan via
agent eksternal), **Code intelligence** (AST, simbol, call hierarchy, 19
bahasa), **Auditable memory** (deterministik, ternilai, rollback-exact). Nama
lama tidak lagi mencerminkan produk. Framing "universal" dilarang operator.

## Pemetaan old → new

| Lama | Baru | Cakupan |
|------|------|---------|
| Paket `know_your_soul` (`src/know_your_soul/`) | `know_your_context` (`src/know_your_context/`, via `git mv`) | seluruh impor internal, semua tests, bench/tools/scripts, `--cov` di CI |
| Nama proyek `know-your-soul` | `know-your-context` | `pyproject.toml`, `uv.lock` (diregenerasi), badge/URL README |
| Console script `know-your-soul` | `know-your-context` (tanpa alias; `kyc` TIDAK disediakan) | `[project.scripts]`, `prog=` argparse, Docker `ENTRYPOINT` |
| `--version` flag | **baru** — `know-your-context --version` → `know-your-context 0.3.0` | `cli.py` + 1 test baru (`test_cli_version_flag`) |
| Server MCP `know-your-soul` | `know-your-context` | `SERVER_NAME`, `version("know-your-context")`, `prog="know-your-context-mcp"`; tool names menjadi `mcp__know-your-context__*` |
| Default store `know-your-soul.sqlite3` | `know-your-context.sqlite3` | `cli.py`, `ops/config.py`; juga `know-your-context-projection`, `know-your-context.sock` |
| Env `KNOW_YOUR_SOUL_*` | `KNOW_YOUR_CONTEXT_*` | **bersih, tanpa alias** — fallback lama `SOUL_STORE` (dari rename sebelumnya) ikut DIHAPUS atas perintah operator; 2 test fallback diganti |
| URI provenance `soul://` | `context://` | src (importer default, MCP resources, fallback projection), tests, bench — lihat pengecualian beku di bawah |
| Crate Rust `know-your-soul-kernel` / modul PyO3 `know_your_soul_kernel` | `know-your-context-kernel` / `know_your_context_kernel` | `kernel-rust/Cargo.toml`, `kernel-rust/pyproject.toml`, `lib.rs`/`main.rs`, `parity.rs`, `kernel_bridge.py`; rebuild via `PYO3_PYTHON=.venv/bin/python cargo build [--release] --features pyo3`; .so lama dihapus |
| Judul report "SOUL Knowledge Graph Report" / "SOUL Graph Analysis Report" / DOT `digraph soul_knowledge` | "Know Your Context Graph …" / `digraph know_your_context` | `projection.py`, `report_v2.py`, `graph_report.py`, `builtin.py`, `dot.py` + 8 assertion test |
| User/group container `soul` | `context` | `deploy/Dockerfile` |
| Docker image tag `know-your-soul:ci` | `know-your-context:ci` | `.github/workflows/ci.yml` |
| README EN+ID | rewrite brand + posisi tiga pilar | tagline, blok tiga pilar setelah "Why", split "Universal ingestion" → "Knowledge ingestion" + "Code intelligence" |

## Yang SENGAJA TIDAK diubah (beserta alasannya)

| Token | Lokasi | Alasan |
|-------|--------|--------|
| `soul://` di `tests/fixtures/*.json` (3 file) | fixtures FROZEN | byte-frozen; diganti = mematahkan kontrak pin. Didokumentasikan sebagai legacy scheme di `docs/operations.md` |
| `know-your-soul <cmd>` di `docs/contracts/*.md` (6 file) | kontrak FROZEN | sha256-pinned oleh `tests/test_contract_v3_shape.py` + `tests/test_extraction_contract_v4_shape.py` — satu byte drift gagal. Rekaman historis; CLI aktif terdokumentasi di README/operations |
| `SourceKind.SOUL_CORPUS`, `SOUL_MD`, "raw SOUL", path `/root/.hermes/knowledge/soul` | src, bench | Nama input eksternal (korpus SOUL.md), bukan brand; enum value tersimpan di store on-disk |
| salt `soul-fragment`, idempotency key `soul:root:*`, artifak backup `soul.db`, state `.soul-watch.json`, thread `soul-source-watch` | src | Identifier format data on-disk / frozen behavior — mengganti memutus kontinuitas store & backup lama dan/atau byte-pin test |
| `kys-kernel` (binary), `kys_token` (cookie), `KYS_DISABLE_RUST_KERNEL`/`KYS_REQUIRE_RUST_KERNEL` (env internal) | kernel, webview, tests | Singkatan internal, bukan permukaan brand; di luar scope operator. Keputusan akhir diserahkan ke coordinator |
| `bench/corpus/`, `bench/cases.json`, `bench/tokens/{manifest,questions}.json`, `bench/**/results/*` | bench | Korpus + kasus + ledger beku (sha256-pinned di manifest / rekaman historis) |
| `docs/{adr,mathematics,recovery,research,superpowers}/`, `.superpowers/` | docs historis | Rekam jejak point-in-time (preseden: rename SUB-55) |
| `bench/competitive/`, `bench/scorecard/`, `tests/test_competitive_bench.py`, `tests/test_scorecard.py` | scope SUB-113/114 | Milik agent lain; coordinator menyesuaikan referensi nama setelah mereka selesai |
| `NOTICE` | root | Dipertahankan sesuai perintah |

## Bukti gate (working tree pasca-rename)

| Gate | Perintah | Exit | Hasil |
|------|----------|------|-------|
| pytest penuh (sepi, final) | `KYS_REQUIRE_RUST_KERNEL=1 .venv/bin/pytest tests/ -q` | 0 | **6830 passed, 1 skipped** (skip = test SUB-113 by-design tanpa codegraph CLI), 13m22s |
| ruff check (scope SUB-115) | `ruff check src tests bench/{run.py,codebase,institutional,memory,tokens} tools scripts kernel-rust` | 0 | All checks passed |
| ruff format (scope SUB-115) | `ruff format --check` (scope sama) | 0 | 297 files already formatted |
| pyright | `.venv/bin/pyright` | 0 | 0 errors, 0 warnings |
| uv lock | `uv lock --check` | 0 | konsisten (lock diregenerasi + `uv sync`: know-your-context==0.3.0 terinstal, know-your-soul ter-uninstall) |
| license_guard | `python scripts/license_guard.py` | 0 | OK |
| bench check | `python bench/run.py check` | 0 | OK (cases schema, frozen corpus integrity, estimator parity) |
| bandit | `bandit -ll -r src` | 0 | 0 medium+ |
| Rust kernel | `cargo test --release` | 0 | 5/5 + parity; bridge memuat `know_your_context_kernel` (61/61 test kernel Python dengan `KYS_REQUIRE_RUST_KERNEL=1`) |
| CLI baru | `know-your-context --version` | 0 | `know-your-context 0.3.0`; entry `know-your-soul` hilang dari `.venv/bin` |

Insiden run#1 (6 gagal): 4 kontrak-shape — test mengutip nama CLI dari doc v3/v4 beku, ekspektasi test dikembalikan ke `know-your-soul` (doc tidak boleh berubah); 1 power-loss — artifak edit header/dot di tengah run (child process membaca file baru); 1 storm gate — flake beban CPU dari cargo build paralel (hijau standalone 9/9). Semua tertutup di run final.

Catatan koordinator: sisa `know-your-soul` di `bench/competitive/`, `bench/scorecard/`, `tests/test_competitive_bench.py`, `tests/test_scorecard.py` adalah scope SUB-113/114; ruff repo-wide akan merah di 2 file mereka yang masih mid-flight (bukan milik SUB-115).
