#!/usr/bin/env python3
"""Build .sbx sidecars for the blocks a workload touches.

This is the offline half. It fetches each tile in full once and records restart
points, which is why it is a separate tool: no query can pay for this itself.
In practice an archive maintainer or a caching service builds these, the way
overviews and STAC catalogs are already built once and shared.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))
sys.path.insert(0, str(ROOT))

from spec import WorkloadSpec                     # noqa: E402
from theoretical import blocks_for_window         # noqa: E402
from tile_index import build_index                # noqa: E402
from georange_io import sbx                        # noqa: E402

BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
ENDPOINT = os.environ.get("MINIO_DIRECT", "http://minio:9000")


def s3():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", "georange-io"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", "georange-io123"),
        region_name="us-east-1",
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": "path"}, max_pool_connections=16))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="results/specs/w6p5.json")
    ap.add_argument("--span", type=int, default=262144)
    ap.add_argument("--dir", default="results/sbx")
    a = ap.parse_args()

    idx = json.loads((ROOT / "results" / "cog_index.json").read_text())
    spec = WorkloadSpec.load(ROOT / a.spec)
    cli = s3()

    want: dict[str, set] = defaultdict(set)
    for r in spec.reads:
        rec = idx.get(r.key)
        if not rec:
            continue
        for bi in blocks_for_window(rec, 0, r.x, r.y, r.w, r.h):
            want[r.key].add(bi)

    outdir = ROOT / a.dir
    t0 = time.time()
    tot_src = tot_idx = n_blocks = 0
    for n, key in enumerate(sorted(want), 1):
        L = idx[key]["levels"][0]
        blocks = {}
        for bi in sorted(want[key]):
            cnt, off = L["bytecounts"][bi], L["offsets"][bi]
            if cnt <= 0:
                continue
            comp = cli.get_object(Bucket=BUCKET, Key=key,
                                  Range=f"bytes={off}-{off+cnt-1}")["Body"].read()
            ti, _ = build_index(comp, a.span)
            blocks[bi] = ti.points
            tot_src += cnt
            n_blocks += 1
        if blocks:
            size = sbx.write(outdir / (key + ".sbx"), a.span, blocks)
            tot_idx += size
        if n % 8 == 0 or n == len(want):
            print(f"  {n}/{len(want)} files, {n_blocks:,} blocks", flush=True)

    print(f"\nbuilt {len(want)} sidecars for {n_blocks:,} blocks in "
          f"{time.time()-t0:.0f}s")
    print(f"  source blocks {tot_src/1e6:,.1f} MB")
    print(f"  sidecars      {tot_idx/1e6:,.1f} MB "
          f"({100*tot_idx/max(1,tot_src):.1f}% of the blocks indexed)")
    print(f"  -> {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
