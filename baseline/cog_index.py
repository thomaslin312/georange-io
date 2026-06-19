#!/usr/bin/env python3
"""Build a per-object block index from the COGs' own TIFF structures.

For every staged object and every IFD (native resolution plus each overview)
this records the tile grid and the exact compressed byte count of every tile,
read straight out of TileOffsets / TileByteCounts.

Everything downstream that talks about a "theoretical minimum" uses this file.
It is produced by reading MinIO *directly*, never through the logging proxy,
so indexing contributes nothing to any measurement.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

import boto3
import numpy as np
import tifffile
from botocore.config import Config

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "results" / "cog_index.json"

ENDPOINT = os.environ.get("MINIO_DIRECT", "http://127.0.0.1:9100")
ACCESS = os.environ.get("MINIO_ACCESS_KEY", "georange-io")
SECRET = os.environ.get("MINIO_SECRET_KEY", "georange-io123")
BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")


def client():
    return boto3.client(
        "s3", endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS, aws_secret_access_key=SECRET,
        region_name="us-east-1",
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": "path"},
                      retries={"max_attempts": 5, "mode": "standard"},
                      max_pool_connections=32),
    )


class S3File(io.RawIOBase):
    """Minimal seekable read-only file over ranged S3 GETs, with a read-ahead
    cache. tifffile only touches the header region of a COG, so this stays cheap."""

    def __init__(self, cli, bucket: str, key: str, size: int, block: int = 1 << 20):
        self._c, self._b, self._k = cli, bucket, key
        self._size = size
        self._pos = 0
        self._block = block
        self._cache: dict[int, bytes] = {}
        self.bytes_read = 0
        self.n_gets = 0

    def _chunk(self, idx: int) -> bytes:
        if idx not in self._cache:
            lo = idx * self._block
            hi = min(lo + self._block, self._size) - 1
            if lo > hi:
                self._cache[idx] = b""
            else:
                r = self._c.get_object(Bucket=self._b, Key=self._k,
                                       Range=f"bytes={lo}-{hi}")
                self._cache[idx] = r["Body"].read()
                self.bytes_read += len(self._cache[idx])
                self.n_gets += 1
        return self._cache[idx]

    def readable(self) -> bool: return True
    def seekable(self) -> bool: return True
    def tell(self) -> int: return self._pos

    def seek(self, off: int, whence: int = 0) -> int:
        self._pos = (off if whence == 0 else
                     self._pos + off if whence == 1 else self._size + off)
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self._size - self._pos
        n = max(0, min(n, self._size - self._pos))
        out = bytearray()
        while n > 0:
            idx = self._pos // self._block
            off = self._pos - idx * self._block
            c = self._chunk(idx)
            take = c[off:off + n]
            if not take:
                break
            out += take
            self._pos += len(take)
            n -= len(take)
        return bytes(out)

    def readinto(self, b) -> int:
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def index_object(cli, key: str, size: int) -> dict:
    f = S3File(cli, BUCKET, key, size)
    with tifffile.TiffFile(f) as tf:
        levels = []
        # Series 0 pages: page 0 is native, subsequent reduced-resolution pages
        # are the overviews. Masks (if any) are skipped: they are separate
        # SubIFDs / pages flagged as such.
        pages = list(tf.pages)
        base_w = base_h = None
        for p in pages:
            if p.tilewidth in (0, None) or p.tilelength in (0, None):
                # Not internally tiled. The whole project assumes tiled COGs.
                return {"key": key, "size": size, "tiled": False,
                        "error": "IFD is stripped, not tiled"}
            photo = int(p.photometric) if p.photometric is not None else -1
            newsub = int(p.tags["NewSubfileType"].value) if "NewSubfileType" in p.tags else 0
            is_mask = (photo == 4) or bool(newsub & 0b100)
            w, h = int(p.imagewidth), int(p.imagelength)
            if base_w is None:
                base_w, base_h = w, h
            offs = np.asarray(p.dataoffsets, dtype=np.int64)
            cnts = np.asarray(p.databytecounts, dtype=np.int64)
            tw, th = int(p.tilewidth), int(p.tilelength)
            nbx = (w + tw - 1) // tw
            nby = (h + th - 1) // th
            spp = int(p.samplesperpixel or 1)
            planar = int(p.planarconfig) if p.planarconfig is not None else 1
            nplanes = spp if planar == 2 else 1
            expect = nbx * nby * nplanes
            if offs.size != expect:
                return {"key": key, "size": size, "tiled": True,
                        "error": f"tile count {offs.size} != grid {expect}"}
            levels.append({
                "level": len(levels), "is_mask": is_mask,
                "width": w, "height": h, "blockw": tw, "blockh": th,
                "nbx": nbx, "nby": nby, "nplanes": nplanes,
                "dtype": str(p.dtype), "samples": spp, "planar": planar,
                "compression": int(p.compression),
                # a partial-tile reader must undo the predictor itself
                "predictor": int(p.predictor) if p.predictor is not None else 1,
                "byteorder": tf.byteorder,
                "offsets": offs.tolist(), "bytecounts": cnts.tolist(),
                "empty_tiles": int((cnts == 0).sum()),
                "sum_bytes": int(cnts.sum()),
            })
        # In a header-first (cloud-optimized) layout every IFD and every
        # out-of-line tag value precedes the first tile of pixel data, so the
        # offset of the earliest tile is exactly the extent of the metadata a
        # reader must have in hand before it can locate anything. That is the
        # honest header term in the theoretical minimum.
        first_tile = min((min((o for o in l["offsets"] if o > 0), default=size)
                          for l in levels), default=size)
        header_bytes = int(min(first_tile, size))
        return {"key": key, "size": size, "tiled": True,
                "bigtiff": bool(tf.is_bigtiff),
                "header_bytes": header_bytes,
                "first_tile_offset": int(first_tile),
                "index_gets": f.n_gets, "index_bytes": f.bytes_read,
                "levels": levels}


def add_geo(rec: dict) -> dict:
    """Attach CRS and geotransform per level using rasterio, direct to MinIO."""
    import rasterio
    from rasterio.env import Env
    url = f"{ENDPOINT}/{BUCKET}/{rec['key']}"
    with Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
             CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
             GDAL_HTTP_MAX_RETRY="3"):
        with rasterio.open(f"/vsicurl/{url}") as ds:
            rec["crs"] = ds.crs.to_string() if ds.crs else None
            rec["epsg"] = ds.crs.to_epsg() if ds.crs else None
            t = ds.transform
            rec["transform"] = [t.a, t.b, t.c, t.d, t.e, t.f]
            rec["bounds"] = list(ds.bounds)
            rec["nodata"] = ds.nodata
            rec["block_shape"] = list(ds.block_shapes[0])
            rec["gdal_ovr_decim"] = list(ds.overviews(1))
    # per-level transform: overviews cover the same extent
    W0 = rec["levels"][0]["width"]; H0 = rec["levels"][0]["height"]
    a, b, c, d, e, f = rec["transform"]
    for L in rec["levels"]:
        sx = W0 / L["width"]; sy = H0 / L["height"]
        L["transform"] = [a * sx, b, c, d, e * sy, f]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", default=str(ROOT / "data" / "staged.json"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    staged = json.loads(Path(args.staged).read_text())
    keys = sorted(staged)[: args.limit or None]
    cli = client()

    out: dict[str, dict] = {}
    bad = []
    for i, k in enumerate(keys, 1):
        try:
            rec = index_object(cli, k, staged[k]["bytes"])
            if not rec.get("tiled") or "error" in rec:
                bad.append((k, rec.get("error", "not tiled")))
            else:
                rec = add_geo(rec)
            rec["dataset"] = staged[k].get("dataset")
            rec["tile"] = staged[k].get("tile")
            rec["role"] = staged[k].get("role")
            out[k] = rec
            nb = sum(l["nbx"] * l["nby"] for l in rec.get("levels", []))
            print(f"[{i}/{len(keys)}] {k:44s} levels={len(rec.get('levels',[]))} "
                  f"blocks={nb} hdr={rec.get('header_bytes')}B", flush=True)
        except Exception as e:  # noqa: BLE001
            bad.append((k, repr(e)[:180]))
            print(f"[{i}/{len(keys)}] {k}: FAIL {e}", file=sys.stderr, flush=True)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out))
    print(f"\nwrote {args.out}: {len(out)} objects", flush=True)

    if bad:
        print("\n" + "=" * 72, file=sys.stderr)
        print("!! NOT INTERNALLY TILED / UNPARSEABLE -- the whole project assumes",
              file=sys.stderr)
        print("!! tiled COGs. These objects break that assumption:", file=sys.stderr)
        for k, e in bad:
            print(f"!!   {k}: {e}", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
