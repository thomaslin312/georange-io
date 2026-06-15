#!/usr/bin/env python3
"""Resolve a multi-date Sentinel-2 stack over one MGRS tile, for W6 TIMESERIES.

The Phase-0 corpus is single-date by design, so it cannot exercise the access
pattern where the granularity tax is largest: the same tiny window read across
many acquisitions. This stages that pattern properly, one MGRS tile and many
dates, so the reads land on the same ground in the same grid every time.

Red and the scene classification layer only. Adding the other 10 m bands would
quadruple the download for no change in the access structure, since every band
is read the same way.
"""
from __future__ import annotations

import argparse, json, sys, time, urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
STAC = "https://earth-search.aws.element84.com/v1/search"
TILE = "11SLA"
BBOX = [-118.6, 36.2, -118.2, 36.6]      # inside the tile
YEAR = "2024"
ASSETS = ["red", "scl"]                   # B04 at 10 m, SCL at 20 m


def search(cloud_lt: float) -> dict:
    body = {"collections": ["sentinel-2-l2a"], "bbox": BBOX,
            "datetime": f"{YEAR}-01-01T00:00:00Z/{YEAR}-12-31T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": cloud_lt}}, "limit": 300}
    last = None
    for i in range(6):
        try:
            req = urllib.request.Request(
                STAC, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception as e:      # noqa: BLE001
            last = e
            print(f"  STAC retry {i}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"STAC unreachable: {last}")


def head(url: str, retries: int = 4):
    last = None
    for i in range(retries):
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, method="HEAD"), timeout=60)
            return int(r.headers["Content-Length"])
        except Exception as e:      # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"HEAD failed {url}: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", type=int, default=24)
    ap.add_argument("--cloud-lt", type=float, default=10.0)
    ap.add_argument("--out", default=str(HERE / "sources_w6.yaml"))
    a = ap.parse_args()

    r = search(a.cloud_lt)
    by = defaultdict(list)
    for f in r["features"]:
        if (f["properties"].get("grid:code") or "").endswith(TILE):
            by[f["properties"]["datetime"][:10]].append(f)
    dates = sorted(by)
    if len(dates) < a.dates:
        raise RuntimeError(f"only {len(dates)} dates available, wanted {a.dates}")
    # evenly spaced across the year, so the stack covers a full phenological cycle
    step = len(dates) / a.dates
    picked = [dates[int(i * step)] for i in range(a.dates)]

    entries = []
    for d in picked:
        f = by[d][0]
        for asset in ASSETS:
            href = f["assets"][asset]["href"]
            band = href.rsplit("/", 1)[-1].replace(".tif", "")
            entries.append({
                "key": f"s2ts/{TILE}/{d}/{band}.tif", "url": href,
                "size": head(href), "etag": "",
                "dataset": "sentinel2_l2a_ts", "role": f"s2_{asset}",
                "tile": TILE, "band": band, "asset": asset,
                "scene_id": f["id"], "datetime": f["properties"]["datetime"],
                "cloud_cover": f["properties"].get("eo:cloud_cover"),
                "epsg": f["properties"].get("proj:epsg"), "bbox": f["bbox"],
            })
        print(f"  {d}  cloud {f['properties'].get('eo:cloud_cover'):5.1f}%  {f['id']}",
              file=sys.stderr)

    doc = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "bucket": "georange-io",
           "provenance": {"sentinel2_l2a_ts": {
               "stac": STAC, "tile": TILE, "year": YEAR,
               "cloud_cover_lt": a.cloud_lt, "assets": ASSETS,
               "n_dates": len(picked), "dates": picked,
               "note": "single MGRS tile, many dates: the time-series access pattern",
               "license": "Copernicus Sentinel data, free and open"}},
           "objects": entries}

    import yaml
    Path(a.out).write_text(yaml.safe_dump(doc, sort_keys=False, width=200))
    tot = sum(e["size"] for e in entries)
    print(f"\nwrote {a.out}: {len(entries)} objects over {len(picked)} dates, "
          f"{tot/1e9:.2f} GB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
