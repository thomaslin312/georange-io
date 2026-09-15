#!/usr/bin/env python3
"""Draw the README figure: which pixels a query asks for, and what each reader
has to download to answer it.

Every row is a real query shape from the audit, and everything drawn comes from
results/audit_shapes_sidecar.json: the pixels and windows requested inside each
tile, the restart row a sidecar read starts from, and the measured bytes. The
only simplification is resolution: a 1,024-row tile is drawn as a 12x12 grid of
cells (6x6 when four tiles share a panel), so one cell stands for many pixels.

Shading follows SparseReader._plan_fetch. GDAL downloads every touched tile in
full. GeoRange IO downloads from the top of the tile to the deepest requested
row plus its 3% margin. With a sidecar it starts at the recorded restart row
instead of the top.

The SVG carries its own background and a prefers-color-scheme block, because
GitHub and PyPI both show it as an image on light and dark pages alike.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "results" / "audit_shapes_sidecar.json"
OUT = ROOT / ".github" / "assets" / "how-it-works.svg"

MARGIN = 0.03                 # SparseReader(margin=0.03), as in the audit
W = 760
PAD = 32
LABEL_W = 196
COL_X = [PAD + LABEL_W + i * 166 for i in range(3)]
CELL, GAP, TILE_GAP = 6, 1, 3
FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

# (audit shape, row title, row subtitle, how many tiles to draw)
ROWS = [
    ("point at row 5 of a block", "One pixel near the top", "row 5 of 1,024", 1),
    ("point at row 1010 of a block", "One pixel near the bottom", "row 1,010 of 1,024", 1),
    ("10 points, scattered", "Ten scattered pixels", "{tiles} tiles, 4 shown", 4),
    ("one 512x512 window", "A 512 × 512 window", "across {tiles} tiles", 4),
    ("5 points x 12 dates", "5 pixels over 12 dates", "{tiles} tiles, 1 shown", 1),
    ("whole 1024x1024 block", "A whole tile", "1,024 × 1,024 pixels", 1),
]


def text(x, y, s, cls, anchor="start"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" '
            f'text-anchor="{anchor}">{escape(s)}</text>')


def size(n):
    return f"{n / 1e6:.2f} MB" if n >= 1e6 else f"{n / 1e3:.1f} kB"


def gain(base, n):
    g = base / n
    return "no saving" if g < 1.05 else f"{g:.1f}× less"


def tile(x, y, n, t, mode, outline=True):
    """One tile as an n x n grid. mode: 'gdal' | 'plain' | 'sidecar'."""
    bw, bh = t["block_size"]
    cell = lambda v, full: min(n - 1, v * n // full)
    deepest = max(r[3] - 1 for r in t["requested"])
    last = min(n - 1, math.ceil(((deepest + 1) / bh + MARGIN) * n) - 1)
    first = cell(t["restart_row"], bh) if mode == "sidecar" else 0
    points = {(cell(r[1], bh), cell(r[0], bw)) for r in t["requested"]
              if r[2] - r[0] == 1 and r[3] - r[1] == 1}
    out = []
    step = CELL + GAP
    for row in range(n):
        fetched = mode == "gdal" or first <= row <= last
        for col in range(n):
            cls = "px" if (row, col) in points else "on" if fetched else "off"
            out.append(f'<rect x="{x + col * step}" y="{y + row * step}" '
                       f'width="{CELL}" height="{CELL}" rx="1" class="{cls}"/>')
    for r in t["requested"] if outline else []:
        if r[2] - r[0] > 1 or r[3] - r[1] > 1:
            c0, r0 = cell(r[0], bw), cell(r[1], bh)
            c1, r1 = cell(r[2] - 1, bw), cell(r[3] - 1, bh)
            out.append(f'<rect x="{x + c0 * step - 1.5}" y="{y + r0 * step - 1.5}" '
                       f'width="{(c1 - c0 + 1) * step + 2}" '
                       f'height="{(r1 - r0 + 1) * step + 2}" rx="2" class="win"/>')
    return out


def panel(x, y, tiles, mode, stacked=False):
    out = []
    if len(tiles) == 1:
        n = 12
        if stacked:
            span = n * (CELL + GAP) - GAP
            for d in (8, 4):
                out.append(f'<rect x="{x + d}" y="{y - d}" width="{span}" '
                           f'height="{span}" rx="2" class="stack"/>')
        out += tile(x, y, n, tiles[0], mode)
        return out
    n = 6
    span = n * (CELL + GAP) - GAP
    nbx = tiles[0]["nbx"]
    pos = [(t["block"] % nbx, t["block"] // nbx) for t in tiles]
    adjacent = (max(p[0] for p in pos) - min(p[0] for p in pos) <= 1 and
                max(p[1] for p in pos) - min(p[1] for p in pos) <= 1)
    step = CELL + GAP
    box = None
    for i, t in enumerate(tiles):
        if adjacent:            # a window: keep the tiles where they really are
            gx = pos[i][0] - min(p[0] for p in pos)
            gy = pos[i][1] - min(p[1] for p in pos)
        else:                   # scattered: just lay the shown tiles out 2x2
            gx, gy = i % 2, i // 2
        tx, ty = x + gx * (span + TILE_GAP), y + gy * (span + TILE_GAP)
        out += tile(tx, ty, n, t, mode, outline=not adjacent)
        bw, bh = t["block_size"]
        for r in t["requested"]:
            if adjacent and (r[2] - r[0] > 1 or r[3] - r[1] > 1):
                x0 = tx + min(n - 1, r[0] * n // bw) * step
                y0 = ty + min(n - 1, r[1] * n // bh) * step
                x1 = tx + min(n - 1, (r[2] - 1) * n // bw) * step + CELL
                y1 = ty + min(n - 1, (r[3] - 1) * n // bh) * step + CELL
                box = (x0, y0, x1, y1) if box is None else (
                    min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1))
    if box:
        out.append(f'<rect x="{box[0] - 2}" y="{box[1] - 2}" width="{box[2] - box[0] + 4}" '
                   f'height="{box[3] - box[1] + 4}" rx="2" class="win"/>')
    return out


def main() -> int:
    doc = json.loads(SRC.read_text())
    shapes = {r["shape"]: r for r in doc["shapes"]}
    if not all(r["sidecar_identical_to_plain"] for r in doc["shapes"]):
        raise SystemExit("sidecar reads disagreed with plain reads; not drawing")
    pct = 100 * doc["sidecars"]["index_bytes"] / doc["sidecars"]["tile_bytes"]

    out = []
    y = PAD + 10
    out.append(text(PAD, y, "Which pixels you ask for decides what gets downloaded", "h"))
    for i, line in enumerate([
        "A COG is split into tiles, and each tile is one compressed stream that can only be",
        "decoded from the top. GDAL downloads a whole tile to read any pixel in it. GeoRange IO",
        "stops at the deepest row it needs, and a sidecar lets it start just above that row.",
    ]):
        out.append(text(PAD, y + 24 + i * 19, line, "sub"))

    ly = y + 100
    lx = PAD
    for cls, label, gap in [("px", "pixel asked for", 128), ("win", "window asked for", 140),
                            ("on", "downloaded", 102), ("off", "not downloaded", 0)]:
        if cls == "win":
            out.append(f'<rect x="{lx + 0.5}" y="{ly - 9.5}" width="10" height="10" '
                       f'rx="2" class="win"/>')
        else:
            out.append(f'<rect x="{lx}" y="{ly - 10}" width="11" height="11" '
                       f'rx="2" class="{cls}"/>')
        out.append(text(lx + 18, ly, label, "desc"))
        lx += gap

    hy = ly + 42
    for x, label in zip(COL_X, ["GDAL", "GeoRange IO", "GeoRange IO + sidecar"]):
        out.append(text(x, hy, label, "name"))
    out.append(f'<line x1="{PAD}" y1="{hy + 12}" x2="{W - PAD}" y2="{hy + 12}" class="rule"/>')

    grid = 12 * (CELL + GAP) - GAP
    row_h = grid + 30
    alt = []
    top = hy + 34
    for i, (key, title, sub, shown) in enumerate(ROWS):
        r = shapes[key]
        tiles = r["tiles"][:shown]
        shared = [t for t in r["tiles"] if len(t["requested"]) > 1
                  and all(q[2] - q[0] == 1 for q in t["requested"])]
        if shown > 1 and shared and shared[0] not in tiles:
            tiles = tiles[:shown - 1] + shared[:1]
        ry = top + i * row_h
        out.append(text(PAD, ry + 16, title, "name"))
        out.append(text(PAD, ry + 34, sub.format(tiles=len(r["tiles"])), "desc"))
        cells = [("gdal", r["gdal_bytes"]), ("plain", r["gr_bytes"]),
                 ("sidecar", r["gr_sidecar_bytes"])]
        for x, (mode, nbytes) in zip(COL_X, cells):
            out += panel(x, ry + 4, tiles, mode, stacked=key.endswith("dates"))
            tx = x + grid + 14
            out.append(text(tx, ry + 16, size(nbytes), "value"))
            out.append(text(tx, ry + 34, "whole tile" if mode == "gdal"
                            else gain(r["gdal_bytes"], nbytes), "desc"))
        alt.append(f"{title}: GDAL {size(r['gdal_bytes'])}, GeoRange IO "
                   f"{size(r['gr_bytes'])} ({gain(r['gdal_bytes'], r['gr_bytes'])}), "
                   f"with a sidecar {size(r['gr_sidecar_bytes'])} "
                   f"({gain(r['gdal_bytes'], r['gr_sidecar_bytes'])}).")
        if i < len(ROWS) - 1:
            sep = ry + row_h - 13
            out.append(f'<line x1="{PAD}" y1="{sep}" x2="{W - PAD}" y2="{sep}" class="rule"/>')

    fy = top + len(ROWS) * row_h + 4
    out.append(f'<line x1="{PAD}" y1="{fy - 13}" x2="{W - PAD}" y2="{fy - 13}" class="rule"/>')
    out.append(text(PAD, fy + 8, "Bytes measured on Sentinel-2 scenes on AWS. Every value was "
                    "identical to GDAL's.", "desc"))
    out.append(text(PAD, fy + 26, f"A sidecar is built once per file and is about {pct:.0f}% "
                    "of the size of the tiles it covers. Each tile is drawn as 12 × 12 cells.",
                    "desc"))
    H = fy + 26 + PAD

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">
<title id="t">Which pixels you ask for decides what gets downloaded</title>
<desc id="d">{escape(' '.join(alt))}</desc>
<style>
  svg {{ --surface:#fcfcfb; --border:rgba(11,11,11,0.10); --ink:#0b0b0b; --ink2:#52514e;
        --off:#e8e7e1; --rule:#e1e0d9; --on:#6da7ec; --px:#eb6834; --stack:#c3c2b7; }}
  @media (prefers-color-scheme: dark) {{
    svg {{ --surface:#1a1a19; --border:rgba(255,255,255,0.10); --ink:#ffffff; --ink2:#c3c2b7;
          --off:#383835; --rule:#2c2c2a; --on:#2a78d6; --px:#d95926; --stack:#52514e; }}
  }}
  text {{ font-family:{FONT}; }}
  .bg {{ fill:var(--surface); stroke:var(--border); }}
  .h {{ font-size:17px; font-weight:600; fill:var(--ink); }}
  .sub {{ font-size:13px; fill:var(--ink2); }}
  .name {{ font-size:13px; font-weight:600; fill:var(--ink); }}
  .desc {{ font-size:12px; fill:var(--ink2); }}
  .value {{ font-size:13px; font-weight:600; fill:var(--ink); }}
  .on {{ fill:var(--on); }}
  .off {{ fill:var(--off); }}
  .px {{ fill:var(--px); }}
  .win {{ fill:none; stroke:var(--px); stroke-width:2; }}
  .stack {{ fill:var(--surface); stroke:var(--stack); stroke-width:1; }}
  .rule {{ stroke:var(--rule); stroke-width:1; }}
</style>
<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" class="bg"/>
{chr(10).join(out)}
</svg>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg)
    print(f"wrote {OUT.relative_to(ROOT)}  ({W}x{H})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
