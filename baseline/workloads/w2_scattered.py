#!/usr/bin/env python3
"""W2 SCATTERED -- 5,000 clustered point samples across the full region.

Real sampling is never uniform: field sites, survey plots and training points
cluster. Points are drawn from a mixture of Gaussian clusters plus a uniform
background, so most points fall near a few hundred blocks while a tail lands
anywhere.

Each point is a 1x1 read, which is what "sample this raster at these
coordinates" actually issues. Both full-coverage layers are sampled, so a point
costs two reads in two different files.
"""
from __future__ import annotations

import argparse
import numpy as np

from common import REGION, SEEDS, finalize
from geo import Index, km_per_deg
from spec import Read, WorkloadSpec

NAME = "W2"


def generate(n_points: int = 5000, n_clusters: int = 40,
             sigma_km: float = 15.0, background: float = 0.10,
             seed: int = SEEDS["W2"], idx: Index | None = None) -> WorkloadSpec:
    idx = idx or Index()
    rng = np.random.default_rng(seed)

    cx = rng.uniform(REGION["west"], REGION["east"], n_clusters)
    cy = rng.uniform(REGION["south"], REGION["north"], n_clusters)

    n_bg = int(round(n_points * background))
    n_cl = n_points - n_bg
    which = rng.integers(0, n_clusters, n_cl)

    lons = np.empty(n_points)
    lats = np.empty(n_points)
    for i in range(n_cl):
        c = which[i]
        kx, ky = km_per_deg(cy[c])
        lons[i] = cx[c] + rng.normal(0, sigma_km / kx)
        lats[i] = cy[c] + rng.normal(0, sigma_km / ky)
    lons[n_cl:] = rng.uniform(REGION["west"], REGION["east"], n_bg)
    lats[n_cl:] = rng.uniform(REGION["south"], REGION["north"], n_bg)

    lons = np.clip(lons, REGION["west"], REGION["east"] - 1e-9)
    lats = np.clip(lats, REGION["south"], REGION["north"] - 1e-9)

    # Visit order is the draw order, i.e. cluster-interleaved rather than
    # sorted. A reader gets no help from the ordering, which is the point.
    order = rng.permutation(n_points)

    reads: list[Read] = []
    dropped = 0
    for i in order:
        lon, lat = float(lons[i]), float(lats[i])
        for dset in ("cop_dem_glo30", "esa_worldcover_v200"):
            key = idx.covering(dset, lon, lat)
            if key is None:
                dropped += 1
                continue
            px, py = idx.to_pixel(key, 0, lon, lat)
            W, H = idx.shape(key, 0)
            x = int(np.clip(int(px), 0, W - 1))
            y = int(np.clip(int(py), 0, H - 1))
            reads.append(Read(key, 0, x, y, 1, 1))

    return WorkloadSpec(
        name=NAME, seed=seed,
        params={"n_points": n_points, "n_clusters": n_clusters,
                "sigma_km": sigma_km, "background_frac": background,
                "region": REGION, "dropped_reads": dropped},
        reads=reads,
        notes=("5,000 clustered point samples, each a 1x1 read at native "
               "resolution from both DEM and WorldCover. Visit order is "
               "randomised so spatial locality is present in the data but not "
               "in the request order."),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--clusters", type=int, default=40)
    ap.add_argument("--sigma-km", type=float, default=15.0)
    a = ap.parse_args()
    finalize(generate(a.n, a.clusters, a.sigma_km))
