#!/usr/bin/env python3
"""Gate 1: does GDAL's own AdviseRead close the cross-call scheduling gap?

Phase 0 found GDAL issues 1.3x to 4.5x the minimum number of requests because
it merges byte ranges only within a single read call and cannot see what is
coming. GDAL already exposes AdviseRead, which is exactly a "here is what I am
about to need" hint. If it prefetches and coalesces, most of that gap is
reachable without a new reader, and the scheduling half of this project loses
its justification.

Two conditions, both measured at the proxy:

  naive     read the windows one at a time
  advised   call AdviseRead over the bounding box of the batch first, then
            read exactly the same windows

Each condition runs in its own subprocess. Running both in one process makes
the second one free, because GDAL's /vsicurl cache is process-wide and the
first condition has already fetched everything. The first version of this gate
did exactly that and reported that AdviseRead eliminated every request.

Blocks for the synthetic case are chosen from the largest tiles in the file.
Sentinel-2 scenes are rotated swaths, so the corner blocks are nodata and
compress to almost nothing; measuring against those says nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

from spec import WorkloadSpec  # noqa: E402

CONTROL = os.environ.get("GEORANGE_IO_PROXY_CONTROL", "http://proxy:9010")
BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
RAW = ROOT / "results" / "raw"


def ctl(path, payload=None, method="POST"):
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    req = urllib.request.Request(
        path if path.startswith("http") else CONTROL + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method)
    return json.load(urllib.request.urlopen(req, timeout=300))


def count(label: str) -> tuple[int, int]:
    p = RAW / f"{label}.jsonl"
    n = b = 0
    for line in p.read_text().splitlines():
        d = json.loads(line or "{}")
        if d.get("record") == "req":
            n += 1
            b += d.get("resp_body_bytes", 0)
    p.unlink(missing_ok=True)
    return n, b


CFG = {
    "AWS_S3_ENDPOINT": os.environ.get("AWS_S3_ENDPOINT", "proxy:9000"),
    "AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE",
    "AWS_NO_SIGN_REQUEST": "YES", "AWS_DEFAULT_REGION": "us-east-1",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "VSI_CACHE": "TRUE", "VSI_CACHE_SIZE": "536870912",
    "CPL_VSIL_CURL_CHUNK_SIZE": "16384",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
}


def run(windows, key, advise, label):
    """windows: list of (x, y, w, h) in native pixels of `key`."""
    from osgeo import gdal
    gdal.UseExceptions()
    for k, v in CFG.items():
        gdal.SetConfigOption(k, v)
    gdal.SetCacheMax(2 * 1024 ** 3)

    ctl("/session/start", {"label": label, "meta": {"advise": advise}})
    t0 = time.perf_counter()
    ds = gdal.Open(f"/vsis3/{BUCKET}/{key}")
    band = ds.GetRasterBand(1)
    if advise:
        x0 = min(w[0] for w in windows); y0 = min(w[1] for w in windows)
        x1 = max(w[0] + w[2] for w in windows); y1 = max(w[1] + w[3] for w in windows)
        band.AdviseRead(x0, y0, x1 - x0, y1 - y0)
    for (x, y, w, h) in windows:
        band.ReadAsArray(x, y, w, h)
    wall = time.perf_counter() - t0
    ds = None
    band = None
    ctl("/session/stop")
    n, b = count(label)
    return n, b, wall


def pick_adjacent_blocks(L: dict, n: int) -> list[tuple[int, int, int, int]]:
    """A run of n horizontally adjacent, data-bearing blocks."""
    nbx, nby = L["nbx"], L["nby"]
    cnts = L["bytecounts"]
    best, best_score = None, -1
    for by in range(nby):
        for bx in range(nbx - n + 1):
            idxs = [by * nbx + bx + i for i in range(n)]
            if any(cnts[i] == 0 for i in idxs):
                continue
            score = min(cnts[i] for i in idxs)
            if score > best_score:
                best_score, best = score, (bx, by)
    if best is None:
        raise RuntimeError("no run of data-bearing blocks found")
    bx, by = best
    out = []
    for i in range(n):
        x = (bx + i) * L["blockw"]
        y = by * L["blockh"]
        # the last block row and column of a COG are partial
        out.append((x, y, min(L["blockw"], L["width"] - x),
                    min(L["blockh"], L["height"] - y)))
    return out


def child(args) -> int:
    """Run one condition and print its result as JSON."""
    payload = json.loads(Path(args.windows_file).read_text())
    n, b, wall = run([tuple(w) for w in payload["windows"]], payload["key"],
                     args.mode == "advised", args.label)
    print("RESULT " + json.dumps({"requests": n, "bytes": b,
                                  "wall_s": round(wall, 2)}))
    return 0


def run_isolated(windows, key, mode, label) -> dict:
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"windows": windows, "key": key}, fh)
        path = fh.name
    cp = subprocess.run(
        [sys.executable, __file__, "--mode", mode, "--label", label,
         "--windows-file", path],
        capture_output=True, text=True, timeout=1800)
    os.unlink(path)
    for line in cp.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise RuntimeError(f"child failed for {label}:\n{cp.stdout[-500:]}\n"
                       f"{cp.stderr[-1500:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency-ms", type=float, default=50.0)
    ap.add_argument("--out", default="results/gate_adviseread.json")
    ap.add_argument("--mode", choices=["naive", "advised"])
    ap.add_argument("--label", default="")
    ap.add_argument("--windows-file", default="")
    args = ap.parse_args()

    if args.mode:
        return child(args)

    idx = json.loads((ROOT / "results" / "cog_index.json").read_text())
    ctl("/shape", {"latency_ms": args.latency_ms, "jitter_ms": 0,
                   "bandwidth_mbps": 0})

    report = {"latency_ms": args.latency_ms, "cases": []}

    # --- Case 1: a clean synthetic signal. N adjacent blocks, read one at a
    # time. If AdviseRead does anything at all, it shows here.
    key = "s2/11SLA/B04.tif"
    L = idx[key]["levels"][0]
    bw, bh = L["blockw"], L["blockh"]
    for nblocks in (4, 10):
        wins = pick_adjacent_blocks(L, nblocks)
        a = run_isolated(wins, key, "naive", f"_G1.synth{nblocks}.naive")
        b = run_isolated(wins, key, "advised", f"_G1.synth{nblocks}.advised")
        report["cases"].append({
            "case": f"{nblocks} adjacent data blocks of {key}",
            "naive": a, "advised": b})
        print(f"{nblocks:>3} adjacent blocks   naive {a['requests']:>5} req "
              f"{a['bytes']/1e6:8.1f} MB {a['wall_s']:6.2f}s   advised "
              f"{b['requests']:>5} req {b['bytes']/1e6:8.1f} MB {b['wall_s']:6.2f}s")

    # --- Case 2: a real workload. W3's corridor is fully known in advance, so
    # batching the hint over a run of consecutive windows is exactly what an
    # engine would do.
    spec = WorkloadSpec.load(ROOT / "results" / "specs" / "w3.json")
    target = max({r.key for r in spec.reads},
                 key=lambda k: sum(1 for r in spec.reads if r.key == k))
    wins = [(r.x, r.y, r.w, r.h) for r in spec.reads
            if r.key == target and r.level == 0][:400]
    a = run_isolated(wins, target, "naive", "_G1.w3.naive")
    b = run_isolated(wins, target, "advised", "_G1.w3.advised")
    report["cases"].append({
        "case": f"W3 corridor, {len(wins)} windows of {target}",
        "naive": a, "advised": b})
    print(f"W3 corridor {len(wins):>4} win  naive {a['requests']:>5} req "
          f"{a['bytes']/1e6:8.1f} MB {a['wall_s']:6.2f}s   advised "
          f"{b['requests']:>5} req {b['bytes']/1e6:8.1f} MB {b['wall_s']:6.2f}s")

    verdict = all(c["advised"]["requests"] >= c["naive"]["requests"] * 0.95
                  for c in report["cases"])
    for c in report["cases"]:
        c["request_ratio"] = round(
            c["advised"]["requests"] / max(1, c["naive"]["requests"]), 3)
        c["byte_ratio"] = round(
            c["advised"]["bytes"] / max(1, c["naive"]["bytes"]), 3)
    report["adviseread_is_a_noop"] = verdict
    report["verdict"] = ("AdviseRead does not reduce requests: the scheduling "
                         "gap is real and unreachable through GDAL"
                         if verdict else
                         "AdviseRead reduces requests: much of the scheduling "
                         "gap is reachable without a new reader")
    (ROOT / args.out).write_text(json.dumps(report, indent=2))
    print(f"\nVERDICT: {report['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
