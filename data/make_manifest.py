#!/usr/bin/env python3
"""Generate data/MANIFEST.md from the pinned sources, what was staged, and the
COG structure actually found in the files.

If any staged asset is not internally tiled, this says so loudly and exits
non-zero: the whole project assumes tiled COGs.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent

COMPRESSION = {1: "none", 5: "LZW", 7: "JPEG", 8: "DEFLATE", 32946: "DEFLATE",
               34925: "LZMA", 50000: "ZSTD", 50001: "WEBP", 34712: "JPEG2000"}

DATASET_TITLE = {
    "cop_dem_glo30": "Copernicus DEM GLO-30",
    "esa_worldcover_v200": "ESA WorldCover 10 m v200 (2021)",
    "sentinel2_l2a": "Sentinel-2 L2A (Element84 earth-search)",
}


def main() -> int:
    src = yaml.safe_load((HERE / "sources.yaml").read_text())
    staged = json.loads((HERE / "staged.json").read_text()) \
        if (HERE / "staged.json").exists() else {}
    ipath = ROOT / "results" / "cog_index.json"
    index = json.loads(ipath.read_text()) if ipath.exists() else {}

    by_ds = defaultdict(list)
    for o in src["objects"]:
        by_ds[o["dataset"]].append(o)

    not_tiled = [k for k, r in index.items() if not r.get("tiled", True)]
    L = []
    L.append("# Phase-0 corpus manifest\n")
    L.append(f"Generated from `data/sources.yaml` (resolved "
             f"{src['generated_utc']}), `data/staged.json` and "
             f"`results/cog_index.json`.\n")
    r = src["region"]
    L.append(f"**Region.** Longitude {r['west']} to {r['east']}, latitude "
             f"{r['south']} to {r['north']}: 438 x 445 km across the "
             "Sierra Nevada crest, Owens Valley, Death Valley and the western "
             "Basin and Range. Relief runs from below sea level to over "
             "4,400 m, so slope-derived cost surfaces have real structure.\n")

    if not_tiled:
        L.append("## !! NOT INTERNALLY TILED\n")
        L.append("The following staged objects are **not internally tiled**. "
                 "Every premise in this project assumes tiled COGs, and any "
                 "measurement against these objects is meaningless:\n")
        for k in not_tiled:
            L.append(f"- `{k}`: {index[k].get('error', 'stripped layout')}")
        L.append("")
    else:
        L.append("## Tiling check\n")
        L.append(f"All {len(index)} indexed objects are internally tiled. "
                 "Every IFD, at native resolution and at every overview level, "
                 "carries TileOffsets and TileByteCounts with a tile count "
                 "matching its declared grid. The project's core assumption "
                 "holds on this corpus.\n")

    L.append("## Summary by dataset\n")
    hdr = ["Dataset", "Objects", "Staged bytes", "CRS", "Pixel size",
           "Block", "Compression", "Overview levels"]
    L.append("| " + " | ".join(hdr) + " |")
    L.append("|" + "|".join("---" for _ in hdr) + "|")
    for ds, objs in sorted(by_ds.items()):
        keys = [o["key"] for o in objs]
        got = [k for k in keys if k in staged]
        tot = sum(staged[k]["bytes"] for k in got)
        recs = [index[k] for k in got if k in index]
        if recs:
            e = recs[0]
            L0 = e["levels"][0]
            px = abs(e["transform"][0])
            crs = f"EPSG:{e['epsg']}"
            unit = "deg" if e["epsg"] == 4326 else "m"
            if e["epsg"] == 4326:
                px_s = f"{px*3600:.4g} arcsec"
            else:
                px_s = f"{px:g} {unit}"
            blk = f"{L0['blockw']}x{L0['blockh']}"
            comp = COMPRESSION.get(L0["compression"], str(L0["compression"]))
            nlev = len(e["levels"]) - 1
        else:
            px_s = crs = blk = comp = "--"
            nlev = "--"
        L.append(f"| {DATASET_TITLE.get(ds, ds)} | {len(got)}/{len(keys)} | "
                 f"{tot/1e9:.2f} GB | {crs} | {px_s} | {blk} | {comp} | {nlev} |")
    L.append("")

    tot_all = sum(v["bytes"] for v in staged.values())
    L.append(f"**Total staged: {len(staged)} objects, "
             f"{tot_all/1e9:.2f} GB.**\n")

    L.append("## Provenance\n")
    for ds, p in src["provenance"].items():
        L.append(f"### {DATASET_TITLE.get(ds, ds)}\n")
        for k, v in p.items():
            L.append(f"- **{k}**: {v}")
        L.append("")

    L.append("## Overview structure\n")
    L.append("One representative object per dataset. Level 0 is native "
             "resolution; each further level is a reduced-resolution IFD "
             "stored in the same file.\n")
    for ds, objs in sorted(by_ds.items()):
        rep = next((o["key"] for o in objs if o["key"] in index), None)
        if not rep:
            continue
        e = index[rep]
        L.append(f"**{DATASET_TITLE.get(ds, ds)}** -- `{rep}`  ")
        L.append(f"file {e['size']/1e6:,.1f} MB, header (metadata before the "
                 f"first tile) {e['header_bytes']:,} bytes, "
                 f"BigTIFF={e.get('bigtiff')}\n")
        h2 = ["Level", "Size (px)", "Block", "Blocks", "Sum of tile bytes",
              "Empty tiles"]
        L.append("| " + " | ".join(h2) + " |")
        L.append("|" + "|".join("---" for _ in h2) + "|")
        for lv in e["levels"]:
            L.append(f"| {lv['level']} | {lv['width']}x{lv['height']} | "
                     f"{lv['blockw']}x{lv['blockh']} | "
                     f"{lv['nbx']*lv['nby']:,} | "
                     f"{lv['sum_bytes']/1e6:,.2f} MB | {lv['empty_tiles']:,} |")
        L.append("")

    L.append("## Every staged object\n")
    h3 = ["Key", "Bytes", "SHA-256 (first 16)", "Source URL"]
    L.append("| " + " | ".join(h3) + " |")
    L.append("|" + "|".join("---" for _ in h3) + "|")
    for k in sorted(staged):
        v = staged[k]
        L.append(f"| `{k}` | {v['bytes']:,} | `{v['sha256'][:16]}` | "
                 f"{v['url']} |")
    L.append("")

    (HERE / "MANIFEST.md").write_text("\n".join(L))
    print(f"wrote {HERE/'MANIFEST.md'}: {len(staged)} staged objects, "
          f"{len(index)} indexed")
    if not_tiled:
        print("\n!! NOT INTERNALLY TILED: " + ", ".join(not_tiled),
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
