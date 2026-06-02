#!/usr/bin/env python3
"""Resolve the Phase-0 corpus to a pinned list of source objects.

Writes data/sources.yaml. Re-running this against a live STAC endpoint is how
the corpus definition is regenerated; the committed sources.yaml is the pinned
artifact that fetch.py actually consumes, so staging never depends on STAC
being reachable.

Region: lon [-120, -115], lat [36, 40]  (~445 x 445 km, western US)
"""
from __future__ import annotations
import argparse, json, sys, time, urllib.request
from pathlib import Path

REGION = {"west": -120.0, "south": 36.0, "east": -115.0, "north": 40.0}

# --- Copernicus DEM GLO-30 -------------------------------------------------
DEM_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
DEM_LATS = [36, 37, 38, 39]          # 1-degree tiles, SW corner
DEM_LONS = [120, 119, 118, 117, 116]  # west longitudes

# --- ESA WorldCover v200 (2021) -------------------------------------------
WC_BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
WC_TILES = ["N36W120", "N36W117", "N39W120", "N39W117"]  # 3-degree grid

# --- Sentinel-2 L2A --------------------------------------------------------
S2_STAC = "https://earth-search.aws.element84.com/v1/search"
S2_DATE = "2024-09-24"
S2_COLS = ["L", "M", "N", "P"]
S2_ROWS = ["A", "B", "C"]
S2_ZONE = "11S"
S2_ASSETS = ["blue", "green", "red", "nir", "swir16", "scl"]  # B02 B03 B04 B08 B11 SCL


def head(url: str, retries: int = 4) -> tuple[int, str]:
    last = None
    for i in range(retries):
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, method="HEAD"), timeout=60)
            return int(r.headers["Content-Length"]), r.headers.get("ETag", "").strip('"')
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"HEAD failed for {url}: {last}")


def stac_search() -> list[dict]:
    body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": [REGION["west"] - 0.5, REGION["south"] - 1.0,
                 REGION["east"] + 0.5, REGION["north"] + 0.5],
        "datetime": f"{S2_DATE}T00:00:00Z/{S2_DATE}T23:59:59Z",
        "limit": 100,
    }
    last = None
    for i in range(6):
        try:
            req = urllib.request.Request(
                S2_STAC, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120))["features"]
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  STAC retry {i}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"STAC unreachable: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "sources.yaml"))
    ap.add_argument("--no-head", action="store_true",
                    help="skip HEAD requests (sizes recorded as null)")
    args = ap.parse_args()

    entries: list[dict] = []

    print("Resolving Copernicus DEM GLO-30 ...", file=sys.stderr)
    for lat in DEM_LATS:
        for lon in DEM_LONS:
            stem = f"Copernicus_DSM_COG_10_N{lat:02d}_00_W{lon:03d}_00_DEM"
            url = f"{DEM_BASE}/{stem}/{stem}.tif"
            size, etag = (None, "") if args.no_head else head(url)
            entries.append({
                "key": f"dem/{stem}.tif", "url": url, "size": size, "etag": etag,
                "dataset": "cop_dem_glo30", "role": "elevation",
                "tile": f"N{lat:02d}W{lon:03d}",
                "bbox": [-lon, lat, -lon + 1, lat + 1],
            })

    print("Resolving ESA WorldCover v200 ...", file=sys.stderr)
    for t in WC_TILES:
        stem = f"ESA_WorldCover_10m_2021_v200_{t}_Map"
        url = f"{WC_BASE}/{stem}.tif"
        size, etag = (None, "") if args.no_head else head(url)
        lat = int(t[1:3]); lon = int(t[4:7])
        entries.append({
            "key": f"worldcover/{stem}.tif", "url": url, "size": size, "etag": etag,
            "dataset": "esa_worldcover_v200", "role": "landcover", "tile": t,
            "bbox": [-lon, lat, -lon + 3, lat + 3],
        })

    print("Resolving Sentinel-2 L2A via Element84 STAC ...", file=sys.stderr)
    feats = stac_search()
    want = {f"{S2_ZONE}{c}{r}" for c in S2_COLS for r in S2_ROWS}
    by_grid: dict[str, dict] = {}
    for f in feats:
        g = (f["properties"].get("grid:code") or "").replace("MGRS-", "")
        if g in want and g not in by_grid:
            by_grid[g] = f
    missing = want - set(by_grid)
    if missing:
        raise RuntimeError(f"STAC did not return required MGRS tiles: {sorted(missing)}")

    for g in sorted(by_grid):
        f = by_grid[g]
        p = f["properties"]
        for a in S2_ASSETS:
            href = f["assets"][a]["href"]
            band = href.rsplit("/", 1)[-1].replace(".tif", "")
            size, etag = (None, "") if args.no_head else head(href)
            entries.append({
                "key": f"s2/{g}/{band}.tif", "url": href, "size": size, "etag": etag,
                "dataset": "sentinel2_l2a", "role": f"s2_{a}", "tile": g,
                "band": band, "asset": a,
                "scene_id": f["id"], "datetime": p["datetime"],
                "cloud_cover": p.get("eo:cloud_cover"),
                "epsg": p.get("proj:epsg"), "bbox": f["bbox"],
            })

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "region": REGION,
        "bucket": "georange-io",
        "provenance": {
            "cop_dem_glo30": {
                "base": DEM_BASE,
                "note": "AWS Open Data, Copernicus DEM GLO-30, COG, EPSG:4326, 1 arcsec",
                "license": "Copernicus DEM open licence (ESA/Airbus)",
            },
            "esa_worldcover_v200": {
                "base": WC_BASE,
                "note": "ESA WorldCover 10m v200 (2021), COG, EPSG:4326, 1/3 arcsec",
                "license": "CC BY 4.0",
            },
            "sentinel2_l2a": {
                "stac": S2_STAC, "date": S2_DATE,
                "mgrs": sorted(want), "assets": S2_ASSETS,
                "note": "Element84 earth-search v1, sentinel-cogs bucket, EPSG:32611",
                "license": "Copernicus Sentinel data, free and open",
            },
        },
        "objects": entries,
    }

    out = Path(args.out)
    try:
        import yaml
        out.write_text(yaml.safe_dump(doc, sort_keys=False, width=200))
    except ImportError:
        out.with_suffix(".json").write_text(json.dumps(doc, indent=2))
        out = out.with_suffix(".json")

    n = len(entries)
    tot = sum(e["size"] or 0 for e in entries)
    print(f"wrote {out}: {n} objects, {tot/1e9:.2f} GB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
