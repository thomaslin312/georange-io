#!/usr/bin/env python3
"""The .sbx sidecar: restart points into a COG's tile streams.

One file per COG. A block's checkpoints can be located without reading any
other block's, because the 32 kB windows dominate the size and loading them all
would defeat the purpose.

    magic   b"SBX1"
    u32     span used when building
    u32     number of blocks with entries
    table   per block: u32 block id, u32 point count, u64 body offset
    body    per point: u64 in_byte, u8 bits, u64 out, u32 window length, window

This is deliberately a sidecar rather than something a query builds. Building
requires decompressing a tile in full, and a sparse query never revisits a tile
often enough to pay that back, so the index has to be published alongside the
archive or accumulated by a long-lived service.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"SBX1"
_HDR = struct.Struct("<4sII")
_ENT = struct.Struct("<IIQ")
_PT = struct.Struct("<QBQI")


@dataclass
class Point:
    in_byte: int
    bits: int
    out: int
    window: bytes


def write(path: str | Path, span: int, blocks: dict[int, list]) -> int:
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
    out += _HDR.pack(MAGIC, span, len(ids))
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
        magic, self.span, n = _HDR.unpack(self._fh.read(_HDR.size))
        if magic != MAGIC:
            raise ValueError(f"{path}: not an sbx file")
        raw = self._fh.read(_ENT.size * n)
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
            in_byte, bits, out, wlen = _PT.unpack(self._fh.read(_PT.size))
            pts.append(Point(in_byte, bits, out, self._fh.read(wlen)))
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
