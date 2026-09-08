#!/usr/bin/env python3
"""The .sbx sidecar: restart points into a COG's tile streams.

One file per COG. A block's checkpoints can be located without reading any
other block's, because the 32 kB windows dominate the size and loading them all
would defeat the purpose.

    magic   b"SBX3"
    u32     span used when building, 0 if derived per tile
    u32     number of blocks with entries
    u64     size of the source object, in bytes
    u64     hash of the source tile layout
    u16     byte length of the source identity
    bytes   source identity (content SHA-256, object version, or ETag)
    table   per block: u32 block id, u32 point count, u64 body offset
    body    per point: u64 in_byte, u8 bits, u64 out, u32 window length, window

The identity, size and layout hash are not optional. A restart point is a byte offset
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

MAGIC = b"SBX3"
LEGACY_MAGIC = b"SBX2"
_HDR = struct.Struct("<4sIIQQH")
_LEGACY_HDR = struct.Struct("<4sIIQQ")
_ENT = struct.Struct("<IIQ")
_PT = struct.Struct("<QBQI")
_MAX_TABLE_BYTES = 64 << 20
_MAX_WINDOW_BYTES = 32768


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
          source_size: int, source_hash: int,
          source_identity: str) -> int:
    """blocks maps a block index to its list of Checkpoint-like objects."""
    if not source_identity or not isinstance(source_identity, str):
        raise ValueError("a non-empty source identity is required")
    identity = source_identity.encode("utf-8")
    if len(identity) > 4096:
        raise ValueError("source identity is too long")
    ids = sorted(blocks)
    body = bytearray()
    table = []
    for b in ids:
        table.append((b, len(blocks[b]), len(body)))
        for p in blocks[b]:
            if p.bits < 0 or p.bits > 7 or len(p.window) > _MAX_WINDOW_BYTES:
                raise ValueError("invalid DEFLATE checkpoint")
            body += _PT.pack(p.in_byte, p.bits, p.out, len(p.window))
            body += p.window
    out = bytearray()
    out += _HDR.pack(MAGIC, span, len(ids), source_size, source_hash,
                     len(identity))
    out += identity
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
        magic = self._fh.read(4)
        if len(magic) != 4:
            self._fh.close()
            raise ValueError(f"{path}: truncated sidecar header")
        self._fh.seek(0)
        if magic == MAGIC:
            header = self._fh.read(_HDR.size)
            if len(header) != _HDR.size:
                self._fh.close()
                raise ValueError(f"{path}: truncated sidecar header")
            (_magic, self.span, n, self.source_size,
             self.source_hash, identity_len) = _HDR.unpack(header)
            if identity_len > 4096:
                self._fh.close()
                raise ValueError(f"{path}: unreasonable source identity length")
            identity = self._fh.read(identity_len)
            if len(identity) != identity_len:
                self._fh.close()
                raise ValueError(f"{path}: truncated source identity")
            try:
                self.source_identity = identity.decode("utf-8")
            except UnicodeDecodeError as exc:
                self._fh.close()
                raise ValueError(f"{path}: invalid source identity") from exc
            table_start = _HDR.size + identity_len
        elif magic == LEGACY_MAGIC:
            header = self._fh.read(_LEGACY_HDR.size)
            if len(header) != _LEGACY_HDR.size:
                self._fh.close()
                raise ValueError(f"{path}: truncated legacy sidecar header")
            (_magic, self.span, n, self.source_size,
             self.source_hash) = _LEGACY_HDR.unpack(header)
            self.source_identity = None
            table_start = _LEGACY_HDR.size
        else:
            self._fh.close()
            raise ValueError(f"{path}: not an sbx file")
        table_bytes = _ENT.size * n
        file_size = self.path.stat().st_size
        if table_bytes > _MAX_TABLE_BYTES or table_start + table_bytes > file_size:
            self._fh.close()
            raise ValueError(f"{path}: invalid or excessive sidecar table")
        raw = self._fh.read(table_bytes)
        self._table = {}
        for i in range(n):
            b, cnt, off = _ENT.unpack_from(raw, i * _ENT.size)
            if b in self._table:
                self._fh.close()
                raise ValueError(f"{path}: duplicate block entry")
            self._table[b] = (cnt, off)
        self._body = table_start + _ENT.size * n
        body_size = file_size - self._body
        if any(off > body_size or cnt * _PT.size > body_size - off
               for cnt, off in self._table.values()):
            self._fh.close()
            raise ValueError(f"{path}: checkpoint table points outside the file")
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
        previous_in = previous_out = -1
        for _ in range(cnt):
            raw = self._fh.read(_PT.size)
            if len(raw) != _PT.size:
                raise ValueError(f"{self.path}: truncated checkpoint")
            in_byte, bits, out, wlen = _PT.unpack(raw)
            if bits > 7 or wlen > _MAX_WINDOW_BYTES:
                raise ValueError(f"{self.path}: invalid checkpoint metadata")
            if in_byte < previous_in or out <= previous_out:
                raise ValueError(f"{self.path}: checkpoints are not ordered")
            window = self._fh.read(wlen)
            if len(window) != wlen:
                raise ValueError(f"{self.path}: truncated checkpoint window")
            pts.append(Point(in_byte, bits, out, window))
            previous_in, previous_out = in_byte, out
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
        want_identity = (rec.get("content_identity") or
                         rec.get("object_identity"))
    except Exception:
        sc.close()
        raise
    if not want_identity or not sc.source_identity:
        sc.close()
        raise StaleSidecar(
            f"{path}: sidecar and source require a strong identity binding")
    if (sc.source_size != want_size or sc.source_hash != want_hash or
            sc.source_identity != want_identity):
        sc.close()
        raise StaleSidecar(
            f"{path}: built for a different object "
            f"(size {sc.source_size} vs {want_size}, "
            f"layout {sc.source_hash:x} vs {want_hash:x}, "
            f"identity match {sc.source_identity == want_identity})")
    return sc
