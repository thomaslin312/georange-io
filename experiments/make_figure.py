#!/usr/bin/env python3
"""Draw the README figure from the committed live AWS benchmark.

The top panel is a schematic of one tile. The bottom panel is measured, and
every number in it is read from results/audit_bench_aws.json so the figure
cannot drift from the evidence. The schematic's proportions come from the same
file: the no-sidecar bar ends where the measured byte share would put it, and
the sidecar bar is the measured sidecar share wide.

The SVG carries its own background and a prefers-color-scheme block, because
GitHub and PyPI both show it as an image on light and dark pages alike.
"""
from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "results" / "audit_bench_aws.json"
OUT = ROOT / ".github" / "assets" / "how-it-works.svg"

W = 760
PAD = 32
X0 = 262                # track start; the label column sits to its left
X1 = W - PAD            # track end
BAR_H = 20
FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


def text(x, y, s, cls, anchor="start"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" '
            f'text-anchor="{anchor}">{escape(s)}</text>')


def span(x_start, x_end, y, cls, round_left=True):
    """A horizontal mark with a 4px rounded data-end; square at a baseline."""
    r = min(4.0, (x_end - x_start) / 2)
    h = BAR_H
    if round_left:
        d = (f"M{x_start + r:.1f},{y:.1f} H{x_end - r:.1f} "
             f"Q{x_end:.1f},{y:.1f} {x_end:.1f},{y + r:.1f} V{y + h - r:.1f} "
             f"Q{x_end:.1f},{y + h:.1f} {x_end - r:.1f},{y + h:.1f} "
             f"H{x_start + r:.1f} Q{x_start:.1f},{y + h:.1f} "
             f"{x_start:.1f},{y + h - r:.1f} V{y + r:.1f} "
             f"Q{x_start:.1f},{y:.1f} {x_start + r:.1f},{y:.1f} Z")
    else:
        d = (f"M{x_start:.1f},{y:.1f} H{x_end - r:.1f} "
             f"Q{x_end:.1f},{y:.1f} {x_end:.1f},{y + r:.1f} V{y + h - r:.1f} "
             f"Q{x_end:.1f},{y + h:.1f} {x_end - r:.1f},{y + h:.1f} "
             f"H{x_start:.1f} Z")
    return f'<path d="{d}" class="{cls}"/>'


