#!/usr/bin/env python3
"""Window reads, overview levels, and refusal of a stale sidecar.

The point-read path is covered by verify.py against whole workloads. This
covers what that cannot reach: multi-block windows, non-native levels, every
predictor in the corpus, and the failure the sidecar identity stamp exists to
prevent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from georange_io.reader import SparseReader, Unsupported   # noqa: E402
from georange_io import sbx                                 # noqa: E402

BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
PROXY = os.environ.get("GEORANGE_IO_PROXY_HTTP", "http://proxy:9000")
IDX = ROOT / "results" / "cog_index.json"


def gdal_window(key, level, x, y, w, h):
    import rasterio
    from rasterio.windows import Window
    cfg = {"AWS_S3_ENDPOINT": os.environ.get("AWS_S3_ENDPOINT", "proxy:9000"),
           "AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE",
           "AWS_NO_SIGN_REQUEST": "YES", "AWS_DEFAULT_REGION": "us-east-1",
           "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"}
    # Open the overview itself rather than decimating the native image.
    # Overviews are built by successive halving, so for an odd-sized raster
    # (SCL is 5490) level 3 is 687 and not 5490/8, and a decimated native read
    # would not land on the same pixels.
    with rasterio.Env(**cfg):
        kw = {} if level == 0 else {"OVERVIEW_LEVEL": level - 1}
        with rasterio.open(f"/vsis3/{BUCKET}/{key}", **kw) as ds:
            return ds.read(1, window=Window(x, y, w, h))


def main() -> int:
    idx = json.loads(IDX.read_text())
    rng = np.random.default_rng(11)
    picks = [
        ("s2ts/11SLA/2024-01-01/B04.tif", "uint16, predictor 2, 1024 blocks"),
        ("s2ts/11SLA/2024-01-01/SCL.tif", "uint8, predictor 2, 512 blocks"),
        (sorted(k for k in idx if k.startswith("dem/"))[0],
         "float32, predictor 3"),
        (sorted(k for k in idx if k.startswith("worldcover/"))[0],
         "uint8, no predictor"),
    ]
    rd = SparseReader(IDX, PROXY, BUCKET, margin=0.05)

    fails = 0
    print("windows and overview levels, against GDAL:\n")
    for key, what in picks:
        nlev = len(idx[key]["levels"])
        for level in (0, 1, min(3, nlev - 1)):
            if level >= nlev:
                continue
            L = idx[key]["levels"][level]
            reqs, truth = [], []
            for _ in range(3):
                w = int(rng.integers(1, min(900, L["width"]) + 1))
                h = int(rng.integers(1, min(900, L["height"]) + 1))
                x = int(rng.integers(0, L["width"] - w + 1))
                y = int(rng.integers(0, L["height"] - h + 1))
                reqs.append((key, level, x, y, w, h))
                truth.append(gdal_window(key, level, x, y, w, h))
            got = rd.read(reqs)
            ok = all(np.array_equal(a, b) for a, b in zip(truth, got))
            fails += (not ok)
            shp = ", ".join(f"{r[4]}x{r[5]}" for r in reqs)
            print(f"  {key.split('/')[-1]:<9} L{level}  {what:<34} "
                  f"[{shp}]  identical={ok}")

    print("\nstale sidecar refusal:")
    key = picks[0][0]
    p = ROOT / "results" / "sbx" / (key + ".sbx")
    if not p.exists():
        print("  (no sidecar built for this key; skipping)")
    else:
        good = sbx.open_for(p, idx[key])
        print(f"  a matching sidecar opens: {good.source_size == idx[key]['size']}")
        good.close()
        bogus = dict(idx[key])
        bogus["size"] = int(bogus["size"]) + 1
        try:
            sbx.open_for(p, bogus)
            print("  !! a sidecar for a DIFFERENT object was accepted")
            fails += 1
        except sbx.StaleSidecar as e:
            print(f"  a sidecar for a different object is refused: "
                  f"{str(e).split(': ')[-1][:52]}...")
        bogus2 = json.loads(json.dumps(idx[key]))
        bogus2["levels"][0]["offsets"][0] += 8
        try:
            sbx.open_for(p, bogus2)
            print("  !! a sidecar against a re-tiled layout was accepted")
            fails += 1
        except sbx.StaleSidecar:
            print("  a sidecar against a changed tile layout is refused")

    print("\nrefusal of out-of-bounds windows:")
    k0 = picks[0][0]
    L0 = idx[k0]["levels"][0]
    try:
        rd.read([(k0, 0, L0["width"] - 4, 0, 64, 64)])
        print("  !! a window running off the edge was accepted")
        fails += 1
    except Unsupported:
        print("  a window running off the edge is refused")
    part = rd.read([(k0, 0, L0["width"] - 4, 0, 64, 64)], allow_partial=True)
    print(f"  allow_partial=True zero-fills instead: shape {part[0].shape}, "
          f"{int((part[0][:, 4:] == 0).all())} beyond the edge is zero")

    print("\nrefusal of unreadable encodings:")
    try:
        rd.sample([("no/such/file.tif", 0, 0)])
        print("  !! unknown file accepted")
        fails += 1
    except Unsupported:
        print("  unknown file refused")

    print("\nno prebuilt index:")
    lazy = SparseReader(None, PROXY, BUCKET, margin=0.05)
    k0 = picks[0][0]
    L0 = idx[k0]["levels"][0]
    reqs = [(k0, 0, 4000, 4000, 300, 300)]
    a = lazy.read(reqs)[0]
    b = rd.read(reqs)[0]
    ok = np.array_equal(a, b)
    fails += (not ok)
    print(f"  a file described from its own header reads identically: {ok} "
          f"({lazy.idx.described} file(s) described)")

    print("\ndelegation to GDAL:")
    import copy
    seed = {k: copy.deepcopy(idx[k]) for k in (picks[0][0], picks[2][0])}
    seed[picks[2][0]]["levels"][0]["compression"] = 5      # pretend LZW
    mixed = SparseReader(seed, PROXY, BUCKET, margin=0.05)
    mreq = [(picks[0][0], 0, 2000, 2000, 32, 32),
            (picks[2][0], 0, 900, 900, 32, 32)]
    fast, slow = mixed.split([mixed._norm(r) for r in mreq])
    got = mixed.read_any(mreq)
    truth = [gdal_window(*mixed._norm(r)) for r in mreq]
    ok = (len(slow) == 1 and
          all(np.array_equal(x, y) for x, y in zip(truth, got)))
    fails += (not ok)
    print(f"  one file refused and routed to GDAL, both results correct: {ok} "
          f"(fast {fast}, delegated {slow})")

    print(f"\n{'ALL CHECKS PASSED' if not fails else str(fails)+' FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
