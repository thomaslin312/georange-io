# Phase-0 corpus manifest

Generated from `data/sources.yaml` (resolved 2026-09-07T01:06:17Z), `data/staged.json` and `results/cog_index.json`.

**Region.** Longitude -120.0 to -115.0, latitude 36.0 to 40.0: 438 x 445 km across the Sierra Nevada crest, Owens Valley, Death Valley and the western Basin and Range. Relief runs from below sea level to over 4,400 m, so slope-derived cost surfaces have real structure.

## Tiling check

All 96 indexed objects are internally tiled. Every IFD, at native resolution and at every overview level, carries TileOffsets and TileByteCounts with a tile count matching its declared grid. The project's core assumption holds on this corpus.

## Summary by dataset

| Dataset | Objects | Staged bytes | CRS | Pixel size | Block | Compression | Overview levels |
|---|---|---|---|---|---|---|---|
| Copernicus DEM GLO-30 | 20/20 | 0.79 GB | EPSG:4326 | 1 arcsec | 1024x1024 | DEFLATE | 3 |
| ESA WorldCover 10 m v200 (2021) | 4/4 | 0.45 GB | EPSG:4326 | 0.3 arcsec | 1024x1024 | DEFLATE | 6 |
| Sentinel-2 L2A (Element84 earth-search) | 72/72 | 8.21 GB | EPSG:32611 | 10 m | 1024x1024 | DEFLATE | 4 |

**Total staged: 96 objects, 9.45 GB.**

## Provenance

### Copernicus DEM GLO-30

- **base**: https://copernicus-dem-30m.s3.amazonaws.com
- **note**: AWS Open Data, Copernicus DEM GLO-30, COG, EPSG:4326, 1 arcsec
- **license**: Copernicus DEM open licence (ESA/Airbus)

### ESA WorldCover 10 m v200 (2021)

- **base**: https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map
- **note**: ESA WorldCover 10m v200 (2021), COG, EPSG:4326, 1/3 arcsec
- **license**: CC BY 4.0

### Sentinel-2 L2A (Element84 earth-search)

- **stac**: https://earth-search.aws.element84.com/v1/search
- **date**: 2024-09-24
- **mgrs**: ['11SLA', '11SLB', '11SLC', '11SMA', '11SMB', '11SMC', '11SNA', '11SNB', '11SNC', '11SPA', '11SPB', '11SPC']
- **assets**: ['blue', 'green', 'red', 'nir', 'swir16', 'scl']
- **note**: Element84 earth-search v1, sentinel-cogs bucket, EPSG:32611
- **license**: Copernicus Sentinel data, free and open

## Overview structure

One representative object per dataset. Level 0 is native resolution; each further level is a reduced-resolution IFD stored in the same file.

**Copernicus DEM GLO-30** -- `dem/Copernicus_DSM_COG_10_N36_00_W120_00_DEM.tif`  
file 43.1 MB, header (metadata before the first tile) 1,260 bytes, BigTIFF=False

| Level | Size (px) | Block | Blocks | Sum of tile bytes | Empty tiles |
|---|---|---|---|---|---|
| 0 | 3600x3600 | 1024x1024 | 16 | 32.18 MB | 0 |
| 1 | 1800x1800 | 1024x1024 | 4 | 8.25 MB | 0 |
| 2 | 900x900 | 1024x1024 | 1 | 2.13 MB | 0 |
| 3 | 450x450 | 1024x1024 | 1 | 0.56 MB | 0 |

**ESA WorldCover 10 m v200 (2021)** -- `worldcover/ESA_WorldCover_10m_2021_v200_N36W120_Map.tif`  
file 115.5 MB, header (metadata before the first tile) 27,560 bytes, BigTIFF=False

