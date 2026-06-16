#!/usr/bin/env python3
"""How much of the theoretical minimum is forced by block granularity?

The amplification numbers compare GDAL against the blocks a workload requires.
That denominator is not the same as what the workload actually asked for: a
COG can only be read a whole block at a time, so a query for one pixel costs a
whole 1024x1024 block.

This measures the gap between the two by counting, per workload, the unique
pixels the reads actually request against the pixels contained in the blocks
those reads touch. Overlapping windows are counted once, using a per-block
bitmap, so corridor workloads are not double counted.

The result says how much of the traffic is attributable to the format's
granularity rather than to any reader's behaviour, and therefore how much of
the prize is out of reach of a better scheduler.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

from spec import WorkloadSpec          # noqa: E402
from theoretical import load_index, blocks_for_window  # noqa: E402


def measure(spec: WorkloadSpec, idx: dict) -> dict:
    # windows grouped by the block they touch, so each block is rasterised once
    per_block: dict[tuple[str, int, int], list] = defaultdict(list)
    for r in spec.reads:
        rec = idx.get(r.key)
        if not rec or r.level >= len(rec.get("levels", ())):
            continue
        for bi in blocks_for_window(rec, r.level, r.x, r.y, r.w, r.h):
            per_block[(r.key, r.level, bi)].append(r)

    req_px = 0
    blk_px = 0
    for (key, lvl, bi), reads in per_block.items():
        L = idx[key]["levels"][lvl]
        bw, bh = L["blockw"], L["blockh"]
        nbx = L["nbx"]
        bx, by = bi % nbx, bi // nbx
        x0, y0 = bx * bw, by * bh
        w = min(bw, L["width"] - x0)
        h = min(bh, L["height"] - y0)
        blk_px += bw * bh
        m = np.zeros((h, w), bool)
        for r in reads:
            ax0 = max(r.x, x0) - x0
            ay0 = max(r.y, y0) - y0
            ax1 = min(r.x + r.w, x0 + w) - x0
            ay1 = min(r.y + r.h, y0 + h) - y0
            if ax1 > ax0 and ay1 > ay0:
                m[ay0:ay1, ax0:ax1] = True
        req_px += int(m.sum())

    t = spec.theoretical
    mb = t["min_total_bytes"] / 1e6
    return {
        "unique_requested_px": req_px,
        "px_in_blocks_touched": blk_px,
        "granularity_ratio": round(blk_px / req_px, 2) if req_px else None,
        "min_total_mb": round(mb, 2),
        "mb_if_reads_were_pixel_exact": round(mb * req_px / blk_px, 3)
        if blk_px else None,
    }


def main() -> int:
    idx = load_index(str(ROOT / "results" / "cog_index.json"))
    out = {}
    print(f"{'Workload':<6} {'unique req px':>16} {'px in blocks':>16} "
          f"{'granularity':>12} {'min MB':>9} {'MB if exact':>12}")
    for w in ("w1", "w2", "w3", "w4", "w5", "w6", "w6d"):
        p = ROOT / "results" / "specs" / f"{w}.json"
        if not p.exists():
            continue
        spec = WorkloadSpec.load(p)
        r = measure(spec, idx)
        out[spec.name] = r
        print(f"{spec.name:<6} {r['unique_requested_px']:>16,} "
              f"{r['px_in_blocks_touched']:>16,} "
              f"{r['granularity_ratio']:>11.1f}x {r['min_total_mb']:>8.1f} "
              f"{r['mb_if_reads_were_pixel_exact']:>11.2f}")
    (ROOT / "results" / "granularity.json").write_text(json.dumps(out, indent=2))
    print("\nwrote results/granularity.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
