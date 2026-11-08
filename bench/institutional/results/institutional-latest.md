# Institutional graph benchmark — skor

Referensi: `networkx 3.7 (+ self-validated numpy PageRank reference; networkx.pagerank requires scipy which is intentionally not a dependency)` · repeats=3 · quick=False

**8/8 available kernels PASS correctness** (skipped jujur: 2 — kernel belum tersedia)

| Kernel | Suite | Status | Detail |
|---|---|---|---|
| pagerank | LDBC Graphalytics: PR, GAPBS: PR | PASS | epsilon rel<=0.001 abs<=0.0001 vs self-validated numpy reference (L1 residual < 1e-10) |
| wcc | LDBC Graphalytics: WCC, GAPBS: CC | PASS | equivalence (same weak components as networkx) |
| bc | GAPBS: BC | PASS | epsilon rel<=1e-06 abs<=1e-09 vs networkx Brandes (normalized, directed) |
| cdlp | LDBC Graphalytics: CDLP | PASS | exact-cover everywhere + equivalence vs networkx asyn_lpa(seed=0) on unambiguous datasets |
| lcc | LDBC Graphalytics: LCC | PASS | epsilon rel<=1e-06 abs<=1e-09 vs networkx.clustering (undirected) |
| tc | GAPBS: TC | PASS | exact integer equality vs networkx.triangles sum/3 |
| louvain | project-local (GAPBS-adjacent) | PASS | exact-cover + modularity cross-check vs networkx + equivalence on unambiguous datasets |
| cycles | project-local (no LDBC/GAPBS counterpart) | PASS | exact set match vs networkx.simple_cycles (canonical rotation) |
| bfs | LDBC Graphalytics: BFS, GAPBS: BFS | SKIP | kernel belum tersedia: tidak ada entrypoint BFS publik (hanya analytics.centrality._bfs_distances privat) |
| sssp | LDBC Graphalytics: SSSP, GAPBS: SSSP | SKIP | kernel belum tersedia: graph read-model tidak membawa bobot edge; tidak ada SSSP publik |

## pagerank

- ours runs a fixed 64 iterations (no convergence stop); public entrypoint computes the full centrality suite, so ratio_public overstates per-kernel cost — ratio_kernel isolates PageRank.
- fixed-iteration float clone cross-check OK (max_abs_err=1.11e-16, tol=1e-9)

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | 3.97e-13 | 14.200 | 0.923 | 1.078 | 13.2x | 0.9x |
| diamond | 4 | 4 | pass | yes | 4e-13 | 11.589 | 1.423 | 1.301 | 8.9x | 1.1x |
| two_triangles_bridge | 6 | 7 | pass | yes | 4.01e-13 | 11.161 | 2.702 | 2.587 | 4.3x | 1.0x |
| star9_bidir | 9 | 16 | pass | yes | 1.09e-05 | 15.716 | 2.402 | 4.878 | 3.2x | 0.5x |
| directed_cycle6 | 6 | 6 | pass | yes | 3.33e-13 | 5.669 | 1.569 | 0.119 | 47.6x | 13.2x |
| two_cycles_shared_node | 5 | 6 | pass | yes | 1.86e-06 | 5.267 | 1.441 | 4.689 | 1.1x | 0.3x |
| disconnected3 | 6 | 3 | pass | yes | 3.97e-13 | 5.849 | 1.220 | 1.245 | 4.7x | 1.0x |
| cliques_4x5 | 20 | 80 | pass | yes | 0 | 28.255 | 10.194 | 0.274 | 103.3x | 37.3x |
| erdos_n64_m192_s11 | 64 | 192 | pass | yes | 4.88e-13 | 106.947 | 21.120 | 2.204 | 48.5x | 9.6x |
| erdos_n256_m1024_s12 | 256 | 1024 | pass | yes | 4.97e-13 | 492.954 | 140.022 | 5.754 | 85.7x | 24.3x |
| erdos_n512_m2048_s13 | 512 | 2048 | pass | yes | 5e-13 | 1305.337 | 151.877 | 11.872 | 109.9x | 12.8x |

## wcc

