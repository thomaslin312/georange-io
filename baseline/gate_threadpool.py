#!/usr/bin/env python3
"""Gate 2: does naive parallelism close the latency gap?

Phase 0 measured GDAL using one or two connections on W1 through W4, so its
requests were effectively serialised and wall time tracked requests x RTT. A
consumer that simply drove the same read list from a thread pool would overlap
those requests without reducing the request count at all.

If that recovers most of the wall time, then the round-trip headroom Phase 0
found is not a reason to build anything: it is a reason to use threads.

Each thread count runs in its own process so the caches start cold, and each
thread gets its own dataset handle because GDAL's RasterIO is not safe to call
concurrently on one handle. That is itself worth measuring: separate handles
may refetch, so parallelism can cost bytes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

from spec import WorkloadSpec  # noqa: E402

CONTROL = os.environ.get("GEORANGE_IO_PROXY_CONTROL", "http://proxy:9010")
BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
RAW = ROOT / "results" / "raw"

CFG = {
    "AWS_S3_ENDPOINT": os.environ.get("AWS_S3_ENDPOINT", "proxy:9000"),
    "AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE",
    "AWS_NO_SIGN_REQUEST": "YES", "AWS_DEFAULT_REGION": "us-east-1",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_CACHEMAX": 2 * 1024 ** 3,
    "VSI_CACHE": "TRUE", "VSI_CACHE_SIZE": "536870912",
    "CPL_VSIL_CURL_CHUNK_SIZE": "16384",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
}


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
    conns = set()
    for line in p.read_text().splitlines():
        d = json.loads(line or "{}")
        if d.get("record") == "req":
            n += 1
            b += d.get("resp_body_bytes", 0)
            conns.add(d.get("conn"))
    p.unlink(missing_ok=True)
    return n, b, len(conns)


def child(spec_path: str, nthreads: int, label: str, latency: float) -> int:
    import threading
    import rasterio
    from rasterio.windows import Window

    spec = WorkloadSpec.load(spec_path)
    ctl("/shape", {"latency_ms": latency, "jitter_ms": 0, "bandwidth_mbps": 0})
    ctl("/session/start", {"label": label, "meta": {"threads": nthreads}})

    local = threading.local()
    handles = []
    lock = threading.Lock()

    def get(key):
        # one handle per thread per file: GDAL RasterIO is not safe to call
        # concurrently on a single dataset handle
        d = getattr(local, "ds", None)
        if d is None:
            d = local.ds = {}
        if key not in d:
            h = rasterio.open(f"/vsis3/{BUCKET}/{key}")
            d[key] = h
            with lock:
                handles.append(h)
        return d[key]

    def work(r):
        ds = get(r.key)
        fct = 1 if r.level == 0 else ds.overviews(1)[r.level - 1]
        x0 = max(0, r.x * fct); y0 = max(0, r.y * fct)
        x1 = min(ds.width, (r.x + r.w) * fct); y1 = min(ds.height, (r.y + r.h) * fct)
        if x1 <= x0 or y1 <= y0:
            return 0
        out = None if r.level == 0 else (max(1, (y1 - y0) // fct),
                                         max(1, (x1 - x0) // fct))
        a = ds.read(1, window=Window(x0, y0, x1 - x0, y1 - y0), out_shape=out)
        return int(a.size > 0)

    t0 = time.perf_counter()
    with rasterio.Env(**CFG):
        if nthreads == 1:
            for r in spec.reads:
                work(r)
        else:
            with ThreadPoolExecutor(nthreads) as ex:
                list(ex.map(work, spec.reads))
        for h in handles:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
    wall = time.perf_counter() - t0
    ctl("/session/stop")
    n, b, c = tally(label)
    print("RESULT " + json.dumps({"threads": nthreads, "requests": n,
                                  "bytes": b, "connections": c,
                                  "wall_s": round(wall, 2)}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", nargs="*", default=["w3", "w2"])
    ap.add_argument("--threads", nargs="*", type=int, default=[1, 4, 8, 16])
    ap.add_argument("--latency-ms", type=float, default=150.0)
    ap.add_argument("--out", default="results/gate_threadpool.json")
    ap.add_argument("--child", type=int, default=0)
    ap.add_argument("--spec", default="")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    if a.child:
        return child(a.spec, a.child, a.label, a.latency_ms)

    import subprocess
    report = {"latency_ms": a.latency_ms, "workloads": {}}
    for w in a.workloads:
        spec_path = ROOT / "results" / "specs" / f"{w}.json"
        if not spec_path.exists():
            continue
        spec = WorkloadSpec.load(spec_path)
        floor = spec.theoretical.get("min_requests_coalesced")
        rows = []
        for n in a.threads:
            label = f"_G2.{w.upper()}.t{n}"
            cp = subprocess.run(
                [sys.executable, __file__, "--child", str(n),
                 "--spec", str(spec_path), "--label", label,
                 "--latency-ms", str(a.latency_ms)],
                capture_output=True, text=True, timeout=3600)
            got = None
            for line in cp.stdout.splitlines():
                if line.startswith("RESULT "):
                    got = json.loads(line[7:])
            if got is None:
                raise RuntimeError(f"{label} failed:\n{cp.stderr[-1500:]}")
            rows.append(got)
            print(f"{w.upper():<3} {n:>3} threads  {got['requests']:>6,} req  "
                  f"{got['bytes']/1e6:8.1f} MB  {got['connections']:>3} conns  "
                  f"{got['wall_s']:8.2f}s")
        base = rows[0]
        for r in rows:
            r["speedup_vs_1_thread"] = round(base["wall_s"] / r["wall_s"], 2)
            r["extra_bytes_vs_1_thread"] = round(r["bytes"] / base["bytes"], 3)
        report["workloads"][w.upper()] = {
            "min_requests_coalesced": floor,
            "scheduler_floor_wall_s": round(
                base["wall_s"] * floor / base["requests"], 2),
            "runs": rows}
        print(f"    request floor {floor}, so a perfect serial scheduler would "
              f"take ~{report['workloads'][w.upper()]['scheduler_floor_wall_s']}s\n")

    (ROOT / a.out).write_text(json.dumps(report, indent=2))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
