#!/usr/bin/env python3
"""The .sbx sidecar: restart points into a COG's tile streams.

One file per COG. A block's checkpoints can be located without reading any
other block's, because the 32 kB windows dominate the size and loading them all
would defeat the purpose.

    magic   b"SBX2"
    u32     span used when building, 0 if derived per tile
    u32     number of blocks with entries
    u64     size of the source object, in bytes
    u64     hash of the source tile layout
    table   per block: u32 block id, u32 point count, u64 body offset
    body    per point: u64 in_byte, u8 bits, u64 out, u32 window length, window

The size and layout hash are not optional. A restart point is a byte offset
into a specific stream; against a re-processed or replaced COG those offsets
still parse and still decompress, and yield silently wrong pixels. A stale
sidecar is indistinguishable from a fresh one without them, so `open_for`
refuses on mismatch rather than trusting the filename.

This is deliberately a sidecar rather than something a query builds. Building
requires decompressing a tile in full, and a sparse query never revisits a tile
often enough to pay that back, so the index has to be published alongside the
archive or accumulated by a long-lived service.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"SBX2"
_HDR = struct.Struct("<4sIIQQ")
_ENT = struct.Struct("<IIQ")
_PT = struct.Struct("<QBQI")


class StaleSidecar(Exception):
    """The sidecar does not describe the object it is being used against."""


def layout_hash(level: dict) -> int:
    """A cheap fingerprint of a file's tile layout at level 0.

    Restart points are offsets into specific streams, so any change to the tile
    table invalidates every one of them. FNV-1a over the offset and byte-count
    arrays catches re-tiling, re-compression and re-ordering alike.
    """
    h = 0xcbf29ce484222325
    for arr in (level["offsets"], level["bytecounts"]):
        for v in arr:
            v &= (1 << 64) - 1
            for _ in range(8):
                h = ((h ^ (v & 0xFF)) * 0x100000001b3) & ((1 << 64) - 1)
                v >>= 8
    return h


@dataclass
class Point:
    in_byte: int
    bits: int
    out: int
    window: bytes


def write(path: str | Path, span: int, blocks: dict[int, list],
          source_size: int, source_hash: int) -> int:
    """blocks maps a block index to its list of Checkpoint-like objects."""
    ids = sorted(blocks)
    body = bytearray()
    table = []
    for b in ids:
        table.append((b, len(blocks[b]), len(body)))
        for p in blocks[b]:
            body += _PT.pack(p.in_byte, p.bits, p.out, len(p.window))
            body += p.window
    out = bytearray()
    out += _HDR.pack(MAGIC, span, len(ids), source_size, source_hash)
    for t in table:
        out += _ENT.pack(*t)
    out += body
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(out))
    return len(out)


class Sbx:
    """Random access into a sidecar without loading every window."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = self.path.open("rb")
        header = self._fh.read(_HDR.size)
        if len(header) != _HDR.size:
            self._fh.close()
            raise ValueError(f"{path}: truncated sidecar header")
        (magic, self.span, n, self.source_size,
         self.source_hash) = _HDR.unpack(header)
        if magic != MAGIC:
            self._fh.close()
            raise ValueError(f"{path}: not an sbx v2 file")
        raw = self._fh.read(_ENT.size * n)
        if len(raw) != _ENT.size * n:
            self._fh.close()
            raise ValueError(f"{path}: truncated sidecar table")
        self._table = {}
        for i in range(n):
            b, cnt, off = _ENT.unpack_from(raw, i * _ENT.size)
            self._table[b] = (cnt, off)
        self._body = _HDR.size + _ENT.size * n
        self._cache: dict[int, list[Point]] = {}

    def __contains__(self, block: int) -> bool:
        return block in self._table

    def points(self, block: int) -> list[Point]:
        got = self._cache.get(block)
        if got is not None:
            return got
        ent = self._table.get(block)
        if ent is None:
            return []
        cnt, off = ent
        self._fh.seek(self._body + off)
        pts = []
        for _ in range(cnt):
            raw = self._fh.read(_PT.size)
            if len(raw) != _PT.size:
                raise ValueError(f"{self.path}: truncated checkpoint")
            in_byte, bits, out, wlen = _PT.unpack(raw)
            window = self._fh.read(wlen)
            if len(window) != wlen:
                raise ValueError(f"{self.path}: truncated checkpoint window")
            pts.append(Point(in_byte, bits, out, window))
        self._cache[block] = pts
        return pts

    def best(self, block: int, out_offset: int) -> Point | None:
        """The deepest restart point at or before out_offset."""
        best = None
        for p in self.points(block):
            if p.out <= out_offset:
                best = p
            else:
                break
        return best

    def close(self):
        self._fh.close()


def open_for(path: str | Path, rec: dict) -> "Sbx":
    """Open a sidecar only if it describes this object.

    rec is the object's entry from the COG index. Mismatch raises rather than
    degrading quietly, because the failure mode of a stale sidecar is wrong
    pixels, not an error.
    """
    sc = Sbx(path)
    try:
        want_size = int(rec["size"])
        want_hash = layout_hash(rec["levels"][0])
    except Exception:
        sc.close()
        raise
    if sc.source_size != want_size or sc.source_hash != want_hash:
        sc.close()
        raise StaleSidecar(
            f"{path}: built for a different object "
            f"(size {sc.source_size} vs {want_size}, "
            f"layout {sc.source_hash:x} vs {want_hash:x})")
    return sc
