### corpus

| Dataset | Objects | Staged | CRS | Pixel size | Block | Compression | Overviews | Blocks (all levels) |
|---|---|---|---|---|---|---|---|---|
| Copernicus DEM GLO-30 | 20 | 0.79 GB | EPSG:4326 | 1 arcsec (~31 m) | 1024x1024 | DEFLATE | 3 | 440 |
| ESA WorldCover 10 m v200 (2021) | 4 | 0.45 GB | EPSG:4326 | 0.3 arcsec (~9 m) | 1024x1024 | DEFLATE | 6 | 6,960 |
| Sentinel-2 L2A | 72 | 8.21 GB | EPSG:32611 | 10 m | 1024x1024 | DEFLATE | 4 | 18,480 |
| **Total** | **96** | **9.45 GB** |  |  |  |  |  |  |

### environment

| Component | Value |
|---|---|
| GDAL | 3.13.3 |
| libcurl | 8.18.0 |
| rasterio | 1.5.1 |
| PROJ | 9.8.1 |
| HTTP version negotiated | 1.1 |
| Object store | MinIO, single node, local disk |
| Injected jitter | 0 ms (byte counts stay reproducible) |
| Injected connection setup cost | 0 ms (generous to GDAL) |

### oracle

| Quantity | Value |
|---|---|
| Domain | 10,049 x 3,594 px (240 x 110 km), 36.1 M cells |
| Materialise the whole surface | 3.0 s, peak RSS 356 MB, 60 DEM blocks over 120 reads |
| Dijkstra over the full surface | 22.6 s, peak RSS 3,319 MB (skimage.graph.MCP_Geometric) |
| Ground-truth path | cost 288,632 over 8,037 cells |
| W5 A* demand generator | cost 288,632, 23,444,917 cells expanded in 106.1 s, 43 DEM blocks demanded |
| A* suboptimality vs oracle | +0.0001% |

Path geometry for both is persisted in `results/w5_oracle.json` (`path_lonlat`) and in the W5 spec.

### theoretical

| Workload | Reads | Files | Distinct blocks | Block bytes | Header bytes | Minimum total | Minimum requests (coalesced) | Minimum requests (one per block) |
|---|---|---|---|---|---|---|---|---|
| W1 WINDOWS | 395 | 68 | 770 | 810.0 MB | 292 kB | 810.3 MB | 506 | 838 |
| W2 SCATTERED | 9,893 | 24 | 1,832 | 638.9 MB | 135 kB | 639.1 MB | 490 | 1,856 |
| W3 LINEAR | 7,824 | 23 | 871 | 335.3 MB | 134 kB | 335.5 MB | 273 | 894 |
| W4 HIERARCHICAL | 1,402 | 12 | 564 | 328.9 MB | 43 kB | 329.0 MB | 356 | 576 |
| W5 FRONTIER | 86 | 9 | 506 | 108.7 MB | 90 kB | 108.8 MB | 50 | 515 |

### main_cold

