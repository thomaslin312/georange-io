#!/usr/bin/env python3
"""Does fetching fewer bytes actually save time on a real object store?

Every measurement in this project ran against local MinIO, where bandwidth is
free and latency is injected. That flatters a reader whose whole trick is
fetching less. On a real store a request costs roughly

    time  =  time-to-first-byte  +  bytes / bandwidth

and TTFB does not care how much was asked for. So the saving from fetching a
20 kB prefix instead of a 938 kB tile is real in bytes and egress, but in wall
time it is only the transfer term, and that term shrinks as bandwidth grows.

This measures both terms separately against the real Sentinel-2 archive on AWS,
which is where our corpus was copied from, so the numbers generalise: given a
measured TTFB, the wall-time benefit at any bandwidth follows.
"""
from __future__ import annotations

import argparse
import http.client
import json
import statistics as st
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


def timed_get(conn, host, path, start, length):
    hdrs = {"Range": f"bytes={start}-{start+length-1}",
            "Accept-Encoding": "identity", "Host": host}
    t0 = time.perf_counter()
    conn.request("GET", path, headers=hdrs)
    r = conn.getresponse()
    t1 = time.perf_counter()          # headers in: time to first byte
    body = r.read()
    t2 = time.perf_counter()
    if r.status not in (200, 206):
        raise RuntimeError(f"HTTP {r.status}")
    return t1 - t0, t2 - t1, len(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="data/sources_w6.yaml")
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--out", default="results/gate_s3.json")
    a = ap.parse_args()

    import yaml
    doc = yaml.safe_load((ROOT / a.sources).read_text())
    obj = next(o for o in doc["objects"] if o["key"].endswith("B04.tif"))
    url = urlparse(obj["url"])
    idx = json.loads((ROOT / "results" / "cog_index.json").read_text())
    L = idx[obj["key"]]["levels"][0]
    bi = max(range(len(L["bytecounts"])), key=lambda i: L["bytecounts"][i])
    off, cnt = L["offsets"][bi], L["bytecounts"][bi]

    print(f"real endpoint: {url.netloc}")
    print(f"object: {obj['key']}  ({obj['size']/1e6:.1f} MB)")
    print(f"tile {bi}: {cnt/1024:,.0f} kB compressed at offset {off:,}\n")

    sizes = [s for s in (16384, 65536, 262144, 1048576, cnt) if s <= cnt]
    conn = http.client.HTTPSConnection(url.netloc, timeout=120)
    timed_get(conn, url.netloc, url.path, off, 4096)      # warm the connection

    rows = []
    print(f"{'range':>10} {'TTFB':>12} {'transfer':>12} {'total':>10} "
          f"{'effective':>12}")
    for size in sizes:
        ttfbs, xfers = [], []
        for _ in range(a.reps):
            t, x, n = timed_get(conn, url.netloc, url.path, off, size)
            ttfbs.append(t); xfers.append(x)
        ttfb = st.median(ttfbs); xfer = st.median(xfers)
        bw = (size / xfer / 1e6 * 8) if xfer > 0 else float("inf")
        rows.append({"bytes": size, "ttfb_s": round(ttfb, 4),
                     "transfer_s": round(xfer, 4),
                     "total_s": round(ttfb + xfer, 4),
                     "effective_mbps": round(bw, 1)})
        print(f"{size/1024:>8,.0f}kB {ttfb*1000:>10.1f}ms {xfer*1000:>10.1f}ms "
              f"{(ttfb+xfer)*1000:>8.1f}ms {bw:>10.1f}Mb/s")
    conn.close()

    base_ttfb = st.median([r["ttfb_s"] for r in rows])
    bws = [r["effective_mbps"] for r in rows if r["bytes"] >= 262144]
    eff = st.median(bws) if bws else 0.0
    small, big = rows[0], rows[-1]
    saved_bytes = big["bytes"] - small["bytes"]

    print(f"\n  TTFB is {base_ttfb*1000:.0f} ms and effectively constant; "
          f"transfer scales with size")
    print(f"  effective bandwidth from here: {eff:.0f} Mb/s")
    print(f"  fetching {small['bytes']/1024:,.0f} kB instead of "
          f"{big['bytes']/1024:,.0f} kB saves "
          f"{(big['total_s']-small['total_s'])*1000:.0f} ms of "
          f"{big['total_s']*1000:.0f} ms, i.e. "
          f"{100*(1-small['total_s']/big['total_s']):.0f}% of the request")
    print("\n  the same saving at other bandwidths, TTFB held at "
          f"{base_ttfb*1000:.0f} ms:")
    proj = {}
    for bw in (50, 200, 1000, 5000):
        t_big = base_ttfb + big["bytes"] / (bw * 1e6 / 8)
        t_small = base_ttfb + small["bytes"] / (bw * 1e6 / 8)
        proj[str(bw)] = round(1 - t_small / t_big, 3)
        print(f"    {bw:>5} Mb/s -> saves {100*(1-t_small/t_big):>4.0f}% "
              f"of the request ({t_big*1000:>6.1f}ms -> {t_small*1000:>5.1f}ms)")

    # Project the measured TTFB onto the real workload. At high bandwidth a
    # request costs TTFB and little else, so request count decides; at low
    # bandwidth bytes decide. The two halves of the reader cover opposite
    # regimes, which is why the combined figure is stable across both.
    wl = {}
    try:
        w6 = json.loads((ROOT / "results" / "georange_io_w6.json").read_text())
        cases = {"W6 TIMESERIES": w6}
    except Exception:
        cases = {}
    if cases:
        print("\n  projected wall time on the real archive, TTFB "
              f"{base_ttfb*1000:.0f} ms:")
        for name, r in cases.items():
            print(f"    {name}")
            print(f"      {'bandwidth':>10} {'GDAL':>10} {'georange_io':>12} "
                  f"{'gain':>7}")
            rowsw = []
            for bw in (10, 50, 200, 1000, 5000):
                Bps = bw * 1e6 / 8
                tg = r["gdal"]["requests"] * base_ttfb + r["gdal"]["bytes"] / Bps
                ts = (r["georange-io"]["requests"] * base_ttfb
                      + r["georange-io"]["bytes"] / Bps)
                rowsw.append({"mbps": bw, "gdal_s": round(tg, 1),
                              "georange_io_s": round(ts, 1),
                              "gain": round(tg / ts, 2)})
                print(f"      {bw:>9}M {tg:>9.0f}s {ts:>11.0f}s "
                      f"{tg/ts:>6.2f}x")
            wl[name] = rowsw

    out = {"endpoint": url.netloc, "object": obj["key"], "tile": bi,
           "tile_bytes": cnt, "reps": a.reps, "measurements": rows,
           "median_ttfb_s": round(base_ttfb, 4),
           "effective_mbps": eff,
           "fraction_of_request_saved_by_bandwidth": proj,
           "projected_workload_wall_s": wl}
    (ROOT / a.out).write_text(json.dumps(out, indent=2))
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
