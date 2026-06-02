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

Requests get two denominators for the same reason:

  min_requests             one request per distinct block, plus one header read
                           per file. Safe, but naive: any reader that merges
                           adjacent blocks beats it, so amplification against
                           it can legitimately fall below 1.

  min_requests_coalesced   the number of maximal runs of required tiles that
                           are *neighbours in the file*, plus one header read
                           per file. Two required tiles are treated as
                           coalescable when no other tile's data lies between
                           them; the COG writer leaves a few bytes of ghost
                           padding at each tile boundary (8 bytes in this
                           corpus), which a merged request pays and which is
                           negligible. This is what a reader achieves fetching
                           exactly the required blocks with maximal merging,
                           and it is the denominator used for the headline
                           request amplification.

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
    needed: dict[str, set[tuple[int, int]]] = {}
    for (key, lvl), bset in touched.items():
        L = idx[key]["levels"][lvl]
        cnts = L["bytecounts"]
        b = 0
        e = 0
        for bi in bset:
            if bi < len(cnts):
                c = cnts[bi]
                b += c
                if c == 0:
                    e += 1
                else:
                    needed.setdefault(key, set()).add((lvl, bi))
        block_bytes += b
        n_blocks += len(bset)
        n_empty += e
        per_level.setdefault(str(lvl), {"blocks": 0, "bytes": 0})
        per_level[str(lvl)]["blocks"] += len(bset)
        per_level[str(lvl)]["bytes"] += b

    # Maximal runs of required tiles that are neighbours in the file. Walking
    # the file's complete tile list, a run continues while consecutive tiles
    # are both required; anything else breaks it.
    n_runs = 0
    pad_bytes = 0
    for key, want in needed.items():
        allt = []
        for L in idx[key]["levels"]:
            lvl = L["level"]
            for bi, (o, c) in enumerate(zip(L["offsets"], L["bytecounts"])):
                if c > 0:
                    allt.append((int(o), int(o) + int(c), lvl, bi))
        allt.sort()
        prev_req = False
        prev_end = 0
        for o, e0, lvl, bi in allt:
            is_req = (lvl, bi) in want
            if is_req:
                if prev_req:
                    pad_bytes += max(0, o - prev_end)
                else:
                    n_runs += 1
            prev_req = is_req
            prev_end = e0

    header_bytes = sum(idx[k]["header_bytes"] for k in files)

    return {
        "n_reads": len(reads),
        "n_files": len(files),
        "n_blocks": n_blocks,
        "n_empty_blocks": n_empty,
        "min_block_bytes": block_bytes,
        "header_bytes": header_bytes,
        "min_total_bytes": block_bytes + header_bytes,
        # One request per distinct block, plus a header read per file. Naive:
        # any reader that merges adjacent blocks beats it.
        "min_requests": n_blocks + len(files),
        # Maximal contiguous runs plus a header read per file: what a reader
        # achieves fetching exactly the required blocks, wasting nothing.
        "min_requests_coalesced": n_runs + len(files),
        "n_contiguous_runs": n_runs,
        # Ghost padding a maximally-merged reader would also pay. Reported for
        # honesty; it is negligible and is not added to min_total_bytes.
        "coalescing_pad_bytes": pad_bytes,
        "per_level": per_level,
        "missing": sorted(missing),
    }
