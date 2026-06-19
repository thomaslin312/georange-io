#!/usr/bin/env python3
"""Checkpointed random access into a COG tile's DEFLATE stream.

A COG tile is one DEFLATE stream, so every reader must start at byte zero and
decompress forward. Reading the last row costs the whole tile. Prefix decoding
helps only because it can stop early; it still cannot start late.

DEFLATE can be entered mid-stream if two things are restored: the bit position
in the compressed data, and the 32 kB of prior output that forms the sliding
window. Mark Adler's zran does this for gzip. This applies it to COG tiles.

TIFF makes it unusually clean. Predictors are applied per row and reset at each
row boundary, so a restart point carries no predictor state, only the DEFLATE
window.

Python's zlib does not expose inflatePrime, which the bit-position restore
needs, so libz is driven directly through ctypes.

An index entry is a (compressed byte offset, bit offset, output offset, 32 kB
window). The window dominates its size, which is the whole tradeoff: denser
checkpoints mean cheaper reads and a larger index.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import zlib
from dataclasses import dataclass

WINSIZE = 32768
Z_OK, Z_STREAM_END, Z_NEED_DICT, Z_BUF_ERROR = 0, 1, 2, -5
Z_NO_FLUSH, Z_FINISH, Z_BLOCK = 0, 4, 5

_lib = ctypes.CDLL(ctypes.util.find_library("z") or "libz.so.1")


class ZStream(ctypes.Structure):
    _fields_ = [
        ("next_in", ctypes.POINTER(ctypes.c_ubyte)),
        ("avail_in", ctypes.c_uint),
        ("total_in", ctypes.c_ulong),
        ("next_out", ctypes.POINTER(ctypes.c_ubyte)),
        ("avail_out", ctypes.c_uint),
        ("total_out", ctypes.c_ulong),
        ("msg", ctypes.c_char_p),
        ("state", ctypes.c_void_p),
        ("zalloc", ctypes.c_void_p),
        ("zfree", ctypes.c_void_p),
        ("opaque", ctypes.c_void_p),
        ("data_type", ctypes.c_int),
        ("adler", ctypes.c_ulong),
        ("reserved", ctypes.c_ulong),
    ]


_lib.inflateInit2_.argtypes = [ctypes.POINTER(ZStream), ctypes.c_int,
                               ctypes.c_char_p, ctypes.c_int]
_lib.inflate.argtypes = [ctypes.POINTER(ZStream), ctypes.c_int]
_lib.inflateEnd.argtypes = [ctypes.POINTER(ZStream)]
_lib.inflatePrime.argtypes = [ctypes.POINTER(ZStream), ctypes.c_int, ctypes.c_int]
_lib.inflateSetDictionary.argtypes = [ctypes.POINTER(ZStream),
                                      ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint]
_lib.zlibVersion.restype = ctypes.c_char_p

_VER = _lib.zlibVersion()
_SIZE = ctypes.sizeof(ZStream)


def _init(strm, window_bits):
    rc = _lib.inflateInit2_(ctypes.byref(strm), window_bits, _VER, _SIZE)
    if rc != Z_OK:
        raise RuntimeError(f"inflateInit2 failed: {rc}")


def _wrapped(comp: bytes) -> bool:
    """True if the stream carries a zlib header, as TIFF Deflate does."""
    return len(comp) > 1 and (comp[0] & 0x0F) == 8 and \
        ((comp[0] << 8) | comp[1]) % 31 == 0


@dataclass
class Checkpoint:
    in_byte: int      # compressed offset of the byte holding the restart point
    bits: int         # bits of that byte already consumed by the prior block
    out: int          # uncompressed offset this point corresponds to
    window: bytes     # the 32 kB of prior output


class TileIndex:
    """Restart points into one tile's DEFLATE stream."""

    def __init__(self, points: list[Checkpoint], total_out: int,
                 total_in: int, wrapped: bool):
        self.points = points
        self.total_out = total_out
        self.total_in = total_in
        self.wrapped = wrapped

    @property
    def index_bytes(self) -> int:
        # the window dominates; the offsets are a handful of bytes
        return sum(len(p.window) + 16 for p in self.points)

    def point_for(self, out_offset: int) -> Checkpoint | None:
        best = None
        for p in self.points:
            if p.out <= out_offset:
                best = p
            else:
                break
        return best


