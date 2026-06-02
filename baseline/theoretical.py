#!/usr/bin/env python3
"""The theoretical minimum: what an ideal reader would have to fetch.

Computed from the COGs' own TileOffsets / TileByteCounts, with no reader in the
loop. For a list of reads we determine the exact set of internal blocks the
windows intersect and sum their *compressed* sizes.

Two denominators are reported, because they answer different questions:

  min_block_bytes    the compressed bytes of the distinct blocks touched.
                     A floor no reader can beat, but not by itself achievable:
                     a reader cannot know where the blocks are.

  min_total_bytes    the above plus one header read per distinct object
                     touched, sized at that object's actual metadata extent
                     (the offset of its first tile). This is the honest
                     denominator, and it is the one used for the headline
                     amplification numbers.

The header term matters enormously for W2, where thousands of point samples
touch few blocks but many files.

Empty blocks (TileByteCounts == 0, i.e. sparse/nodata tiles) contribute zero
bytes but are still counted as blocks a reader must resolve.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_index(path: str | None = None) -> dict:
    p = Path(path) if path else ROOT / "results" / "cog_index.json"
    return json.loads(p.read_text())


def blocks_for_window(rec: dict, level: int, x: int, y: int, w: int, h: int):
    """Yield linear block indices intersected by a window at an IFD level."""
    L = rec["levels"][level]
    bw, bh, nbx, nby = L["blockw"], L["blockh"], L["nbx"], L["nby"]
    x0 = max(0, x); y0 = max(0, y)
    x1 = min(L["width"], x + w); y1 = min(L["height"], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    for by in range(y0 // bh, (y1 - 1) // bh + 1):
        for bx in range(x0 // bw, (x1 - 1) // bw + 1):
            if 0 <= bx < nbx and 0 <= by < nby:
                yield by * nbx + bx


def compute(reads, index: dict | None = None) -> dict:
    """Theoretical minimum for an ordered list of Read records."""
    idx = index if index is not None else load_index()
    touched: dict[tuple[str, int], set[int]] = {}
    files: set[str] = set()
    missing: set[str] = set()

    for r in reads:
        key = r.key if hasattr(r, "key") else r[0]
        lvl = r.level if hasattr(r, "level") else int(r[1])
        x, y, w, h = ((r.x, r.y, r.w, r.h) if hasattr(r, "x")
                      else (int(r[2]), int(r[3]), int(r[4]), int(r[5])))
        rec = idx.get(key)
        if rec is None or not rec.get("levels"):
            missing.add(key)
            continue
        if lvl >= len(rec["levels"]):
            missing.add(f"{key}@L{lvl}")
            continue
        files.add(key)
        s = touched.setdefault((key, lvl), set())
        s.update(blocks_for_window(rec, lvl, x, y, w, h))

    block_bytes = 0
    n_blocks = 0
    n_empty = 0
    per_level = {}
    for (key, lvl), bset in touched.items():
        cnts = idx[key]["levels"][lvl]["bytecounts"]
        b = 0
        e = 0
        for bi in bset:
            if bi < len(cnts):
                c = cnts[bi]
                b += c
                e += (c == 0)
        block_bytes += b
        n_blocks += len(bset)
        n_empty += e
        per_level.setdefault(str(lvl), {"blocks": 0, "bytes": 0})
        per_level[str(lvl)]["blocks"] += len(bset)
        per_level[str(lvl)]["bytes"] += b

    header_bytes = sum(idx[k]["header_bytes"] for k in files)

    return {
        "n_reads": len(reads),
        "n_files": len(files),
        "n_blocks": n_blocks,
        "n_empty_blocks": n_empty,
        "min_block_bytes": block_bytes,
        "header_bytes": header_bytes,
        "min_total_bytes": block_bytes + header_bytes,
        # A reader that fetched every distinct block in one request each, plus
        # one header read per file. This is the request-count denominator.
        "min_requests": n_blocks + len(files),
        "per_level": per_level,
        "missing": sorted(missing),
    }