- public entrypoint computes the full connectivity suite (weak+strong+articulation+k-core); ratio_kernel isolates the WCC pass.

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | — | 0.058 | 0.015 | 0.035 | 1.7x | 0.4x |
| diamond | 4 | 4 | pass | yes | — | 0.094 | 0.036 | 0.041 | 2.3x | 0.9x |
| two_triangles_bridge | 6 | 7 | pass | yes | — | 0.134 | 0.043 | 0.057 | 2.3x | 0.8x |
| star9_bidir | 9 | 16 | pass | yes | — | 0.232 | 0.074 | 0.083 | 2.8x | 0.9x |
| directed_cycle6 | 6 | 6 | pass | yes | — | 0.132 | 0.048 | 0.056 | 2.3x | 0.8x |
| two_cycles_shared_node | 5 | 6 | pass | yes | — | 0.108 | 0.019 | 0.023 | 4.7x | 0.8x |
| disconnected3 | 6 | 3 | pass | yes | — | 0.074 | 0.020 | 0.023 | 3.1x | 0.8x |
| cliques_4x5 | 20 | 80 | pass | yes | — | 0.316 | 0.107 | 0.128 | 2.5x | 0.8x |
| erdos_n64_m192_s11 | 64 | 192 | pass | yes | — | 1.664 | 0.282 | 0.245 | 6.8x | 1.2x |
| erdos_n256_m1024_s12 | 256 | 1024 | pass | yes | — | 5.508 | 1.553 | 1.193 | 4.6x | 1.3x |
| erdos_n512_m2048_s13 | 512 | 2048 | pass | yes | — | 10.975 | 3.937 | 3.098 | 3.5x | 1.3x |

## bc

- correctness runs with betweenness_samples >= n, forcing exact Brandes (default 32 samples is an estimator); ratio_kernel compares exact Brandes vs exact Brandes.

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | 0 | 7.757 | 0.063 | 0.063 | 123.9x | 1.0x |
| diamond | 4 | 4 | pass | yes | 0 | 5.040 | 0.100 | 0.117 | 43.0x | 0.9x |
| two_triangles_bridge | 6 | 7 | pass | yes | 5.55e-17 | 5.368 | 0.131 | 0.163 | 32.8x | 0.8x |
| star9_bidir | 9 | 16 | pass | yes | 0 | 9.682 | 0.349 | 0.235 | 41.2x | 1.5x |
| directed_cycle6 | 6 | 6 | pass | yes | 0 | 8.082 | 0.188 | 0.167 | 48.3x | 1.1x |
| two_cycles_shared_node | 5 | 6 | pass | yes | 1.11e-16 | 4.961 | 0.171 | 0.149 | 33.2x | 1.1x |
| disconnected3 | 6 | 3 | pass | yes | 0 | 6.499 | 0.151 | 0.129 | 50.3x | 1.2x |
| cliques_4x5 | 20 | 80 | pass | yes | 0 | 16.481 | 1.402 | 0.699 | 23.6x | 2.0x |
| erdos_n64_m192_s11 | 64 | 192 | pass | yes | 4.16e-17 | 88.473 | 26.830 | 7.641 | 11.6x | 3.5x |
| erdos_n256_m1024_s12 | 256 | 1024 | pass | yes | 2.78e-17 | 1189.984 | 366.960 | 223.992 | 5.3x | 1.6x |

## cdlp

- LPA partitions are only well-defined on unambiguous structure; ambiguous datasets are scored on the exact-cover property only (Graphalytics itself validates CDLP exactly on fixed inputs).
- CDLP variant: canonical-order asynchronous LPA with hop attenuation (delta=0.25, HANP-style) + self-vote + leaf-follow rule, fully deterministic. The previous plain LPA merged two_triangles_bridge into one community; the attenuated variant splits at the bridge, matching networkx asyn_lpa(seed=0) and the Louvain-pinned partition (Q=5/14).

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | — | 1.640 | — | — | — | — |
| diamond | 4 | 4 | pass | yes | — | 2.583 | — | — | — | — |
| two_triangles_bridge | 6 | 7 | pass | yes | — | 4.295 | — | 0.231 | 18.6x | — |
| star9_bidir | 9 | 16 | pass | yes | — | 2.658 | — | — | — | — |
| directed_cycle6 | 6 | 6 | pass | yes | — | 4.626 | — | — | — | — |
| two_cycles_shared_node | 5 | 6 | pass | yes | — | 4.640 | — | — | — | — |
| disconnected3 | 6 | 3 | pass | yes | — | 5.053 | — | — | — | — |
| cliques_4x5 | 20 | 80 | pass | yes | — | 4.579 | — | 0.675 | 6.8x | — |
| erdos_n64_m192_s11 | 64 | 192 | pass | yes | — | 10.079 | — | — | — | — |
| erdos_n256_m1024_s12 | 256 | 1024 | pass | yes | — | 39.600 | — | — | — | — |
| erdos_n512_m2048_s13 | 512 | 2048 | pass | yes | — | 79.668 | — | — | — | — |

