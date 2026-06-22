#!/usr/bin/env python3
"""Sweep the coalescing cost model and the worker count.

Coalescing trades bytes for round trips: merging two blocks means fetching
everything between them. The reader decides with a bandwidth-delay budget, the
bytes worth spending to save one round trip. This shows the knob working, and
that adding workers changes wall time without changing what is fetched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))
sys.path.insert(0, str(ROOT))

from spec import WorkloadSpec                  # noqa: E402
from georange_io.reader import SparseReader     # noqa: E402

BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
PROXY = os.environ.get("GEORANGE_IO_PROXY_HTTP", "http://proxy:9000")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="results/specs/w6p5.json")
    ap.add_argument("--margin", type=float, default=0.03)
    ap.add_argument("--out", default="results/georange_io_cost_sweep.json")
    a = ap.parse_args()

    spec = WorkloadSpec.load(ROOT / a.spec)
    reqs = [(r.key, r.x, r.y) for r in spec.reads
            if r.level == 0 and r.w == 1 and r.h == 1]
    whole = spec.theoretical["min_total_bytes"]
    print(f"{spec.name}: {len(reqs):,} point reads, whole-block floor "
          f"{whole/1e6:,.1f} MB\n")
    print(f"{'RTT':>7} {'bandwidth':>10} {'budget':>10} {'workers':>8} "
          f"{'requests':>9} {'bytes':>10} {'blk/req':>8} {'wall':>8}")
    rows = []
    for rtt, bw in ((0.005, 1000.0), (0.05, 100.0), (0.15, 50.0),
                    (0.05, 10.0), (0.05, 0.0)):
        for workers in (1, 8):
            rd = SparseReader(ROOT / "results" / "cog_index.json", PROXY,
                              BUCKET, margin=a.margin, workers=workers,
                              rtt_s=rtt, bandwidth_mbps=bw)
            t0 = time.perf_counter()
            rd.sample(reqs)
            wall = time.perf_counter() - t0
            s = rd.stats.as_dict()
            budget = "inf" if bw == 0 else f"{rtt*bw*1e6/8/1e3:,.0f} kB"
            print(f"{rtt*1000:>6.0f}ms {bw:>9.0f}M {budget:>10} {workers:>8} "
                  f"{s['requests']:>9,} {s['bytes_fetched']/1e6:>9,.1f}M "
                  f"{s['blocks_per_request']:>8.2f} {wall:>7.2f}s")
            rows.append({"rtt_s": rtt, "bandwidth_mbps": bw,
                         "workers": workers, **s, "wall_s": round(wall, 2)})
            rd.pool.close()
    (ROOT / a.out).write_text(json.dumps(
        {"spec": spec.name, "n_reads": len(reqs),
         "whole_block_floor_bytes": whole, "runs": rows}, indent=2))
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
