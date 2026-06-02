#!/usr/bin/env python3
"""W3 LINEAR -- 20 corridor queries.

Each corridor is a polyline 150-250 km long with a 2 km buffer, so the swath is
4 km wide and crosses several source tiles. A client issues this as a sequence
of windows marching along the line, which is what is generated here: one window
per 1 km of centreline, sized to the buffered sub-segment.

The demand is highly structured and entirely knowable in advance, but a reader
that treats each window as independent cannot exploit that. Both the DEM and
the 10 m land cover are read along the same corridor, as a real corridor
analysis would.
"""
from __future__ import annotations

import argparse
import math

import numpy as np

from common import REGION, SEEDS, finalize
from geo import Index, Mosaic, km_per_deg
from spec import Read, WorkloadSpec

NAME = "W3"


def make_polyline(rng, min_km: float, max_km: float, n_seg: int):
    """A polyline inside the region, with bounded bearing drift."""
    for _attempt in range(300):
        lon = rng.uniform(REGION["west"] + 0.4, REGION["east"] - 0.4)
        lat = rng.uniform(REGION["south"] + 0.4, REGION["north"] - 0.4)
        bearing = rng.uniform(0, 2 * math.pi)
        target = rng.uniform(min_km, max_km)
        seg = target / n_seg
        pts = [(lon, lat)]
        ok = True
        for _ in range(n_seg):
            bearing += rng.normal(0, 0.35)
            kx, ky = km_per_deg(lat)
            lon += seg * math.sin(bearing) / kx
            lat += seg * math.cos(bearing) / ky
            if not (REGION["west"] + 0.15 < lon < REGION["east"] - 0.15 and
                    REGION["south"] + 0.15 < lat < REGION["north"] - 0.15):
                ok = False
                break
            pts.append((lon, lat))
        if ok:
            return pts, target
    raise RuntimeError("could not place a corridor inside the region")


def densify(pts, step_km: float):
    """Walk the polyline, emitting sub-segments of about step_km."""
    out = []
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        kx, ky = km_per_deg((y0 + y1) / 2)
        d = math.hypot((x1 - x0) * kx, (y1 - y0) * ky)
        n = max(1, int(round(d / step_km)))
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            out.append(((x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0),
                        (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)))
    return out


def generate(n_corridors: int = 20, buffer_km: float = 2.0,
             step_km: float = 1.0, min_km: float = 150.0, max_km: float = 250.0,
             n_seg: int = 5, seed: int = SEEDS["W3"],
             idx: Index | None = None) -> WorkloadSpec:
    idx = idx or Index()
    rng = np.random.default_rng(seed)

    layers = []
    if idx.objects(dataset="cop_dem_glo30"):
        layers.append(("cop_dem_glo30", Mosaic.build(idx, "cop_dem_glo30", 0)))
    if idx.objects(dataset="esa_worldcover_v200"):
        layers.append(("esa_worldcover_v200",
                       Mosaic.build(idx, "esa_worldcover_v200", 0)))

    reads: list[Read] = []
    corridors = []
    for _ci in range(n_corridors):
        pts, target = make_polyline(rng, min_km, max_km, n_seg)
        corridors.append({"vertices": [[round(a, 6), round(b, 6)] for a, b in pts],
                          "target_km": round(target, 1)})
        for (a, b) in densify(pts, step_km):
            lon0, lat0 = a
            lon1, lat1 = b
            kx, ky = km_per_deg((lat0 + lat1) / 2)
            dlon = buffer_km / kx
            dlat = buffer_km / ky
            w = min(lon0, lon1) - dlon
            e = max(lon0, lon1) + dlon
            s = min(lat0, lat1) - dlat
            n = max(lat0, lat1) + dlat
            for _name, mos in layers:
                gx0, gy0 = mos.world_to_global(w, n)
                gx1, gy1 = mos.world_to_global(e, s)
                gw = int(gx1) - int(gx0) + 1
                gh = int(gy1) - int(gy0) + 1
                for key, x, y, ww, hh in mos.split_window(int(gx0), int(gy0),
                                                          gw, gh):
                    reads.append(Read(key, 0, x, y, ww, hh))

    return WorkloadSpec(
        name=NAME, seed=seed,
        params={"n_corridors": n_corridors, "buffer_km": buffer_km,
                "step_km": step_km, "length_km": [min_km, max_km],
                "segments_per_corridor": n_seg, "region": REGION,
                "corridors": corridors},
        reads=reads,
        notes=("20 corridors, polyline 150-250 km with a 2 km buffer, walked "
               "in 1 km steps. Each step emits one window per layer, split at "
               "tile boundaries, so a corridor naturally crosses several "
               "source assets."),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--buffer-km", type=float, default=2.0)
    ap.add_argument("--step-km", type=float, default=1.0)
    a = ap.parse_args()
    finalize(generate(a.n, a.buffer_km, a.step_km))