| Workload | Config | 5ms bytes x | 5ms req x | 5ms wall | 50ms bytes x | 50ms req x | 50ms wall | 150ms bytes x | 150ms req x | 150ms wall |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 WINDOWS | DEFAULT | 1.05 | 1.3 | 14.9s | 1.05 | 1.3 | 41.2s | 1.05 | 1.3 | 98.1s |
| W1 WINDOWS | TUNED 16 kB | 1.01 | 1.3 | 11.0s | 1.01 | 1.3 | 51.1s | 1.01 | 1.3 | 95.4s |
| W1 WINDOWS | TUNED 256 kB | -- | -- | -- | 1.09 | 1.2 | 34.8s | -- | -- | -- |
| W1 WINDOWS | TUNED 1 MB | -- | -- | -- | 1.40 | 1.2 | 36.0s | -- | -- | -- |
| W1 WINDOWS | TUNED 16 kB + MT decode | 1.00 | 1.3 | 19.8s | 1.00 | 1.3 | 38.9s | 1.00 | 1.3 | 93.1s |
| W2 SCATTERED | DEFAULT | 7.12 | 13.5 | 86.5s | 7.12 | 13.5 | 459.8s | 7.12 | 13.5 | 1,127.5s |
| W2 SCATTERED | TUNED 16 kB | 1.20 | 4.5 | 27.9s | 1.20 | 4.5 | 146.3s | 1.20 | 4.5 | 370.9s |
| W2 SCATTERED | TUNED 256 kB | -- | -- | -- | 2.01 | 4.3 | 139.2s | -- | -- | -- |
| W2 SCATTERED | TUNED 1 MB | -- | -- | -- | 4.53 | 4.2 | 145.4s | -- | -- | -- |
| W2 SCATTERED | TUNED 16 kB + MT decode | 1.20 | 4.5 | 26.6s | 1.20 | 4.5 | 141.2s | 1.20 | 4.5 | 368.5s |
| W3 LINEAR | DEFAULT | 1.27 | 3.1 | 9.9s | 1.27 | 3.1 | 47.9s | 1.27 | 3.1 | 122.6s |
| W3 LINEAR | TUNED 16 kB | 1.05 | 2.7 | 8.2s | 1.05 | 2.7 | 43.9s | 1.05 | 2.7 | 108.1s |
| W3 LINEAR | TUNED 256 kB | -- | -- | -- | 1.36 | 2.4 | 39.9s | -- | -- | -- |
| W3 LINEAR | TUNED 1 MB | -- | -- | -- | 2.41 | 2.2 | 36.8s | -- | -- | -- |
| W3 LINEAR | TUNED 16 kB + MT decode | 1.01 | 2.8 | 8.5s | 1.01 | 2.8 | 43.1s | 1.01 | 2.8 | 108.1s |
| W4 HIERARCHICAL | DEFAULT | 1.05 | 1.5 | 9.7s | 1.05 | 1.5 | 37.1s | 1.05 | 1.5 | 94.3s |
| W4 HIERARCHICAL | TUNED 16 kB | 1.05 | 1.5 | 6.8s | 1.05 | 1.5 | 38.4s | 1.05 | 1.5 | 93.3s |
| W4 HIERARCHICAL | TUNED 256 kB | -- | -- | -- | 1.29 | 1.4 | 34.1s | -- | -- | -- |
| W4 HIERARCHICAL | TUNED 1 MB | -- | -- | -- | 1.96 | 1.0 | 26.0s | -- | -- | -- |
| W4 HIERARCHICAL | TUNED 16 kB + MT decode | 1.05 | 1.5 | 17.1s | 1.05 | 1.5 | 35.8s | 1.05 | 1.5 | 92.5s |
| W5 FRONTIER | DEFAULT | 1.01 | 4.3 | 1.8s | 1.01 | 4.3 | 7.4s | 1.01 | 4.3 | 18.7s |
| W5 FRONTIER | TUNED 16 kB | 1.01 | 4.2 | 2.1s | 1.01 | 4.2 | 7.7s | 1.01 | 4.2 | 18.7s |
| W5 FRONTIER | TUNED 256 kB | -- | -- | -- | 1.10 | 4.0 | 7.3s | -- | -- | -- |
| W5 FRONTIER | TUNED 1 MB | -- | -- | -- | 1.36 | 4.0 | 7.6s | -- | -- | -- |
| W5 FRONTIER | TUNED 16 kB + MT decode | 1.01 | 4.2 | 1.5s | 1.01 | 4.2 | 7.3s | 1.01 | 4.2 | 18.1s |

### main_warm

