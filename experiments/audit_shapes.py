#!/usr/bin/env python3
"""Where does the reader lose?

The verification suites use the workloads the library was designed for. An
audit has to look for the cases where it is no better, or worse, than GDAL, so
that the advertised gain can be stated with its actual boundaries.

Deterministic traffic only: bytes and requests are measured at the proxy, wall
time is ignored, because the point is the shape of the access pattern rather
than this machine's network.

Every (shape, engine) pair runs in its own subprocess. GDAL's block cache and
/vsicurl cache are process-global, so running several shapes in one process let
later shapes read blocks earlier ones had already fetched and report zero
requests, which made GDAL look free and the comparison meaningless.
"""
from __future__ import annotations

import json, os, sys, urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "baseline"))
from georange_io import SparseReader                      # noqa: E402

BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "switchback")
PROXY = os.environ.get("GEORANGE_IO_PROXY_HTTP", "http://proxy:9000")
CONTROL = os.environ.get("GEORANGE_IO_PROXY_CONTROL", "http://proxy:9010")
RAW = ROOT / "results" / "raw"
IDX = json.loads((ROOT / "results" / "cog_index.json").read_text())
KEY = "s2ts/11SLA/2024-01-01/B04.tif"
SCENES = sorted(k for k in IDX if k.startswith("s2ts/") and k.endswith("B04.tif"))


def ctl(path, payload=None, method="POST"):
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    r = urllib.request.Request(CONTROL + path, data=data,
                               headers={"Content-Type": "application/json"} if data else {},
                               method=method)
    return json.load(urllib.request.urlopen(r, timeout=600))


def tally(label):
    p = RAW / f"{label}.jsonl"
    n = b = 0
    for line in p.read_text().splitlines():
        d = json.loads(line or "{}")
        if d.get("record") == "req":
            n += 1; b += d.get("resp_body_bytes", 0)
    p.unlink(missing_ok=True)
    return n, b


def gdal_run(reqs, label):
    import rasterio
    from rasterio.windows import Window
    cfg = {"AWS_S3_ENDPOINT": "proxy:9000", "AWS_HTTPS": "NO",
           "AWS_VIRTUAL_HOSTING": "FALSE", "AWS_NO_SIGN_REQUEST": "YES",
           "AWS_DEFAULT_REGION": "us-east-1",
           "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES", "GDAL_HTTP_MULTIRANGE": "YES",
           "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_CACHEMAX": 2*1024**3,
           "VSI_CACHE": "TRUE", "VSI_CACHE_SIZE": "536870912",
           "CPL_VSIL_CURL_CHUNK_SIZE": "16384"}
    ctl("/session/start", {"label": label, "meta": {}})
    outs = []
    with rasterio.Env(**cfg):
        cache = {}
        for (k, lvl, x, y, w, h) in reqs:
            ck = (k, lvl)
            ds = cache.get(ck)
            if ds is None:
                kw = {} if lvl == 0 else {"OVERVIEW_LEVEL": lvl - 1}
                ds = cache[ck] = rasterio.open(f"/vsis3/{BUCKET}/{k}", **kw)
            outs.append(ds.read(1, window=Window(x, y, w, h)))
        for d in cache.values(): d.close()
    ctl("/session/stop")
    return outs, *tally(label)


def gr_run(reqs, label, **kw):
    rd = SparseReader(None, PROXY, BUCKET, margin=0.03, **kw)
    ctl("/session/start", {"label": label, "meta": {}})
    outs = rd.read([(k, lvl, x, y, w, h) for (k, lvl, x, y, w, h) in reqs])
    ctl("/session/stop")
    try: rd.close()
    except Exception: pass
    return outs, *tally(label)


def child(shape_name):
    reqs = shapes()[shape_name]
    eng = os.environ["AUDIT_ENGINE"]
    fn = gdal_run if eng == "gdal" else gr_run
    outs, n, b = fn(reqs, f"_A.{eng}")
    digest = __import__("hashlib").sha256(
        b"".join(np.ascontiguousarray(o).tobytes() for o in outs)).hexdigest()[:16]
    print("RESULT " + json.dumps({"requests": n, "bytes": b, "digest": digest}))


