#!/usr/bin/env python3
"""Can sparse tile access skip most of the tile?

A COG tile is a single DEFLATE stream. Reading pixel row r of a 1024-row tile
requires inflating rows 0..r and nothing after it, so a reader that only needs
an early row can stop. And because it can stop, it never needed the tail of the
compressed stream either, so it could have fetched only a prefix of the tile's
byte range.

GDAL's tile abstraction is all-or-nothing: it fetches the whole tile and
inflates the whole tile whatever you asked for. This measures what that costs.

For a sample of real tiles it records, at every row boundary, how many
compressed bytes had to be consumed to produce that many rows. That gives:

  * the byte saving available to a reader that fetches only a prefix
  * the decode saving available to a reader that stops early
  * how well row position predicts compressed position, which is what a
    speculative prefix fetch has to guess

Correctness is verified against rasterio for predictor-2 tiles: the pixel value
recovered from a truncated inflate must equal the value GDAL returns.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "baseline"))

BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")
ENDPOINT = os.environ.get("MINIO_DIRECT", "http://minio:9000")


def s3():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", "georange-io"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", "georange-io123"),
        region_name="us-east-1",
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": "path"}, max_pool_connections=16))


def consumed_for_rows(comp: bytes, row_bytes: int, nrows: int) -> np.ndarray:
    """Compressed bytes consumed to produce each row boundary.

    zlib's decompressobj with max_length stops as soon as it has produced the
    requested output and leaves the rest of the input in unconsumed_tail, so
    the difference is the compressed prefix that was actually required.
    """
    out = np.zeros(nrows + 1, np.int64)
    d = zlib.decompressobj()
    produced = 0
    fed = comp
    for r in range(1, nrows + 1):
        need = row_bytes
        guard = 0
        while need > 0:
            chunk = d.decompress(fed, need)
            produced += len(chunk)
            need -= len(chunk)
            fed = d.unconsumed_tail
            guard += 1
            if (not chunk and not fed) or guard > 64:
                break
        out[r] = len(comp) - len(fed)
        if not fed and produced < r * row_bytes:
            out[r:] = len(comp)
            break
    return out


def verify_predictor2(comp: bytes, row_bytes: int, row: int, dtype: np.dtype,
                      width: int) -> np.ndarray:
    """Recover one row from a truncated inflate, undoing horizontal
    differencing. Used to prove the early-stop result is not just a timing
    claim: the values must match what GDAL returns."""
    d = zlib.decompressobj()
    want = (row + 1) * row_bytes
    buf = b""
    fed = comp
    guard = 0
    while len(buf) < want and guard < 4096:
        chunk = d.decompress(fed, want - len(buf))
        buf += chunk
        fed = d.unconsumed_tail
        guard += 1
        if not chunk and not fed:
            break
    raw = buf[row * row_bytes:(row + 1) * row_bytes]
    deltas = np.frombuffer(raw, dtype=dtype, count=width)
    return np.cumsum(deltas.astype(np.int64)).astype(dtype)


def undo_predictor2(row: np.ndarray) -> np.ndarray:
    """Horizontal differencing, applied per row per sample."""
    return np.cumsum(row, dtype=np.int64).astype(row.dtype)


def sample_tiles(idx: dict, dataset: str, band: str | None, n: int, rng):
    keys = sorted(k for k, r in idx.items()
                  if r.get("dataset") == dataset
                  and (band is None or k.endswith(f"/{band}.tif")))
    out = []
    for k in keys:
        L = idx[k]["levels"][0]
        nz = [i for i, c in enumerate(L["bytecounts"]) if c > 0]
        if nz:
            out.append((k, int(rng.choice(nz))))
    rng.shuffle(out)
    return out[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20240929)
    ap.add_argument("--out", default="results/prefix_decode.json")
    args = ap.parse_args()

    idx = json.loads((ROOT / "results" / "cog_index.json").read_text())
    rng = np.random.default_rng(args.seed)
    cli = s3()

    targets = {
        "sentinel2_l2a_B04": ("sentinel2_l2a", "B04"),
        "sentinel2_l2a_B08": ("sentinel2_l2a", "B08"),
        "cop_dem_glo30": ("cop_dem_glo30", None),
        "esa_worldcover_v200": ("esa_worldcover_v200", None),
    }

    report = {}
    for label, (ds, band) in targets.items():
        picks = sample_tiles(idx, ds, band, args.tiles, rng)
        if not picks:
            continue
        curves = []
        full_ms = []
        stats = {"n_tiles": 0, "compressed_bytes": 0}
        pred = None
        for key, bi in picks:
            rec = idx[key]
            L = rec["levels"][0]
            off, cnt = L["offsets"][bi], L["bytecounts"][bi]
            if cnt <= 0:
                continue
            comp = cli.get_object(Bucket=BUCKET, Key=key,
                                  Range=f"bytes={off}-{off+cnt-1}")["Body"].read()
            dt = np.dtype(L["dtype"])
            spp = L["samples"] if L.get("planar", 1) == 1 else 1
            row_bytes = L["blockw"] * dt.itemsize * spp
            nrows = L["blockh"]
            pred = L.get("compression")

            t0 = time.perf_counter()
            raw = zlib.decompress(comp)
            full_ms.append((time.perf_counter() - t0) * 1000)

            c = consumed_for_rows(comp, row_bytes, nrows)
            curves.append(c / max(1, len(comp)))
            stats["n_tiles"] += 1
            stats["compressed_bytes"] += len(comp)

        # Correctness: recover a row from a truncated inflate and compare
        # with what GDAL returns for the same pixels. Only meaningful for the
        # predictor-2 integer tiles.
        verified = None
        if label.startswith("sentinel2"):
            import rasterio
            key, bi = picks[0]
            rec = idx[key]; L = rec["levels"][0]
            off, cnt = L["offsets"][bi], L["bytecounts"][bi]
            comp = cli.get_object(Bucket=BUCKET, Key=key,
                                  Range=f"bytes={off}-{off+cnt-1}")["Body"].read()
            dt = np.dtype(L["dtype"])
            row_bytes = L["blockw"] * dt.itemsize
            bx, by = bi % L["nbx"], bi // L["nbx"]
            row = int(rng.integers(0, min(L["blockh"], 256)))
            mine = verify_predictor2(comp, row_bytes, row, dt, L["blockw"])
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
                with rasterio.open(f"/vsicurl/{ENDPOINT}/{BUCKET}/{key}") as ds:
                    truth = ds.read(1, window=rasterio.windows.Window(
                        bx * L["blockw"], by * L["blockh"] + row,
                        L["blockw"], 1))[0]
            verified = bool(np.array_equal(mine, truth))
            print(f"   correctness: row {row} of tile {bi} in "
                  f"{key.split('/')[-2]}/{key.split('/')[-1]} "
                  f"recovered from truncated inflate matches GDAL: {verified}")

        if not curves:
            continue
        C = np.vstack(curves)                       # tiles x (nrows+1)
        nrows = C.shape[1] - 1
        rowfrac = np.arange(nrows + 1) / nrows

        # A single pixel lands on a uniformly random row, so the expected
        # compressed prefix a reader needs is the mean over rows.
        per_tile_mean = C[:, 1:].mean(axis=1)
        report[label] = {
            "n_tiles": stats["n_tiles"],
            "mean_compressed_tile_bytes": int(stats["compressed_bytes"]
                                              / max(1, stats["n_tiles"])),
            "rows": nrows,
            "mean_full_inflate_ms": round(float(np.mean(full_ms)), 3),
            "expected_prefix_fraction_single_pixel":
                round(float(per_tile_mean.mean()), 4),
            "expected_saving_single_pixel":
                round(1 - float(per_tile_mean.mean()), 4),
            "prefix_fraction_at_row_fraction": {
                f"{q:.2f}": round(float(C[:, int(q * nrows)].mean()), 4)
                for q in (0.1, 0.25, 0.5, 0.75, 0.9, 1.0)},
            # How badly row position mispredicts compressed position. A
            # speculative fetch must add this much margin to avoid a second
            # round trip.
            "max_overshoot_of_rowfrac":
                round(float(np.max(C - rowfrac[None, :])), 4),
            "p95_overshoot_of_rowfrac":
                round(float(np.percentile(C - rowfrac[None, :], 95)), 4),
            "truncated_inflate_matches_gdal": verified,
        }
        r = report[label]
        print(f"{label:<22} {r['n_tiles']:>3} tiles, "
              f"{r['mean_compressed_tile_bytes']/1024:>6.0f} kB/tile")
        print(f"   single pixel needs {r['expected_prefix_fraction_single_pixel']*100:5.1f}% "
              f"of the compressed tile -> {r['expected_saving_single_pixel']*100:5.1f}% saved")
        print(f"   prefix fraction at row fraction: "
              + "  ".join(f"{k}->{v:.2f}" for k, v in
                          r["prefix_fraction_at_row_fraction"].items()))
        print(f"   row-position mispredicts compressed position by up to "
              f"{r['max_overshoot_of_rowfrac']*100:.1f}% "
              f"(p95 {r['p95_overshoot_of_rowfrac']*100:.1f}%)")

    (ROOT / args.out).write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
