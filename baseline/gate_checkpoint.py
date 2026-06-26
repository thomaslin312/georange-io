#!/usr/bin/env python3
"""Does checkpointed random access beat prefix decoding on the real workload?

Three ways to serve the same W6 read, measured on the blocks it actually
touches:

  whole tile      what every existing reader must do
  prefix          start at byte zero, stop at the target row
  checkpointed    start at the nearest restart point, stop at the target row

The checkpointed reader needs a side index, and that is the cost, twice over.
Each restart point carries a 32 kB DEFLATE window, so the index has a storage
size that trades against read cost. And the window has to be in hand at read
time: if the index is held locally the read costs only the compressed bytes,
but if it is fetched per query the 32 kB window is part of the read.

Both are reported, because they are different deployments. A pipeline working
a hot region caches the index; a cold one-shot query does not.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

from spec import WorkloadSpec                     # noqa: E402
from theoretical import blocks_for_window         # noqa: E402
from georange_io.tile_index import (build_index,           # noqa: E402
                                   fetch_bytes_for, read_from)

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


def prefix_cost(comp: bytes, need_out: int, wrapped: bool) -> int:
    import zlib
    d = zlib.decompressobj(15 if wrapped else -15)
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
    ap.add_argument("--blocks", type=int, default=300)
    ap.add_argument("--spans", nargs="*", type=int,
                    default=[65536, 131072, 262144, 524288])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="results/gate_checkpoint.json")
    args = ap.parse_args()

    idx = json.loads((ROOT / "results" / "cog_index.json").read_text())
    spec = WorkloadSpec.load(ROOT / args.spec)
    rng = np.random.default_rng(args.seed)
    cli = s3()

    deepest: dict[tuple[str, int], int] = defaultdict(int)
    for r in spec.reads:
        rec = idx.get(r.key)
        if not rec:
            continue
        L = rec["levels"][0]
        for bi in blocks_for_window(rec, 0, r.x, r.y, r.w, r.h):
            row = max(0, min(L["blockh"] - 1,
                             (r.y + r.h - 1) - (bi // L["nbx"]) * L["blockh"]))
            k = (r.key, bi)
            if row > deepest[k]:
                deepest[k] = row

    keys = sorted(deepest)
    sample = [keys[i] for i in rng.choice(len(keys),
                                          size=min(args.blocks, len(keys)),
                                          replace=False)]
    print(f"{len(keys):,} blocks touched by {spec.name}, sampling {len(sample)}")

    totals = {"whole": 0, "prefix": 0}
    ck = {s: {"fetch": 0, "decode": 0, "index": 0, "points": 0, "window": 0}
          for s in args.spans}
    dec = {"whole": 0, "prefix": 0}
    verified = 0
    checked = 0

    for n, (key, bi) in enumerate(sample, 1):
        L = idx[key]["levels"][0]
        cnt, off = L["bytecounts"][bi], L["offsets"][bi]
        if cnt <= 0:
            continue
        dt = np.dtype(L["dtype"])
        row_bytes = L["blockw"] * dt.itemsize
        row = deepest[(key, bi)]
        need_out = (row + 1) * row_bytes
        comp = cli.get_object(Bucket=BUCKET, Key=key,
                              Range=f"bytes={off}-{off+cnt-1}")["Body"].read()

        totals["whole"] += cnt
        dec["whole"] += L["blockh"] * row_bytes
        wrapped = (comp[0] & 0x0F) == 8 and ((comp[0] << 8) | comp[1]) % 31 == 0
        totals["prefix"] += prefix_cost(comp, need_out, wrapped)
        dec["prefix"] += need_out

        for span in args.spans:
            ti, full = build_index(comp, span)
            fb, db = fetch_bytes_for(comp, ti, row * row_bytes, need_out)
            ck[span]["fetch"] += fb
            ck[span]["decode"] += db
            ck[span]["index"] += ti.index_bytes
            ck[span]["points"] += len(ti.points)
            pt = ti.point_for(row * row_bytes)
            ck[span]["window"] += len(pt.window) if pt else 0
            if checked < 40 and span == args.spans[-1]:
                got = read_from(comp, ti, row * row_bytes, need_out)
                verified += int(got == full[row * row_bytes:need_out])
                checked += 1
        if n % 50 == 0:
            print(f"  {n}/{len(sample)}", flush=True)

    out = {"spec": spec.name, "blocks_sampled": len(sample),
           "blocks_touched": len(keys),
           "whole_mb": round(totals["whole"] / 1e6, 1),
           "prefix_mb": round(totals["prefix"] / 1e6, 1),
           "verified_rows": f"{verified}/{checked}",
           "spans": {}}
    print(f"\n{spec.name}, {len(sample)} blocks   "
          f"(scaled to all {len(keys):,} blocks in brackets)")
    scale = len(keys) / len(sample)
    print(f"  whole tiles                   {out['whole_mb']:>6,.1f} MB "
          f"{1.0:>6.2f}x")
    print(f"  prefix                        {out['prefix_mb']:>6,.1f} MB "
          f"{totals['whole']/totals['prefix']:>6.2f}x")
    for span in args.spans:
        c = ck[span]
        cold = c["fetch"] + c["window"]
        out["spans"][str(span)] = {
            "checkpoints_per_tile": round(c["points"] / len(sample), 1),
            "fetch_mb_index_cached": round(c["fetch"] / 1e6, 1),
            "fetch_mb_index_remote": round(cold / 1e6, 1),
            "decode_mb": round(c["decode"] / 1e6, 1),
            "index_mb": round(c["index"] / 1e6, 1),
            "index_pct_of_archive": round(100 * c["index"] / totals["whole"], 1),
            "speedup_cached": round(totals["whole"] / max(1, c["fetch"]), 2),
            "speedup_remote": round(totals["whole"] / max(1, cold), 2),
            "speedup_vs_prefix_cached": round(
                totals["prefix"] / max(1, c["fetch"]), 2),
        }
        v = out["spans"][str(span)]
        print(f"  checkpoint {span//1024:>4} kB   cached "
              f"{v['fetch_mb']:>7,.1f} MB {v['speedup_cached']:>6.2f}x   "
              f"remote {v['fetch_mb_index_remote']:>7,.1f} MB "
              f"{v['speedup_remote']:>5.2f}x   "
              f"index {v['index_pct_of_archive']:>5.1f}% of archive"
              if False else
              f"  checkpoint {span//1024:>4} kB   "
              f"index cached {v['fetch_mb_index_cached']:>6,.1f} MB "
              f"{v['speedup_cached']:>6.2f}x    "
              f"index fetched {v['fetch_mb_index_remote']:>6,.1f} MB "
              f"{v['speedup_remote']:>5.2f}x    "
              f"index size {v['index_pct_of_archive']:>5.1f}% of archive")
    print(f"\n  mid-stream restart verified on {out['verified_rows']} rows")
    (ROOT / args.out).write_text(json.dumps(out, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