def shapes():
    L = IDX[KEY]["levels"][0]
    bw, bh = L["blockw"], L["blockh"]
    rng = np.random.default_rng(4)
    return {
        "1 point": [(KEY, 0, 5000, 5000, 1, 1)],
        "10 points, same block": [(KEY, 0, 4100 + i, 4100 + 3 * i, 1, 1)
                                  for i in range(10)],
        "10 points, scattered": [(KEY, 0, int(x), int(y), 1, 1) for x, y in
                                 zip(rng.integers(1100, 9800, 10),
                                     rng.integers(1100, 9800, 10))],
        "point at row 5 of a block": [(KEY, 0, 4100, 4 * bh + 5, 1, 1)],
        "point at row 1010 of a block": [(KEY, 0, 4100, 4 * bh + 1010, 1, 1)],
        "one 512x512 window": [(KEY, 0, 3000, 3000, 512, 512)],
        "one 2048x2048 window": [(KEY, 0, 2000, 2000, 2048, 2048)],
        "40 x 256x256 windows": [(KEY, 0, int(x), int(y), 256, 256) for x, y in
                                 zip(rng.integers(0, 10000, 40),
                                     rng.integers(0, 10000, 40))],
        "whole 1024x1024 block": [(KEY, 0, 4 * bw, 4 * bh, bw, bh)],
        "5 points x 12 dates": [(k, 0, int(x), int(y), 1, 1) for k in SCENES[:12]
                                for x, y in zip(rng.integers(1100, 9800, 5),
                                                rng.integers(1100, 9800, 5))],
    }


def run_isolated(shape_name, engine):
    import subprocess
    env = dict(os.environ, AUDIT_ENGINE=engine)
    cp = subprocess.run([sys.executable, __file__, "--child", shape_name],
                        capture_output=True, text=True, env=env, timeout=1800)
    for line in cp.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise RuntimeError(f"{engine}/{shape_name} failed:\n{cp.stderr[-1200:]}")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        return child(sys.argv[2]) or 0
    ctl("/shape", {"latency_ms": 0, "jitter_ms": 0, "bandwidth_mbps": 0})
    cases = shapes()

    print(f"{'query shape':<30} {'GDAL req/MB':>18} {'GeoRange req/MB':>20} "
          f"{'req':>7} {'bytes':>7}  ok")
    rows = []
    for name in cases:
        G = run_isolated(name, "gdal")
        S = run_isolated(name, "gr")
        gn, gb, sn, sb = G["requests"], G["bytes"], S["requests"], S["bytes"]
        ok = G["digest"] == S["digest"]
        rq = gn / max(1, sn); by = gb / max(1, sb)
        flag = "" if (rq >= 1 and by >= 1) else "   <-- worse"
        print(f"{name:<30} {gn:>7,} {gb/1e6:>9.2f}M {sn:>8,} {sb/1e6:>10.2f}M "
              f"{rq:>6.2f}x {by:>6.2f}x  {ok}{flag}")
        rows.append({"shape": name, "gdal_requests": gn, "gdal_bytes": gb,
                     "gr_requests": sn, "gr_bytes": sb,
                     "request_gain": round(rq, 3), "byte_gain": round(by, 3),
                     "identical": bool(ok)})
    (ROOT / "results" / "audit_shapes.json").write_text(json.dumps(rows, indent=2))
    bad = [r for r in rows if not r["identical"]]
    worse = [r for r in rows if r["byte_gain"] < 1 or r["request_gain"] < 1]
    print(f"\n  incorrect: {len(bad)}   |   no better than GDAL: {len(worse)}")
    for r in worse:
        print(f"    {r['shape']}: {r['request_gain']}x requests, "
              f"{r['byte_gain']}x bytes")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