| Workload | Config | 5ms bytes x | 5ms req x | 5ms wall | 50ms bytes x | 50ms req x | 50ms wall | 150ms bytes x | 150ms req x | 150ms wall |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 WINDOWS | DEFAULT | 1.04 | 1.1 | 9.5s | 1.04 | 1.1 | 32.0s | 1.04 | 1.1 | 74.8s |
| W1 WINDOWS | TUNED 16 kB | 0.00 | 0.0 | 0.3s | 0.00 | 0.0 | 0.3s | 0.00 | 0.0 | 0.3s |
| W1 WINDOWS | TUNED 256 kB | -- | -- | -- | 0.00 | 0.0 | 0.2s | -- | -- | -- |
| W1 WINDOWS | TUNED 1 MB | -- | -- | -- | 0.00 | 0.0 | 0.2s | -- | -- | -- |
| W1 WINDOWS | TUNED 16 kB + MT decode | 0.00 | 0.0 | 0.2s | 0.00 | 0.0 | 0.2s | 0.00 | 0.0 | 0.2s |
| W2 SCATTERED | DEFAULT | 7.00 | 13.3 | 81.6s | 7.00 | 13.3 | 434.7s | 7.00 | 13.3 | 1,096.9s |
| W2 SCATTERED | TUNED 16 kB | 0.52 | 3.1 | 16.6s | 0.52 | 3.1 | 95.6s | 0.52 | 3.1 | 248.0s |
| W2 SCATTERED | TUNED 256 kB | -- | -- | -- | 1.04 | 2.9 | 92.4s | -- | -- | -- |
| W2 SCATTERED | TUNED 1 MB | -- | -- | -- | 2.77 | 2.8 | 92.4s | -- | -- | -- |
| W2 SCATTERED | TUNED 16 kB + MT decode | 0.52 | 3.1 | 16.5s | 0.52 | 3.1 | 93.0s | 0.52 | 3.1 | 246.9s |
| W3 LINEAR | DEFAULT | 1.22 | 2.9 | 9.2s | 1.22 | 2.9 | 43.1s | 1.22 | 2.9 | 111.2s |
| W3 LINEAR | TUNED 16 kB | 0.00 | 0.0 | 0.2s | 0.00 | 0.0 | 0.5s | 0.00 | 0.0 | 0.6s |
| W3 LINEAR | TUNED 256 kB | -- | -- | -- | 0.00 | 0.0 | 1.0s | -- | -- | -- |
| W3 LINEAR | TUNED 1 MB | -- | -- | -- | 0.00 | 0.0 | 0.6s | -- | -- | -- |
| W3 LINEAR | TUNED 16 kB + MT decode | 0.00 | 0.0 | 0.4s | 0.00 | 0.0 | 0.6s | 0.00 | 0.0 | 0.7s |
| W4 HIERARCHICAL | DEFAULT | 0.72 | 1.2 | 5.3s | 0.72 | 1.2 | 29.7s | 0.72 | 1.2 | 75.0s |
| W4 HIERARCHICAL | TUNED 16 kB | 0.00 | 0.0 | 0.1s | 0.00 | 0.0 | 0.3s | 0.00 | 0.0 | 0.4s |
| W4 HIERARCHICAL | TUNED 256 kB | -- | -- | -- | 0.00 | 0.0 | 0.2s | -- | -- | -- |
| W4 HIERARCHICAL | TUNED 1 MB | -- | -- | -- | 0.00 | 0.0 | 0.3s | -- | -- | -- |
| W4 HIERARCHICAL | TUNED 16 kB + MT decode | 0.00 | 0.0 | 0.2s | 0.00 | 0.0 | 0.1s | 0.00 | 0.0 | 0.1s |
| W5 FRONTIER | DEFAULT | 1.00 | 3.8 | 1.2s | 1.00 | 3.8 | 6.0s | 1.00 | 3.8 | 15.7s |
| W5 FRONTIER | TUNED 16 kB | 0.00 | 0.0 | 0.2s | 0.00 | 0.0 | 0.2s | 0.00 | 0.0 | 0.4s |
| W5 FRONTIER | TUNED 256 kB | -- | -- | -- | 0.00 | 0.0 | 0.2s | -- | -- | -- |
| W5 FRONTIER | TUNED 1 MB | -- | -- | -- | 0.00 | 0.0 | 0.3s | -- | -- | -- |
| W5 FRONTIER | TUNED 16 kB + MT decode | 0.00 | 0.0 | 0.0s | 0.00 | 0.0 | 0.0s | 0.00 | 0.0 | 0.1s |

### headroom

| Workload | Best TUNED | Bytes x | Requests x | Byte headroom | Request headroom | DEFAULT bytes x | Warm bytes x |
|---|---|---|---|---|---|---|---|
| W1 WINDOWS | TUNED 16 kB + MT decode | 1.00 | 1.3 | 0.5% | 23.9% | 1.05 | 0.00 |
| W2 SCATTERED | TUNED 16 kB | 1.20 | 4.5 | 17.0% | 77.9% | 7.12 | 0.52 |
| W3 LINEAR | TUNED 16 kB + MT decode | 1.01 | 2.8 | 1.1% | 63.8% | 1.27 | 0.00 |
| W4 HIERARCHICAL | TUNED 16 kB | 1.05 | 1.5 | 4.6% | 35.2% | 1.05 | 0.00 |
| W5 FRONTIER | TUNED 16 kB + MT decode | 1.01 | 4.2 | 0.5% | 76.3% | 1.01 | 0.00 |

### attribution

| Workload | Config | Header | Needed | Redundant refetch | Never needed | Range padding | Blocks refetched |
|---|---|---|---|---|---|---|---|
| W1 WINDOWS | TUNED 16 kB + MT decode | 0.0% | 99.5% | 0.0% | 0.4% | 0.0% | 47 of 1,093 |
| W2 SCATTERED | TUNED 16 kB | 0.0% | 83.0% | 15.7% | 1.3% | 0.0% | 1,638 of 2,625 |
| W3 LINEAR | TUNED 16 kB + MT decode | 0.0% | 98.8% | 0.1% | 1.0% | 0.0% | 44 of 927 |
| W4 HIERARCHICAL | TUNED 16 kB | 0.0% | 95.4% | 0.1% | 4.5% | 0.0% | 28 of 1,429 |
| W5 FRONTIER | TUNED 16 kB + MT decode | 0.1% | 99.4% | 0.2% | 0.3% | 0.0% | 16 of 535 |

