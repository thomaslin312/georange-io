#!/usr/bin/env python3
"""W5 FRONTIER -- A* least-cost path, blocks demanded online.

A cost surface is derived from DEM slope and WorldCover class. An A* search
runs from a start point to a goal about 200 km away, over the DEM's native
30 m grid. No part of the surface is materialised up front: when the frontier
reaches a cell whose block is not resident, that block is demanded, along with
the land cover blocks covering the same ground.

This is the workload whose demand is least knowable to a general-purpose
reader and most knowable one step ahead, so it is the sharpest test of the
project's premise.

Slope is computed inside each loaded block with one-sided differences at the
block edge rather than fetching a halo. That is a deliberate simplification of
the generator, not of the oracle; the oracle in oracle_w5.py computes slope
over the fully materialised surface.
"""
from __future__ import annotations

import argparse
import heapq
import math
import time

import numpy as np

from common import REGION, SEEDS, finalize
from geo import Index, Mosaic, haversine_km, km_per_deg
from spec import Read, WorkloadSpec

NAME = "W5"

# ESA WorldCover v200 class -> relative traversal cost per metre.
LC_COST = {10: 1.6, 20: 1.3, 30: 1.0, 40: 1.1, 50: 2.0, 60: 1.0,
           70: 3.0, 80: 50.0, 90: 3.0, 95: 5.0, 100: 1.2}
LC_DEFAULT = 1.5
SLOPE_K = 0.08          # cost multiplier per degree of slope
SLOPE_CAP = 45.0
MIN_COST = 1.0          # floor used by the heuristic, keeps it admissible


def lc_table() -> np.ndarray:
    t = np.full(256, LC_DEFAULT, np.float32)
    for k, v in LC_COST.items():
        t[k] = v
    t[0] = 1e6             # WorldCover 0 == no data
    return t


class Surface:
    """Lazily materialised cost surface, one DEM block at a time."""

    def __init__(self, idx: Index, dem: Mosaic, wc: Mosaic, reader,
                 ox: int, oy: int, dw: int, dh: int):
        self.idx, self.dem, self.wc, self.rd = idx, dem, wc, reader
        self.ox, self.oy, self.dw, self.dh = ox, oy, dw, dh
        self.cost = np.full(dw * dh, np.nan, np.float32)
        self.loaded: set[tuple[str, int, int]] = set()
        self.reads: list[Read] = []
        self.lc = lc_table()
        self.dem_block_bytes = 0

    def _read_wc(self, gx0, gy0, w, h) -> np.ndarray:
        out = np.zeros((h, w), np.uint8)
        covered = 0
        for key, x, y, ww, hh in self.wc.split_window(gx0, gy0, w, h):
            self.reads.append(Read(key, 0, x, y, ww, hh))
            a = self.rd.read(key, 0, x, y, ww, hh)
            ox = self.wc.origin[key][0] + x - gx0
            oy = self.wc.origin[key][1] + y - gy0
            out[oy:oy + hh, ox:ox + ww] = a
            covered += ww * hh
        if covered < w * h:
            # Silently zero-filling here would map to WorldCover class 0, whose
            # cost is enormous, and the search would route around a hole in the
            # corpus while looking like it was routing around terrain.
            raise RuntimeError(
                f"land cover covers only {covered:,} of {w*h:,} requested "
                f"cells at global ({gx0},{gy0}) {w}x{h}: the WorldCover "
                "mosaic does not span the DEM domain, so some tiles are "
                "missing from the corpus")
        return out

    def ensure(self, dx: int, dy: int) -> None:
        gx, gy = dx + self.ox, dy + self.oy
        loc = self.dem.locate(gx, gy)
        if loc is None:
            return
        key, lx, ly = loc
        L = self.idx[key]["levels"][0]
        bw, bh = L["blockw"], L["blockh"]
        bx, by = lx // bw, ly // bh
        tag = (key, bx, by)
        if tag in self.loaded:
            return
        self.loaded.add(tag)

        x0, y0 = bx * bw, by * bh
        w = min(bw, L["width"] - x0)
        h = min(bh, L["height"] - y0)
        self.reads.append(Read(key, 0, x0, y0, w, h))
        elev = self.rd.read(key, 0, x0, y0, w, h).astype(np.float32)
        nd = self.idx[key].get("nodata")
        bad = ~np.isfinite(elev) if nd is None else (~np.isfinite(elev)) | (elev == nd)

        # geographic extent of this block, in mosaic-global DEM pixels
        gox, goy = self.dem.origin[key]
        ggx0, ggy0 = gox + x0, goy + y0
        Xc, _ = self.dem.global_to_world(np.arange(ggx0, ggx0 + w), 0)
        _, Yc = self.dem.global_to_world(0, np.arange(ggy0, ggy0 + h))

        # metres per pixel varies with latitude, so slope is computed with the
        # local scale rather than a constant.
        lat_mid = float(np.mean(Yc))
        kx, ky = km_per_deg(lat_mid)
        mx = abs(self.dem.res_x) * kx * 1000.0
        my = abs(self.dem.res_y) * ky * 1000.0

        e = np.where(bad, np.nan, elev)
        e = np.where(np.isnan(e), np.nanmean(e) if np.isfinite(np.nanmean(e)) else 0.0, e)
        gy_, gx_ = np.gradient(e, my, mx)
        slope = np.degrees(np.arctan(np.hypot(gx_, gy_)))
        np.clip(slope, 0, SLOPE_CAP, out=slope)

        # land cover for the same ground, decimated to the DEM grid
        wgx = np.floor((Xc - self.wc.x0) / self.wc.res_x).astype(np.int64)
        wgy = np.floor((Yc - self.wc.y0) / self.wc.res_y).astype(np.int64)
        wx0, wx1 = int(wgx.min()), int(wgx.max())
        wy0, wy1 = int(wgy.min()), int(wgy.max())
        lcw = self._read_wc(wx0, wy0, wx1 - wx0 + 1, wy1 - wy0 + 1)
        lcb = lcw[np.ix_(wgy - wy0, wgx - wx0)]

        c = self.lc[lcb] * (1.0 + SLOPE_K * slope)
        c[bad] = np.inf

        ddx0, ddy0 = ggx0 - self.ox, ggy0 - self.oy
        sx0, sy0 = max(0, ddx0), max(0, ddy0)
        sx1, sy1 = min(self.dw, ddx0 + w), min(self.dh, ddy0 + h)
        if sx1 <= sx0 or sy1 <= sy0:
            return
        sub = c[sy0 - ddy0:sy1 - ddy0, sx0 - ddx0:sx1 - ddx0]
        flat = self.cost.reshape(self.dh, self.dw)
        flat[sy0:sy1, sx0:sx1] = sub


