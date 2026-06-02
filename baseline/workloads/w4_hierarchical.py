#!/usr/bin/env python3
"""W4 HIERARCHICAL -- coarse-to-fine descent driven by the data itself.

Start at the coarsest overview of a Sentinel-2 scene, compute a vegetation
index, keep the most promising cells, descend one level, and repeat to native
resolution. What is read at each level is decided by what was read at the level
above, so the demand cannot be known before the descent starts, but it is
entirely predictable one level ahead.

Cell size is held constant in pixels, so each cell splits into four on the way
down. Generation reads real pixels directly from MinIO, never through the
proxy; the resulting demand order is frozen into the spec and replayed
identically under every config.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from common import SEEDS, finalize
from geo import Index
from spec import Read, WorkloadSpec

NAME = "W4"


def generate(n_scenes: int = 6, cell: int = 256, keep_frac: float = 0.40,
             start_level: int | None = None, seed: int = SEEDS["W4"],
             idx: Index | None = None) -> WorkloadSpec:
    from direct import Reader

    idx = idx or Index()
    rng = np.random.default_rng(seed)

    refs = idx.objects(dataset="sentinel2_l2a", band="B04")
    if not refs:
        raise RuntimeError("no Sentinel-2 B04 objects in the index")
    pick = sorted(rng.choice(refs, size=min(n_scenes, len(refs)),
                             replace=False).tolist())

    reads: list[Read] = []
    trace = []

    with Reader() as rd:
        for ref in pick:
            stem = ref.rsplit("/", 1)[0]
            red_k, nir_k = f"{stem}/B04.tif", f"{stem}/B08.tif"
            if red_k not in idx or nir_k not in idx:
                continue
            nlev = idx.levels(red_k)
            L = (nlev - 1) if start_level is None else min(start_level, nlev - 1)

            W, H = idx.shape(red_k, L)
            active = [(cx, cy)
                      for cy in range((H + cell - 1) // cell)
                      for cx in range((W + cell - 1) // cell)]
            per_scene = []

            while True:
                W, H = idx.shape(red_k, L)
                scores: dict[tuple[int, int], float] = {}
                for (cx, cy) in active:
                    x, y = cx * cell, cy * cell
                    w = min(cell, W - x)
                    h = min(cell, H - y)
                    if w <= 0 or h <= 0:
                        continue
                    reads.append(Read(red_k, L, x, y, w, h))
                    reads.append(Read(nir_k, L, x, y, w, h))
                    red = rd.read(red_k, L, x, y, w, h).astype(np.float32)
                    nir = rd.read(nir_k, L, x, y, w, h).astype(np.float32)
                    den = nir + red
                    ndvi = np.where(den > 0, (nir - red) / np.maximum(den, 1), 0.0)
                    # score the four child quadrants, which are the cells at
                    # the next finer level
                    hh, ww = ndvi.shape
                    for qy in (0, 1):
                        for qx in (0, 1):
                            sl = ndvi[qy * hh // 2:(qy + 1) * hh // 2,
                                      qx * ww // 2:(qx + 1) * ww // 2]
                            if sl.size:
                                scores[(cx * 2 + qx, cy * 2 + qy)] = float(sl.mean())
                per_scene.append({"level": L, "cells_read": len(active),
                                  "children_scored": len(scores)})
                if L == 0 or not scores:
                    break
                k = max(1, int(round(len(scores) * keep_frac)))
                active = [c for c, _ in sorted(scores.items(),
                                               key=lambda kv: -kv[1])[:k]]
                L -= 1

            trace.append({"scene": stem, "levels": per_scene})

    return WorkloadSpec(
        name=NAME, seed=seed,
        params={"n_scenes": len(trace), "scenes": pick, "cell_px": cell,
                "keep_frac": keep_frac, "bands": ["B04", "B08"],
                "descent": trace},
        reads=reads,
        notes=("Coarse-to-fine descent on Sentinel-2. Begins at the coarsest "
               "overview covering the whole scene, scores 256x256 cells by "
               "NDVI, keeps the top 40% of child cells, descends one level and "
               "repeats to native resolution. Demand at each level is decided "
               "by pixels read at the level above."),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", type=int, default=6)
    ap.add_argument("--cell", type=int, default=256)
    ap.add_argument("--keep", type=float, default=0.40)
    a = ap.parse_args()
    finalize(generate(a.scenes, a.cell, a.keep))
