#!/usr/bin/env python3
"""Run one workload spec under one GDAL configuration and record what it cost.

Bytes and request counts come from the proxy, never from GDAL. GDAL's own
CPL_DEBUG output can be enabled for cross-checking but is not consulted here.

One invocation performs two passes in the same process:

    cold   a fresh process, empty GDAL block cache, empty /vsicurl cache
    warm   the identical read sequence again, caches as the cold pass left them

Both passes are captured as separate proxy sessions, so the warm numbers show
exactly how much of the traffic was avoidable by caching alone.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

from spec import WorkloadSpec  # noqa: E402

CONTROL = os.environ.get("GEORANGE_IO_PROXY_CONTROL", "http://127.0.0.1:9210")
BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
ENDPOINT_HOST = os.environ.get("AWS_S3_ENDPOINT", "proxy:9000")

# ---------------------------------------------------------------------------
# GDAL configurations under test.
#
# The four settings every config carries are connection requirements for
# reaching MinIO through the proxy, not tuning: endpoint, plaintext HTTP,
# path-style addressing, and anonymous access against a public-read bucket.
# DEFAULT adds nothing beyond them.
# ---------------------------------------------------------------------------
CONNECT = {
    "AWS_S3_ENDPOINT": ENDPOINT_HOST,
    "AWS_HTTPS": "NO",
    "AWS_VIRTUAL_HOSTING": "FALSE",
    "AWS_NO_SIGN_REQUEST": "YES",
    "AWS_DEFAULT_REGION": "us-east-1",
}

TUNED_BASE = {
    # Coalesce ranges that are adjacent or nearly so, instead of one request
    # per block.
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    # Let GDAL issue several ranges for one read instead of serialising them.
    "GDAL_HTTP_MULTIRANGE": "YES",
    # Multiplex over one connection where the transport allows it.
    "GDAL_HTTP_MULTIPLEX": "YES",
    # Never list the bucket on open. Without this, opening an object costs a
    # LIST round trip that buys nothing here.
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    # Generous block cache: 2 GiB of decoded blocks.
    #
    # rasterio intercepts this key and passes it straight to GDALSetCacheMax64,
    # which takes BYTES -- unlike GDAL's own string form, where "2048" means
    # megabytes. Passing the string here would have configured a 2 kB cache and
    # silently made every TUNED run look terrible. It is given in bytes, as an
    # int, and asserted at run time.
    "GDAL_CACHEMAX": 2 * 1024 ** 3,
    # Generous /vsicurl byte cache: 512 MB of raw fetched ranges.
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "536870912",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
}

CONFIGS: dict[str, dict] = {
    "DEFAULT": dict(CONNECT),
    "TUNED_chunk16k": {**CONNECT, **TUNED_BASE, "CPL_VSIL_CURL_CHUNK_SIZE": "16384"},
    "TUNED_chunk256k": {**CONNECT, **TUNED_BASE, "CPL_VSIL_CURL_CHUNK_SIZE": "262144"},
    "TUNED_chunk1m": {**CONNECT, **TUNED_BASE, "CPL_VSIL_CURL_CHUNK_SIZE": "1048576"},
}


def ctl(path: str, payload: dict | None = None, method: str = "POST") -> dict:
    url = f"{CONTROL}{path}"
    data = json.dumps(payload if payload is not None else {}).encode() \
        if method == "POST" else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def gdal_versions() -> dict:
    from osgeo import gdal
    import rasterio
    info = {"gdal": gdal.VersionInfo("RELEASE_NAME"),
            "rasterio": rasterio.__version__,
            "rasterio_gdal": rasterio.__gdal_version__}
    try:
        info["proj"] = rasterio.__proj_version__
    except Exception:  # noqa: BLE001
        pass
    try:
        bi = gdal.VersionInfo("BUILD_INFO") or ""
        info["build_info"] = {k: v for k, v in
                              (l.split("=", 1) for l in bi.strip().splitlines()
                               if "=" in l)}
        info["curl"] = info["build_info"].get("CURL_VERSION")
    except Exception:  # noqa: BLE001
        pass
    return info


def _clip(r, ds_w: int, ds_h: int, fct: int):
    """Clip a read window to the dataset. Windows are clipped rather than read
    boundless: rasterio's boundless path builds a temporary VRT per read, which
    bypasses GDAL's block cache entirely and would turn every measurement into
    a measurement of that artefact."""
    x0 = max(0, r.x * fct)
    y0 = max(0, r.y * fct)
    x1 = min(ds_w, (r.x + r.w) * fct)
    y1 = min(ds_h, (r.y + r.h) * fct)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def run_pass(spec: WorkloadSpec, label: str, meta: dict, max_wall_s: float,
             ds_cache: dict) -> dict:
    """Execute the read sequence once, bracketed by a proxy capture session.

    ds_cache is owned by the caller and shared between the cold and warm
    passes, so the warm pass sees GDAL's block cache and the /vsicurl range
    cache exactly as the cold pass left them. Closing datasets in between would
    discard both and make "warm" meaningless.
    """
    import rasterio
    from rasterio.windows import Window

    ctl("/session/start", {"label": label, "meta": meta})
    t0 = time.perf_counter()
    truncated = False
    n_done = n_skipped = 0
    checksum = 0.0

    for r in spec.reads:
        if time.perf_counter() - t0 > max_wall_s:
            truncated = True
            break
        ds = ds_cache.get(r.key)
        if ds is None:
            ds = rasterio.open(f"/vsis3/{BUCKET}/{r.key}")
            ds_cache[r.key] = ds
        if r.level == 0:
            fct = 1
            out_shape = None
        else:
            fct = ds.overviews(1)[r.level - 1]
            out_shape = (r.h, r.w)
        c = _clip(r, ds.width, ds.height, fct)
        if c is None:
            n_skipped += 1
            continue
        x, y, w, h = c
        if out_shape is not None:
            out_shape = (max(1, h // fct), max(1, w // fct))
        a = ds.read(1, window=Window(x, y, w, h), out_shape=out_shape)
        n_done += 1
        if a.size:
            checksum += float(a.flat[0])

    wall = time.perf_counter() - t0
    summary = ctl("/session/stop")
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS bytes.
    rss_mb = rss_kb / 1024.0 if sys.platform.startswith("linux") else rss_kb / 1e6

    return {"pass": label.rsplit(".", 1)[-1], "wall_s": round(wall, 4),
            "reads_executed": n_done, "reads_skipped": n_skipped,
            "reads_total": len(spec.reads),
            "truncated": truncated, "peak_rss_mb": round(rss_mb, 1),
            "proxy": summary, "log": summary.get("path"),
            "checksum": checksum}


def analyse_log(path: Path, theoretical: dict) -> dict:
    """Derive the headline metrics from the proxy's own record."""
    n_req = n_bytes = 0
    by_kind: dict[str, int] = {}
    bytes_by_kind: dict[str, int] = {}
    per_key: dict[str, int] = {}
    ranges: list[int] = []
    conns: set[str] = set()
    http_versions: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("record") != "req":
            continue
        n_req += 1
        b = d.get("resp_body_bytes", 0)
        n_bytes += b
        k = d.get("kind", "?")
        by_kind[k] = by_kind.get(k, 0) + 1
        bytes_by_kind[k] = bytes_by_kind.get(k, 0) + b
        if d.get("key"):
            per_key[d["key"]] = per_key.get(d["key"], 0) + b
        if d.get("range"):
            ranges.append(b)
        conns.add(d.get("conn", "?"))
        hv = d.get("http_version", "?")
        http_versions[hv] = http_versions.get(hv, 0) + 1
        st = str(d.get("status"))
        statuses[st] = statuses.get(st, 0) + 1

    mb = theoretical["min_total_bytes"]
    mr = theoretical["min_requests"]
    return {
        "bytes_fetched": n_bytes, "requests": n_req,
        "byte_amplification": (n_bytes / mb) if mb else None,
        "request_amplification": (n_req / mr) if mr else None,
        "min_total_bytes": mb, "min_requests": mr,
        "min_block_bytes": theoretical["min_block_bytes"],
        "header_bytes": theoretical["header_bytes"],
        "n_blocks": theoretical["n_blocks"],
        "requests_by_kind": by_kind, "bytes_by_kind": bytes_by_kind,
        "distinct_keys_touched": len(per_key),
        "mean_range_bytes": (sum(ranges) / len(ranges)) if ranges else 0,
        "connections": len(conns),
        "http_versions": http_versions,
        "statuses": statuses,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--config", required=True, choices=sorted(CONFIGS))
    ap.add_argument("--latency-ms", type=float, default=0.0)
    ap.add_argument("--jitter-ms", type=float, default=0.0)
    ap.add_argument("--connect-latency-ms", type=float, default=0.0)
    ap.add_argument("--bandwidth-mbps", type=float, default=0.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--max-wall-s", type=float, default=900.0)
    args = ap.parse_args()

    spec = WorkloadSpec.load(args.spec)
    cfg = CONFIGS[args.config]

    ctl("/shape", {"latency_ms": args.latency_ms, "jitter_ms": args.jitter_ms,
                   "connect_latency_ms": args.connect_latency_ms,
                   "bandwidth_mbps": args.bandwidth_mbps})

    base = args.tag or f"{spec.name}.{args.config}.rtt{int(args.latency_ms)}"
    meta = {"workload": spec.name, "config": args.config,
            "latency_ms": args.latency_ms, "jitter_ms": args.jitter_ms,
            "bandwidth_mbps": args.bandwidth_mbps}

    import rasterio
    raw_dir = ROOT / "results" / "raw"
    passes = []
    ds_cache: dict[str, object] = {}
    env = rasterio.Env(**cfg)
    env.__enter__()
    if "GDAL_CACHEMAX" in cfg:
        from osgeo import gdal
        got = gdal.GetCacheMax()
        if got < 256 * 1024 ** 2:
            raise RuntimeError(
                f"GDAL block cache is only {got} bytes; the TUNED config did "
                "not take effect")
    try:
      for phase in ("cold", "warm"):
        p = run_pass(spec, f"{base}.{phase}", {**meta, "phase": phase},
                     args.max_wall_s, ds_cache)
        # The proxy writes to its own /logs mount; the same directory is
        # results/raw/ here.
        lg = raw_dir / Path(p["log"]).name
        p["log"] = str(lg)
        if not lg.exists():
            raise RuntimeError(f"proxy log not visible at {lg}; the bench and "
                               "proxy containers are not sharing results/raw")
        p["metrics"] = analyse_log(lg, spec.theoretical)
        passes.append(p)
        m = p["metrics"] or {}
        print(f"  {phase:4s} {m.get('requests',0):7,} req  "
              f"{m.get('bytes_fetched',0)/1e6:9.2f} MB  "
              f"byte_amp={m.get('byte_amplification') or 0:6.2f}  "
              f"req_amp={m.get('request_amplification') or 0:7.2f}  "
              f"{p['wall_s']:7.2f}s  rss={p['peak_rss_mb']:.0f}MB"
              + ("  [TRUNCATED]" if p["truncated"] else ""), flush=True)
    finally:
        for d in ds_cache.values():
            try:
                d.close()
            except Exception:  # noqa: BLE001
                pass
        env.__exit__(None, None, None)

    out = {
        "workload": spec.name, "config": args.config,
        "gdal_config": cfg,
        "latency_ms": args.latency_ms, "jitter_ms": args.jitter_ms,
        "connect_latency_ms": args.connect_latency_ms,
        "bandwidth_mbps": args.bandwidth_mbps,
        "spec": {"seed": spec.seed, "n_reads": len(spec.reads),
                 "params": {k: v for k, v in spec.params.items()
                            if k not in ("corridors", "descent", "path_lonlat")}},
        "theoretical": spec.theoretical,
        "versions": gdal_versions(),
        "passes": passes,
        "ts": time.time(),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
