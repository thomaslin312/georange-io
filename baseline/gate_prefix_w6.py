#!/usr/bin/env python3
"""Gate 3b: does prefix fetching move the floor on the time-series workload?

Every amplification number in this project divides by the same denominator: the
compressed size of the whole blocks a query touches. GDAL sits at 1.02x to 1.09x
of that on W6, so there is no reader-side byte headroom there either.

But that denominator assumes a reader must take whole blocks, and a DEFLATE
stream does not require it. For each block W6 actually touches, this computes
the compressed prefix needed to reach the deepest row any of its sample points
falls on, and sums those instead of the full block sizes.

Three figures are reported:

  whole blocks    the denominator used everywhere else in this project
  oracle prefix   the prefix a reader would need if it knew the length exactly
  speculative     the prefix a reader actually fetches, estimating length from
                  row position plus a safety margin, and paying a second
                  request when it guesses short
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

from spec import WorkloadSpec                    # noqa: E402
from theoretical import blocks_for_window        # noqa: E402

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


def prefix_bytes(comp: bytes, need_out: int) -> int:
    """Compressed bytes required to produce need_out bytes of output."""
    d = zlib.decompressobj()
    got = 0
    fed = comp
    guard = 0
    while got < need_out and guard < 8192:
        chunk = d.decompress(fed, need_out - got)
        got += len(chunk)
        fed = d.unconsumed_tail
        guard += 1
        if not chunk and not fed:
            break
    return len(comp) - len(fed)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="results/specs/w6.json")
    ap.add_argument("--margin", type=float, default=0.12,
                    help="safety margin a speculative fetch adds to its "
                         "row-fraction estimate")
    ap.add_argument("--out", default="results/gate_prefix_w6.json")
    args = ap.parse_args()

    idx = json.loads((ROOT / "results" / "cog_index.json").read_text())
    spec = WorkloadSpec.load(ROOT / args.spec)
    cli = s3()

    # deepest row needed within each block actually touched
    deepest: dict[tuple[str, int], int] = defaultdict(int)
    for r in spec.reads:
        rec = idx.get(r.key)
        if not rec:
            continue
        L = rec["levels"][0]
        for bi in blocks_for_window(rec, 0, r.x, r.y, r.w, r.h):
            by = bi // L["nbx"]
            row_in_block = (r.y + r.h - 1) - by * L["blockh"]
            row_in_block = max(0, min(L["blockh"] - 1, row_in_block))
            k = (r.key, bi)
            if row_in_block > deepest[k]:
                deepest[k] = row_in_block

    whole = oracle = spec_bytes = 0
    extra_requests = 0
    per_source: dict[str, dict] = defaultdict(
        lambda: {"blocks": 0, "whole": 0, "oracle": 0, "spec": 0})

    for n, ((key, bi), row) in enumerate(sorted(deepest.items()), 1):
        rec = idx[key]
        L = rec["levels"][0]
        cnt = L["bytecounts"][bi]
        off = L["offsets"][bi]
        if cnt <= 0:
            continue
        dt = np.dtype(L["dtype"])
        row_bytes = L["blockw"] * dt.itemsize
        comp = cli.get_object(Bucket=BUCKET, Key=key,
                              Range=f"bytes={off}-{off+cnt-1}")["Body"].read()
        need = prefix_bytes(comp, (row + 1) * row_bytes)

        # what a speculative reader would fetch: row fraction plus margin
        guess = min(cnt, int(cnt * ((row + 1) / L["blockh"] + args.margin)))
        if guess < need:
            extra_requests += 1
            guess = cnt          # undershot: it has to fetch the rest
        whole += cnt
        oracle += need
        spec_bytes += guess
        src = "SCL" if key.endswith("SCL.tif") else "B04"
        p = per_source[src]
        p["blocks"] += 1; p["whole"] += cnt; p["oracle"] += need; p["spec"] += guess
        if n % 400 == 0:
            print(f"  {n}/{len(deepest)} blocks", flush=True)

    out = {
        "spec": spec.name, "blocks": len(deepest),
        "margin": args.margin,
        "whole_blocks_mb": round(whole / 1e6, 1),
        "oracle_prefix_mb": round(oracle / 1e6, 1),
        "speculative_prefix_mb": round(spec_bytes / 1e6, 1),
        "oracle_saving": round(1 - oracle / whole, 4),
        "speculative_saving": round(1 - spec_bytes / whole, 4),
        "undershoot_blocks": extra_requests,
        "undershoot_rate": round(extra_requests / max(1, len(deepest)), 4),
        "per_source": {k: {**v,
                           "oracle_saving": round(1 - v["oracle"] / v["whole"], 4),
                           "speculative_saving": round(1 - v["spec"] / v["whole"], 4)}
                       for k, v in per_source.items()},
    }
    (ROOT / args.out).write_text(json.dumps(out, indent=2))
    print(f"\n{spec.name}: {len(deepest):,} blocks touched")
    print(f"  whole blocks           {out['whole_blocks_mb']:>9,.1f} MB   "
          "(the denominator used everywhere else)")
    print(f"  oracle prefix          {out['oracle_prefix_mb']:>9,.1f} MB   "
          f"{out['oracle_saving']*100:5.1f}% saved")
    print(f"  speculative prefix     {out['speculative_prefix_mb']:>9,.1f} MB   "
          f"{out['speculative_saving']*100:5.1f}% saved, "
          f"{out['undershoot_rate']*100:.1f}% of blocks needed a second request")
    for k, v in out["per_source"].items():
        print(f"    {k}: {v['blocks']:,} blocks, oracle {v['oracle_saving']*100:.1f}%, "
              f"speculative {v['speculative_saving']*100:.1f}%")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
