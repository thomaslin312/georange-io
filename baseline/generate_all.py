#!/usr/bin/env python3
"""Generate all five workload specs. Runs inside the bench container."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))
sys.path.insert(0, str(ROOT / "baseline" / "workloads"))

from common import finalize            # noqa: E402
from geo import Index                  # noqa: E402


def main() -> int:
    only = set(a.upper() for a in sys.argv[1:])
    idx = Index()
    print(f"index: {len(list(idx.keys()))} objects\n")

    import w1_windows, w2_scattered, w3_linear, w4_hierarchical, w5_frontier

    gens = [("W1", w1_windows.generate), ("W2", w2_scattered.generate),
            ("W3", w3_linear.generate), ("W4", w4_hierarchical.generate),
            ("W5", w5_frontier.generate)]
    rc = 0
    for name, fn in gens:
        if only and name not in only:
            continue
        print(f"--- {name}")
        t0 = time.time()
        try:
            finalize(fn(idx=idx))
            print(f"  generated in {time.time()-t0:.1f}s\n")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  {name} FAILED: {e}\n", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
