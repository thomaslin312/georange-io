#!/usr/bin/env python3
"""End-to-end against the real archive: the last unmeasured claim.

Section 12 of REPORT.md projects a wall-time gain by composing a measured
per-request cost with measured request counts. That was all the connection here
allowed. This measures it directly instead: the same requests, against the
public Sentinel-2 bucket on AWS, through GDAL and through GeoRange IO, on the
same machine at the same time.

Each engine runs in its own subprocess so neither starts with a warm cache.
The order is shuffled for every repetition to avoid systematically giving one
engine a warmer CDN or a quieter network. GDAL gets the best configuration
Phase 0 found rather than defaults.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "baseline"))

HOST = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
BUCKET = "sentinel-s2-l2a-cogs"

GDAL_CFG = {
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_CACHEMAX": 2 * 1024 ** 3,
    "VSI_CACHE": "TRUE", "VSI_CACHE_SIZE": "536870912",
    "CPL_VSIL_CURL_CHUNK_SIZE": "16384",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
}


def digest(values: np.ndarray) -> str:
    """Exact, order-sensitive identity after a common numeric representation."""
    canonical = np.ascontiguousarray(values, dtype="<f8")
    return hashlib.sha256(canonical.view(np.uint8)).hexdigest()


def build_requests(n_points: int, n_dates: int, seed: int = 5):
    """Real AWS keys for one MGRS tile across many acquisitions."""
    import yaml
    doc = yaml.safe_load((ROOT / "data" / "sources_w6.yaml").read_text())
    objs = [o for o in doc["objects"] if o["key"].endswith("B04.tif")]
    objs = objs[:n_dates]
    keys, staged = [], []
    for o in objs:
        # keys are relative to the bucket; the reader joins host, bucket and key
        keys.append(o["url"].split(f"{HOST}/{BUCKET}/", 1)[1])
        staged.append(o["key"])
    rng = np.random.default_rng(seed)
    xs = rng.integers(1100, 9800, n_points)
    ys = rng.integers(1100, 9800, n_points)
    reqs = [(k, int(x), int(y)) for k in keys for x, y in zip(xs, ys)]
    return reqs, dict(zip(keys, staged))


def child_gdal(reqs):
    import rasterio
    from rasterio.windows import Window
    ranges = []

    class Grab:
        def write(self, s):
            ranges.extend(re.findall(r"Downloading\s+(\d+)-(\d+)", s))
        def flush(self): pass

    import logging
    logging.basicConfig(level=logging.DEBUG, stream=Grab(), format="%(message)s")
    os.environ["CPL_DEBUG"] = "ON"

    t0 = time.perf_counter()
    out = np.zeros(len(reqs))
    with rasterio.Env(**GDAL_CFG):
        cache = {}
        for i, (key, x, y) in enumerate(reqs):
            ds = cache.get(key)
            if ds is None:
                ds = cache[key] = rasterio.open(
                    f"/vsicurl/{HOST}/{BUCKET}/{key}")
            out[i] = float(ds.read(1, window=Window(x, y, 1, 1))[0, 0])
        for d in cache.values():
            d.close()
    wall = time.perf_counter() - t0
    nbytes = sum(int(b) - int(a) + 1 for a, b in ranges)
    return {"engine": "gdal", "wall_s": round(wall, 2), "requests": len(ranges),
            "bytes": nbytes, "value_sha256": digest(out)}


def child_georange_io(reqs, sbx_dir, workers, bw):
    from georange_io import SparseReader
    content_identities = {}
    if sbx_dir:
        import yaml
        staged = json.loads((ROOT / "data" / "staged.json").read_text())
        sources = yaml.safe_load(
            (ROOT / "data" / "sources_w6.yaml").read_text())["objects"]
        for source in sources:
            prefix = f"{HOST}/{BUCKET}/"
            if source["url"].startswith(prefix):
                aws_key = source["url"][len(prefix):]
                # named sha, not digest: a local binding here shadows the
                # module-level digest() helper for the whole function, and
                # Python then treats every use of the name as local, so the
                # value hash at the end raised UnboundLocalError even when no
                # sidecar was configured
                sha = staged.get(source["key"], {}).get("sha256")
                if sha:
                    content_identities[aws_key] = f"sha256:{sha}"
    rd = SparseReader(None, HOST, BUCKET, margin=0.03, workers=workers,
                      rtt_s=0.12, bandwidth_mbps=bw,
                      sbx_dir=sbx_dir or None,
                      content_identities=content_identities)
    t0 = time.perf_counter()
    out = rd.sample(reqs)
    wall = time.perf_counter() - t0
    st = rd.stats.as_dict()
    return {"engine": "georange-io", "wall_s": round(wall, 2),
            "requests": st["requests"], "bytes": st["bytes_fetched"],
            "blocks_from_checkpoint": st["blocks_from_checkpoint"],
            "workers": workers, "value_sha256": digest(out)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", type=int, default=10)
    ap.add_argument("--dates", type=int, default=24)
    ap.add_argument("--sbx-dir", default="")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--bandwidth-mbps", type=float, default=100.0)
    ap.add_argument("--repetitions", type=int, default=3)
    ap.add_argument("--order-seed", type=int, default=202606)
    ap.add_argument("--engine", default="")
    ap.add_argument("--out", default="results/bench_aws.json")
    a = ap.parse_args()

    reqs, key_map = build_requests(a.points, a.dates)

    if a.engine == "gdal":
        print("RESULT " + json.dumps(child_gdal(reqs)))
        return 0
    if a.engine == "georange-io":
        print("RESULT " + json.dumps(
            child_georange_io(reqs, a.sbx_dir, a.workers, a.bandwidth_mbps)))
        return 0

    # sidecars were built against the staged copies, which are byte-identical
    # to the AWS objects, so they are valid here once renamed to the AWS keys
    sbx_dir = ""
    src = ROOT / "results" / "sbx"
    if src.exists():
        dst = ROOT / "results" / "sbx_aws"
        made = 0
        for aws_key, staged_key in key_map.items():
            s = src / (staged_key + ".sbx")
            d = dst / (aws_key + ".sbx")
            if s.exists() and not d.exists():
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_bytes(s.read_bytes())
                made += 1
        if any(dst.rglob("*.sbx")):
            sbx_dir = str(dst)
            print(f"sidecars available for the AWS keys ({made} copied)")

    if a.repetitions < 1:
        ap.error("--repetitions must be at least 1")
    print(f"\n{len(reqs):,} point reads: {a.points} locations x {a.dates} "
          f"acquisitions, {a.repetitions} repetitions live from {HOST}\n")
    runs = []
    base_plan = [("gdal", {}), ("georange-io", {"workers": 1}),
                 ("georange-io", {"workers": 8})]
    if sbx_dir:
        base_plan.append(("georange-io", {"workers": 8, "sbx": sbx_dir}))

    print(f"{'run':>3} {'engine':<28} {'requests':>9} {'bytes':>11} {'wall':>9}")
    for repetition in range(1, a.repetitions + 1):
        plan = list(base_plan)
        random.Random(a.order_seed + repetition).shuffle(plan)
        for engine, kw in plan:
            cmd = [sys.executable, __file__, "--engine", engine,
                   "--points", str(a.points), "--dates", str(a.dates),
                   "--bandwidth-mbps", str(a.bandwidth_mbps)]
            if "workers" in kw:
                cmd += ["--workers", str(kw["workers"])]
            if "sbx" in kw:
                cmd += ["--sbx-dir", kw["sbx"]]
            # A single transient read error against a live object store used
            # to abort the whole benchmark and lose every repetition. Both
            # engines hit these; GDAL surfaced a TIFFReadEncodedTile failure
            # mid-run. Retry the whole measurement rather than the request, so
            # a retried attempt is still a clean cold-cache process.
            got = None
            last = ""
            for attempt in range(3):
                cp = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=3600)
                for line in cp.stdout.splitlines():
                    if line.startswith("RESULT "):
                        got = json.loads(line[7:])
                if got is not None:
                    break
                last = cp.stderr[-1500:]
                print(f"    {engine}: attempt {attempt + 1} failed, retrying",
                      flush=True)
                time.sleep(2 * (attempt + 1))
            if got is None:
                raise RuntimeError(
                    f"{engine} failed after 3 attempts:\n{last}")
            label = "GDAL" if engine == "gdal" else "GeoRange IO"
            if engine == "georange-io":
                label += f", {kw['workers']} worker(s)"
                if "sbx" in kw:
                    label += " + sidecar"
            got.update(label=label, repetition=repetition)
            runs.append(got)
            print(f"{repetition:>3} {label:<28} {got['requests']:>9,} "
                  f"{got['bytes']/1e6:>10,.1f}M {got['wall_s']:>8.2f}s")

    same = len({r["value_sha256"] for r in runs}) == 1
    summary = {}
    for label in dict.fromkeys(r["label"] for r in runs):
        selected = [r for r in runs if r["label"] == label]
        summary[label] = {
            "repetitions": len(selected),
            "requests_median": statistics.median(r["requests"] for r in selected),
            "bytes_median": statistics.median(r["bytes"] for r in selected),
            "wall_s_median": round(statistics.median(
                r["wall_s"] for r in selected), 2),
            "wall_s_min": min(r["wall_s"] for r in selected),
            "wall_s_max": max(r["wall_s"] for r in selected),
        }
    baseline = summary["GDAL"]
    print(f"\n  every engine returned byte-identical value arrays: {same}")
    print(f"\n  {'median against GDAL':<30} {'requests':>9} {'bytes':>9} {'wall':>9}")
    for label, row in summary.items():
        if label == "GDAL":
            continue
        print(f"  {label:<30} "
              f"{baseline['requests_median']/max(1,row['requests_median']):>8.2f}x "
              f"{baseline['bytes_median']/max(1,row['bytes_median']):>8.2f}x "
              f"{baseline['wall_s_median']/max(0.01,row['wall_s_median']):>8.2f}x")

    (ROOT / a.out).write_text(json.dumps(
        {"host": HOST, "points": a.points, "dates": a.dates,
         "n_reads": len(reqs), "repetitions": a.repetitions,
         "order_seed": a.order_seed, "values_agree": same,
         "runs": runs, "summary": summary}, indent=2))
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
