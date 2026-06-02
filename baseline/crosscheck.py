#!/usr/bin/env python3
"""Cross-check the proxy's request accounting against GDAL's own view.

The proxy is the source of truth for Phase 0. This runs one workload with
CPL_DEBUG enabled, counts the ranges GDAL says it downloaded, and compares
that with what the proxy recorded. They should agree closely; a large
disagreement means the instrument is wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SNIPPET = r'''
import json, logging, os, sys
sys.path.insert(0, "/work/baseline")
# rasterio routes GDAL's CPL_DEBUG output into Python logging rather than
# stderr, so it has to be captured from the logger to be seen at all.
logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                    format="GDALDBG %(message)s")
import urllib.request
from spec import WorkloadSpec
from rasterio.windows import Window
import rasterio

CONTROL = os.environ.get("GEORANGE_IO_PROXY_CONTROL", "http://proxy:9010")
BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")

def ctl(path, payload=None, method="POST"):
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    req = urllib.request.Request(
        CONTROL + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method)
    return json.load(urllib.request.urlopen(req, timeout=300))

spec = WorkloadSpec.load(sys.argv[1])
reads = spec.reads[:int(sys.argv[2])]
cfg = json.loads(sys.argv[3])
ctl("/shape", {"latency_ms": 0, "jitter_ms": 0})
ctl("/session/start", {"label": "_XCHECK", "meta": {}})
with rasterio.Env(**{k: (int(v) if k == "GDAL_CACHEMAX" else v)
                     for k, v in cfg.items()}):
    ds_cache = {}
    for r in reads:
        ds = ds_cache.get(r.key)
        if ds is None:
            ds = rasterio.open("/vsis3/%s/%s" % (BUCKET, r.key))
            ds_cache[r.key] = ds
        fct = 1 if r.level == 0 else ds.overviews(1)[r.level - 1]
        x0 = max(0, r.x * fct); y0 = max(0, r.y * fct)
        x1 = min(ds.width, (r.x + r.w) * fct); y1 = min(ds.height, (r.y + r.h) * fct)
        if x1 <= x0 or y1 <= y0:
            continue
        out = None if r.level == 0 else (max(1, (y1-y0)//fct), max(1, (x1-x0)//fct))
        ds.read(1, window=Window(x0, y0, x1-x0, y1-y0), out_shape=out)
    for d in ds_cache.values():
        d.close()
s = ctl("/session/stop")
print("PROXY " + json.dumps(s))
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="results/specs/w3.json")
    ap.add_argument("--reads", type=int, default=400)
    ap.add_argument("--out", default="results/crosscheck.json")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "baseline"))
    from harness import CONFIGS
    cfg = {k: (str(v) if k != "GDAL_CACHEMAX" else str(v))
           for k, v in CONFIGS["TUNED_chunk256k"].items()}

    compose = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]
    cp = subprocess.run(
        compose + ["exec", "-T", "-e", "CPL_DEBUG=ON", "bench",
                   "python3", "-c", SNIPPET, args.spec, str(args.reads),
                   json.dumps(cfg)],
        capture_output=True, text=True, timeout=1800)

    dbg = cp.stderr
    # GDAL emits one line per range it pulls, as "S3: Downloading <lo>-<hi>".
    pat = re.compile(r"(?:S3|VSIS3|VSICURL)\s*:\s*Downloading\s+(\d+)-(\d+)",
                     re.I)
    spans = [(int(a), int(b)) for a, b in pat.findall(dbg)]
    gdal_bytes = sum(b - a + 1 for a, b in spans)

    proxy = {}
    for line in cp.stdout.splitlines():
        if line.startswith("PROXY "):
            proxy = json.loads(line[6:])

    lg = ROOT / "results" / "raw" / "_XCHECK.jsonl"
    p_ranges = 0
    p_bytes = 0
    if lg.exists():
        for line in lg.read_text().splitlines():
            d = json.loads(line or "{}")
            if d.get("record") == "req" and d.get("range"):
                p_ranges += 1
                p_bytes += d.get("resp_body_bytes", 0)

    out = {
        "spec": args.spec, "reads": args.reads,
        "gdal_cpl_debug": {"ranges": len(spans), "bytes": gdal_bytes},
        "proxy": {"range_requests": p_ranges, "range_bytes": p_bytes,
                  "all_requests": proxy.get("n_requests"),
                  "all_bytes": proxy.get("n_bytes")},
        "agreement": {
            "request_delta": (p_ranges - len(spans)),
            "byte_delta": (p_bytes - gdal_bytes),
            "byte_ratio": (p_bytes / gdal_bytes) if gdal_bytes else None,
        },
        "note": ("GDAL emits one CPL_DEBUG line per tile-data range it pulls, "
                 "but none for the chunk reads it does to get a file's size "
                 "and headers. The proxy counts every HTTP request. The "
                 "expected difference is therefore a small number of header "
                 "fetches, each exactly CPL_VSIL_CURL_CHUNK_SIZE bytes; if "
                 "byte_delta divides evenly by the chunk size into "
                 "request_delta reads, the two views agree completely."),
        "header_chunk_reconciliation": {
            "chunk_size": int(cfg.get("CPL_VSIL_CURL_CHUNK_SIZE", 0) or 0),
            "delta_is_whole_chunks": (
                bool(int(cfg.get("CPL_VSIL_CURL_CHUNK_SIZE", 0) or 0)) and
                (p_bytes - gdal_bytes) ==
                (p_ranges - len(spans)) * int(cfg["CPL_VSIL_CURL_CHUNK_SIZE"])),
        },
    }
    (ROOT / args.out).write_text(json.dumps(out, indent=2))
    if lg.exists():
        lg.unlink()
    print(json.dumps(out["gdal_cpl_debug"]), json.dumps(out["proxy"]))
    print(json.dumps(out["agreement"]))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
