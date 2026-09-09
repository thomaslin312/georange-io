# GeoRange IO

An access-pattern-aware sparse reader for large remote Cloud Optimized GeoTIFF
(COG) archives. It plans a whole batch of point or window reads before fetching
any of them, fetches each compressed block once, stops DEFLATE decoding at the
last row a caller actually needs, and coalesces ranges against an explicit
bandwidth/latency cost model.

Values are byte-identical to GDAL's. The gain is in bytes moved.

```bash
pip install georange-io
```

The distribution name is `georange-io`; the Python import is `georange_io`.

## Quick start

```python
from georange_io import SparseReader

with SparseReader(
    base_url="https://sentinel-cogs.s3.us-west-2.amazonaws.com",
    bucket="sentinel-s2-l2a-cogs",
    timeout_s=30,
    retries=3,
    workers=8,
) as rd:
    values  = rd.sample([("path/B04.tif", 5000, 3000), ...])
    windows = rd.read([("path/B04.tif", 0, 100, 100, 512, 512), ...])
```

Nothing has to be indexed in advance. Each file is described from its own
header on first use, which for a cloud-optimised file is a single request.
`read_any` delegates anything the fast path declines to GDAL, so a caller gets
one uniform result either way.

## Why this exists

The library came out of a measurement study that set out to find headroom in
GDAL's COG reader and largely failed to. On windowed, linear and hierarchical
access, correctly configured GDAL fetches within 1–9% of the theoretical
minimum — the compressed bytes of the blocks the query actually intersects.
There is no meaningful byte headroom there, and this reader does not claim any.

What remains is block granularity. A one-pixel time-series read still pays for
an entire tile, because a tile is one DEFLATE stream and GDAL starts every
stream at byte zero. That is the gap this reader attacks: plan the batch, fetch
each block once, decode only as deep as needed, and where a sidecar exists,
enter the stream at a restart point instead of at the beginning.

## Measured result

Three repetitions against the live AWS Sentinel-2 archive, 60 sparse reads over
12 acquisitions. Engine order is shuffled on every repetition, each engine runs
in a cold subprocess, and every returned value array is compared by SHA-256.
All 12 runs agreed exactly.

| Engine | Requests | Bytes | Wall, median | Wall, range |
|---|---:|---:|---:|---:|
| tuned GDAL 3.13 | 72 | 91.3 MB | 84.6 s | 64.7–90.6 s |
| GeoRange IO, 1 worker | 73 | 44.5 MB | 31.8 s | 26.1–106.7 s |
| GeoRange IO, 8 workers | 72 | 44.5 MB | 5.7 s | 5.5–8.3 s |
| GeoRange IO, 8 workers + sidecar | 72 | 10.4 MB | 4.3 s | 4.2–7.0 s |

**2.05× fewer bytes, or 8.74× with a precomputed sidecar index.** The byte
figures reproduced identically on every repetition and are the number to quote.

Wall time is shown with its full range because it is not reproducible: it
depends on the link, and the spread above overlaps between configurations.
Request counts tie on first access because both readers must fetch 12 COG
headers before they can fetch anything else.

Raw per-run measurements are committed under `results/`, not just summaries.

## What it supports

Tiled, little-endian, single-sample, contiguous COGs using Deflate or Adobe
Deflate. Predictor 1 is accepted directly, predictor 2 for integer data, and
predictor 3 for floating-point data. Point reads, windows and overview levels
are supported.

The reader refuses rather than guessing. An encoding it cannot decode, a window
running off the edge of a level, an index that does not describe the object
being read, or a sidecar built for a different file are all errors — the
failure mode of each is plausible-looking wrong pixels rather than a crash.
Files outside the contract are reported by `supports` / `why_unsupported`,
partitioned by `split`, or delegated to GDAL by `read_any`.

## API contract

The public names exported from `georange_io`:

- `SparseReader`, `Stats`, `Unsupported`
- `TransportError`, `CorruptObject`, `ObjectChanged`
- `CogIndex`, `RangeResult`, `describe`
- `Sbx`, `StaleSidecar`, `open_for`

Names beginning with an underscore, and the internals of `tile_index`, are not
public. `SparseReader` is safe to call from multiple threads; calls on one
instance are serialized while its worker pool parallelizes the planned ranges.
Use it as a context manager or call `close()`.

Custom transports implement:

```python
get_range(path: str, start: int, length: int) -> RangeResult
```

The legacy `(body, total_size)` tuple return is still supported but cannot
provide object-version validation. `RangeResult.object_identity` should be a
stable namespaced value such as `version-id:...` or `etag:...`.

For private stores, pass static HTTP `headers` or a `headers_for(path, start,
end)` callback returning per-request authentication headers.

### Errors

Catch `Unsupported` for capability and resource limits, `TransportError` for
remote protocol failures, `CorruptObject` (a `TransportError`) when fetched
bytes cannot be decoded as the tile they claim to be, and `ObjectChanged` for
source-version races. No other exception type is part of the contract;
randomized fuzzing of TIFF headers and sidecars asserts that corrupt input
surfaces as one of these rather than as an underlying `zlib.error`.

