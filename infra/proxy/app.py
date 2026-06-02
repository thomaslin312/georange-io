#!/usr/bin/env python3
"""GeoRange IO Phase-0 logging reverse proxy.

Sits between the GDAL client and MinIO. This is the *only* source of truth for
bytes fetched and request counts in Phase 0.

Per request it records:
    ts_start, ts_end, duration_s, method, path, object key, query,
    the exact Range request header, upstream status, Content-Range,
    upstream Content-Length, and the response body bytes actually written
    to the client, plus the client's HTTP version and a per-connection id.

It also injects:
    latency_ms          fixed delay applied once per HTTP request, before the
                        first response byte. Models one RTT on a warm socket.
    jitter_ms           extra uniform(0, jitter) added to that delay.
    connect_latency_ms  extra delay on the first request of each new TCP
                        connection. Models connection setup cost. Default 0.
    bandwidth_mbps      response body throughput cap, enforced by chunked
                        pacing on the write path.

Control API (separate port) drives capture sessions and the RTT sweep so the
harness never has to restart the proxy.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

UPSTREAM = os.environ.get("UPSTREAM", "http://minio:9000").rstrip("/")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "9000"))
CONTROL_PORT = int(os.environ.get("CONTROL_PORT", "9010"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/logs"))
CHUNK = 64 * 1024

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
}


@dataclass
class Shape:
    """Injected network conditions."""
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    connect_latency_ms: float = 0.0
    bandwidth_mbps: float = 0.0  # 0 == uncapped

    def request_delay_s(self, rng: random.Random) -> float:
        d = self.latency_ms
        if self.jitter_ms > 0:
            d += rng.uniform(0.0, self.jitter_ms)
        return max(0.0, d) / 1000.0


@dataclass
class Session:
    label: str
    started: float
    path: Path
    fh: object
    n_req: int = 0
    n_bytes: int = 0
    meta: dict = field(default_factory=dict)


class Proxy:
    def __init__(self) -> None:
        self.shape = Shape()
        self.session: Session | None = None
        self.rng = random.Random(0xC0FFEE)
        self.http: ClientSession | None = None
        self.seen_conns: set[int] = set()
        self.conn_ids: dict[int, str] = {}
        self.lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        # Generous upstream pool: the proxy must never be the bottleneck or the
        # concurrency limiter. Any serialization we measure must be GDAL's.
        self.http = ClientSession(
            connector=TCPConnector(limit=0, limit_per_host=0,
                                   force_close=False, enable_cleanup_closed=True),
            timeout=ClientTimeout(total=None, sock_connect=30, sock_read=300),
            auto_decompress=False,
        )
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    async def stop(self) -> None:
        if self.http:
            await self.http.close()

    # -- capture sessions --------------------------------------------------
    def open_session(self, label: str, meta: dict) -> Session:
        self.close_session()
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in label)
        path = LOG_DIR / f"{safe}.jsonl"
        fh = path.open("w", buffering=1)
        header = {"record": "session_start", "label": label, "meta": meta,
                  "shape": self.shape.__dict__, "upstream": UPSTREAM,
                  "ts": time.time()}
        fh.write(json.dumps(header) + "\n")
        self.session = Session(label=label, started=time.time(), path=path,
                               fh=fh, meta=meta)
        self.seen_conns.clear()
        return self.session

    def close_session(self) -> dict | None:
        s = self.session
        if s is None:
            return None
        summary = {"record": "session_end", "label": s.label,
                   "n_requests": s.n_req, "n_bytes": s.n_bytes,
                   "wall_s": time.time() - s.started, "ts": time.time()}
        s.fh.write(json.dumps(summary) + "\n")
        s.fh.close()
        self.session = None
        return {**summary, "path": str(s.path)}

    def log(self, rec: dict) -> None:
        s = self.session
        if s is None:
            return
        s.n_req += 1
        s.n_bytes += rec.get("resp_body_bytes", 0)
        rec["seq"] = s.n_req
        s.fh.write(json.dumps(rec) + "\n")

    # -- the data path -----------------------------------------------------
    async def handle(self, request: web.Request) -> web.StreamResponse:
        t0 = time.perf_counter()
        wall0 = time.time()

        tr = request.transport
        tid = id(tr) if tr is not None else 0
        new_conn = tid not in self.seen_conns
        if new_conn:
            self.seen_conns.add(tid)
            self.conn_ids[tid] = uuid.uuid4().hex[:8]
        conn_id = self.conn_ids.get(tid, "?")

        delay = self.shape.request_delay_s(self.rng)
        if new_conn and self.shape.connect_latency_ms > 0:
            delay += self.shape.connect_latency_ms / 1000.0
        if delay > 0:
            await asyncio.sleep(delay)

        url = f"{UPSTREAM}{request.rel_url}"
        fwd = {k: v for k, v in request.headers.items()
               if k.lower() not in HOP_BY_HOP}
        # The client's own encoding preference must survive untouched. aiohttp
        # would otherwise inject "Accept-Encoding: gzip", MinIO would compress,
        # and we would both hand GDAL a body it never asked for and count
        # compressed bytes instead of the bytes the origin would really send.
        if not any(k.lower() == "accept-encoding" for k in fwd):
            fwd["Accept-Encoding"] = "identity"
        body = await request.read() if request.body_exists else None

        rng_hdr = request.headers.get("Range")
        assert self.http is not None

        t_up = time.perf_counter()
        try:
            async with self.http.request(
                    request.method, url, headers=fwd, data=body,
                    allow_redirects=False,
                    skip_auto_headers=("Accept-Encoding", "Accept",
                                       "User-Agent")) as up:
                ttfb = time.perf_counter() - t_up
                out_hdrs = {k: v for k, v in up.headers.items()
                            if k.lower() not in HOP_BY_HOP}
                resp = web.StreamResponse(status=up.status, headers=out_hdrs)
                up_cl = up.headers.get("Content-Length")
                if up_cl and up_cl.isdigit():
                    resp.content_length = int(up_cl)
                else:
                    resp.enable_chunked_encoding()
                await resp.prepare(request)

                n = 0
                cap = self.shape.bandwidth_mbps
                bps = cap * 1e6 / 8.0 if cap > 0 else 0.0
                t_stream = time.perf_counter()
                async for chunk in up.content.iter_chunked(CHUNK):
                    await resp.write(chunk)
                    n += len(chunk)
                    if bps > 0:
                        target = n / bps
                        drift = target - (time.perf_counter() - t_stream)
                        if drift > 0:
                            await asyncio.sleep(drift)
                await resp.write_eof()
                status = up.status
                up_len = up.headers.get("Content-Length")
                cr = up.headers.get("Content-Range")
                err = None
        except Exception as e:  # noqa: BLE001
            status, up_len, cr, n, ttfb, err = 599, None, None, 0, 0.0, repr(e)[:200]
            resp = web.Response(status=502, text="proxy upstream error")

        dt = time.perf_counter() - t0
        path = request.path
        key = path.lstrip("/")
        bucket, _, obj = key.partition("/")
        qs = request.query_string
        kind = ("LIST" if "list-type" in qs or (not obj and request.method == "GET")
                else request.method + ("_RANGE" if rng_hdr else ""))

        self.log({
            "record": "req", "ts": wall0, "ts_end": time.time(),
            "duration_s": round(dt, 6), "ttfb_s": round(ttfb, 6),
            "injected_delay_s": round(delay, 6),
            "method": request.method, "kind": kind,
            "path": path, "bucket": bucket, "key": obj or None,
            "query": qs or None,
            "range": rng_hdr,
            "status": status,
            "upstream_content_length": int(up_len) if up_len and up_len.isdigit() else None,
            "content_range": cr,
            "resp_body_bytes": n,
            "http_version": f"{request.version.major}.{request.version.minor}",
            "conn": conn_id, "new_conn": new_conn,
            "error": err,
        })
        return resp

    # -- control -----------------------------------------------------------
    async def c_health(self, _: web.Request) -> web.Response:
        return web.json_response({"ok": True, "upstream": UPSTREAM,
                                  "shape": self.shape.__dict__,
                                  "session": self.session.label if self.session else None})

    async def c_shape(self, request: web.Request) -> web.Response:
        body = await request.json()
        for k in ("latency_ms", "jitter_ms", "connect_latency_ms", "bandwidth_mbps"):
            if k in body:
                setattr(self.shape, k, float(body[k]))
        return web.json_response(self.shape.__dict__)

    async def c_start(self, request: web.Request) -> web.Response:
        body = await request.json()
        async with self.lock:
            s = self.open_session(body["label"], body.get("meta", {}))
        return web.json_response({"label": s.label, "path": str(s.path)})

    async def c_stop(self, _: web.Request) -> web.Response:
        async with self.lock:
            out = self.close_session()
        return web.json_response(out or {"error": "no active session"},
                                 status=200 if out else 409)


def build(proxy: Proxy) -> tuple[web.Application, web.Application]:
    data = web.Application(client_max_size=1024 ** 3)
    data.router.add_route("*", "/{tail:.*}", proxy.handle)

    ctl = web.Application()
    ctl.router.add_get("/health", proxy.c_health)
    ctl.router.add_post("/shape", proxy.c_shape)
    ctl.router.add_post("/session/start", proxy.c_start)
    ctl.router.add_post("/session/stop", proxy.c_stop)
    return data, ctl


async def main() -> None:
    proxy = Proxy()
    await proxy.start()
    data, ctl = build(proxy)

    r1 = web.AppRunner(data, access_log=None)
    await r1.setup()
    await web.TCPSite(r1, "0.0.0.0", PROXY_PORT, backlog=512).start()

    r2 = web.AppRunner(ctl, access_log=None)
    await r2.setup()
    await web.TCPSite(r2, "0.0.0.0", CONTROL_PORT).start()

    print(f"[proxy] data :{PROXY_PORT} -> {UPSTREAM}   control :{CONTROL_PORT}",
          flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await proxy.stop()


if __name__ == "__main__":
    asyncio.run(main())
