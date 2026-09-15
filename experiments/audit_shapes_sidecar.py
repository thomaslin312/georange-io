#!/usr/bin/env python3
"""The query-shape audit, repeated with a sidecar, against the live archive.

audit_shapes.py measured GDAL and GeoRange IO without sidecars behind the local
logging proxy. This adds the sidecar column. It runs against the public AWS
objects the local corpus was copied from, which are byte-identical (pinned by
SHA-256 in data/staged.json), so it needs no Docker.

A sidecar only helps the tiles it holds restart points for, so first it builds
sidecars covering every tile these shapes touch, with the same 15% index budget
build_sidecars.py uses. That is what a sidecar built for the whole object would
contain for these tiles; building only the touched ones keeps the download to
those tiles rather than every object in full.

GeoRange IO counts its own bytes. To show that count is the same thing the
proxy measured, every shape is first run without a sidecar and must reproduce
audit_shapes.json byte for byte; the sidecar numbers are only written if it
does. GDAL is not re-run: it does not read sidecars, so its column is unchanged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from georange_io import SparseReader                      # noqa: E402

HOST = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
BUCKET = "sentinel-s2-l2a-cogs"
SBX = ROOT / "results" / "sbx_shapes"


def aws_keys():
    staged = json.loads((ROOT / "data" / "staged.json").read_text())
    prefix = f"{HOST}/{BUCKET}/"
    keys, ids = {}, {}
    for o in yaml.safe_load((ROOT / "data" / "sources_w6.yaml").read_text())["objects"]:
        if o["url"].startswith(prefix):
            aws = o["url"][len(prefix):]
            keys[o["key"]] = aws
            sha = staged.get(o["key"], {}).get("sha256")
            if sha:
                ids[aws] = f"sha256:{sha}"
    return keys, ids


def build(all_reqs, ids):
    """Sidecars for exactly the tiles the shapes read, from the live objects."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    sys.path.insert(0, str(ROOT / "baseline"))
    from build_sidecars import restart_span
    from theoretical import blocks_for_window
    from georange_io import sbx
    from georange_io.tile_index import build_index

    index = json.loads((ROOT / "results" / "cog_index.json").read_text())
    want = {}
    for staged_key, aws_key, lvl, x, y, w, h in all_reqs:
        rec = index[staged_key]
        want.setdefault((staged_key, aws_key), set()).update(
            blocks_for_window(rec, lvl, x, y, w, h))

    def one(job):
        staged_key, aws_key, bi = job
        L = index[staged_key]["levels"][0]
        off, cnt = L["offsets"][bi], L["bytecounts"][bi]
        span = restart_span(L, cnt)
        if span is None:
            return aws_key, bi, None
        req = urllib.request.Request(f"{HOST}/{BUCKET}/{aws_key}",
                                     headers={"Range": f"bytes={off}-{off + cnt - 1}"})
        comp = urllib.request.urlopen(req, timeout=120).read()
        assert len(comp) == cnt, (aws_key, bi, len(comp), cnt)
        return aws_key, bi, build_index(comp, span)[0].points

    jobs = [(s, a, bi) for (s, a), blocks in want.items() for bi in sorted(blocks)]

    def already_built(staged_key, aws_key):
        path = SBX / (aws_key + ".sbx")
        if not path.exists():
            return False
        L = index[staged_key]["levels"][0]
        have = sbx.Sbx(path)
        try:
            return all(bi in have for bi in want[(staged_key, aws_key)]
                       if restart_span(L, L["bytecounts"][bi]) is not None)
        finally:
            have.close()

    todo = {k for k in want if not already_built(*k)}
    points = {}
    with ThreadPoolExecutor(8) as ex:
        for aws_key, bi, pts in ex.map(one, [j for j in jobs if j[:2] in todo]):
            if pts:
                points.setdefault(aws_key, {})[bi] = pts
    for (staged_key, aws_key) in todo:
        if aws_key not in points:
            continue
        rec = index[staged_key]; L = rec["levels"][0]
        sbx.write(SBX / (aws_key + ".sbx"), 0, points[aws_key],
                  source_size=int(rec["size"]), source_hash=sbx.layout_hash(L),
                  source_identity=ids[aws_key])
    on_disk = sum((SBX / (a + ".sbx")).stat().st_size for _, a in want
                  if (SBX / (a + ".sbx")).exists())
    src = sum(index[s]["levels"][0]["bytecounts"][bi] for s, a, bi in jobs)
    print(f"sidecars for {len(jobs)} tiles in {len(want)} objects "
          f"({len(todo)} built now): "
          f"{on_disk / 1e6:.1f} MB of index for {src / 1e6:.1f} MB of tiles "
          f"({100 * on_disk / src:.1f}%)\n")
    return {"tiles": len(jobs), "index_bytes": on_disk, "tile_bytes": src}


