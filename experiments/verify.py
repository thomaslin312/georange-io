#!/usr/bin/env python3
"""Correctness gate: the sparse reader must agree with GDAL exactly.

Everything the reader claims rests on producing identical values while
fetching fewer bytes. If a single pixel disagrees, the byte numbers are
meaningless. This runs a workload spec through both and compares every value,
then reports what each fetched.

Fetches go through the logging proxy so the byte and request counts are
measured the same way as every other number in this project, rather than being
self-reported.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))
sys.path.insert(0, str(ROOT))

from spec import WorkloadSpec                      # noqa: E402
from georange_io.reader import SparseReader         # noqa: E402

BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
PROXY = os.environ.get("GEORANGE_IO_PROXY_HTTP", "http://proxy:9000")
CONTROL = os.environ.get("GEORANGE_IO_PROXY_CONTROL", "http://proxy:9010")
RAW = ROOT / "results" / "raw"


def ctl(path, payload=None, method="POST"):
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    req = urllib.request.Request(
        CONTROL + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method)
    return json.load(urllib.request.urlopen(req, timeout=600))


def tally(label: str):
    p = RAW / f"{label}.jsonl"
    n = b = 0
    for line in p.read_text().splitlines():
        d = json.loads(line or "{}")
        if d.get("record") == "req":
            n += 1
            b += d.get("resp_body_bytes", 0)
    p.unlink(missing_ok=True)
    return n, b


def gdal_values(reqs, label):
    import rasterio
    from rasterio.windows import Window
    cfg = {"AWS_S3_ENDPOINT": os.environ.get("AWS_S3_ENDPOINT", "proxy:9000"),
           "AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE",
           "AWS_NO_SIGN_REQUEST": "YES", "AWS_DEFAULT_REGION": "us-east-1",
           "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
           "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
           "GDAL_CACHEMAX": 2 * 1024 ** 3, "VSI_CACHE": "TRUE",
           "VSI_CACHE_SIZE": "536870912", "CPL_VSIL_CURL_CHUNK_SIZE": "16384"}
    ctl("/session/start", {"label": label, "meta": {"engine": "gdal"}})
    t0 = time.perf_counter()
    out = np.zeros(len(reqs), np.float64)
    with rasterio.Env(**cfg):
        ds_cache = {}
        for i, (key, x, y) in enumerate(reqs):
            ds = ds_cache.get(key)
            if ds is None:
                ds = ds_cache[key] = rasterio.open(f"/vsis3/{BUCKET}/{key}")
            out[i] = float(ds.read(1, window=Window(x, y, 1, 1))[0, 0])
        for d in ds_cache.values():
            d.close()
    wall = time.perf_counter() - t0
    ctl("/session/stop")
    n, b = tally(label)
    return out, n, b, wall


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="results/specs/w6p5.json")
    ap.add_argument("--latency-ms", type=float, default=0.0)
    ap.add_argument("--margin", type=float, default=0.12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--rtt-s", type=float, default=0.05)
    ap.add_argument("--bandwidth-mbps", type=float, default=100.0)
    ap.add_argument("--sbx-dir", default="")
    ap.add_argument("--no-index", action="store_true",
                    help="describe every file from its own header instead of "
                         "using the prebuilt index")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    spec = WorkloadSpec.load(ROOT / args.spec)
    reads = [r for r in spec.reads if r.level == 0 and r.w == 1 and r.h == 1]
    if len(reads) != len(spec.reads):
        print(f"note: {len(spec.reads)-len(reads)} non-point reads skipped")
    if args.limit:
        reads = reads[:args.limit]
    reqs = [(r.key, r.x, r.y) for r in reads]
    print(f"{spec.name}: {len(reqs):,} point reads over "
          f"{len({k for k,_,_ in reqs})} files")

    ctl("/shape", {"latency_ms": args.latency_ms, "jitter_ms": 0,
                   "bandwidth_mbps": 0})

    truth, gn, gb, gw = gdal_values(reqs, "_V.gdal")
    print(f"  GDAL       {gn:>6,} req  {gb/1e6:9.2f} MB  {gw:7.2f}s")

    rd = SparseReader(None if args.no_index
                      else ROOT / "results" / "cog_index.json", PROXY, BUCKET,
                      margin=args.margin, workers=args.workers,
                      rtt_s=args.rtt_s, bandwidth_mbps=args.bandwidth_mbps,
                      sbx_dir=(ROOT / args.sbx_dir) if args.sbx_dir else None)
    unsupported = sorted({k for k, _, _ in reqs if not rd.supports(k)})
    if unsupported:
        print(f"  !! reader declines {len(unsupported)} files: {unsupported[:3]}")
    ctl("/session/start", {"label": "_V.georange_io", "meta": {"engine": "sb"}})
    t0 = time.perf_counter()
    got = rd.sample(reqs)
    sw = time.perf_counter() - t0
    ctl("/session/stop")
    sn, sb = tally("_V.georange_io")
    print(f"  GeoRange IO {sn:>6,} req  {sb/1e6:9.2f} MB  {sw:7.2f}s")

    same = np.array_equal(truth, got)
    bad = int((truth != got).sum())
    print(f"\n  values identical to GDAL: {same}  ({len(reqs)-bad:,}/{len(reqs):,} match)")
    if not same:
        idx = np.flatnonzero(truth != got)[:5]
        for i in idx:
            print(f"    {reqs[i]}  gdal={truth[i]}  GeoRange IO={got[i]}")
    print(f"  bytes   {gb/max(1,sb):.2f}x fewer      "
          f"requests {gn/max(1,sn):.2f}x fewer")
    print(f"  reader stats: {json.dumps(rd.stats.as_dict())}")
    if args.no_index:
        print(f"  described {getattr(rd.idx, 'described', 0)} files from their "
              "own headers, with no prebuilt index")

    res = {"spec": spec.name, "n_reads": len(reqs), "identical": bool(same),
           "mismatches": bad, "margin": args.margin,
           "gdal": {"requests": gn, "bytes": gb, "wall_s": round(gw, 2)},
           "georange-io": {"requests": sn, "bytes": sb, "wall_s": round(sw, 2)},
           "byte_gain": round(gb / max(1, sb), 3),
           "request_gain": round(gn / max(1, sn), 3),
           "reader_stats": rd.stats.as_dict(),
           "declined_files": unsupported}
    if args.out:
        (ROOT / args.out).write_text(json.dumps(res, indent=2))
        print(f"  wrote {args.out}")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