### bandwidth

| Workload | Config | Bytes fetched | Bytes x | Requests | Wall, uncapped | Wall, 100 Mbps | Slowdown |
|---|---|---|---|---|---|---|---|
| W2 SCATTERED | TUNED 16 kB | 770 MB | 1.20 | 2,220 | 146.3s | 201.6s | 1.38x |
| W2 SCATTERED | TUNED 1 MB | 2,894 MB | 4.53 | 2,039 | 145.4s | 358.7s | 2.47x |
| W2 SCATTERED | TUNED 256 kB | 1,286 MB | 2.01 | 2,093 | 139.2s | 236.0s | 1.70x |
| W3 LINEAR | TUNED 16 kB | 351 MB | 1.05 | 749 | 43.9s | 69.5s | 1.58x |
| W3 LINEAR | TUNED 1 MB | 809 MB | 2.41 | 613 | 36.8s | 97.4s | 2.65x |
| W3 LINEAR | TUNED 256 kB | 457 MB | 1.36 | 647 | 39.9s | 71.4s | 1.79x |

### granularity

| Workload | Unique pixels requested | Pixels in the blocks touched | Granularity cost | Theoretical minimum | If reads were pixel-exact |
|---|---|---|---|---|---|
| W1 WINDOWS | 101,941,876 | 807,403,520 | 7.9x | 810.3 MB | 102.31 MB |
| W2 SCATTERED | 9,892 | 1,920,991,232 | 194,196.4x | 639.1 MB | 0.00 MB |
| W3 LINEAR | 300,393,025 | 913,309,696 | 3.0x | 335.5 MB | 110.34 MB |
| W4 HIERARCHICAL | 86,171,446 | 264,241,152 | 3.1x | 329.0 MB | 107.28 MB |
| W5 FRONTIER | 434,473,604 | 530,579,456 | 1.2x | 108.8 MB | 89.07 MB |

### multithread

| Workload | RTT | Requests, 1 thread | Requests, MT | Wall, 1 thread | Wall, MT | Speedup |
|---|---|---|---|---|---|---|
| W1 WINDOWS | 5 ms | 665 | 665 | 11.0s | 19.8s | 0.56x |
| W1 WINDOWS | 50 ms | 665 | 665 | 51.1s | 38.9s | 1.31x |
| W1 WINDOWS | 150 ms | 665 | 665 | 95.4s | 93.1s | 1.02x |
| W2 SCATTERED | 5 ms | 2,220 | 2,220 | 27.9s | 26.6s | 1.05x |
| W2 SCATTERED | 50 ms | 2,220 | 2,220 | 146.3s | 141.2s | 1.04x |
| W2 SCATTERED | 150 ms | 2,220 | 2,220 | 370.9s | 368.5s | 1.01x |
| W3 LINEAR | 5 ms | 749 | 754 | 8.2s | 8.5s | 0.97x |
| W3 LINEAR | 50 ms | 749 | 754 | 43.9s | 43.1s | 1.02x |
| W3 LINEAR | 150 ms | 749 | 754 | 108.1s | 108.1s | 1.00x |
| W4 HIERARCHICAL | 5 ms | 549 | 549 | 6.8s | 17.1s | 0.40x |
| W4 HIERARCHICAL | 50 ms | 549 | 549 | 38.4s | 35.8s | 1.07x |
| W4 HIERARCHICAL | 150 ms | 549 | 549 | 93.3s | 92.5s | 1.01x |
| W5 FRONTIER | 5 ms | 211 | 211 | 2.1s | 1.5s | 1.35x |
| W5 FRONTIER | 50 ms | 211 | 211 | 7.7s | 7.3s | 1.05x |
| W5 FRONTIER | 150 ms | 211 | 211 | 18.7s | 18.1s | 1.03x |

### prefix

| Source | Tiles | Mean tile | Prefix needed | Saving | p95 margin | Worst margin | Matches GDAL |
|---|---|---|---|---|---|---|---|
| cop_dem_glo30 | 20 | 1,968 kB | 54.9% | 45.1% | 36.4% | 48.3% | n/a |
| esa_worldcover_v200 | 4 | 62 kB | 50.2% | 49.8% | 6.8% | 7.6% | n/a |
| sentinel2_l2a_B04 | 12 | 1,075 kB | 51.3% | 48.7% | 11.2% | 27.6% | yes |
| sentinel2_l2a_B08 | 12 | 1,023 kB | 49.6% | 50.4% | 0.9% | 1.4% | yes |