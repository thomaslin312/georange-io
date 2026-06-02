#!/usr/bin/env python3
"""W1 WINDOWS -- 200 independent random 512x512 windows, uniformly placed.

The case existing readers are built for: every window is independent and
nothing about the order is predictable. This establishes parity. If tuned GDAL
is not close to the theoretical minimum here, something is wrong with the
measurement rather than with GDAL.

A window over the multi-band Sentinel-2 asset expands into one read per band,
because that is what actually happens when an application asks for a window of
a multi-band scene.
"""
from __future__ import annotations

import argparse
import numpy as np

from common import REGION, SEEDS, S2_BANDS_10M, finalize
from geo import Index, lonlat_to
from spec import Read, WorkloadSpec

NAME = "W1"


def generate(n_windows: int = 200, size: int = 512, seed: int = SEEDS["W1"],
             idx: Index | None = None) -> WorkloadSpec:
    idx = idx or Index()
    rng = np.random.default_rng(seed)

    dem = idx.objects(dataset="cop_dem_glo30")
    wc = idx.objects(dataset="esa_worldcover_v200")
    s2_ref = idx.objects(dataset="sentinel2_l2a", band="B04")
    families = [f for f, ks in (("dem", dem), ("wc", wc), ("s2", s2_ref)) if ks]

    reads: list[Read] = []
    placed = {f: 0 for f in families}
    misses = 0
    for _ in range(n_windows):
        fam = families[int(rng.integers(len(families)))]
        for _try in range(40):
            lon = rng.uniform(REGION["west"], REGION["east"])
            lat = rng.uniform(REGION["south"], REGION["north"])
            if fam == "dem":
                key = idx.covering("cop_dem_glo30", lon, lat)
                keys = [key] if key else []
            elif fam == "wc":
                key = idx.covering("esa_worldcover_v200", lon, lat)
                keys = [key] if key else []
            else:
                X, Y = lonlat_to(32611, lon, lat)
                key = idx.covering("sentinel2_l2a", float(X[0]), float(Y[0]),
                                   band="B04")
                keys = ([key.rsplit("/", 1)[0] + f"/{b}.tif" for b in S2_BANDS_10M]
                        if key else [])
                keys = [k for k in keys if k in idx]
            if not keys:
                continue
            ref = keys[0]
            if idx.epsg(ref) == 4326:
                px, py = idx.to_pixel(ref, 0, lon, lat)
            else:
                X, Y = lonlat_to(idx.epsg(ref), lon, lat)
                px, py = idx.to_pixel(ref, 0, float(X[0]), float(Y[0]))
            W, H = idx.shape(ref, 0)
            if W < size or H < size:
                continue
            x = int(np.clip(int(px) - size // 2, 0, W - size))
            y = int(np.clip(int(py) - size // 2, 0, H - size))
            for k in keys:
                reads.append(Read(k, 0, x, y, size, size))
            placed[fam] += 1
            break
        else:
            misses += 1

    return WorkloadSpec(
        name=NAME, seed=seed,
        params={"n_windows": n_windows, "size": size, "region": REGION,
                "s2_bands": S2_BANDS_10M, "placed": placed,
                "unplaceable": misses},
        reads=reads,
        notes=("200 independent uniformly-placed 512x512 windows at native "
               "resolution. Window family chosen uniformly among DEM, "
               "WorldCover and Sentinel-2; an S2 window expands to one read "
               "per 10 m band."),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--size", type=int, default=512)
    a = ap.parse_args()
    finalize(generate(a.n, a.size))