## lcc

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | 0 | 0.096 | — | 0.084 | 1.1x | — |
| diamond | 4 | 4 | pass | yes | 3.33e-13 | 0.049 | — | 0.083 | 0.6x | — |
| two_triangles_bridge | 6 | 7 | pass | yes | 3.33e-13 | 0.069 | — | 0.147 | 0.5x | — |
| star9_bidir | 9 | 16 | pass | yes | 0 | 0.155 | — | 0.146 | 1.1x | — |
| directed_cycle6 | 6 | 6 | pass | yes | 0 | 0.070 | — | 0.142 | 0.5x | — |
| two_cycles_shared_node | 5 | 6 | pass | yes | 3.33e-13 | 0.073 | — | 0.156 | 0.5x | — |
| disconnected3 | 6 | 3 | pass | yes | 0 | 0.092 | — | 0.083 | 1.1x | — |
| cliques_4x5 | 20 | 80 | pass | yes | 0 | 0.266 | — | 0.360 | 0.7x | — |
| erdos_n64_m192_s11 | 64 | 192 | pass | yes | 4.44e-13 | 1.183 | — | 1.435 | 0.8x | — |
| erdos_n256_m1024_s12 | 256 | 1024 | pass | yes | 4.87e-13 | 4.642 | — | 9.563 | 0.5x | — |
| erdos_n512_m2048_s13 | 512 | 2048 | pass | yes | 4.87e-13 | 7.641 | — | 30.025 | 0.3x | — |

## tc

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | — | 0.066 | — | 0.066 | 1.0x | — |
| diamond | 4 | 4 | pass | yes | — | 0.090 | — | 0.066 | 1.4x | — |
| two_triangles_bridge | 6 | 7 | pass | yes | — | 0.117 | — | 0.088 | 1.3x | — |
| star9_bidir | 9 | 16 | pass | yes | — | 0.141 | — | 0.114 | 1.2x | — |
| directed_cycle6 | 6 | 6 | pass | yes | — | 0.100 | — | 0.092 | 1.1x | — |
| two_cycles_shared_node | 5 | 6 | pass | yes | — | 0.120 | — | 0.084 | 1.4x | — |
| disconnected3 | 6 | 3 | pass | yes | — | 0.111 | — | 0.071 | 1.6x | — |
| cliques_4x5 | 20 | 80 | pass | yes | — | 0.424 | — | 0.297 | 1.4x | — |
| erdos_n64_m192_s11 | 64 | 192 | pass | yes | — | 1.208 | — | 1.016 | 1.2x | — |
| erdos_n256_m1024_s12 | 256 | 1024 | pass | yes | — | 6.337 | — | 5.957 | 1.1x | — |
| erdos_n512_m2048_s13 | 512 | 2048 | pass | yes | — | 9.251 | — | 7.849 | 1.2x | — |

## louvain

- modularity of OUR partition is recomputed with networkx.community.modularity and compared to ours (12-decimal) at abs<=1e-9.

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | — | 0.079 | — | — | — | — |
| diamond | 4 | 4 | pass | yes | — | 0.155 | — | — | — | — |
| two_triangles_bridge | 6 | 7 | pass | yes | — | 0.172 | — | 0.896 | 0.2x | — |
| star9_bidir | 9 | 16 | pass | yes | — | 0.280 | — | — | — | — |
| directed_cycle6 | 6 | 6 | pass | yes | — | 0.313 | — | — | — | — |
| two_cycles_shared_node | 5 | 6 | pass | yes | — | 0.317 | — | — | — | — |
| disconnected3 | 6 | 3 | pass | yes | — | 0.303 | — | — | — | — |
| cliques_4x5 | 20 | 80 | pass | yes | — | 0.936 | — | 2.181 | 0.4x | — |
| erdos_n64_m192_s11 | 64 | 192 | pass | yes | — | 11.718 | — | — | — | — |
| erdos_n256_m1024_s12 | 256 | 1024 | pass | yes | — | 60.039 | — | — | — | — |
| erdos_n512_m2048_s13 | 512 | 2048 | pass | yes | — | 225.785 | — | — | — | — |

## cycles

- bounded to small datasets (n<=12): simple-cycle counts explode combinatorially.

| Dataset | n | m | Correctness | Deterministic | Max abs err | Ours (public) ms | Ours (kernel) ms | Reference ms | Ratio public | Ratio kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| path3 | 3 | 2 | pass | yes | — | 0.066 | — | 0.170 | 0.4x | — |
| diamond | 4 | 4 | pass | yes | — | 0.072 | — | 0.167 | 0.4x | — |
| two_triangles_bridge | 6 | 7 | pass | yes | — | 0.116 | — | 0.185 | 0.6x | — |
| star9_bidir | 9 | 16 | pass | yes | — | 0.164 | — | 0.722 | 0.2x | — |
| directed_cycle6 | 6 | 6 | pass | yes | — | 0.096 | — | 0.425 | 0.2x | — |
| two_cycles_shared_node | 5 | 6 | pass | yes | — | 0.090 | — | 0.554 | 0.2x | — |
| disconnected3 | 6 | 3 | pass | yes | — | 0.054 | — | 0.141 | 0.4x | — |
