#!/usr/bin/env python3
"""Extract a pixel time series straight from the public Sentinel-2 archive.

No staging, no index, no configuration. Run it against AWS as-is.
"""
import sys, time
sys.path.insert(0, __file__.rsplit("/", 2)[0])

import numpy as np
from georange_io import SparseReader

BASE = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
BUCKET = "sentinel-s2-l2a-cogs"
SCENES = [   # a few 2024 acquisitions over MGRS 11SLA, eastern Sierra Nevada
    "11/S/LA/2024/1/S2A_11SLA_20240101_0_L2A",
    "11/S/LA/2024/5/S2A_11SLA_20240507_0_L2A",
    "11/S/LA/2024/9/S2A_11SLA_20240924_0_L2A",
]
# How much a prefix read saves depends on where the pixel falls in its
# 1024-row block: a pixel on row 50 needs 5% of the tile, one on row 1000 needs
# nearly all of it. These are spread across a block so the average is honest.
PIXELS = [(5000, 3 * 1024 + 90), (5001, 3 * 1024 + 380),
          (7200, 5 * 1024 + 640), (7201, 5 * 1024 + 950)]

rd = SparseReader(base_url=BASE, bucket=BUCKET, margin=0.05, workers=4)
reqs = [(f"{s}/B04.tif", x, y) for s in SCENES for (x, y) in PIXELS]

t0 = time.perf_counter()
vals = rd.sample(reqs)
wall = time.perf_counter() - t0

st = rd.stats.as_dict()
print(f"{len(reqs)} samples from {len(SCENES)} acquisitions in {wall:.1f}s")
print(f"  fetched {st['bytes_fetched']/1e6:.1f} MB in {st['requests']} requests")
print(f"  whole blocks would have been "
      f"{st['bytes_if_whole_blocks']/1e6:.1f} MB "
      f"({st['byte_gain_vs_whole_blocks']}x more)")
depths = sorted({y % 1024 for (_x, y) in PIXELS})
print(f"  rows needed within each block: {depths} of 1024, so this is roughly "
      "the average case")
print("  a .sbx sidecar would remove the rest, by starting mid-tile instead "
      "of at its beginning")
for i, s in enumerate(SCENES):
    row = vals[i * len(PIXELS):(i + 1) * len(PIXELS)]
    print(f"  {s.split('/')[-1][:22]}  red = {row}")