def main() -> int:
    run = json.loads(SRC.read_text())
    if not run.get("values_agree"):
        raise SystemExit("benchmark values did not agree with GDAL; not drawing")
    s = run["summary"]
    gdal = s["GDAL"]["bytes_median"]
    prefix = s["GeoRange IO, 1 worker(s)"]["bytes_median"]
    sidecar = s["GeoRange IO, 8 worker(s) + sidecar"]["bytes_median"]
    n_reads, n_dates, reps = run["n_reads"], run["dates"], run["repetitions"]

    track = X1 - X0
    row_x = X0 + track * prefix / gdal
    restart_x = row_x - track * sidecar / gdal

    out = []
    y = PAD + 8
    out.append(text(PAD, y, "One tile is one compressed stream", "h"))
    out.append(text(PAD, y + 22, "To read one pixel, a reader has to decompress "
                    "from some starting point up to that pixel's row.", "sub"))

    rows_top = y + 64
    rows = [
        ("GDAL", "decompresses the whole tile", X0, X1),
        ("GeoRange IO", "stops at that row", X0, row_x),
        ("GeoRange IO + sidecar", "starts at a saved restart point", restart_x, row_x),
    ]
    step = 50
    for i, (name, desc, a, b) in enumerate(rows):
        t = rows_top + i * step
        out.append(text(PAD, t + 13, name, "name"))
        out.append(text(PAD, t + 30, desc, "desc"))
        out.append(span(X0, X1, t + 6, "track"))
        out.append(span(a, b, t + 6, "fill"))
    marker_top = rows_top - 12
    marker_bottom = rows_top + 2 * step + 6 + BAR_H + 6
    out.append(f'<line x1="{row_x:.1f}" y1="{marker_top:.1f}" x2="{row_x:.1f}" '
               f'y2="{marker_bottom:.1f}" class="marker"/>')
    out.append(text(row_x, marker_top - 6, "the pixel's row", "desc", "middle"))
    legend_y = marker_bottom + 20
    out.append(f'<rect x="{X0}" y="{legend_y - 9:.1f}" width="10" height="10" '
               f'rx="2" class="fill"/>')
    out.append(text(X0 + 16, legend_y, "downloaded and decompressed", "desc"))
    out.append(f'<rect x="{X0 + 214}" y="{legend_y - 9:.1f}" width="10" height="10" '
               f'rx="2" class="track"/>')
    out.append(text(X0 + 230, legend_y, "skipped", "desc"))

    divider = legend_y + 24
    out.append(f'<line x1="{PAD}" y1="{divider:.1f}" x2="{W - PAD}" '
               f'y2="{divider:.1f}" class="rule"/>')

    y = divider + 40
    out.append(text(PAD, y, "Measured on the live AWS Sentinel-2 archive", "h"))
    out.append(text(PAD, y + 22, f"Bytes downloaded for {n_reads} point reads across "
                    f"{n_dates} scenes, median of {reps} runs.", "sub"))
    out.append(text(PAD, y + 40, "Every value was identical to GDAL's.", "sub"))

    bars_top = y + 64
    label_room = 150
    scale = (track - label_room) / gdal
    bars = [("GDAL", gdal, ""),
            ("GeoRange IO", prefix, f"{gdal / prefix:.2f}× less"),
            ("GeoRange IO + sidecar", sidecar, f"{gdal / sidecar:.2f}× less")]
    for i, (name, b, ratio) in enumerate(bars):
        t = bars_top + i * 38
        out.append(text(PAD, t + 15, name, "name"))
        end = X0 + b * scale
        out.append(span(X0, end, t, "fill", round_left=False))
        out.append(text(end + 8, t + 15, f"{b / 1e6:.1f} MB", "value"))
        if ratio:
            out.append(text(end + 8 + 62, t + 15, ratio, "desc"))
    out.append(f'<line x1="{X0}" y1="{bars_top - 6}" x2="{X0}" '
               f'y2="{bars_top + 2 * 38 + BAR_H + 6}" class="axis"/>')

    H = bars_top + 2 * 38 + BAR_H + PAD
    alt = (f"Top: a schematic of one compressed tile. GDAL decompresses all of it; "
           f"GeoRange IO stops at the row it needs; with a sidecar it also starts "
           f"at a saved restart point near that row. Bottom: measured bytes "
           f"downloaded for {n_reads} point reads on AWS. GDAL {gdal / 1e6:.1f} MB, "
           f"GeoRange IO {prefix / 1e6:.1f} MB ({gdal / prefix:.2f}× less), "
           f"GeoRange IO with a sidecar {sidecar / 1e6:.1f} MB "
           f"({gdal / sidecar:.2f}× less). Every value was identical to GDAL's.")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">
<title id="t">How GeoRange IO reads one pixel</title>
<desc id="d">{escape(alt)}</desc>
<style>
  svg {{ --surface:#fcfcfb; --border:rgba(11,11,11,0.10); --ink:#0b0b0b; --ink2:#52514e;
        --muted:#898781; --track:#e1e0d9; --axis:#c3c2b7; --fill:#2a78d6; }}
  @media (prefers-color-scheme: dark) {{
    svg {{ --surface:#1a1a19; --border:rgba(255,255,255,0.10); --ink:#ffffff; --ink2:#c3c2b7;
          --muted:#898781; --track:#383835; --axis:#383835; --fill:#3987e5; }}
  }}
  text {{ font-family:{FONT}; }}
  .bg {{ fill:var(--surface); stroke:var(--border); }}
  .h {{ font-size:16px; font-weight:600; fill:var(--ink); }}
  .sub {{ font-size:13px; fill:var(--ink2); }}
  .name {{ font-size:13px; font-weight:600; fill:var(--ink); }}
  .desc {{ font-size:12px; fill:var(--ink2); }}
  .value {{ font-size:13px; font-weight:600; fill:var(--ink); }}
  .track {{ fill:var(--track); }}
  .fill {{ fill:var(--fill); }}
  .marker {{ stroke:var(--ink); stroke-width:1.5; }}
  .rule {{ stroke:var(--track); stroke-width:1; }}
  .axis {{ stroke:var(--axis); stroke-width:1; }}
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
