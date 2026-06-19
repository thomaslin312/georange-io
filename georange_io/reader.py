#!/usr/bin/env python3
"""A sparse reader for COG archives.

Given a list of (file, x, y) requests it returns the pixel values, fetching far
less than any existing reader because it does two things they cannot:

  it plans      the whole request set is known before the first byte is
                fetched, so requests are grouped by block and each block is
                fetched once, in file order, to the depth the deepest request
                in it needs
  it truncates  a block's DEFLATE stream is fetched and inflated only as far as
                that deepest row, instead of in full

Everything it does not handle it declines, so a caller can fall back to GDAL.
Correctness is not negotiable here: the values must be identical to GDAL's, and
georange_io/verify.py checks that against every read in a workload.
"""
from __future__ import annotations

import json
import urllib.request
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SUPPORTED_COMPRESSION = {8, 32946}          # Deflate, Adobe Deflate
SUPPORTED_PREDICTOR = {1, 2, 3}


@dataclass
class Stats:
    requests: int = 0
    bytes_fetched: int = 0
    blocks: int = 0
    undershoots: int = 0
    bytes_if_whole_blocks: int = 0
    declined: int = 0

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["byte_gain_vs_whole_blocks"] = (
            round(self.bytes_if_whole_blocks / self.bytes_fetched, 3)
            if self.bytes_fetched else None)
        return d


@dataclass
class _Job:
    key: str
    block: int
    offset: int
    count: int
    deepest_row: int
    items: list = field(default_factory=list)   # (req_i, local_x, local_y)


class SparseReader:
    def __init__(self, index_path: str | Path, base_url: str, bucket: str,
                 margin: float = 0.12, min_fetch: int = 16384):
        self.idx = json.loads(Path(index_path).read_text())
        self.base = base_url.rstrip("/")
        self.bucket = bucket
        self.margin = margin
        self.min_fetch = min_fetch
        self.stats = Stats()

    # -- capability check --------------------------------------------------
    def supports(self, key: str) -> bool:
        rec = self.idx.get(key)
        if not rec or not rec.get("levels"):
            return False
        L = rec["levels"][0]
        return (L["compression"] in SUPPORTED_COMPRESSION
                and L.get("predictor", 1) in SUPPORTED_PREDICTOR
                and L.get("samples", 1) == 1
                and L.get("planar", 1) == 1
                and rec.get("byteorder", "<") == "<")

    # -- planning ----------------------------------------------------------
    def plan(self, requests) -> list[_Job]:
        groups: dict[tuple[str, int], _Job] = {}
        for i, (key, x, y) in enumerate(requests):
            L = self.idx[key]["levels"][0]
            bx, by = x // L["blockw"], y // L["blockh"]
            bi = by * L["nbx"] + bx
            k = (key, bi)
            j = groups.get(k)
            if j is None:
                j = groups[k] = _Job(key=key, block=bi,
                                     offset=L["offsets"][bi],
                                     count=L["bytecounts"][bi],
                                     deepest_row=0)
            ly = y - by * L["blockh"]
            j.items.append((i, x - bx * L["blockw"], ly))
            if ly > j.deepest_row:
                j.deepest_row = ly
        # file order, so the fetches walk the archive forwards
        return sorted(groups.values(), key=lambda j: (j.key, j.offset))

    # -- fetching ----------------------------------------------------------
    def _get(self, key: str, start: int, length: int) -> bytes:
        req = urllib.request.Request(
            f"{self.base}/{self.bucket}/{key}",
            headers={"Range": f"bytes={start}-{start+length-1}",
                     "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
        self.stats.requests += 1
        self.stats.bytes_fetched += len(data)
        return data

    def _inflate_to(self, job: _Job, need_out: int, wrapped: bool) -> bytes:
        """Fetch a speculative prefix and inflate to need_out, extending the
        fetch only if the guess fell short."""
        L = self.idx[job.key]["levels"][0]
        frac = (job.deepest_row + 1) / L["blockh"] + self.margin
        guess = int(min(job.count, max(self.min_fetch, job.count * frac)))

        buf = self._get(job.key, job.offset, guess)
        got = guess
        d = zlib.decompressobj(15 if wrapped else -15)
        out = bytearray()
        feed = buf
        while len(out) < need_out:
            chunk = d.decompress(feed, need_out - len(out))
            out += chunk
            feed = d.unconsumed_tail
            if len(out) >= need_out:
                break
            if not feed:
                if got >= job.count:
                    break
                # undershot: take the rest of the block
                self.stats.undershoots += 1
                feed = self._get(job.key, job.offset + got, job.count - got)
                got = job.count
        return bytes(out)

    # -- decoding ----------------------------------------------------------
    @staticmethod
    def _undo_row(raw: bytes, dt: np.dtype, width: int, predictor: int):
        if predictor == 1:
            return np.frombuffer(raw, dtype=dt, count=width)
        if predictor == 2:
            v = np.frombuffer(raw, dtype=dt, count=width)
            return np.cumsum(v.astype(np.int64)).astype(dt)
        # predictor 3: bytes are stored as separated planes, most significant
        # first, with horizontal differencing applied across the byte stream
        b = np.frombuffer(raw, dtype=np.uint8, count=width * dt.itemsize)
        b = np.cumsum(b.astype(np.int64)).astype(np.uint8)
        planes = b.reshape(dt.itemsize, width)
        # a value's bytes are (plane0, plane1, ...), i.e. big-endian order
        return np.ascontiguousarray(planes.T).tobytes()

    # -- public ------------------------------------------------------------
    def sample(self, requests) -> np.ndarray:
        """requests: sequence of (key, x, y). Returns one value per request."""
        out = np.zeros(len(requests), np.float64)
        jobs = self.plan(requests)
        for job in jobs:
            rec = self.idx[job.key]
            L = rec["levels"][0]
            dt = np.dtype(L["dtype"])
            width = L["blockw"]
            row_bytes = width * dt.itemsize
            pred = L.get("predictor", 1)
            self.stats.blocks += 1
            self.stats.bytes_if_whole_blocks += job.count

            if job.count == 0:                    # sparse tile: all nodata
                fill = rec.get("nodata") or 0
                for (i, _lx, _ly) in job.items:
                    out[i] = fill
                continue

            raw = self._inflate_to(job, (job.deepest_row + 1) * row_bytes,
                                   wrapped=True)
            for (i, lx, ly) in job.items:
                row = raw[ly * row_bytes:(ly + 1) * row_bytes]
                if len(row) < row_bytes:
                    raise RuntimeError(
                        f"{job.key} block {job.block}: short inflate, "
                        f"{len(row)} of {row_bytes} bytes for row {ly}")
                vals = self._undo_row(row, dt, width, pred)
                if pred == 3:
                    v = np.frombuffer(vals, dtype=dt.newbyteorder(">"),
                                      count=width)
                else:
                    v = vals
                out[i] = float(v[lx])
        return out
