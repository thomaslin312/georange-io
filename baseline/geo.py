#!/usr/bin/env python3
"""Geometry helpers over the COG block index.

Everything here works from each object's own geotransform, so no assumptions
about registration or tiling are baked in beyond what the files declare.

One property of this corpus is worth stating because W3 and W5 rely on it:
Copernicus DEM GLO-30 is 1 arcsec and ESA WorldCover is exactly 1/3 arcsec,
both EPSG:4326. The 3:1 ratio is exact, so a DEM cell maps onto a whole 3x3
WorldCover group with no resampling. The cost surface therefore needs no warp,
and no warp-induced read pattern contaminates the measurement.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


class Index:
    def __init__(self, path: str | Path | None = None):
        p = Path(path) if path else ROOT / "results" / "cog_index.json"
        self.d: dict = json.loads(p.read_text())

    def __contains__(self, k): return k in self.d
    def __getitem__(self, k): return self.d[k]
    def keys(self): return self.d.keys()

    def objects(self, dataset: str | None = None, role: str | None = None,
                band: str | None = None) -> list[str]:
        out = []
        for k, r in self.d.items():
            if dataset and r.get("dataset") != dataset:
                continue
            if role and r.get("role") != role:
                continue
            if band and not k.endswith(f"/{band}.tif"):
                continue
            out.append(k)
        return sorted(out)

    def levels(self, key: str) -> int:
        return len(self.d[key]["levels"])

    def shape(self, key: str, level: int = 0) -> tuple[int, int]:
        L = self.d[key]["levels"][level]
        return L["width"], L["height"]

    def transform(self, key: str, level: int = 0) -> tuple[float, ...]:
        return tuple(self.d[key]["levels"][level]["transform"])

    def bounds(self, key: str) -> tuple[float, float, float, float]:
        return tuple(self.d[key]["bounds"])

    def epsg(self, key: str) -> int | None:
        return self.d[key].get("epsg")

    def to_pixel(self, key: str, level: int, X, Y):
        a, b, c, d, e, f = self.transform(key, level)
        px = (np.asarray(X, dtype=float) - c) / a
        py = (np.asarray(Y, dtype=float) - f) / e
        return np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)

    def to_world(self, key: str, level: int, px, py):
        a, b, c, d, e, f = self.transform(key, level)
        X = c + (np.asarray(px, dtype=float) + 0.5) * a
        Y = f + (np.asarray(py, dtype=float) + 0.5) * e
        return X, Y

    def covering(self, dataset: str, X: float, Y: float,
                 band: str | None = None) -> str | None:
        for k in self.objects(dataset=dataset, band=band):
            w, s, e, n = self.bounds(k)
            if w <= X < e and s < Y <= n:
                return k
        return None


def lonlat_to(epsg: int, lon, lat):
    """Reproject lon/lat (EPSG:4326) into the target CRS."""
    if epsg == 4326:
        return np.asarray(lon, float), np.asarray(lat, float)
    from rasterio.warp import transform as rio_transform
    xs, ys = rio_transform("EPSG:4326", f"EPSG:{epsg}",
                           np.atleast_1d(lon).tolist(),
                           np.atleast_1d(lat).tolist())
    return np.asarray(xs), np.asarray(ys)


@dataclass
class Mosaic:
    """A virtual global grid over a set of same-CRS, same-resolution tiles.

    Used by the corridor and frontier workloads, which need to address the
    whole region as one raster while the reads still resolve to real objects.
    """
    keys: list[str]
    res_x: float
    res_y: float           # negative, north-up
    x0: float              # left edge of global pixel 0
    y0: float              # top edge of global row 0
    width: int
    height: int
    tile_w: int
    tile_h: int
    origin: dict           # key -> (global_x_off, global_y_off)

    @staticmethod
    def build(idx: Index, dataset: str, level: int = 0,
              band: str | None = None) -> "Mosaic":
        keys = idx.objects(dataset=dataset, band=band)
        if not keys:
            raise RuntimeError(f"no objects for dataset={dataset} band={band}")
        a, b, c, d, e, f = idx.transform(keys[0], level)
        x0 = min(idx.transform(k, level)[2] for k in keys)
        y0 = max(idx.transform(k, level)[5] for k in keys)
        origin = {}
        maxx = maxy = 0
        for k in keys:
            ta, _, tc, _, te, tf = idx.transform(k, level)
            gx = int(round((tc - x0) / a))
            gy = int(round((tf - y0) / e))
            w, h = idx.shape(k, level)
            origin[k] = (gx, gy)
            maxx = max(maxx, gx + w)
            maxy = max(maxy, gy + h)
        tw, th = idx.shape(keys[0], level)
        return Mosaic(keys=keys, res_x=a, res_y=e, x0=x0, y0=y0,
                      width=maxx, height=maxy, tile_w=tw, tile_h=th,
                      origin=origin)

    def world_to_global(self, X, Y):
        gx = np.floor((np.asarray(X, float) - self.x0) / self.res_x).astype(np.int64)
        gy = np.floor((np.asarray(Y, float) - self.y0) / self.res_y).astype(np.int64)
        return gx, gy

    def global_to_world(self, gx, gy):
        X = self.x0 + (np.asarray(gx, float) + 0.5) * self.res_x
        Y = self.y0 + (np.asarray(gy, float) + 0.5) * self.res_y
        return X, Y

    def locate(self, gx: int, gy: int) -> tuple[str, int, int] | None:
        """Global pixel -> (object key, local x, local y)."""
        for k, (ox, oy) in self.origin.items():
            lx, ly = gx - ox, gy - oy
            if 0 <= lx < self.tile_w and 0 <= ly < self.tile_h:
                return k, lx, ly
        return None

    def split_window(self, gx: int, gy: int, w: int, h: int):
        """Split a global window into per-object (key, x, y, w, h) pieces."""
        out = []
        for k, (ox, oy) in self.origin.items():
            x0 = max(gx, ox); y0 = max(gy, oy)
            x1 = min(gx + w, ox + self.tile_w); y1 = min(gy + h, oy + self.tile_h)
            if x1 > x0 and y1 > y0:
                out.append((k, x0 - ox, y0 - oy, x1 - x0, y1 - y0))
        return sorted(out)


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def km_per_deg(lat: float) -> tuple[float, float]:
    """Local scale: km per degree of longitude and latitude."""
    return (111.320 * math.cos(math.radians(lat)), 110.574)
