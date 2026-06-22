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

The coalescing rule is the part a general reader cannot have. Merging two
blocks means fetching everything between them, so it is worth it exactly when

    extra bytes / bandwidth  <  round trips saved x RTT

which needs both a cost model and knowledge of what is coming. GDAL has a fixed
byte-gap heuristic instead, and the bandwidth-capped sweep in REPORT.md showed
the right answer flips when bandwidth is finite.

Everything it does not handle it declines, so a caller can fall back to GDAL.
Correctness is not negotiable: values must be identical to GDAL's, and
georange_io/verify.py checks that against every read in a workload.
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "baseline") not in sys.path:
    sys.path.insert(0, str(_ROOT / "baseline"))
from tile_index import inflate_at            # noqa: E402
from georange_io.sbx import Sbx               # noqa: E402

SUPPORTED_COMPRESSION = {8, 32946}          # Deflate, Adobe Deflate
SUPPORTED_PREDICTOR = {1, 2, 3}


class Unsupported(Exception):
    """Raised rather than returning values this reader cannot produce correctly.

    Everything here rests on being byte-identical to GDAL. A file with an
    encoding the partial-decode path does not implement must be refused, not
    guessed at: silently wrong pixels are far worse than an exception, and a
    caller can fall back to GDAL for whatever is refused."""


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

    def __init__(self, base: str):
        u = urlparse(base)
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.https = u.scheme == "https"
        self.local = threading.local()

    def _conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            cls = (http.client.HTTPSConnection if self.https
                   else http.client.HTTPConnection)
            c = self.local.conn = cls(self.host, self.port, timeout=300)
        return c

    def get_range(self, path: str, start: int, length: int) -> bytes:
        hdrs = {"Range": f"bytes={start}-{start+length-1}",
                "Accept-Encoding": "identity"}
        for attempt in (0, 1):
            c = self._conn()
            try:
                c.request("GET", path, headers=hdrs)
                r = c.getresponse()
                data = r.read()
                if r.status not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status} for {path}")
                return data
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
                self.local.conn = None
                if attempt:
                    raise
        raise RuntimeError("unreachable")

    def close(self):
        c = getattr(self.local, "conn", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            self.local.conn = None


class SparseReader:
    def __init__(self, index_path: str | Path, base_url: str, bucket: str,
                 margin: float = 0.03, min_fetch: int = 16384,
                 rtt_s: float = 0.05, bandwidth_mbps: float = 100.0,
                 workers: int = 1, sbx_dir: str | Path | None = None):
        self.idx = json.loads(Path(index_path).read_text())
        self.bucket = bucket
        self.margin = margin
        self.min_fetch = min_fetch
        self.workers = max(1, workers)
        # bandwidth-delay product: the bytes worth spending to save one round
        # trip. Zero disables coalescing, infinity merges everything in a file.
        self.merge_budget = (rtt_s * bandwidth_mbps * 1e6 / 8.0
                             if bandwidth_mbps > 0 else float("inf"))
        self.pool = _Pool(base_url)
        self.stats = Stats()
        self._lock = threading.Lock()
        self.sbx_dir = Path(sbx_dir) if sbx_dir else None
        self._sbx: dict[str, object] = {}
        self._sbx_lock = threading.Lock()

    def _sidecar(self, key: str):
        if self.sbx_dir is None:
            return None
        with self._sbx_lock:
            if key not in self._sbx:
                p = self.sbx_dir / (key + ".sbx")
                try:
                    self._sbx[key] = Sbx(p) if p.exists() else None
                except Exception:
                    self._sbx[key] = None
            return self._sbx[key]

    # -- capability check --------------------------------------------------
    def supports(self, key: str) -> bool:
        rec = self.idx.get(key)
        if not rec or not rec.get("levels"):
            return False
        return not self.why_unsupported(key)

    def why_unsupported(self, key: str) -> str | None:
        rec = self.idx.get(key)
        if not rec or not rec.get("levels"):
            return "not in the index"
        L = rec["levels"][0]
        if L["compression"] not in SUPPORTED_COMPRESSION:
            return f"compression {L['compression']} is not Deflate"
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
        return None

    def split(self, requests):
        """Partition requests into those this reader can serve and those a
        caller must hand to GDAL."""
        ok, no = [], []
        for i, r in enumerate(requests):
            (ok if self.supports(r[0]) else no).append(i)
        return ok, no

    # -- planning ----------------------------------------------------------
    def _plan_fetch(self, key: str, job: _Job) -> None:
        """Decide where this block's fetch starts and how long it should be."""
        L = self.idx[key]["levels"][0]
        dt = np.dtype(L["dtype"])
        row_bytes = L["blockw"] * dt.itemsize
        target_out = (job.deepest_row + 1) * row_bytes
        uncomp = L["blockh"] * row_bytes

        # The restart point must sit at or before the *shallowest* row the
        # block is asked for, not the deepest, or the earlier rows fall before
        # the window and cannot be reconstructed.
        first_out = job.shallowest_row * row_bytes
        pt = None
        sc = self._sidecar(key)
        if sc is not None and job.block in sc:
            pt = sc.best(job.block, first_out)
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
                                     count=L["bytecounts"][bi])
            ly = y - by * L["blockh"]
            j.items.append((i, x - bx * L["blockw"], ly))
            if ly > j.deepest_row:
                j.deepest_row = ly
            if ly < j.shallowest_row:
                j.shallowest_row = ly
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
            if not run or j.key != run[0].key:
                close()
                run.append(j)
                continue
            start = run[0].start
            merged = (j.start + j.prefix) - start
            separate = sum(x.prefix for x in run) + j.prefix
            saved = len(run)                       # round trips saved by merging
            if merged - separate <= self.merge_budget * saved:
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

    def _inflate_from(self, key: str, comp: bytes, job: _Job,
                      need_rel: int) -> bytes:
        """Inflate from this block's restart point, refetching the rest of the
        block if the speculative range fell short."""
        pt = job.point
        raw = inflate_at(comp, pt.bits, pt.window, need_rel)
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
        data = self.pool.get_range(f"/{self.bucket}/{key}", start, length)
        with self._lock:
            self.stats.requests += 1
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

    def _run_group(self, g: _Group, out: np.ndarray) -> None:
        buf = self._fetch(g.key, g.start, g.length)
        rec = self.idx[g.key]
        L = rec["levels"][0]
        dt = np.dtype(L["dtype"])
        width = L["blockw"]
        row_bytes = width * dt.itemsize
        pred = L.get("predictor", 1)
        for job in g.jobs:
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
            for (i, lx, ly) in job.items:
                off = ly * row_bytes - base
                row = raw[off:off + row_bytes]
                if len(row) < row_bytes:
                    raise RuntimeError(
                        f"{g.key} block {job.block}: short inflate, "
                        f"{len(row)} of {row_bytes} bytes for row {ly}")
                vals = self._undo_row(row, dt, width, pred)
                if pred == 3:
                    v = np.frombuffer(vals, dtype=dt.newbyteorder(">"),
                                      count=width)
                else:
                    v = vals
                out[i] = float(v[lx])

    # -- public ------------------------------------------------------------
    def sample(self, requests) -> np.ndarray:
        bad = {}
        for key in {r[0] for r in requests}:
            why = self.why_unsupported(key)
            if why:
                bad[key] = why
        if bad:
            first = list(bad.items())[:3]
            raise Unsupported(
                f"{len(bad)} file(s) cannot be served correctly by this "
                f"reader; use split() and fall back to GDAL. "
                + "; ".join(f"{k}: {v}" for k, v in first))
        out = np.zeros(len(requests), np.float64)
        jobs = self.plan(requests)
        groups = self.coalesce(jobs)
        self.stats.blocks = len([j for j in jobs if j.count])
        self.stats.groups = len(groups)
        self.stats.bytes_if_whole_blocks = sum(j.count for j in jobs)

        for j in jobs:                              # sparse tiles need no fetch
            if j.count == 0:
                fill = self.idx[j.key].get("nodata") or 0
                for (i, _lx, _ly) in j.items:
                    out[i] = fill

        if self.workers == 1:
            for g in groups:
                self._run_group(g, out)
        else:
            with ThreadPoolExecutor(self.workers) as ex:
                list(ex.map(lambda g: self._run_group(g, out), groups))
        return out