### Defaults

| Setting | Default |
|---|---|
| socket timeout | 30 s |
| retries after the first attempt | 3 |
| backoff, doubled per retry | 0.25 s |
| maximum coalesced range | 128 MiB |
| maximum returned arrays per call | 1 GiB |
| maximum uncompressed tile | 64 MiB |

Output, compressed-range and uncompressed-block limits exist to protect
services from accidental or hostile allocations. Tune them for the deployment.

### Sidecars

A `.sbx` sidecar holds checkpoints into a tile's DEFLATE stream so a read can
enter mid-stream rather than at byte zero. It drives libz through `ctypes`,
because Python's `zlib` does not expose `inflatePrime`, which restoring the bit
position requires.

Sidecars use the identity-bound SBX3 format and are accepted only when their
content SHA-256, object version or ETag matches the source record. Older
identity-free SBX2 files can be inspected by `Sbx` but `open_for` rejects them,
because they cannot prove payload identity. For byte-identical objects copied
between stores, pass a trusted mapping:

```python
SparseReader(..., content_identities={"scene.tif": "sha256:..."})
```

A sidecar is an accelerator and never changes an answer. If one is unreadable,
stale, or holds no restart point for the blocks being read, the reader falls
back to reading tiles from the beginning, logs a warning on the `georange_io`
logger, and records the reason in `Stats`.

### Versioning

Semantic versioning. Before 1.0 a minor release may change APIs or file
formats and a patch release will not. Deprecations remain for at least one
minor release where a safe compatibility path exists. The SBX magic and version
are independent of the Python package version.

## Correctness

Correctness is the gate, not a nicety: the claim is identical values for fewer
bytes, so `experiments/verify.py` compares every single value against GDAL and
fails on one mismatch.

```bash
make test      # windows, overview levels, and every refusal path
make verify    # every value must match GDAL, on three workloads
```

`verify` currently passes on 14,933 reads spanning all three predictors, both
block sizes and both dtypes in the corpus, plus window reads at every overview
level.

One limit is worth stating plainly. A zlib stream is self-verifying only at its
Adler-32 trailer, and prefix decoding exists precisely so that the trailer is
never reached. Corruption inside a tile's compressed payload that still inflates
will therefore not be detected, and can produce wrong values; GDAL, which always
decodes the whole tile, would catch it. Corruption of the header is refused.
If you are reading from a store without end-to-end integrity checking and wrong
values are worse than slow ones, use `read_any` or GDAL directly.

## Reproducing the measurements

```bash
make baseline
```

That brings up the infrastructure, stages the corpus, indexes every COG's block
layout, generates the workload specs, runs the sweep, computes the W5 oracle
and regenerates `results/` and `data/MANIFEST.md` from scratch. The corpus is
about 9.5 GB from public AWS buckets, so the first `make stage` is the slow
step; it is resumable and idempotent.

```bash
make up                 # infrastructure only
make stage index specs  # corpus and workloads, no measurement
make sweep RTTS="50"    # one latency instead of the full sweep
make sweep-bwcap        # the bandwidth-capped comparison on its own
make analyze            # regenerate tables and plots from existing runs
make bench-aws          # the live AWS comparison in the table above
```

Every byte and request in the local sweep is counted at a logging proxy
(`infra/proxy/app.py`) sitting between GDAL and MinIO, which records the exact
`Range` header, response byte count and duration of each request. GDAL's own
`CPL_DEBUG` output is used to cross-check that accounting, not as the source of
truth. The denominator is computed with no reader in the loop:
`baseline/cog_index.py` parses each COG's `TileOffsets` and `TileByteCounts`,
and `baseline/theoretical.py` maps each query geometry onto the exact set of
blocks it requires and sums their compressed sizes.

```
amplification = bytes_actually_fetched / bytes_theoretically_required
```

Two assumptions the design rests on are checked rather than asserted:
`baseline/check_invariance.py` confirms that what GDAL fetches does not depend
on injected latency, and `baseline/crosscheck.py` reconciles the proxy's
accounting against GDAL's own record of the ranges it pulled.

Workload seeds are fixed and persisted in each spec; GDAL, PROJ, libcurl and
rasterio versions are recorded in every result row; raw proxy logs are
committed gzipped under `results/raw/`; and the corpus is pinned by URL, byte
count and SHA-256 in `data/sources.yaml` and `data/staged.json`.

## Layout

| Path | What it is |
|---|---|
| `georange_io/` | the installable library |
| `tests/` | unit tests, no infrastructure required |
| `examples/` | runnable usage against the public archive |
| `experiments/` | verification, benchmarks, sidecar building |
| `baseline/` | block index, theoretical minimum, workloads, analysis |
| `infra/` | MinIO, the logging proxy, the pinned GDAL bench container |
| `data/` | pinned source list, staging, manifest generation |
| `results/` | specs, raw proxy logs, per-run JSON, summary CSV, plots |

## License

MIT. See [LICENSE](LICENSE).