def build_index(comp: bytes, span: int) -> tuple[TileIndex, bytes]:
    """Decompress once, recording a restart point at the first DEFLATE block
    boundary after every `span` bytes of output. Returns the index and the
    full uncompressed data."""
    wrapped = _wrapped(comp)
    strm = ZStream()
    _init(strm, 15 if wrapped else -15)
    try:
        buf_in = (ctypes.c_ubyte * len(comp)).from_buffer_copy(comp)
        out_chunk = (ctypes.c_ubyte * WINSIZE)()
        strm.next_in = buf_in
        strm.avail_in = len(comp)

        out = bytearray()
        points: list[Checkpoint] = []
        last = 0
        while True:
            strm.next_out = out_chunk
            strm.avail_out = WINSIZE
            rc = _lib.inflate(ctypes.byref(strm), Z_BLOCK)
            if rc not in (Z_OK, Z_STREAM_END, Z_BUF_ERROR):
                raise RuntimeError(f"inflate failed: {rc}")
            produced = WINSIZE - strm.avail_out
            if produced:
                out += bytes(out_chunk[:produced])
            at_block = bool(strm.data_type & 128)
            last_block = bool(strm.data_type & 64)
            if (at_block and not last_block and len(out) >= WINSIZE
                    and (not points or len(out) - last >= span)):
                bits = strm.data_type & 7
                points.append(Checkpoint(
                    in_byte=int(strm.total_in), bits=bits, out=len(out),
                    window=bytes(out[-WINSIZE:])))
                last = len(out)
            if rc == Z_STREAM_END:
                break
            if produced == 0 and strm.avail_in == 0:
                break
        return TileIndex(points, len(out), int(strm.total_in), wrapped), bytes(out)
    finally:
        _lib.inflateEnd(ctypes.byref(strm))


def read_from(comp: bytes, idx: TileIndex, out_start: int, out_end: int) -> bytes:
    """Decompress output bytes [out_start, out_end) starting from the nearest
    checkpoint rather than from the beginning of the stream."""
    p = idx.point_for(out_start)
    if p is None:
        d = zlib.decompressobj(15 if idx.wrapped else -15)
        return d.decompress(comp, out_end)[out_start:out_end]

    strm = ZStream()
    _init(strm, -15)                      # raw: we are entering mid-stream
    try:
        off = p.in_byte - (1 if p.bits else 0)
        if p.bits:
            rc = _lib.inflatePrime(ctypes.byref(strm), p.bits,
                                   comp[off] >> (8 - p.bits))
            if rc != Z_OK:
                raise RuntimeError(f"inflatePrime failed: {rc}")
        win = (ctypes.c_ubyte * len(p.window)).from_buffer_copy(p.window)
        rc = _lib.inflateSetDictionary(ctypes.byref(strm), win, len(p.window))
        if rc != Z_OK:
            raise RuntimeError(f"inflateSetDictionary failed: {rc}")

        tail = comp[off + (1 if p.bits else 0):]
        buf_in = (ctypes.c_ubyte * len(tail)).from_buffer_copy(tail)
        strm.next_in = buf_in
        strm.avail_in = len(tail)

        want = out_end - p.out
        out = bytearray()
        chunk = (ctypes.c_ubyte * WINSIZE)()
        while len(out) < want:
            strm.next_out = chunk
            strm.avail_out = min(WINSIZE, want - len(out))
            rc = _lib.inflate(ctypes.byref(strm), Z_NO_FLUSH)
            produced = min(WINSIZE, want - len(out)) - strm.avail_out
            if produced:
                out += bytes(chunk[:produced])
            if rc == Z_STREAM_END:
                break
            if rc not in (Z_OK, Z_BUF_ERROR):
                raise RuntimeError(f"inflate failed: {rc}")
            if produced == 0 and strm.avail_in == 0:
                break
        return bytes(out[out_start - p.out:out_end - p.out])
    finally:
        _lib.inflateEnd(ctypes.byref(strm))


def fetch_bytes_for(comp: bytes, idx: TileIndex, out_start: int,
                    out_end: int) -> tuple[int, int]:
    """What a checkpointed reader actually costs to serve [out_start, out_end).

    Returns (compressed bytes fetched, uncompressed bytes decoded). The reader
    fetches from its checkpoint's byte offset up to wherever the target output
    completes, so both ends are tight rather than running to the end of the
    stream.
    """
    p = idx.point_for(out_start)
    if p is None:
        d = zlib.decompressobj(15 if idx.wrapped else -15)
        got = d.decompress(comp, out_end)
        return len(comp) - len(d.unconsumed_tail), len(got)

    strm = ZStream()
    _init(strm, -15)
    try:
        off = p.in_byte - (1 if p.bits else 0)
        if p.bits:
            _lib.inflatePrime(ctypes.byref(strm), p.bits,
                              comp[off] >> (8 - p.bits))
        win = (ctypes.c_ubyte * len(p.window)).from_buffer_copy(p.window)
        _lib.inflateSetDictionary(ctypes.byref(strm), win, len(p.window))
        tail = comp[off + (1 if p.bits else 0):]
        buf_in = (ctypes.c_ubyte * len(tail)).from_buffer_copy(tail)
        strm.next_in = buf_in
        strm.avail_in = len(tail)

        want = out_end - p.out
        done = 0
        chunk = (ctypes.c_ubyte * WINSIZE)()
        while done < want:
            take = min(WINSIZE, want - done)
            strm.next_out = chunk
            strm.avail_out = take
            rc = _lib.inflate(ctypes.byref(strm), Z_NO_FLUSH)
            done += take - strm.avail_out
            if rc == Z_STREAM_END:
                break
            if rc not in (Z_OK, Z_BUF_ERROR):
                break
            if strm.avail_in == 0 and take - strm.avail_out == 0:
                break
        # + the one byte holding the primed bits, if any
        return int(strm.total_in) + (1 if p.bits else 0), done
    finally:
        _lib.inflateEnd(ctypes.byref(strm))
