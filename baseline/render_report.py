#!/usr/bin/env python3
"""Inject generated tables into REPORT.md between markers.

Prose in REPORT.md is written by hand; every table and number inside a
<!-- BEGIN:name --> / <!-- END:name --> pair is regenerated from results/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    rp = ROOT / "REPORT.md"
    tp = ROOT / "results" / "tables.json"
    if not rp.exists():
        print("REPORT.md not present; nothing to render")
        return 0
    if not tp.exists():
        print("results/tables.json not present; run baseline/analyze.py first")
        return 1
    parts = json.loads(tp.read_text())
    text = rp.read_text()
    n = 0
    for name, body in parts.items():
        pat = re.compile(
            rf"(<!-- BEGIN:{re.escape(name)} -->)(.*?)(<!-- END:{re.escape(name)} -->)",
            re.S)
        if pat.search(text):
            text = pat.sub(lambda m: f"{m.group(1)}\n\n{body}\n\n{m.group(3)}",
                           text)
            n += 1
    rp.write_text(text)
    print(f"rendered {n} table blocks into REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
