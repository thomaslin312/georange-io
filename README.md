# GeoRange IO

A sparse reader for remote Cloud Optimized GeoTIFF (COG) archives. GeoRange IO
plans a batch of point and window reads before fetching, downloads each tile
only as far as the requested rows require, and returns values identical to
GDAL's while transferring fewer bytes.

![Pixel diagram comparing what GDAL, GeoRange IO, and GeoRange IO with a sidecar download for six query shapes. Relative to GDAL: a pixel near the top of a tile, 18.5x less for both; a pixel near the bottom, no saving without a sidecar and 9.4x less with one; ten scattered pixels, 1.6x and 4.9x less; a 512 by 512 window, 1.4x and 3.3x less; five pixels over 12 dates, 2.0x and 9.5x less; a whole tile, no saving for either.](https://raw.githubusercontent.com/thomaslin312/georange-io/main/.github/assets/how-it-works.svg)

## Installation

```bash
pip install georange-io
```

The package imports as `georange_io`. Install `georange-io[fallback]` to add
rasterio, which `read_any` uses for files outside the supported format.

## Quick start

```python
from georange_io import SparseReader

with SparseReader(
    base_url="https://sentinel-cogs.s3.us-west-2.amazonaws.com",
    bucket="sentinel-s2-l2a-cogs",
    workers=8,
) as rd:
    values = rd.sample([("path/B04.tif", 5000, 3000), ...])
    windows = rd.read([("path/B04.tif", 0, 100, 100, 512, 512), ...])
```

No index is required. Each file is described from its own header on first use,
which takes one request for a cloud-optimized file.

## How it works

Each COG tile is a single DEFLATE stream that can only be decoded from its
start. GDAL downloads and decodes the entire tile to read any pixel in it.
GeoRange IO:

1. Groups all requests by tile, so each tile is fetched once.
2. Downloads each tile only down to the deepest requested row.
3. Merges nearby ranges when a round trip costs more than the extra bytes.
4. With an optional `.sbx` sidecar, starts decoding at a restart point just
   above the first requested row instead of at the start of the tile.

The saving therefore depends on where the requested pixels sit within their
tiles. Pixels near the top of a tile are cheap; pixels near the bottom need a
sidecar; reading a whole tile saves nothing.

## Performance

60 point reads over 12 Sentinel-2 acquisitions on AWS, three repetitions with
shuffled engine order. All runs returned identical values.

| Engine | Requests | Bytes | Wall time, median (range) |
|---|---:|---:|---:|
| GDAL 3.13, tuned | 72 | 91.3 MB | 84.6 s (64.7 to 90.6) |
| GeoRange IO, 1 worker | 73 | 44.5 MB | 31.8 s (26.1 to 106.7) |
| GeoRange IO, 8 workers | 72 | 44.5 MB | 5.7 s (5.5 to 8.3) |
| GeoRange IO, 8 workers, sidecar | 72 | 10.4 MB | 4.3 s (4.2 to 7.0) |

GeoRange IO transfers 2.05x fewer bytes, or 8.74x with a sidecar. Byte counts
were identical across repetitions. Wall time depends on the network and varied
widely, so treat it as indicative. A sidecar is built once per file and is
about 14% of the size of the tiles it covers.

## Supported files

Tiled, little-endian, single-sample, contiguous COGs compressed with Deflate or
Adobe Deflate, using predictor 1, 2 (integer data) or 3 (floating-point data).
Point reads, windows and overview levels are supported.

The reader raises an error rather than guess. Check a file with `supports` or
`why_unsupported`, partition requests with `split`, or use `read_any` to
delegate unsupported files to GDAL.

## API

Public names exported from `georange_io`:

| Group | Names |
|---|---|
| Reading | `SparseReader`, `Stats` |
| Errors | `Unsupported`, `TransportError`, `CorruptObject`, `ObjectChanged` |
| Indexing | `CogIndex`, `RangeResult`, `describe` |
| Sidecars | `Sbx`, `StaleSidecar`, `open_for` |

`SparseReader` is thread-safe and should be used as a context manager or closed
with `close()`. Names beginning with an underscore are private.

**Errors.** `Unsupported` covers unsupported formats and resource limits,
`TransportError` covers remote failures, `CorruptObject` (a subclass of
`TransportError`) means fetched bytes could not be decoded, and `ObjectChanged`
means the source changed between requests. No other exception type is raised
for corrupt input.

**Authentication.** Pass static `headers`, or a `headers_for(path, start, end)`
callback for per-request headers.

**Custom transports** implement
`get_range(path: str, start: int, length: int) -> RangeResult`.

**Sidecars** use the SBX3 format and are accepted only when their SHA-256,
object version or ETag matches the source. A missing, stale or unusable sidecar
never changes results: the reader falls back to decoding from the start of the
tile, logs a warning and records the reason in `Stats`. For identical objects
copied between stores, pass `content_identities={"scene.tif": "sha256:..."}`.

**Defaults.**

| Setting | Default |
|---|---|
| Socket timeout | 30 s |
| Retries | 3, with 0.25 s backoff doubling per retry |
| Maximum coalesced range | 128 MiB |
| Maximum output per call | 1 GiB |
| Maximum uncompressed tile | 64 MiB |

## Correctness

Values are compared against GDAL one by one, never sampled. The reader has
been verified identical to GDAL on 14,933 reads covering all three predictors,
both tile sizes, both data types and every overview level.

Because decoding stops before the end of a tile, the zlib checksum at the end of
the stream is never checked. Corruption inside a tile's compressed data may
therefore go undetected, whereas GDAL would report it. Corrupt headers are
rejected. If your storage does not guarantee integrity, use GDAL for reads
where silent errors are unacceptable.

## Versioning

GeoRange IO follows semantic versioning. Before 1.0, minor releases may change
the API or sidecar format; patch releases will not.

## License

MIT. See [LICENSE](https://github.com/thomaslin312/georange-io/blob/main/LICENSE).
