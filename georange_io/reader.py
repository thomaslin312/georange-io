#!/usr/bin/env python3
"""A sparse reader for COG archives.

Given a list of (file, x, y) requests it returns the pixel values, fetching far
less than any existing reader because it knows the whole request set before it
fetches the first byte. Three things follow from that:

  planning     requests are grouped so each block is fetched exactly once, in
               file order
  truncation   a block's DEFLATE stream is fetched and inflated only as far as
               the deepest request in it needs, not in full
  coalescing   runs of nearby blocks are merged into single requests, but only
               when the extra bytes cost less than the round trips they save
  restarting   where a .sbx sidecar exists, a block is entered at the nearest
               DEFLATE restart point rather than at byte zero, so neither the
               fetch nor the inflate has to begin at the start of the tile

It reads points or windows, at native resolution or any overview level. It
refuses anything it cannot serve correctly, and it validates the tile offsets
it was given against the object it is reading, for free, from the Content-Range
of the first response.

The coalescing rule is the part a general reader cannot have. Merging two
blocks means fetching everything between them, so it is worth it exactly when

    extra bytes / bandwidth  <  round trips saved x RTT

which needs both a cost model and knowledge of what is coming. GDAL has a fixed
byte-gap heuristic instead, and the bandwidth-capped sweep under results/ showed
the right answer flips when bandwidth is finite.

Everything it does not handle it declines, so a caller can fall back to GDAL.
Correctness is not negotiable: values must be identical to GDAL's, and
experiments/verify.py checks that against every read in a workload.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import ssl
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import numpy as np

from georange_io.tile_index import inflate_at  # noqa: E402
from georange_io.sbx import (Sbx, StaleSidecar,      # noqa: E402,F401
                            open_for as sbx_open_for)
from georange_io.cog import (CogIndex, RangeResult,  # noqa: E402
                             as_range_result)

SUPPORTED_COMPRESSION = {8, 32946}          # Deflate, Adobe Deflate
SUPPORTED_PREDICTOR = {1, 2, 3}


_log = logging.getLogger("georange_io")


class Unsupported(Exception):
    """Raised rather than returning values this reader cannot produce correctly.

    Everything here rests on being byte-identical to GDAL. A file with an
    encoding the partial-decode path does not implement must be refused, not
    guessed at: silently wrong pixels are far worse than an exception, and a
    caller can fall back to GDAL for whatever is refused."""


class TransportError(RuntimeError):
    """A remote server did not satisfy the validated range-read contract."""


class CorruptObject(TransportError):
    """The bytes fetched could not be decoded as the tile they claim to be.

    Raised instead of letting zlib's own exception escape: the README promises a
    caller only has to handle Unsupported, TransportError and ObjectChanged, and
    a bare zlib.error from a corrupt or truncated stream broke that contract.
    Found by randomized fuzzing of TIFF headers.
    """


class ObjectChanged(TransportError):
    """The source object changed between metadata and pixel range reads."""


class _HTTPStatusError(TransportError):
    def __init__(self, status: int, path: str, retryable: bool):
        super().__init__(f"HTTP {status} for {path}")
        self.status = status
        self.retryable = retryable


@dataclass
class Stats:
    requests: int = 0
    bytes_fetched: int = 0
    blocks: int = 0
    groups: int = 0
    undershoots: int = 0
    coalesced_extra_bytes: int = 0
    bytes_if_whole_blocks: int = 0
    blocks_from_checkpoint: int = 0
    checkpoint_window_bytes: int = 0
    stale_sidecars: int = 0
    invalid_sidecars: int = 0
    sidecars_loaded: int = 0
    sidecars_missing: int = 0
    delegated: int = 0
    retries: int = 0

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["byte_gain_vs_whole_blocks"] = (
            round(self.bytes_if_whole_blocks / self.bytes_fetched, 3)
            if self.bytes_fetched else None)
        d["blocks_per_request"] = (round(self.blocks / self.requests, 2)
                                   if self.requests else None)
        return d


@dataclass
class _Job:
    key: str
    level: int
    block: int
    offset: int
    count: int
    deepest_row: int = 0
    shallowest_row: int = 1 << 30
    prefix: int = 0                              # bytes this block alone needs
    start: int = 0                               # absolute offset to fetch from
    point: object = None                         # restart point, if any
    items: list = field(default_factory=list)    # (req_i, local_x, local_y)


@dataclass
class _Group:
    """One HTTP request covering one or more blocks."""
    key: str
    start: int
    length: int
    jobs: list


class _Pool:
    """Thread-local keep-alive connections. One TCP connection per worker, so
    the request count the proxy sees is not inflated by reconnects."""

    RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(self, base: str, timeout_s: float = 30.0,
                 retries: int = 3, backoff_s: float = 0.25,
                 headers: dict[str, str] | None = None,
                 headers_for=None,
                 ssl_context: ssl.SSLContext | None = None):
        u = urlparse(base)
        if u.scheme not in {"http", "https"} or not u.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        if u.query or u.fragment:
            raise ValueError("base_url cannot contain a query string or fragment")
        if u.username is not None or u.password is not None:
            raise ValueError(
                "credentials in base_url are not supported; use headers instead")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if retries < 0 or backoff_s < 0:
            raise ValueError("retries and backoff_s must be non-negative")
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.https = u.scheme == "https"
        self.base_path = u.path.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)
        self.headers = dict(headers or {})
        self.headers_for = headers_for
        self.ssl_context = ssl_context
        self.local = threading.local()
        self._connections: set = set()
        self._lock = threading.Lock()
        self._closed = False

    def _conn(self):
        if self._closed:
            raise RuntimeError("transport is closed")
        c = getattr(self.local, "conn", None)
        if c is None:
            cls = (http.client.HTTPSConnection if self.https
                   else http.client.HTTPConnection)
            kwargs = {"timeout": self.timeout_s}
            if self.https and self.ssl_context is not None:
                kwargs["context"] = self.ssl_context
            c = self.local.conn = cls(self.host, self.port, **kwargs)
            with self._lock:
                self._connections.add(c)
        return c

    def _discard(self, connection) -> None:
        try:
            connection.close()
        except Exception:
            pass
        with self._lock:
            self._connections.discard(connection)
        if getattr(self.local, "conn", None) is connection:
            self.local.conn = None

    @staticmethod
    def _identity(response) -> str | None:
        version = response.getheader("x-amz-version-id")
        if version and version != "null":
            return f"version-id:{version}"
        etag = response.getheader("ETag")
        if etag:
            return f"etag:{etag.strip()}"
        return None

    def get_range(self, path: str, start: int, length: int):
        """Returns (body, total object size or None).

        The total comes out of Content-Range at no extra cost, which is what
        lets the reader check that the tile offsets it was handed still
        describe the object it is actually reading."""
        if start < 0 or length <= 0:
            raise ValueError(f"invalid byte range: start={start}, length={length}")
        requested_end = start + length - 1
        request_path = self.base_path + "/" + path.lstrip("/")
        hdrs = dict(self.headers)
        if self.headers_for is not None:
            dynamic = self.headers_for(request_path, start, requested_end)
            if dynamic:
                hdrs.update(dict(dynamic))
        hdrs.update({"Range": f"bytes={start}-{requested_end}",
                     "Accept-Encoding": "identity"})
        attempts = 0
        for attempt in range(self.retries + 1):
            attempts += 1
            c = self._conn()
            try:
                c.request("GET", request_path, headers=hdrs)
                r = c.getresponse()
                data = r.read()
                if r.status != 206:
                    raise _HTTPStatusError(
                        r.status, request_path, r.status in self.RETRY_STATUSES)
                cr = r.getheader("Content-Range")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", cr or "")
                if match is None:
                    raise TransportError(
                        f"invalid Content-Range {cr!r} for {request_path}")
                got_start, got_end, total = map(int, match.groups())
                expected_end = min(requested_end, total - 1)
                if (got_start != start or got_end != expected_end or
                        len(data) != got_end - got_start + 1):
                    raise TransportError(
                        f"unexpected range for {request_path}: requested "
                        f"{start}-{requested_end}, received {got_start}-{got_end} "
                        f"with {len(data)} bytes")
                return RangeResult(data, total, self._identity(r), attempts)
            except Exception as exc:
                self._discard(c)
                retryable = not isinstance(exc, _HTTPStatusError) or exc.retryable
                if attempt >= self.retries or not retryable:
                    raise
                if self.backoff_s:
                    time.sleep(self.backoff_s * (2 ** attempt))
        raise RuntimeError("unreachable")

    def close(self):
        with self._lock:
            self._closed = True
            connections = list(self._connections)
            self._connections.clear()
        for c in connections:
            try:
                c.close()
            except Exception:
                pass
        self.local.conn = None


class SparseReader:
    def __init__(self, index=None, base_url: str = "", bucket: str = "",
                 margin: float = 0.03, min_fetch: int = 16384,
                 rtt_s: float = 0.05, bandwidth_mbps: float = 100.0,
                 workers: int = 1, sbx_dir: str | Path | None = None,
                 gdal_path_for=None, pool=None, timeout_s: float = 30.0,
                 retries: int = 3, backoff_s: float = 0.25,
                 headers: dict[str, str] | None = None,
                 headers_for=None,
                 ssl_context: ssl.SSLContext | None = None,
                 content_identities: dict[str, str] | None = None,
                 max_fetch_bytes: int = 128 << 20,
                 max_output_bytes: int = 1 << 30,
                 max_block_bytes: int = 64 << 20):
        if margin < 0 or min_fetch <= 0:
            raise ValueError("margin must be non-negative and min_fetch positive")
        if rtt_s < 0 or bandwidth_mbps < 0:
            raise ValueError("rtt_s and bandwidth_mbps must be non-negative")
        if (max_fetch_bytes <= 0 or max_output_bytes <= 0 or
                max_block_bytes <= 0):
            raise ValueError("fetch, output and block limits must be positive")
        if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
            raise ValueError("workers must be a positive integer")
        self.bucket = bucket
        # an injectable transport keeps the reader testable without a network,
        # an object store, or a staged corpus
        self.pool = pool if pool is not None else _Pool(
            base_url, timeout_s=timeout_s, retries=retries,
            backoff_s=backoff_s, headers=headers, headers_for=headers_for,
            ssl_context=ssl_context)
        # index may be a prebuilt mapping, a path to one, or nothing at all;
        # anything it does not cover is described from the object's own header
        seed = None
        if isinstance(index, (str, Path)):
            seed = json.loads(Path(index).read_text())
        elif isinstance(index, dict):
            seed = index
        if index is not None and not isinstance(index, (str, Path, dict)):
            self.idx = index
        else:
            self.idx = CogIndex(pool=self.pool,
                                path_for=self._object_path, seed=seed)
        self.margin = margin
        self.min_fetch = min_fetch
        self.workers = workers
        self.max_fetch_bytes = int(max_fetch_bytes)
        self.max_output_bytes = int(max_output_bytes)
        self.max_block_bytes = int(max_block_bytes)
        self.content_identities = dict(content_identities or {})
        if any(not isinstance(k, str) or not isinstance(v, str) or not v or
               len(v.encode("utf-8")) > 4096
               for k, v in self.content_identities.items()):
            raise ValueError("content identities must be non-empty strings")
        # bandwidth-delay product: the bytes worth spending to save one round
        # trip. Zero disables coalescing, infinity merges everything in a file.
        self.merge_budget = (rtt_s * bandwidth_mbps * 1e6 / 8.0
                             if bandwidth_mbps > 0 else float("inf"))
        self.gdal_path_for = gdal_path_for or (
            lambda k: f"/vsis3/{bucket}/{k}")
        self.stats = Stats()
        self._lock = threading.Lock()
        self.sbx_dir = Path(sbx_dir) if sbx_dir else None
        self._sbx: dict[str, object] = {}
        self._sidecar_warned = False
        self._sbx_lock = threading.Lock()
        self._index_requests_accounted = 0
        self._index_bytes_accounted = 0
        self._index_retries_accounted = 0
        self._operation_lock = threading.RLock()
        self._closed = False

    def _object_path(self, key: str) -> str:
        return "/" + quote(f"{self.bucket}/{key}".lstrip("/"), safe="/")

    def _sync_index_stats(self) -> None:
        """Include on-demand COG header traffic in the public counters."""
        requests = int(getattr(self.idx, "header_requests", 0))
        nbytes = int(getattr(self.idx, "header_bytes", 0))
        retries = int(getattr(self.idx, "header_retries", 0))
        with self._lock:
            self.stats.requests += requests - self._index_requests_accounted
            self.stats.bytes_fetched += nbytes - self._index_bytes_accounted
            # Header traffic is unavoidable in both the sparse and whole-tile
            # paths, so include it on both sides of this diagnostic ratio.
            self.stats.bytes_if_whole_blocks += (
                nbytes - self._index_bytes_accounted)
            self._index_requests_accounted = requests
            self._index_bytes_accounted = nbytes
            self.stats.retries += retries - self._index_retries_accounted
            self._index_retries_accounted = retries

    def _sidecar(self, key: str):
        if self.sbx_dir is None:
            return None
        with self._sbx_lock:
            if key not in self._sbx:
                p = self.sbx_dir / (key + ".sbx")
                if not p.exists():
                    with self._lock:
                        self.stats.sidecars_missing += 1
                    self._sbx[key] = None
                else:
                    try:
                        rec = self.idx[key]
                        content_identity = self.content_identities.get(key)
                        if content_identity is not None:
                            rec = dict(rec)
                            rec["content_identity"] = content_identity
                        self._sbx[key] = sbx_open_for(p, rec)
                        with self._lock:
                            self.stats.sidecars_loaded += 1
                    except StaleSidecar as e:
                        # a sidecar built for a different object would decode
                        # cleanly and return wrong pixels, so drop it loudly
                        with self._lock:
                            self.stats.stale_sidecars += 1
                        _log.warning("ignoring stale sidecar for %s: %s", key, e)
                        self._sbx[key] = None
                    except Exception as e:
                        with self._lock:
                            self.stats.invalid_sidecars += 1
                        _log.warning("ignoring invalid sidecar for %s: %s", key, e)
                        self._sbx[key] = None
            return self._sbx[key]

    def _disable_sidecar(self, key: str, sidecar, error: Exception) -> None:
        with self._sbx_lock:
            if self._sbx.get(key) is sidecar:
                self._sbx[key] = None
        try:
            sidecar.close()
        except Exception:
            pass
        with self._lock:
            self.stats.invalid_sidecars += 1
        _log.warning("disabling invalid sidecar for %s: %s", key, error)

    # -- capability check --------------------------------------------------
    def supports(self, key: str, level: int = 0) -> bool:
        rec = self.idx.get(key)
        if not rec or not rec.get("levels"):
            return False
        return not self.why_unsupported(key, level)

    def _invalid_level_metadata(self, rec: dict, L: dict) -> str | None:
        """Return a reason instead of trusting a malformed TIFF/index record."""
        try:
            size = int(rec["size"])
            width, height = int(L["width"]), int(L["height"])
            blockw, blockh = int(L["blockw"]), int(L["blockh"])
            nbx, nby = int(L["nbx"]), int(L["nby"])
            dt = np.dtype(L["dtype"])
            offsets = L["offsets"]
            counts = L["bytecounts"]
        except (KeyError, TypeError, ValueError) as exc:
            return f"invalid metadata ({exc})"
        if min(size, width, height, blockw, blockh, nbx, nby) <= 0:
            return "invalid non-positive size or dimension metadata"
        if nbx != (width + blockw - 1) // blockw:
            return "tile-column count does not match image width"
        if nby != (height + blockh - 1) // blockh:
            return "tile-row count does not match image height"
        expected = nbx * nby
        if not isinstance(offsets, list) or not isinstance(counts, list):
            return "tile offsets and byte counts must be lists"
        if len(offsets) != expected or len(counts) != expected:
            return f"tile table length does not match {nbx}x{nby} grid"
        block_bytes = blockw * blockh * dt.itemsize
        if block_bytes > self.max_block_bytes:
            return (f"uncompressed block size {block_bytes:,} exceeds "
                    f"max_block_bytes={self.max_block_bytes:,}")
        for offset, count in zip(offsets, counts):
            if (not isinstance(offset, int) or isinstance(offset, bool) or
                    not isinstance(count, int) or isinstance(count, bool)):
                return "tile offsets and byte counts must be integers"
            if count < 0 or offset < 0 or (count and offset + count > size):
                return "tile range lies outside the source object"
        return None

    def why_unsupported(self, key: str, level: int = 0) -> str | None:
        rec = self.idx.get(key)
        if not rec or not rec.get("levels"):
            error = getattr(self.idx, "errors", {}).get(key)
            if error is not None:
                return (f"description failed ({type(error).__name__}: "
                        f"{error})")
            return "not in the index"
        if not isinstance(level, int) or level < 0 or level >= len(rec["levels"]):
            return f"no level {level}"
        L = rec["levels"][level]
        invalid = self._invalid_level_metadata(rec, L)
        if invalid:
            return invalid
        compression = L.get("compression")
        if compression not in SUPPORTED_COMPRESSION:
            return f"compression {compression} is not Deflate"
        if L.get("predictor", 1) not in SUPPORTED_PREDICTOR:
            return f"predictor {L.get('predictor')} unhandled"
        if L.get("samples", 1) != 1:
            return f"{L.get('samples')} samples per pixel"
        if L.get("planar", 1) != 1:
            return "planar configuration is not contiguous"
        # absence is not evidence of little-endian; an index built before this
        # field existed must be refused rather than assumed
        bo = L.get("byteorder", rec.get("byteorder"))
        if bo != "<":
            return f"byte order {bo!r} is not little-endian"
        dt = np.dtype(L["dtype"])
        predictor = L.get("predictor", 1)
        if predictor == 2 and not np.issubdtype(dt, np.integer):
            return f"predictor 2 requires an integer dtype, not {dt}"
        if predictor == 3 and not np.issubdtype(dt, np.floating):
            return f"predictor 3 requires a floating dtype, not {dt}"
        return None

    def split(self, requests):
        """Partition requests into those this reader can serve and those a
        caller must hand to GDAL."""
        ok, no = [], []
        decisions = {}
        for i, r in enumerate(requests):
            key, level, *_ = self._norm(r)
            pair = (key, level)
            if pair not in decisions:
                decisions[pair] = self.supports(key, level)
            decision = decisions[pair]
            (ok if decision else no).append(i)
        return ok, no

    # -- planning ----------------------------------------------------------
    def _plan_fetch(self, key: str, job: _Job) -> None:
        """Decide where this block's fetch starts and how long it should be."""
        L = self.idx[key]["levels"][job.level]
        dt = np.dtype(L["dtype"])
        row_bytes = L["blockw"] * dt.itemsize
        target_out = (job.deepest_row + 1) * row_bytes
        uncomp = L["blockh"] * row_bytes

        # The restart point must sit at or before the *shallowest* row the
        # block is asked for, not the deepest, or the earlier rows fall before
        # the window and cannot be reconstructed.
        first_out = job.shallowest_row * row_bytes
        pt = None
        # sidecars are built for native resolution only
        sc = self._sidecar(key) if job.level == 0 else None
        if sc is not None and job.block in sc:
            try:
                pt = sc.best(job.block, first_out)
                if pt is not None and not (
                        0 <= pt.bits <= 7 and 0 <= pt.in_byte <= job.count and
                        0 <= pt.out <= uncomp and len(pt.window) <= 32768):
                    raise ValueError(
                        f"{key} block {job.block}: checkpoint is out of bounds")
            except Exception as exc:
                self._disable_sidecar(key, sc, exc)
                pt = None
        if pt is not None and pt.out > 0:
            job.point = pt
            job.start = job.offset + pt.in_byte - (1 if pt.bits else 0)
            frac = (target_out - pt.out) / uncomp + self.margin
            avail = job.offset + job.count - job.start
            job.prefix = int(min(avail, max(self.min_fetch, job.count * frac)))
            with self._lock:
                self.stats.blocks_from_checkpoint += 1
                self.stats.checkpoint_window_bytes += len(pt.window)
        else:
            job.start = job.offset
            frac = target_out / uncomp + self.margin
            job.prefix = int(min(job.count,
                                 max(self.min_fetch, job.count * frac)))

    @staticmethod
    def _norm(r):
        """(key,x,y) | (key,x,y,w,h) | (key,level,x,y,w,h) -> uniform tuple."""
        if len(r) == 3:
            norm = r[0], 0, r[1], r[2], 1, 1
        elif len(r) == 5:
            norm = r[0], 0, r[1], r[2], r[3], r[4]
        elif len(r) == 6:
            norm = tuple(r)
        else:
            raise ValueError(f"unrecognised request shape: {r!r}")
        key, *numbers = norm
        if not isinstance(key, str) or not key:
            raise ValueError("request key must be a non-empty string")
        if any(not isinstance(v, (int, np.integer)) or isinstance(v, bool)
               for v in numbers):
            raise ValueError("request coordinates and dimensions must be integers")
        return (key, *(int(v) for v in numbers))

    def plan(self, requests) -> list[_Job]:
        groups: dict[tuple[str, int, int], _Job] = {}
        for i, raw in enumerate(requests):
            key, lvl, x, y, w, h = self._norm(raw)
            L = self.idx[key]["levels"][lvl]
            x0 = max(0, x); y0 = max(0, y)
            x1 = min(L["width"], x + w); y1 = min(L["height"], y + h)
            if x1 <= x0 or y1 <= y0:
                continue
            for by in range(y0 // L["blockh"], (y1 - 1) // L["blockh"] + 1):
                for bx in range(x0 // L["blockw"], (x1 - 1) // L["blockw"] + 1):
                    bi = by * L["nbx"] + bx
                    k = (key, lvl, bi)
                    j = groups.get(k)
                    if j is None:
                        j = groups[k] = _Job(key=key, level=lvl, block=bi,
                                             offset=L["offsets"][bi],
                                             count=L["bytecounts"][bi])
                    ox, oy = bx * L["blockw"], by * L["blockh"]
                    lx0 = max(x0, ox) - ox
                    ly0 = max(y0, oy) - oy
                    lx1 = min(x1, ox + L["blockw"]) - ox
                    ly1 = min(y1, oy + L["blockh"]) - oy
                    # destination offset within this request's output array
                    j.items.append((i, ly0, ly1, lx0, lx1,
                                    (oy + ly0) - y, (ox + lx0) - x))
                    j.deepest_row = max(j.deepest_row, ly1 - 1)
                    j.shallowest_row = min(j.shallowest_row, ly0)
        for j in groups.values():
            if j.count:
                self._plan_fetch(j.key, j)
        return sorted(groups.values(), key=lambda j: (j.key, j.start))

    def coalesce(self, jobs: list[_Job]) -> list[_Group]:
        """Merge runs of blocks in the same file while the extra bytes stay
        under the bandwidth-delay budget for the round trips saved."""
        out: list[_Group] = []
        run: list[_Job] = []

        def close():
            if not run:
                return
            start = run[0].start
            end = run[-1].start + run[-1].prefix
            out.append(_Group(run[0].key, start, end - start, list(run)))
            sep = sum(j.prefix for j in run)
            with self._lock:
                self.stats.coalesced_extra_bytes += (end - start) - sep
            run.clear()

        for j in jobs:
            if j.count == 0:                       # sparse tile, no fetch
                continue
            if j.prefix > self.max_fetch_bytes:
                raise Unsupported(
                    f"{j.key} block {j.block}: planned range {j.prefix:,} "
                    f"exceeds max_fetch_bytes={self.max_fetch_bytes:,}")
            if (not run or j.key != run[0].key or
                    j.level != run[0].level):
                close()
                run.append(j)
                continue
            start = run[0].start
            merged = (j.start + j.prefix) - start
            separate = sum(x.prefix for x in run) + j.prefix
            saved = len(run)                       # round trips saved by merging
            if (merged <= self.max_fetch_bytes and
                    merged - separate <= self.merge_budget * saved):
                run.append(j)
            else:
                close()
                run.append(j)
        close()
        return out

    # -- fetch and decode --------------------------------------------------
    def _inflate(self, key: str, comp: bytes, need_out: int,
                 job: _Job, have: int) -> bytes:
        """Inflate to need_out, extending the fetch if the guess fell short."""
        try:
            d = zlib.decompressobj(15)
            out = bytearray()
            feed = comp
            got = have
            while len(out) < need_out:
                chunk = d.decompress(feed, need_out - len(out))
                out += chunk
                feed = d.unconsumed_tail
                if len(out) >= need_out or feed:
                    continue
                if got >= job.count:
                    break
                with self._lock:
                    self.stats.undershoots += 1
                extra = self._fetch(key, job.offset + got, job.count - got)
                feed = extra
                got = job.count
            return bytes(out)
        except zlib.error as e:
            # a corrupt or truncated tile stream must surface as one of the
            # library's declared exceptions, not as zlib's own
            raise CorruptObject(
                f"{key}: tile data could not be decompressed: {e}") from e

    def _inflate_from(self, key: str, comp: bytes, job: _Job,
                      need_rel: int) -> bytes:
        """Inflate from this block's restart point, refetching the rest of the
        block if the speculative range fell short."""
        pt = job.point
        try:
            raw = inflate_at(comp, pt.bits, pt.window, need_rel)
        except zlib.error as e:
            raise CorruptObject(
                f"{key}: tile data could not be decompressed from a restart "
                f"point: {e}") from e
        if len(raw) < need_rel:
            with self._lock:
                self.stats.undershoots += 1
            end = job.offset + job.count
            have = job.start + len(comp)
            if have < end:
                comp = comp + self._fetch(key, have, end - have)
                raw = inflate_at(comp, pt.bits, pt.window, need_rel)
        return raw

    def _fetch(self, key: str, start: int, length: int) -> bytes:
        response = as_range_result(
            self.pool.get_range(self._object_path(key), start, length))
        data, total = response
        rec = self.idx[key]
        expected_identity = rec.get("object_identity")
        if expected_identity is not None:
            if response.object_identity is None:
                raise ObjectChanged(
                    f"{key}: range response omitted the object identity "
                    "observed while reading its header")
            if response.object_identity != expected_identity:
                raise ObjectChanged(
                    f"{key}: object changed between header and pixel reads")
        if total is not None:
            want = int(rec.get("size", total))
            if total != want:
                raise Unsupported(
                    f"{key}: the index describes a {want:,}-byte object but "
                    f"the store returned {total:,} bytes; the tile offsets do "
                    "not belong to this file")
        with self._lock:
            self.stats.requests += response.attempts
            self.stats.retries += response.attempts - 1
            self.stats.bytes_fetched += len(data)
        return data

    @staticmethod
    def _undo_row(raw: bytes, dt: np.dtype, width: int, predictor: int):
        if predictor == 1:
            return np.frombuffer(raw, dtype=dt, count=width)
        if predictor == 2:
            v = np.frombuffer(raw, dtype=dt, count=width)
            return np.cumsum(v.astype(np.int64)).astype(dt)
        # predictor 3: byte planes, most significant first, differenced across
        # the row's byte stream
        b = np.frombuffer(raw, dtype=np.uint8, count=width * dt.itemsize)
        b = np.cumsum(b.astype(np.int64)).astype(np.uint8)
        planes = b.reshape(dt.itemsize, width)
        return np.ascontiguousarray(planes.T).tobytes()

    def _run_group(self, g: _Group, out) -> None:
        buf = self._fetch(g.key, g.start, g.length)
        rec = self.idx[g.key]
        for job in g.jobs:
            L = rec["levels"][job.level]
            dt = np.dtype(L["dtype"])
            width = L["blockw"]
            row_bytes = width * dt.itemsize
            pred = L.get("predictor", 1)
            lo = job.start - g.start
            hi = min(len(buf), (job.offset + job.count) - g.start)
            comp = buf[lo:hi]
            target_out = (job.deepest_row + 1) * row_bytes
            if job.point is not None:
                base = job.point.out
                raw = self._inflate_from(g.key, comp, job, target_out - base)
            else:
                base = 0
                raw = self._inflate(g.key, comp, target_out, job,
                                    have=len(comp))
            # decode each needed row once, then slice it for every item
            lo_row, hi_row = job.shallowest_row, job.deepest_row
            rows = {}
            for ly in range(lo_row, hi_row + 1):
                off = ly * row_bytes - base
                row = raw[off:off + row_bytes]
                if len(row) < row_bytes:
                    raise RuntimeError(
                        f"{g.key} block {job.block}: short inflate, "
                        f"{len(row)} of {row_bytes} bytes for row {ly}")
                vals = self._undo_row(row, dt, width, pred)
                rows[ly] = (np.frombuffer(vals, dtype=dt.newbyteorder(">"),
                                          count=width) if pred == 3 else vals)
            for (i, ly0, ly1, lx0, lx1, dy0, dx0) in job.items:
                dst = out[i]
                for ly in range(ly0, ly1):
                    dst[dy0 + (ly - ly0), dx0:dx0 + (lx1 - lx0)] = \
                        rows[ly][lx0:lx1]

    # -- public ------------------------------------------------------------
    def read(self, requests, allow_partial: bool = False) -> list:
        """Read windows. Accepts (key, x, y, w, h) or (key, level, x, y, w, h)
        and returns one 2-D array per request.

        A window not wholly inside the level is refused by default. Quietly
        returning zeros for the part that does not exist is the same class of
        mistake as decoding a file we do not understand: the caller gets an
        array that looks valid. Pass allow_partial=True to opt into zero fill.
        """
        with self._operation_lock:
            if self._closed:
                raise RuntimeError("reader is closed")
            return self._read(requests, allow_partial)

    def _read(self, requests, allow_partial: bool) -> list:
        norm = [self._norm(r) for r in requests]
        bad = {}
        for key, level in {(r[0], r[1]) for r in norm}:
            why = self.why_unsupported(key, level)
            if why:
                bad[(key, level)] = why
        self._sync_index_stats()
        if bad:
            first = list(bad.items())[:3]
            raise Unsupported(
                f"{len(bad)} file level(s) cannot be served correctly by this "
                f"reader; use split() and fall back to GDAL. "
                + "; ".join(f"{k} level {lv}: {v}"
                            for (k, lv), v in first))
        for (k, lv, x, y, w, h) in norm:
            if w <= 0 or h <= 0:
                raise Unsupported(
                    f"{k} level {lv}: window width and height must be "
                    f"positive, got {w}x{h}")
            if not allow_partial:
                L = self.idx[k]["levels"][lv]
                if x < 0 or y < 0 or x + w > L["width"] or y + h > L["height"]:
                    raise Unsupported(
                        f"{k} level {lv}: window ({x},{y},{w},{h}) is not "
                        f"inside {L['width']}x{L['height']}; pass "
                        "allow_partial=True to zero-fill instead")
        output_bytes = sum(
            h * w * np.dtype(self.idx[k]["levels"][lv]["dtype"]).itemsize
            for (k, lv, _x, _y, w, h) in norm)
        if output_bytes > self.max_output_bytes:
            raise Unsupported(
                f"requested output is {output_bytes:,} bytes, exceeding "
                f"max_output_bytes={self.max_output_bytes:,}")
        out = [np.zeros((h, w), np.dtype(
                   self.idx[k]["levels"][lv]["dtype"]))
               for (k, lv, _x, _y, w, h) in norm]
        jobs = self.plan(norm)
        groups = self.coalesce(jobs)
        self.stats.blocks += len([j for j in jobs if j.count])
        self.stats.groups += len(groups)
        self.stats.bytes_if_whole_blocks += sum(j.count for j in jobs)

        for j in jobs:                              # sparse tiles need no fetch
            if j.count == 0:
                fill = self.idx[j.key].get("nodata") or 0
                for (i, ly0, ly1, lx0, lx1, dy0, dx0) in j.items:
                    out[i][dy0:dy0 + (ly1 - ly0),
                           dx0:dx0 + (lx1 - lx0)] = fill

        if self.workers == 1:
            for g in groups:
                self._run_group(g, out)
        else:
            with ThreadPoolExecutor(self.workers) as ex:
                list(ex.map(lambda g: self._run_group(g, out), groups))
        self._warn_if_sidecars_unused()
        return out

    def _warn_if_sidecars_unused(self) -> None:
        """A configured sidecar that never gets used is almost always a bug.

        It is indistinguishable from a working one at the call site: correct
        values, no error, and only a zero in blocks_from_checkpoint to show for
        it. An entire audit cycle reported a sidecar speedup that was not
        happening, because the sidecars had been built from a different
        workload spec and covered none of the blocks being read.
        """
        if self.sbx_dir is None or self._sidecar_warned:
            return
        st = self.stats
        if st.blocks_from_checkpoint or not st.blocks:
            return
        self._sidecar_warned = True
        if st.sidecars_loaded:
            reason = (f"{st.sidecars_loaded} sidecar(s) loaded but none held a "
                      "restart point for the blocks read; they were probably "
                      "built for a different set of pixels")
        elif st.stale_sidecars or st.invalid_sidecars:
            reason = (f"{st.stale_sidecars} stale and {st.invalid_sidecars} "
                      "invalid sidecar(s) were refused")
        else:
            reason = f"no sidecar file was found for any of the objects read"
        _log.warning(
            "sidecar directory %s is configured but no block was served from a "
            "restart point: %s. Reads are correct, just not accelerated.",
            self.sbx_dir, reason)

    def read_any(self, requests, allow_partial: bool = False) -> list:
        """Read everything, delegating whatever this reader cannot decode.

        Anything refused goes to GDAL, so a caller gets one uniform result and
        never has to know which files took the fast path. Requires rasterio.
        """
        with self._operation_lock:
            if self._closed:
                raise RuntimeError("reader is closed")
            return self._read_any(requests, allow_partial)

    def _read_any(self, requests, allow_partial: bool) -> list:
        norm = [self._norm(r) for r in requests]
        if any(w <= 0 or h <= 0 for _k, _lv, _x, _y, w, h in norm):
            raise Unsupported("window width and height must be positive")
        conservative_bytes = sum(w * h * 8 for _k, _lv, _x, _y, w, h in norm)
        if conservative_bytes > self.max_output_bytes:
            raise Unsupported(
                f"requested output may exceed max_output_bytes="
                f"{self.max_output_bytes:,}")
        ok, no = self.split(norm)
        out: list = [None] * len(norm)
        if ok:
            for slot, arr in zip(ok, self.read([norm[i] for i in ok],
                                               allow_partial=allow_partial)):
                out[slot] = arr
        if no:
            import rasterio
            from rasterio.windows import Window
            cache: dict = {}
            try:
                for i in no:
                    key, lvl, x, y, w, h = norm[i]
                    ck = (key, lvl)
                    ds = cache.get(ck)
                    if ds is None:
                        kw = {} if lvl == 0 else {"OVERVIEW_LEVEL": lvl - 1}
                        ds = cache[ck] = rasterio.open(
                            self.gdal_path_for(key), **kw)
                    out[i] = ds.read(1, window=Window(x, y, w, h),
                                     boundless=allow_partial, fill_value=0)
                    with self._lock:
                        self.stats.delegated += 1
            finally:
                for d in cache.values():
                    try:
                        d.close()
                    except Exception:
                        pass
        return out

    def sample(self, points) -> np.ndarray:
        """Read single pixels. points are (key, x, y); returns one value each."""
        arrs = self.read([(k, x, y, 1, 1) for (k, x, y) in points])
        return np.array([a[0, 0] for a in arrs])

    def close(self) -> None:
        """Close sidecars and every transport connection owned by the reader."""
        with self._operation_lock:
            if self._closed:
                return
            self._closed = True
            with self._sbx_lock:
                sidecars = [v for v in self._sbx.values() if v is not None]
                self._sbx.clear()
            for sidecar in sidecars:
                try:
                    sidecar.close()
                except Exception:
                    pass
            close = getattr(self.pool, "close", None)
            if close is not None:
                close()

    def __enter__(self):
        if self._closed:
            raise RuntimeError("reader is closed")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
