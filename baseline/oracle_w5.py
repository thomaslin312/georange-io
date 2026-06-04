#!/usr/bin/env python3
"""Ground truth for W5: the least-cost path over the fully materialised surface.

This is the correctness oracle later phases are checked against, and it is also
the "materialise everything" baseline the brief asks to be costed: how long it
takes and how much memory it needs to build the whole cost surface for one
200 km query.

Reads go direct to MinIO, never through the proxy. The oracle is not a
measured workload.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))
sys.path.insert(0, str(ROOT / "baseline" / "workloads"))

from geo import Index, Mosaic  # noqa: E402
from spec import WorkloadSpec  # noqa: E402


def peak_rss_mb() -> float:
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb / 1024.0 if sys.platform.startswith("linux") else kb / 1e6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="results/specs/w5.json")
    ap.add_argument("--out", default="results/w5_oracle.json")
    args = ap.parse_args()

    from direct import Reader
    from w5_frontier import Surface

    spec = WorkloadSpec.load(ROOT / args.spec)
    P = spec.params
    dw, dh = P["domain_px"]
    ox, oy = P["domain_origin"]
    sx, sy = P["start_px"]
    gx, gy = P["goal_px"]
    mx, my = P["metres_per_px"]

    idx = Index()
    dem = Mosaic.build(idx, "cop_dem_glo30", 0)
    wc = Mosaic.build(idx, "esa_worldcover_v200", 0)

    print(f"materialising {dw} x {dh} = {dw*dh/1e6:.1f} M cells "
          f"({dw*mx/1000:.0f} x {dh*my/1000:.0f} km)", flush=True)

    # Enumerate the DEM blocks intersecting the domain from each tile's own
    # block grid. Stepping by the block size in domain coordinates would skip
    # blocks, because every tile's block grid starts at its own origin and the
    # last block column and row of a 3600 px tile are narrower than 1024.
    targets = []
    for key, (gox, goy) in dem.origin.items():
        L = idx[key]["levels"][0]
        bw, bh = L["blockw"], L["blockh"]
        for by in range(L["nby"]):
            for bx in range(L["nbx"]):
                bgx = gox + bx * bw
                bgy = goy + by * bh
                x0, y0 = bgx - ox, bgy - oy
                x1 = min(dw, x0 + bw)
                y1 = min(dh, y0 + bh)
                if x1 > max(0, x0) and y1 > max(0, y0):
                    targets.append((max(0, x0), max(0, y0)))
    targets.sort(key=lambda t: (t[1], t[0]))
    print(f"  {len(targets)} DEM blocks intersect the domain", flush=True)

    t0 = time.time()
    with Reader() as rd:
        surf = Surface(idx, dem, wc, rd, ox, oy, dw, dh)
        for i, (x, y) in enumerate(targets, 1):
            surf.ensure(x, y)
            if i % 10 == 0 or i == len(targets):
                print(f"  {i}/{len(targets)} blocks resident "
                      f"{len(surf.loaded)}", flush=True)
    t_mat = time.time() - t0
    rss_mat = peak_rss_mb()

    cost = surf.cost.reshape(dh, dw)
    unresolved = int(np.isnan(cost).sum())
    if unresolved:
        raise RuntimeError(
            f"{unresolved:,} of {dw*dh:,} domain cells were never covered by a "
            "loaded block; the block enumeration is incomplete and the oracle "
            "would route around holes rather than terrain")
    finite = np.isfinite(cost)
    print(f"materialised in {t_mat:.1f}s, peak RSS {rss_mat:.0f} MB, "
          f"{finite.mean()*100:.1f}% of cells finite, "
          f"{len(surf.loaded)} DEM blocks, {len(surf.reads)} reads", flush=True)

    # scikit-image MCP_Geometric uses the same edge cost as the A* generator:
    # the mean of the two endpoint costs times the geometric step length.
    from skimage.graph import MCP_Geometric
    work = np.where(finite, cost, np.float64(1e12)).astype(np.float64)

    t1 = time.time()
    mcp = MCP_Geometric(work, sampling=(my, mx), fully_connected=True)
    cum, _ = mcp.find_costs([(sy, sx)], [(gy, gx)])
    total = float(cum[gy, gx])
    path_rc = mcp.traceback((gy, gx))
    if path_rc[0] != (sy, sx) or path_rc[-1] != (gy, gx):
        raise RuntimeError(
            f"traceback runs {path_rc[0]} -> {path_rc[-1]}, expected "
            f"{(sy, sx)} -> {(gy, gx)}")
    # An admissible A* must match Dijkstra exactly. Anything else means one of
    # the two is wrong, not that the generator is approximate.
    t_path = time.time() - t1
    rss_all = peak_rss_mb()

    gxs = np.array([c for _r, c in path_rc]) + ox
    gys = np.array([r for r, _c in path_rc]) + oy
    lons, lats = dem.global_to_world(gxs, gys)
    path_ll = [[round(float(a), 6), round(float(b), 6)]
               for a, b in zip(lons, lats)]

    astar = P.get("astar", {})
    a_cost = astar.get("path_cost")
    out = {
        "domain_px": [dw, dh], "domain_origin": [ox, oy],
        "cells": dw * dh,
        "start_lonlat": P["start_lonlat"], "goal_lonlat": P["goal_lonlat"],
        "metres_per_px": [mx, my],
        "cost_model": P["cost_model"],
        "materialise": {
            "wall_s": round(t_mat, 2), "peak_rss_mb": round(rss_mat, 1),
            "dem_blocks": len(surf.loaded), "reads": len(surf.reads),
            "finite_fraction": round(float(finite.mean()), 4),
        },
        "dijkstra": {
            "engine": "skimage.graph.MCP_Geometric",
            "wall_s": round(t_path, 2), "peak_rss_mb": round(rss_all, 1),
            "path_cost": total, "path_cells": len(path_rc),
        },
        "astar_comparison": {
            "astar_cost": a_cost,
            "astar_expanded": astar.get("expanded"),
            "astar_wall_s": astar.get("wall_s"),
            "astar_blocks_demanded": astar.get("dem_blocks_demanded"),
            "suboptimality": (a_cost / total - 1.0)
            if (a_cost and total) else None,
        },
        "path_lonlat": path_ll,
    }
    p = ROOT / args.out
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))

    print(f"\noracle path cost {total:,.0f} over {len(path_rc):,} cells "
          f"in {t_path:.1f}s, peak RSS {rss_all:.0f} MB")
    if a_cost:
        print(f"A* cost {a_cost:,.0f}  -> suboptimality "
              f"{(a_cost/total-1)*100:+.4f}%")
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
