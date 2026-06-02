#!/usr/bin/env python3
"""Shared plumbing for the workload generators."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

SPEC_DIR = ROOT / "results" / "specs"

# The region every workload is defined over: ~445 x 445 km, western US.
REGION = {"west": -120.0, "south": 36.0, "east": -115.0, "north": 40.0}

# Fixed seeds, one per workload, persisted so runs are comparable.
SEEDS = {"W1": 20240924, "W2": 20240925, "W3": 20240926,
         "W4": 20240927, "W5": 20240928}

S2_BANDS_10M = ["B02", "B03", "B04", "B08"]


def finalize(spec, verbose: bool = True):
    """Attach the theoretical minimum and persist the spec."""
    from theoretical import compute
    spec.theoretical = compute(spec.reads)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    out = SPEC_DIR / f"{spec.name.lower()}.json"
    spec.save(out)
    if verbose:
        t = spec.theoretical
        print(f"{spec.name}: {len(spec.reads)} reads over {t['n_files']} files")
        print(f"  distinct blocks     {t['n_blocks']:,} "
              f"({t['n_empty_blocks']:,} empty/sparse)")
        print(f"  min block bytes     {t['min_block_bytes']/1e6:,.2f} MB")
        print(f"  header bytes        {t['header_bytes']/1e3:,.1f} kB")
        print(f"  MIN TOTAL           {t['min_total_bytes']/1e6:,.2f} MB "
              f"in {t['min_requests']:,} requests")
        if t["missing"]:
            print(f"  !! missing from index: {t['missing'][:5]}")
        print(f"  -> {out}")
    return spec