| Level | Size (px) | Block | Blocks | Sum of tile bytes | Empty tiles |
|---|---|---|---|---|---|
| 0 | 36000x36000 | 1024x1024 | 1,296 | 76.58 MB | 0 |
| 1 | 18000x18000 | 1024x1024 | 324 | 26.83 MB | 0 |
| 2 | 9000x9000 | 1024x1024 | 81 | 8.62 MB | 0 |
| 3 | 4500x4500 | 1024x1024 | 25 | 2.49 MB | 0 |
| 4 | 2250x2250 | 1024x1024 | 9 | 0.68 MB | 0 |
| 5 | 1125x1125 | 1024x1024 | 4 | 0.19 MB | 0 |
| 6 | 562x562 | 1024x1024 | 1 | 0.05 MB | 0 |

**Sentinel-2 L2A (Element84 earth-search)** -- `s2/11SLA/B02.tif`  
file 162.4 MB, header (metadata before the first tile) 3,562 bytes, BigTIFF=False

| Level | Size (px) | Block | Blocks | Sum of tile bytes | Empty tiles |
|---|---|---|---|---|---|
| 0 | 10980x10980 | 1024x1024 | 121 | 120.75 MB | 0 |
| 1 | 5490x5490 | 512x512 | 121 | 31.26 MB | 0 |
| 2 | 2745x2745 | 512x512 | 36 | 7.91 MB | 0 |
| 3 | 1373x1373 | 512x512 | 9 | 1.96 MB | 0 |
| 4 | 687x687 | 512x512 | 4 | 0.50 MB | 0 |

## Every staged object