def pick_endpoints(idx: Index, dem: Mosaic, rng, target_km: float):
    """Start and goal about target_km apart, both inside the mosaic and on
    valid DEM ground."""
    for _ in range(4000):
        lon0 = rng.uniform(REGION["west"] + 0.3, REGION["east"] - 0.3)
        lat0 = rng.uniform(REGION["south"] + 0.3, REGION["north"] - 0.3)
        b = rng.uniform(0, 2 * math.pi)
        kx, ky = km_per_deg(lat0)
        lon1 = lon0 + target_km * math.sin(b) / kx
        lat1 = lat0 + target_km * math.cos(b) / ky
        if not (REGION["west"] + 0.2 < lon1 < REGION["east"] - 0.2 and
                REGION["south"] + 0.2 < lat1 < REGION["north"] - 0.2):
            continue
        if idx.covering("cop_dem_glo30", lon0, lat0) is None:
            continue
        if idx.covering("cop_dem_glo30", lon1, lat1) is None:
            continue
        return (lon0, lat0), (lon1, lat1)
    raise RuntimeError("could not place W5 endpoints")


def generate(target_km: float = 200.0, buffer_km: float = 25.0,
             eps: float = 1.0, max_expansions: int = 6_000_000,
             seed: int = SEEDS["W5"], idx: Index | None = None) -> WorkloadSpec:
    from direct import Reader

    idx = idx or Index()
    rng = np.random.default_rng(seed)
    dem = Mosaic.build(idx, "cop_dem_glo30", 0)
    wc = Mosaic.build(idx, "esa_worldcover_v200", 0)

    (lon0, lat0), (lon1, lat1) = pick_endpoints(idx, dem, rng, target_km)
    great_circle = haversine_km(lon0, lat0, lon1, lat1)

    kx, ky = km_per_deg((lat0 + lat1) / 2)
    bw_deg, bh_deg = buffer_km / kx, buffer_km / ky
    west = min(lon0, lon1) - bw_deg
    east = max(lon0, lon1) + bw_deg
    south = min(lat0, lat1) - bh_deg
    north = max(lat0, lat1) + bh_deg

    gx0, gy0 = dem.world_to_global(west, north)
    gx1, gy1 = dem.world_to_global(east, south)
    ox, oy = int(max(0, gx0)), int(max(0, gy0))
    dw = int(min(dem.width, gx1 + 1)) - ox
    dh = int(min(dem.height, gy1 + 1)) - oy

    sgx, sgy = dem.world_to_global(lon0, lat0)
    ggx, ggy = dem.world_to_global(lon1, lat1)
    s = (int(sgx) - ox, int(sgy) - oy)
    g = (int(ggx) - ox, int(ggy) - oy)

    mlat = (lat0 + lat1) / 2
    kx2, ky2 = km_per_deg(mlat)
    mx = abs(dem.res_x) * kx2 * 1000.0
    my = abs(dem.res_y) * ky2 * 1000.0

    print(f"  domain {dw} x {dh} px  ({dw*mx/1000:.0f} x {dh*my/1000:.0f} km)",
          flush=True)
    print(f"  start {lon0:.4f},{lat0:.4f} -> goal {lon1:.4f},{lat1:.4f} "
          f"({great_circle:.1f} km great circle)", flush=True)

    NEI = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
    step = [math.hypot(dx * mx, dy * my) for dx, dy in NEI]

    with Reader() as rd:
        surf = Surface(idx, dem, wc, rd, ox, oy, dw, dh)
        cost = surf.cost
        n = dw * dh
        gscore = np.full(n, np.inf, np.float32)
        closed = np.zeros(n, np.uint8)
        parent = np.full(n, -1, np.int32)

        def h(i: int) -> float:
            y, x = divmod(i, dw)
            return MIN_COST * math.hypot((x - g[0]) * mx, (y - g[1]) * my)

        si = s[1] * dw + s[0]
        gi = g[1] * dw + g[0]
        surf.ensure(s[0], s[1])
        surf.ensure(g[0], g[1])
        gscore[si] = 0.0
        heap = [(eps * h(si), si)]
        t0 = time.time()
        expanded = 0
        found = False
        while heap:
            f, i = heapq.heappop(heap)
            if closed[i]:
                continue
            closed[i] = 1
            expanded += 1
            if i == gi:
                found = True
                break
            if expanded >= max_expansions:
                break
            y, x = divmod(i, dw)
            gi_cur = gscore[i]
            ci = cost[i]
            if not math.isfinite(ci):
                continue
            for k in range(8):
                dx, dy = NEI[k]
                nx, ny = x + dx, y + dy
                if nx < 0 or ny < 0 or nx >= dw or ny >= dh:
                    continue
                j = ny * dw + nx
                if closed[j]:
                    continue
                cj = cost[j]
                if cj != cj:                      # NaN: block not resident yet
                    surf.ensure(nx, ny)
                    cj = cost[j]
                    if cj != cj:
                        continue
                if not math.isfinite(cj):
                    continue
                ng = gi_cur + 0.5 * (ci + cj) * step[k]
                if ng < gscore[j]:
                    gscore[j] = ng
                    parent[j] = i
                    heapq.heappush(heap, (ng + eps * h(j), j))
        wall = time.time() - t0

    path = []
    total = float(gscore[gi]) if found else None
    if found:
        i = gi
        while i != -1:
            y, x = divmod(int(i), dw)
            path.append((x + ox, y + oy))
            i = int(parent[i])
        path.reverse()

    lons, lats = dem.global_to_world(np.array([p[0] for p in path] or [0]),
                                     np.array([p[1] for p in path] or [0]))
    path_ll = [[round(float(a), 6), round(float(b), 6)]
               for a, b in zip(lons, lats)] if found else []

    print(f"  A*: expanded {expanded:,} cells in {wall:.1f}s, "
          f"found={found}, blocks demanded={len(surf.loaded)}", flush=True)

    return WorkloadSpec(
        name=NAME, seed=seed,
        params={
            "target_km": target_km, "great_circle_km": round(great_circle, 2),
            "buffer_km": buffer_km, "eps": eps,
            "domain_px": [dw, dh], "domain_origin": [ox, oy],
            "start_lonlat": [lon0, lat0], "goal_lonlat": [lon1, lat1],
            "start_px": list(s), "goal_px": list(g),
            "metres_per_px": [mx, my],
            "cost_model": {"lc_cost": LC_COST, "lc_default": LC_DEFAULT,
                           "slope_k": SLOPE_K, "slope_cap_deg": SLOPE_CAP},
            "astar": {"expanded": expanded, "wall_s": round(wall, 2),
                      "found": found, "path_cost": total,
                      "path_cells": len(path),
                      "dem_blocks_demanded": len(surf.loaded),
                      "hit_expansion_cap": expanded >= max_expansions},
            "path_lonlat": path_ll,
        },
        reads=surf.reads,
        notes=("A* over a slope-and-landcover cost surface at DEM native "
               "resolution, 8-connected. Blocks are demanded online as the "
               "frontier expands: a DEM block plus the WorldCover blocks "
               "covering the same ground. The read order in this spec is the "
               "order the search actually demanded them."),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-km", type=float, default=200.0)
    ap.add_argument("--buffer-km", type=float, default=25.0)
    ap.add_argument("--eps", type=float, default=1.0)
    ap.add_argument("--max-expansions", type=int, default=6_000_000)
    a = ap.parse_args()
    finalize(generate(a.target_km, a.buffer_km, a.eps, a.max_expansions))
