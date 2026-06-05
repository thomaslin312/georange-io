#!/usr/bin/env python3
"""Check that what GDAL fetches does not depend on how slow the network is.

The sweep runs the chunk-size comparison at a single RTT on the grounds that
GDAL's fetching decisions are made from its configuration and the file layout,
not from observed latency. That is an assumption about GDAL, so it is checked
rather than asserted: for every (workload, config) measured at more than one
RTT, the cold-pass byte and request counts must be identical.

Exits non-zero if any pair disagrees, which would invalidate the two-phase
sweep design.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    by: dict[tuple[str, str], dict[float, tuple[int, int]]] = defaultdict(dict)
    for f in sorted((ROOT / "results" / "runs").glob("*.json")):
        d = json.loads(f.read_text())
        cold = next((p for p in d["passes"] if p["pass"] == "cold"), None)
        if not cold or not cold.get("metrics") or cold.get("truncated"):
            continue
        m = cold["metrics"]
        by[(d["workload"], d["config"])][d["latency_ms"]] = (
            m["bytes_fetched"], m["requests"])

    bad = []
    checked = 0
    for (w, c), pts in sorted(by.items()):
        if len(pts) < 2:
            continue
        checked += 1
        vals = set(pts.values())
        rtts = sorted(pts)
        if len(vals) > 1:
            bad.append((w, c, pts))
            print(f"  {w} {c}: NOT invariant across RTT")
            for t in rtts:
                print(f"      {t:6g} ms  {pts[t][0]:>14,} bytes  "
                      f"{pts[t][1]:>7,} requests")
        else:
            b, r = next(iter(vals))
            print(f"  {w} {c}: invariant over {rtts} ms "
                  f"({b:,} bytes, {r:,} requests)")

    out = {"pairs_checked": checked, "violations": len(bad),
           "detail": [{"workload": w, "config": c,
                       "by_rtt": {str(k): v for k, v in p.items()}}
                      for w, c, p in bad]}
    (ROOT / "results" / "rtt_invariance.json").write_text(json.dumps(out, indent=2))
    print(f"\n{checked} (workload, config) pairs measured at multiple RTTs, "
          f"{len(bad)} violations")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
