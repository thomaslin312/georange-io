#!/usr/bin/env python3
"""W6 TIMESERIES -- the same small window read across many acquisitions.

The Phase-0 corpus is single-date, so it could not test the access pattern
where the block-granularity tax is largest: one location, many dates. This
reads sample points from 24 Sentinel-2 acquisitions over one MGRS tile, with
the scene classification layer alongside for cloud masking, which is what any
real time-series extraction does.

Two orderings are generated from the same points and dates, because the
difference between them is exactly what demand-aware reordering is worth:

  point-major   for each location, read every date. The natural shape of
                "extract a time series at these coordinates", and the one that
                thrashes: every point revisits all 48 files.
  date-major    for each date, read every location. The natural shape of
                "loop over scenes", and far kinder to any cache.

Neither is a strawman. Both appear in real code, and a reader given the demand
set up front can turn the first into the second.
"""
from __future__ import annotations

import argparse

import numpy as np

from common import SEEDS, finalize
from geo import Index
from spec import Read, WorkloadSpec

NAME = "W6"
SEED = 20240930


def generate(n_points: int = 100, order: str = "point", tile: str = "11SLA",
             seed: int = SEED, idx: Index | None = None) -> WorkloadSpec:
    idx = idx or Index()
    rng = np.random.default_rng(seed)

    b04 = sorted(k for k in idx.keys()
                 if k.startswith(f"s2ts/{tile}/") and k.endswith("B04.tif"))
    scl = sorted(k for k in idx.keys()
                 if k.startswith(f"s2ts/{tile}/") and k.endswith("SCL.tif"))
    if not b04:
        raise RuntimeError(f"no time-series stack staged for {tile}")
    dates = [k.split("/")[2] for k in b04]

    W, H = idx.shape(b04[0], 0)
    # Sentinel-2 tiles are rotated swaths with nodata corners; keep points in
    # the central 80% so they land on real data.
    m = 0.1
    xs = rng.integers(int(W * m), int(W * (1 - m)), n_points)
    ys = rng.integers(int(H * m), int(H * (1 - m)), n_points)

    sW, sH = idx.shape(scl[0], 0)
    sx = (xs * sW // W).astype(int)
    sy = (ys * sH // H).astype(int)

    reads: list[Read] = []
    if order == "point":
        for i in range(n_points):
            for d in range(len(b04)):
                reads.append(Read(b04[d], 0, int(xs[i]), int(ys[i]), 1, 1))
                if d < len(scl):
                    reads.append(Read(scl[d], 0, int(sx[i]), int(sy[i]), 1, 1))
    elif order == "date":
        for d in range(len(b04)):
            for i in range(n_points):
                reads.append(Read(b04[d], 0, int(xs[i]), int(ys[i]), 1, 1))
                if d < len(scl):
                    reads.append(Read(scl[d], 0, int(sx[i]), int(sy[i]), 1, 1))
    else:
        raise ValueError(order)

    return WorkloadSpec(
        name=NAME if order == "point" else "W6D", seed=seed,
        params={"n_points": n_points, "order": order, "tile": tile,
                "n_dates": len(b04), "dates": dates,
                "bands": ["B04", "SCL"],
                "points_xy": [[int(a), int(b)] for a, b in zip(xs, ys)]},
        reads=reads,
        notes=(f"{n_points} sample points read from {len(b04)} Sentinel-2 "
               f"acquisitions over MGRS {tile}, red band plus scene "
               f"classification, 1x1 reads, {order}-major order."),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", type=int, default=100)
    ap.add_argument("--order", choices=["point", "date"], default="point")
    a = ap.parse_args()
    finalize(generate(a.points, a.order))