| Key | Bytes | SHA-256 (first 16) | Source URL |
|---|---|---|---|
| `dem/Copernicus_DSM_COG_10_N36_00_W116_00_DEM.tif` | 39,921,927 | `4bf2408ee5018b86` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N36_00_W116_00_DEM/Copernicus_DSM_COG_10_N36_00_W116_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N36_00_W117_00_DEM.tif` | 40,700,201 | `1973fbf869dcfdbc` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N36_00_W117_00_DEM/Copernicus_DSM_COG_10_N36_00_W117_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N36_00_W118_00_DEM.tif` | 41,856,148 | `f5d944b922c0ffab` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N36_00_W118_00_DEM/Copernicus_DSM_COG_10_N36_00_W118_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N36_00_W119_00_DEM.tif` | 41,456,788 | `b767a4c3e391fa18` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N36_00_W119_00_DEM/Copernicus_DSM_COG_10_N36_00_W119_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N36_00_W120_00_DEM.tif` | 43,118,578 | `1e8c047edc80de6e` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N36_00_W120_00_DEM/Copernicus_DSM_COG_10_N36_00_W120_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N37_00_W116_00_DEM.tif` | 38,878,166 | `51c7147eb9cb5f0b` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N37_00_W116_00_DEM/Copernicus_DSM_COG_10_N37_00_W116_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N37_00_W117_00_DEM.tif` | 38,106,065 | `59f3c9cc8d4bcd3c` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N37_00_W117_00_DEM/Copernicus_DSM_COG_10_N37_00_W117_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N37_00_W118_00_DEM.tif` | 39,049,249 | `1c77dc20ffdbd45c` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N37_00_W118_00_DEM/Copernicus_DSM_COG_10_N37_00_W118_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N37_00_W119_00_DEM.tif` | 39,450,870 | `313efc7d00c909aa` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N37_00_W119_00_DEM/Copernicus_DSM_COG_10_N37_00_W119_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N37_00_W120_00_DEM.tif` | 41,541,372 | `0ef5f2cd935b20da` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N37_00_W120_00_DEM/Copernicus_DSM_COG_10_N37_00_W120_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N38_00_W116_00_DEM.tif` | 37,932,747 | `c70e71d4bbf0ae1f` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N38_00_W116_00_DEM/Copernicus_DSM_COG_10_N38_00_W116_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N38_00_W117_00_DEM.tif` | 38,619,613 | `dc7bd8f7bedfdaab` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N38_00_W117_00_DEM/Copernicus_DSM_COG_10_N38_00_W117_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N38_00_W118_00_DEM.tif` | 38,407,275 | `2d11c15c1de921a7` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N38_00_W118_00_DEM/Copernicus_DSM_COG_10_N38_00_W118_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N38_00_W119_00_DEM.tif` | 38,553,765 | `09672148b81bc3ba` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N38_00_W119_00_DEM/Copernicus_DSM_COG_10_N38_00_W119_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N38_00_W120_00_DEM.tif` | 39,506,663 | `da22eb13205dd65c` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N38_00_W120_00_DEM/Copernicus_DSM_COG_10_N38_00_W120_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N39_00_W116_00_DEM.tif` | 38,072,871 | `1ad76855d7dddb80` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N39_00_W116_00_DEM/Copernicus_DSM_COG_10_N39_00_W116_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N39_00_W117_00_DEM.tif` | 38,133,081 | `79997a8884d00812` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N39_00_W117_00_DEM/Copernicus_DSM_COG_10_N39_00_W117_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N39_00_W118_00_DEM.tif` | 38,976,390 | `51f0e6317cbd93ca` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N39_00_W118_00_DEM/Copernicus_DSM_COG_10_N39_00_W118_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N39_00_W119_00_DEM.tif` | 37,463,530 | `c984f77907a49baa` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N39_00_W119_00_DEM/Copernicus_DSM_COG_10_N39_00_W119_00_DEM.tif |
| `dem/Copernicus_DSM_COG_10_N39_00_W120_00_DEM.tif` | 38,347,132 | `b565a326f452f549` | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N39_00_W120_00_DEM/Copernicus_DSM_COG_10_N39_00_W120_00_DEM.tif |
| `s2/11SLA/B02.tif` | 162,382,525 | `13fd349af5b91121` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LA/2024/9/S2A_11SLA_20240924_0_L2A/B02.tif |
| `s2/11SLA/B03.tif` | 163,497,200 | `58b534b3437304ae` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LA/2024/9/S2A_11SLA_20240924_0_L2A/B03.tif |
| `s2/11SLA/B04.tif` | 165,369,706 | `4bb0219f30905d0a` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LA/2024/9/S2A_11SLA_20240924_0_L2A/B04.tif |
| `s2/11SLA/B08.tif` | 162,284,045 | `2a9d1165961f4243` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LA/2024/9/S2A_11SLA_20240924_0_L2A/B08.tif |
| `s2/11SLA/B11.tif` | 43,085,524 | `1d8d278f5545cdf1` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LA/2024/9/S2A_11SLA_20240924_0_L2A/B11.tif |
| `s2/11SLA/SCL.tif` | 2,667,801 | `6d7cd51b30862339` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LA/2024/9/S2A_11SLA_20240924_0_L2A/SCL.tif |
| `s2/11SLB/B02.tif` | 104,210,090 | `9af490b104ab6d54` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LB/2024/9/S2A_11SLB_20240924_0_L2A/B02.tif |
| `s2/11SLB/B03.tif` | 104,354,845 | `8130174832734a5e` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LB/2024/9/S2A_11SLB_20240924_0_L2A/B03.tif |
| `s2/11SLB/B04.tif` | 105,314,684 | `7b3b2e22ef4c9505` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LB/2024/9/S2A_11SLB_20240924_0_L2A/B04.tif |
| `s2/11SLB/B08.tif` | 103,377,151 | `1477511ab150513f` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LB/2024/9/S2A_11SLB_20240924_0_L2A/B08.tif |
| `s2/11SLB/B11.tif` | 28,479,500 | `82fd966670975e4f` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LB/2024/9/S2A_11SLB_20240924_0_L2A/B11.tif |
| `s2/11SLB/SCL.tif` | 991,270 | `87a4bec357689808` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LB/2024/9/S2A_11SLB_20240924_0_L2A/SCL.tif |
| `s2/11SLC/B02.tif` | 52,620,087 | `182a0b1a77fd3bdd` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LC/2024/9/S2A_11SLC_20240924_0_L2A/B02.tif |
| `s2/11SLC/B03.tif` | 52,347,634 | `84d040714bcc6d6e` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LC/2024/9/S2A_11SLC_20240924_0_L2A/B03.tif |
| `s2/11SLC/B04.tif` | 52,859,287 | `281d5839ead59ad4` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LC/2024/9/S2A_11SLC_20240924_0_L2A/B04.tif |
| `s2/11SLC/B08.tif` | 52,559,107 | `fe409868b2c0b42f` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LC/2024/9/S2A_11SLC_20240924_0_L2A/B08.tif |
| `s2/11SLC/B11.tif` | 14,725,171 | `a0cd0bad2006055e` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LC/2024/9/S2A_11SLC_20240924_0_L2A/B11.tif |
| `s2/11SLC/SCL.tif` | 259,099 | `4bdf272304a004ac` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/LC/2024/9/S2A_11SLC_20240924_0_L2A/SCL.tif |
| `s2/11SMA/B02.tif` | 215,649,058 | `e0382a22a33f83bf` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MA/2024/9/S2A_11SMA_20240924_0_L2A/B02.tif |
| `s2/11SMA/B03.tif` | 214,896,998 | `0bac138f13370306` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MA/2024/9/S2A_11SMA_20240924_0_L2A/B03.tif |
| `s2/11SMA/B04.tif` | 216,988,204 | `0a068228b43082f1` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MA/2024/9/S2A_11SMA_20240924_0_L2A/B04.tif |
| `s2/11SMA/B08.tif` | 217,020,437 | `a30eecc9278d8929` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MA/2024/9/S2A_11SMA_20240924_0_L2A/B08.tif |
| `s2/11SMA/B11.tif` | 59,233,178 | `dbedcd28e396e8f0` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MA/2024/9/S2A_11SMA_20240924_0_L2A/B11.tif |
| `s2/11SMA/SCL.tif` | 1,173,761 | `da30881133ab00fa` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MA/2024/9/S2A_11SMA_20240924_0_L2A/SCL.tif |
| `s2/11SMB/B02.tif` | 207,662,821 | `21b6bd2aaf0b18e6` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MB/2024/9/S2A_11SMB_20240924_0_L2A/B02.tif |
| `s2/11SMB/B03.tif` | 208,412,175 | `f7844e62396617a5` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MB/2024/9/S2A_11SMB_20240924_0_L2A/B03.tif |
| `s2/11SMB/B04.tif` | 211,266,840 | `467a525d24418d0d` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MB/2024/9/S2A_11SMB_20240924_0_L2A/B04.tif |
| `s2/11SMB/B08.tif` | 210,702,640 | `ff25260f8b1aa381` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MB/2024/9/S2A_11SMB_20240924_0_L2A/B08.tif |
| `s2/11SMB/B11.tif` | 57,973,831 | `8cd1bddd2b86b3d2` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MB/2024/9/S2A_11SMB_20240924_0_L2A/B11.tif |
| `s2/11SMB/SCL.tif` | 729,585 | `c5381f0f6a4901ac` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MB/2024/9/S2A_11SMB_20240924_0_L2A/SCL.tif |
| `s2/11SMC/B02.tif` | 205,925,376 | `7fb73b975be7ff29` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MC/2024/9/S2A_11SMC_20240924_0_L2A/B02.tif |
| `s2/11SMC/B03.tif` | 206,728,948 | `5ac131f7e09e2f66` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MC/2024/9/S2A_11SMC_20240924_0_L2A/B03.tif |
| `s2/11SMC/B04.tif` | 209,241,709 | `7130d0a04c41561a` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MC/2024/9/S2A_11SMC_20240924_0_L2A/B04.tif |
| `s2/11SMC/B08.tif` | 207,129,353 | `534fbafb9eb99e5c` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MC/2024/9/S2A_11SMC_20240924_0_L2A/B08.tif |
| `s2/11SMC/B11.tif` | 56,986,410 | `8d6b80eaea264978` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MC/2024/9/S2A_11SMC_20240924_0_L2A/B11.tif |
| `s2/11SMC/SCL.tif` | 806,509 | `c1e73f5a12bcf83d` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/MC/2024/9/S2A_11SMC_20240924_0_L2A/SCL.tif |
| `s2/11SNA/B02.tif` | 208,512,168 | `02ce4717564f6b46` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NA/2024/9/S2A_11SNA_20240924_0_L2A/B02.tif |
| `s2/11SNA/B03.tif` | 207,582,633 | `a07570c0f11db300` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NA/2024/9/S2A_11SNA_20240924_0_L2A/B03.tif |
| `s2/11SNA/B04.tif` | 210,247,454 | `7e4427e31513321f` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NA/2024/9/S2A_11SNA_20240924_0_L2A/B04.tif |
| `s2/11SNA/B08.tif` | 210,744,938 | `beccd16117d6423b` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NA/2024/9/S2A_11SNA_20240924_0_L2A/B08.tif |
| `s2/11SNA/B11.tif` | 57,887,802 | `00be65fdcd0721b3` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NA/2024/9/S2A_11SNA_20240924_0_L2A/B11.tif |
| `s2/11SNA/SCL.tif` | 702,052 | `724bb6724804a771` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NA/2024/9/S2A_11SNA_20240924_0_L2A/SCL.tif |
| `s2/11SNB/B02.tif` | 201,634,265 | `ed9f5f0002f89e96` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NB/2024/9/S2A_11SNB_20240924_0_L2A/B02.tif |
| `s2/11SNB/B03.tif` | 201,278,372 | `4b3f3a283cf7d9ea` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NB/2024/9/S2A_11SNB_20240924_0_L2A/B03.tif |
| `s2/11SNB/B04.tif` | 204,642,191 | `055907735a604ede` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NB/2024/9/S2A_11SNB_20240924_0_L2A/B04.tif |
| `s2/11SNB/B08.tif` | 204,342,201 | `48aebdd653df92d7` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NB/2024/9/S2A_11SNB_20240924_0_L2A/B08.tif |
| `s2/11SNB/B11.tif` | 56,267,367 | `f48d6672c68b9a2f` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NB/2024/9/S2A_11SNB_20240924_0_L2A/B11.tif |
| `s2/11SNB/SCL.tif` | 498,634 | `41a72ba685c16a18` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NB/2024/9/S2A_11SNB_20240924_0_L2A/SCL.tif |
| `s2/11SNC/B02.tif` | 208,733,652 | `562a2ecea4f6d61b` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NC/2024/9/S2A_11SNC_20240924_0_L2A/B02.tif |
| `s2/11SNC/B03.tif` | 209,282,038 | `96eaf7065b50520b` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NC/2024/9/S2A_11SNC_20240924_0_L2A/B03.tif |
| `s2/11SNC/B04.tif` | 212,056,973 | `0322a2409525955b` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NC/2024/9/S2A_11SNC_20240924_0_L2A/B04.tif |
| `s2/11SNC/B08.tif` | 206,566,129 | `cfdd986b41bca752` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NC/2024/9/S2A_11SNC_20240924_0_L2A/B08.tif |
| `s2/11SNC/B11.tif` | 56,824,300 | `5065c67cdbc51828` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NC/2024/9/S2A_11SNC_20240924_0_L2A/B11.tif |
| `s2/11SNC/SCL.tif` | 1,142,663 | `d5ba2c4dadc78e55` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/NC/2024/9/S2A_11SNC_20240924_0_L2A/SCL.tif |
| `s2/11SPA/B02.tif` | 67,527,587 | `130dab057feed237` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PA/2024/9/S2A_11SPA_20240924_0_L2A/B02.tif |
| `s2/11SPA/B03.tif` | 67,899,326 | `64b436aa3324f762` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PA/2024/9/S2A_11SPA_20240924_0_L2A/B03.tif |
| `s2/11SPA/B04.tif` | 69,126,707 | `4cf4c4bb5a344cbd` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PA/2024/9/S2A_11SPA_20240924_0_L2A/B04.tif |
| `s2/11SPA/B08.tif` | 68,677,215 | `649128b2999f1e6d` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PA/2024/9/S2A_11SPA_20240924_0_L2A/B08.tif |
| `s2/11SPA/B11.tif` | 18,855,171 | `ccb11643819220f3` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PA/2024/9/S2A_11SPA_20240924_0_L2A/B11.tif |
| `s2/11SPA/SCL.tif` | 464,028 | `27bc85423c0b58bd` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PA/2024/9/S2A_11SPA_20240924_0_L2A/SCL.tif |
| `s2/11SPB/B02.tif` | 111,813,718 | `0c328027cb263a89` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PB/2024/9/S2A_11SPB_20240924_0_L2A/B02.tif |
| `s2/11SPB/B03.tif` | 111,932,950 | `acabca5cca4ea53d` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PB/2024/9/S2A_11SPB_20240924_0_L2A/B03.tif |
| `s2/11SPB/B04.tif` | 113,862,275 | `fb7eb04d38a61dc1` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PB/2024/9/S2A_11SPB_20240924_0_L2A/B04.tif |
| `s2/11SPB/B08.tif` | 112,960,509 | `cdacda06cef23ae0` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PB/2024/9/S2A_11SPB_20240924_0_L2A/B08.tif |
| `s2/11SPB/B11.tif` | 31,131,599 | `3aa361712aa7c441` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PB/2024/9/S2A_11SPB_20240924_0_L2A/B11.tif |
| `s2/11SPB/SCL.tif` | 390,201 | `119d5d60b16ea2e7` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PB/2024/9/S2A_11SPB_20240924_0_L2A/SCL.tif |
| `s2/11SPC/B02.tif` | 163,785,227 | `4e64d0f7706a4396` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PC/2024/9/S2A_11SPC_20240924_0_L2A/B02.tif |
| `s2/11SPC/B03.tif` | 163,439,077 | `838280755d8ffd62` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PC/2024/9/S2A_11SPC_20240924_0_L2A/B03.tif |
| `s2/11SPC/B04.tif` | 165,189,280 | `a04f94f6167a3393` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PC/2024/9/S2A_11SPC_20240924_0_L2A/B04.tif |
| `s2/11SPC/B08.tif` | 160,621,536 | `f130988375711b6a` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PC/2024/9/S2A_11SPC_20240924_0_L2A/B08.tif |
| `s2/11SPC/B11.tif` | 44,252,803 | `414220f94328f299` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PC/2024/9/S2A_11SPC_20240924_0_L2A/B11.tif |
| `s2/11SPC/SCL.tif` | 852,232 | `d93ae45959f07892` | https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/PC/2024/9/S2A_11SPC_20240924_0_L2A/SCL.tif |
| `worldcover/ESA_WorldCover_10m_2021_v200_N36W117_Map.tif` | 114,433,385 | `e65917c19cb4e525` | https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N36W117_Map.tif |
| `worldcover/ESA_WorldCover_10m_2021_v200_N36W120_Map.tif` | 115,482,414 | `a667a78591c6b654` | https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N36W120_Map.tif |
| `worldcover/ESA_WorldCover_10m_2021_v200_N39W117_Map.tif` | 106,126,091 | `59ea8716b2f8c12e` | https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N39W117_Map.tif |
| `worldcover/ESA_WorldCover_10m_2021_v200_N39W120_Map.tif` | 113,979,712 | `eba26b759d0d9ea0` | https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N39W120_Map.tif |
