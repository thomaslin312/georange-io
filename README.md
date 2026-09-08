# GeoRange IO

An access-pattern-aware sparse reader for large remote Cloud Optimized GeoTIFF
(COG) archives. GeoRange IO plans a batch of point or window reads before
fetching, reuses each compressed block, stops DEFLATE decoding at the last
needed row, and coalesces ranges using an explicit bandwidth/latency model.

The implementation grew out of a measured headroom study. See
[AUDIT.md](AUDIT.md) for the current correctness and performance audit, and
[REPORT.md](REPORT.md) for the underlying experiments.

## Running it

```bash
make baseline
```

That brings up the infrastructure, stages the corpus, indexes every COG's block
layout, generates the five workload specs, runs the whole sweep, computes the
W5 oracle, and regenerates `results/`, `data/MANIFEST.md` and the tables in
`REPORT.md` from scratch.

The corpus is about 9.5 GB fetched from public AWS buckets, so the first
`make stage` is the slow step. It is resumable and idempotent; re-running skips
anything already in MinIO.

Useful subsets:

```bash
make up                 # infrastructure only
make stage index specs  # corpus and workloads, no measurement
make sweep RTTS="50"    # one latency instead of the full sweep
make sweep-bwcap        # the bandwidth-capped comparison on its own
make analyze report     # regenerate tables and plots from existing runs
```

## Layout

| Path | What it is |
|---|---|
| `data/` | pinned source list, staging, manifest generation |
| `infra/` | MinIO, the logging proxy, the pinned GDAL bench container |
| `baseline/` | block index, theoretical minimum, workloads, harness, analysis |
| `results/` | specs, raw proxy logs, per-run JSON, summary CSV, plots |
| `REPORT.md` | the headroom analysis and the recommendation |

## How measurement works

Every byte and every request reported in Phase 0 is counted at the proxy
(`infra/proxy/app.py`), which sits between GDAL and MinIO and logs the exact
`Range` header, response byte count and duration of each request. GDAL's own
`CPL_DEBUG` output is useful for cross-checking and is not the source of truth.

The denominator is computed with no reader in the loop: `baseline/cog_index.py`
parses every COG's `TileOffsets` and `TileByteCounts`, and
`baseline/theoretical.py` maps each workload's query geometry onto the exact
set of internal blocks it requires and sums their compressed sizes.

```
amplification = bytes_actually_fetched / bytes_theoretically_required
```

`baseline/diagnose.py` then maps every fetched byte range back onto the real
tile layout, so waste is attributed to a cause rather than just counted, and
`baseline/granularity.py` measures how much of the minimum is forced by the
format's block size rather than by the query.

`baseline/tile_index.py` builds a checkpoint index into a tile's DEFLATE
stream so a reader can start in the middle of it instead of at byte zero. It
drives libz through ctypes, because Python's `zlib` does not expose
`inflatePrime`, which the bit-position restore needs.
`baseline/gate_checkpoint.py` measures what that is worth against reading whole
tiles, and against merely stopping early.

`baseline/prefix_decode.py` asks a different question: how much of a tile a
sparse read actually needs. A COG tile is one DEFLATE stream, so reading an
early row never requires the tail of it. It measures the byte and decode saving
available to a reader that stops early, and verifies the recovered pixels
against GDAL.

Two assumptions the design rests on are checked rather than asserted.
`baseline/check_invariance.py` confirms that what GDAL fetches does not depend
on injected latency, which is what licenses running the chunk-size comparison at
a single RTT. `baseline/crosscheck.py` reconciles the proxy's accounting against
GDAL's own `CPL_DEBUG` record of the ranges it pulled.

## Installing

```bash
git clone https://github.com/thomaslin312/georange-io.git
cd georange-io
python -m pip install .
```

The distribution name is `georange-io`; the Python import is `georange_io`.
The supported API and compatibility policy are documented in [API.md](API.md).

## The reader

`georange_io/` is the thing the measurements argued for, and it is an
installable package rather than a harness:

```python
from georange_io import SparseReader

with SparseReader(
    base_url="https://sentinel-cogs.s3.us-west-2.amazonaws.com",
    bucket="sentinel-s2-l2a-cogs",
    timeout_s=30,
    retries=3,
    workers=8,
) as rd:
    values = rd.sample([("path/B04.tif", 5000, 3000), ...])
    windows = rd.read([("path/B04.tif", 0, 100, 100, 512, 512), ...])
```

Nothing has to be indexed in advance. Each file is described from its own
header on first use, which for a cloud-optimised file is one request. It groups
requests by block so each is fetched once, fetches and inflates each block only
as far as the deepest request in it needs, merges nearby blocks when the extra
bytes cost less than the round trips saved, and where a `.sbx` sidecar exists
enters a block at a DEFLATE restart point instead of at byte zero.

`read_any` delegates anything the fast path declines to GDAL, so a caller gets
one uniform result.

```bash
make test            # windows, overview levels, and every refusal path
make verify          # every value must match GDAL, on three workloads
```

Correctness is the gate, not a nicety: the claim is identical values for fewer
bytes, so `experiments/verify.py` compares every single value against GDAL and
fails on one mismatch. It currently passes on 14,933 reads spanning all three
predictors, both block sizes and both dtypes in the corpus, plus window reads
at every overview level.

The reader refuses rather than guessing. An encoding it cannot decode, a window
running off the edge of a level, an index that does not describe the object
being read, or a sidecar built for a different file are all errors, because the
failure mode of each is plausible-looking wrong pixels rather than a crash.

For private stores, pass static HTTP `headers` or a `headers_for(path, start,
end)` callback that returns per-request authentication headers. `timeout_s`,
`retries`, and `backoff_s` control bounded exponential retry behavior. Output,
compressed-range, and uncompressed-block limits protect services from accidental
or hostile allocations.

Sidecars use the identity-bound SBX3 format. They are accepted only when their
content SHA-256, object version, or ETag matches the source record. Older
identity-free sidecars fail closed and the reader continues without them.

## Current measured result

A three-repetition live AWS benchmark shuffles engine order on each repetition
and compares SHA-256 digests of every returned value. For 60 sparse reads over
12 Sentinel-2 acquisitions, all 12 engine runs agreed exactly:

| Engine | Requests | Bytes | Median wall time |
|---|---:|---:|---:|
| tuned GDAL | 72 | 91.3 MB | 46.57 s |
| GeoRange IO, 1 worker | 72 | 56.7 MB | 30.01 s |
| GeoRange IO, 8 workers | 72 | 56.7 MB | 11.96 s |
| GeoRange IO, 8 workers + sidecar | 72 | 31.7 MB | 8.24 s |

That is a 1.61× byte reduction and 3.89× median wall-time gain without a
sidecar; sidecars increase the byte reduction to 2.88×. On first access the
request counts tie because both readers must fetch 12 COG headers. The full
limits and methodology are in [AUDIT.md](AUDIT.md).

## Reproducibility

- Every workload has a fixed seed, persisted in its spec (`baseline/workloads/common.py`).
- GDAL, PROJ, libcurl and rasterio versions are recorded in every result row.
- Raw proxy logs are committed gzipped under `results/raw/`, not just summaries.
- The corpus is pinned by URL, byte count and SHA-256 in `data/sources.yaml`
  and `data/staged.json`.