def tiles(reqs, aws):
    """Rows requested in each tile, and the restart row the reader starts from.

    Mirrors SparseReader._plan_fetch: the restart point is the deepest one at
    or before the shallowest requested row, and one at output offset 0 is no
    restart at all. Recorded so the README figure is drawn from committed data
    rather than from sidecar files that are not.
    """
    sys.path.insert(0, str(ROOT / "baseline"))
    from theoretical import blocks_for_window
    from georange_io import sbx
    index = json.loads((ROOT / "results" / "cog_index.json").read_text())
    rows = {}
    for (k, lvl, x, y, w, h) in reqs:
        L = index[k]["levels"][lvl]
        for bi in blocks_for_window(index[k], lvl, x, y, w, h):
            bx, by = bi % L["nbx"], bi // L["nbx"]
            x0 = max(x, bx * L["blockw"]) - bx * L["blockw"]
            y0 = max(y, by * L["blockh"]) - by * L["blockh"]
            x1 = min(x + w, (bx + 1) * L["blockw"]) - bx * L["blockw"]
            y1 = min(y + h, (by + 1) * L["blockh"]) - by * L["blockh"]
            rows.setdefault((k, bi), []).append([x0, y0, x1, y1])
    out = []
    for (k, bi), rects in rows.items():
        L = index[k]["levels"][0]
        row_bytes = L["blockw"] * __import__("numpy").dtype(L["dtype"]).itemsize
        shallow = min(r[1] for r in rects)
        restart = 0
        path = SBX / (aws[k] + ".sbx")
        if path.exists():
            sc = sbx.Sbx(path)
            try:
                pt = sc.best(bi, shallow * row_bytes) if bi in sc else None
            finally:
                sc.close()
            if pt is not None and pt.out > 0:
                restart = pt.out // row_bytes
        out.append({"key": k, "block": bi, "nbx": L["nbx"],
                    "block_size": [L["blockw"], L["blockh"]],
                    "requested": rects, "restart_row": restart})
    return out


def measure(reqs, sidecar, ids):
    rd = SparseReader(None, HOST, BUCKET, margin=0.03,
                      sbx_dir=str(SBX) if sidecar else None,
                      content_identities=ids if sidecar else {})
    outs = rd.read(reqs)
    st = rd.stats.as_dict()
    rd.close()
    digest = __import__("hashlib").sha256(
        b"".join(np.ascontiguousarray(o).tobytes() for o in outs)).hexdigest()[:16]
    return st["requests"], st["bytes_fetched"], st["blocks_from_checkpoint"], digest


def main() -> int:
    sys.path.insert(0, str(ROOT / "experiments"))
    import audit_shapes
    keys, ids = aws_keys()
    baseline = {r["shape"]: r for r in
                json.loads((ROOT / "results" / "audit_shapes.json").read_text())}
    shapes = audit_shapes.shapes()
    built = build([(k, keys[k], lvl, x, y, w, h) for reqs in shapes.values()
                   for (k, lvl, x, y, w, h) in reqs], ids)
    rows, ok = [], True
    print(f"{'query shape':<30} {'audited':>10} {'live':>10} {'match':>6} "
          f"{'sidecar':>10} {'vs GDAL':>8}")
    for name, staged_reqs in shapes.items():
        reqs = [(keys[k], lvl, x, y, w, h) for (k, lvl, x, y, w, h) in staged_reqs]
        n0, b0, _, d0 = measure(reqs, False, ids)
        n1, b1, cp, d1 = measure(reqs, True, ids)
        base = baseline[name]
        match = b0 == base["gr_bytes"]
        ok &= match and d0 == d1
        print(f"{name:<30} {base['gr_bytes']:>10,} {b0:>10,} {str(match):>6} "
              f"{b1:>10,} {base['gdal_bytes'] / b1:>7.2f}x")
        rows.append({"shape": name, "gdal_bytes": base["gdal_bytes"],
                     "gr_bytes": b0, "gr_sidecar_bytes": b1,
                     "gr_sidecar_requests": n1, "blocks_from_checkpoint": cp,
                     "byte_gain": round(base["gdal_bytes"] / b0, 3),
                     "sidecar_byte_gain": round(base["gdal_bytes"] / b1, 3),
                     "sidecar_identical_to_plain": d0 == d1,
                     "tiles": tiles(staged_reqs, keys)})
    if not ok:
        print("\nlive counts did not reproduce the audit, or values differed; not writing")
        return 1
    out = ROOT / "results" / "audit_shapes_sidecar.json"
    out.write_text(json.dumps({"sidecars": built, "shapes": rows}, indent=2))
    print(f"\nall shapes reproduced the audit exactly; wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
