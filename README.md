# GeoRange IO — Phase 0

Baseline instrumentation and headroom analysis for an access-pattern-aware read
engine for large remote rasters.

Phase 0 does not build the engine. It answers one question: **for which access
patterns does GDAL actually leave headroom, and how much?** The output is
[REPORT.md](REPORT.md), with a per-workload verdict and an explicit
keep-or-cut recommendation.

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

## Reproducibility

- Every workload has a fixed seed, persisted in its spec (`baseline/workloads/common.py`).
- GDAL, PROJ, libcurl and rasterio versions are recorded in every result row.
- Raw proxy logs are committed gzipped under `results/raw/`, not just summaries.
- The corpus is pinned by URL, byte count and SHA-256 in `data/sources.yaml`
  and `data/staged.json`.
