#!/usr/bin/env python3
"""Attribute every fetched byte to a cause.

Amplification says how much was wasted. This says why. For each request in a
proxy log the byte range is mapped back onto the object's real tile layout,
and the bytes are split into:

  header        below the first tile offset: TIFF metadata a reader must read
  needed        inside a block the workload genuinely requires, first fetch
  redundant     inside a required block that had already been fetched
  unneeded      inside a block the workload never asked for
  padding       between blocks: chunk alignment and range-merge slack

"needed" is bounded below by the theoretical minimum. Everything else is the
headroom, and which bucket it lands in decides what an engine would have to do
differently: redundant means caching or scheduling, padding means chunk sizing,
unneeded means coalescing too aggressively, and a large request count against
small "needed" means round trips.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def required_blocks(reads, index) -> dict:
    from theoretical import blocks_for_window
    out: dict[tuple[str, int], set] = {}
    for r in reads:
        rec = index.get(r.key)
        if rec is None or r.level >= len(rec.get("levels", [])):
            continue
        out.setdefault((r.key, r.level), set()).update(
            blocks_for_window(rec, r.level, r.x, r.y, r.w, r.h))
    return out


def build_layout(rec: dict) -> tuple[list, list]:
    """Flat sorted list of (start, end, level, block) for every non-empty tile."""
    ivals = []
    for L in rec["levels"]:
        lvl = L["level"]
        for bi, (o, c) in enumerate(zip(L["offsets"], L["bytecounts"])):
            if c > 0:
                ivals.append((int(o), int(o) + int(c), lvl, bi))
    ivals.sort()
    return ivals, [v[0] for v in ivals]


def parse_range(h: str | None, size: int) -> tuple[int, int] | None:
    if not h or not h.startswith("bytes="):
        return None
    part = h[6:].split(",")[0].strip()
    if part.startswith("-"):
        n = int(part[1:])
        return max(0, size - n), size - 1
    lo, _, hi = part.partition("-")
    return int(lo), (int(hi) if hi else size - 1)


def diagnose(log: Path, spec, index) -> dict:
    req = required_blocks(spec.reads, index)
    layouts: dict[str, tuple] = {}
    seen: set[tuple[str, int, int]] = set()

    cat = {"header": 0, "needed": 0, "redundant": 0, "unneeded": 0,
           "padding": 0, "list_and_other": 0}
    n_req = 0
    n_bytes = 0
    block_fetches: dict[tuple[str, int, int], int] = {}

    opener = gzip.open if log.suffix == ".gz" else open
    with opener(log, "rt") as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("record") != "req":
                continue
            n_req += 1
            nb = d.get("resp_body_bytes", 0)
            n_bytes += nb
            key = d.get("key")
            if not key or key not in index:
                cat["list_and_other"] += nb
                continue
            rec = index[key]
            if key not in layouts:
                layouts[key] = build_layout(rec)
            ivals, starts = layouts[key]

            rng = parse_range(d.get("range"), rec["size"])
            if rng is None:
                lo, hi = 0, nb - 1
            else:
                lo, hi = rng
                hi = min(hi, lo + nb - 1)
            if hi < lo:
                continue

            hdr = rec["header_bytes"]
            covered = 0
            if lo < hdr:
                h = min(hi, hdr - 1) - lo + 1
                cat["header"] += h
                covered += h

            i = bisect.bisect_right(starts, hi) - 1
            j = i
            while j >= 0 and ivals[j][1] > lo:
                s, e, lvl, bi = ivals[j]
                ov = min(hi + 1, e) - max(lo, s)
                if ov > 0:
                    tag = (key, lvl, bi)
                    if bi in req.get((key, lvl), ()):
                        if tag in seen:
                            cat["redundant"] += ov
                        else:
                            cat["needed"] += ov
                    else:
                        cat["unneeded"] += ov
                    seen.add(tag)
                    block_fetches[tag] = block_fetches.get(tag, 0) + 1
                    covered += ov
                j -= 1
                if j >= 0 and ivals[j][1] <= lo:
                    break
            span = hi - lo + 1
            cat["padding"] += max(0, span - covered)

    refetched = {k: v for k, v in block_fetches.items() if v > 1}
    tot = sum(cat.values()) or 1
    return {
        "requests": n_req, "bytes": n_bytes,
        "attribution_bytes": cat,
        "attribution_pct": {k: round(100.0 * v / tot, 2) for k, v in cat.items()},
        "distinct_blocks_fetched": len(block_fetches),
        "blocks_fetched_more_than_once": len(refetched),
        "max_fetches_of_one_block": max(block_fetches.values(), default=0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="results/runs")
    ap.add_argument("--raw", default="results/raw")
    ap.add_argument("--specs", default="results/specs")
    ap.add_argument("--out", default="results/diagnosis.json")
    args = ap.parse_args()

    index = json.loads((ROOT / "results" / "cog_index.json").read_text())
    from spec import WorkloadSpec

    specs = {}
    out = {}
    for rf in sorted((ROOT / args.runs).glob("*.json")):
        run = json.loads(rf.read_text())
        w = run["workload"].lower()
        if w not in specs:
            specs[w] = WorkloadSpec.load(ROOT / args.specs / f"{w}.json")
        for phase in ("cold", "warm"):
            for suf in (".jsonl.gz", ".jsonl"):
                lg = ROOT / args.raw / f"{rf.stem}.{phase}{suf}"
                if lg.exists():
                    out[f"{rf.stem}.{phase}"] = diagnose(lg, specs[w], index)
                    break
        print(f"{rf.stem}: done", flush=True)

    (ROOT / args.out).write_text(json.dumps(out, indent=2))
    print(f"wrote {args.out}: {len(out)} logs analysed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
