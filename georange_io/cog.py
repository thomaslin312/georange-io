#!/usr/bin/env python3
"""Describe a remote COG from its own header, so nothing has to be indexed
in advance.

The reader needs a tile map: per level, the tile grid and every tile's offset
and compressed length, plus the dtype, predictor and byte order needed to
decode a partial tile. GDAL exposes almost none of that through its public API,
which is why this project carried a prebuilt JSON index for a fixed corpus.

That is fine for an experiment and useless as a library. This reads the header
directly over ranged GETs. A cloud-optimised file puts every IFD and every tile
table before the first tile of pixel data, so one or two requests of a megabyte
each describe the whole file, however large it is.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RangeResult:
    """One validated range response plus source identity metadata.

    Iteration intentionally yields only ``data`` and ``total`` so transports
    written for the original two-tuple protocol continue to work.
    """

    data: bytes
    total: int | None
    object_identity: str | None = None
    attempts: int = 1

    def __iter__(self):
        yield self.data
        yield self.total


def as_range_result(value) -> RangeResult:
    """Normalize the public transport protocol, preserving old custom pools."""
    if isinstance(value, RangeResult):
        return value
    try:
        data, total = value
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "get_range() must return RangeResult or (bytes, total_size)") from exc
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("get_range() returned a non-bytes body")
    return RangeResult(bytes(data), total)


class RemoteFile(io.RawIOBase):
    """Seekable read-through view of a remote object, for the header only.

    Reads are served in fixed blocks and cached, so a parser that seeks around
    the header a few hundred times costs one or two requests rather than one
    per seek.

    The block size is 32 kB because that is what the corpus actually needs. It
    parses every IFD and tile table in one request for all four products,
    including a WorldCover file with 1,740 tiles. The previous 1 MB default
    pulled 32x more for no benefit, and since header traffic is counted in the
    public statistics it was directly degrading the measured gain: a
    single-block read fetched 2.75 MB where GDAL fetched 1.74 MB, entirely
    because of it.
    """

    def __init__(self, pool, path: str, block: int = 1 << 15):
        self._pool = pool
        self._path = path
        self._block = block
        self._pos = 0
        self._cache: dict[int, bytes] = {}
        self._size: int | None = None
        self.requests = 0
        self.retries = 0
        self.bytes_read = 0
        self.object_identity: str | None = None

    def _chunk(self, idx: int) -> bytes:
        got = self._cache.get(idx)
        if got is not None:
            return got
        lo = idx * self._block
        response = as_range_result(
            self._pool.get_range(self._path, lo, self._block))
        data, total = response
        if total is not None:
            if self._size is not None and total != self._size:
                raise RuntimeError(f"{self._path}: object size changed while reading")
            self._size = total
        elif self._size is None:
            self._size = lo + len(data)
        if response.object_identity is not None:
            if (self.object_identity is not None and
                    response.object_identity != self.object_identity):
                raise RuntimeError(
                    f"{self._path}: object identity changed while reading")
            self.object_identity = response.object_identity
        self.requests += response.attempts
        self.retries += response.attempts - 1
        self.bytes_read += len(data)
        self._cache[idx] = data
        return data

    @property
    def size(self) -> int:
        if self._size is None:
            self._chunk(0)
        return self._size

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self._pos

    def seek(self, off, whence=0):
        self._pos = (off if whence == 0 else self._pos + off
                     if whence == 1 else self.size + off)
        return self._pos

    def read(self, n=-1):
        size = self.size
        if n is None or n < 0:
            n = size - self._pos
        n = max(0, min(n, size - self._pos))
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

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def describe(pool, path: str) -> dict:
    """Build a tile map for one remote COG by reading its header."""
    import tifffile

    f = RemoteFile(pool, path)
    with tifffile.TiffFile(f) as tf:
        levels = []
        for p in tf.pages:
            if p.tilewidth in (0, None) or p.tilelength in (0, None):
                raise ValueError(f"{path}: an IFD is stripped, not tiled")
            w, h = int(p.imagewidth), int(p.imagelength)
            tw, th = int(p.tilewidth), int(p.tilelength)
            spp = int(p.samplesperpixel or 1)
            planar = int(p.planarconfig) if p.planarconfig is not None else 1
            offs = np.asarray(p.dataoffsets, dtype=np.int64)
            cnts = np.asarray(p.databytecounts, dtype=np.int64)
            nbx = (w + tw - 1) // tw
            nby = (h + th - 1) // th
            nplanes = spp if planar == 2 else 1
            if offs.size != nbx * nby * nplanes:
                raise ValueError(
                    f"{path}: tile count {offs.size} does not match the "
                    f"{nbx}x{nby} grid")
            levels.append({
                "level": len(levels), "width": w, "height": h,
                "blockw": tw, "blockh": th, "nbx": nbx, "nby": nby,
                "nplanes": nplanes, "dtype": str(p.dtype), "samples": spp,
                "planar": planar, "compression": int(p.compression),
                "predictor": int(p.predictor) if p.predictor is not None else 1,
                "byteorder": tf.byteorder,
                "offsets": offs.tolist(), "bytecounts": cnts.tolist(),
            })
        nodata = None
        t = tf.pages[0].tags.get("GDAL_NODATA")
        if t is not None:
            try:
                nodata = float(str(t.value).strip("\x00").strip())
            except (TypeError, ValueError):
                nodata = None
        return {"key": path, "size": f.size, "nodata": nodata,
                "object_identity": f.object_identity,
                "levels": levels, "header_requests": f.requests,
                "header_retries": f.retries, "header_bytes": f.bytes_read}


class CogIndex:
    """A tile map per object, built on demand and cached.

    Accepts a prebuilt mapping to seed from, which keeps the experiment
    harnesses working unchanged, but needs neither that nor any pre-pass to
    read a file it has never seen.
    """

    def __init__(self, pool=None, path_for=None, seed: dict | None = None):
        self._pool = pool
        self._path_for = path_for or (lambda k: "/" + k)
        self._d: dict[str, dict] = dict(seed or {})
        self._lock = threading.Lock()
        self.described = 0
        self.header_requests = 0
        self.header_retries = 0
        self.header_bytes = 0
        self.errors: dict[str, Exception] = {}

    def __contains__(self, key): return self.get(key) is not None
    def __getitem__(self, key):
        rec = self.get(key)
        if rec is None:
            raise KeyError(key)
        return rec

    def get(self, key, default=None):
        rec = self._d.get(key)
        if rec is not None:
            return rec
        if self._pool is None:
            return default
        with self._lock:
            if key in self._d:
                return self._d[key]
            try:
                rec = describe(self._pool, self._path_for(key))
            except Exception as exc:
                # A timeout or transient object-store failure must not poison
                # this key for the lifetime of the reader. Only successful
                # descriptions are cached.
                self.errors[key] = exc
                return default
            self.errors.pop(key, None)
            self._d[key] = rec
            self.described += 1
            self.header_requests += int(rec.get("header_requests", 0))
            self.header_retries += int(rec.get("header_retries", 0))
            self.header_bytes += int(rec.get("header_bytes", 0))
            return rec
