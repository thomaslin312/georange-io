#!/usr/bin/env python3
"""Unit tests that need no network, no object store and no staged corpus.

The workload harnesses check the reader against GDAL over 14,933 real reads,
which is the strongest evidence there is, but it needs Docker, MinIO and 9.5 GB
of imagery. Nothing about that runs in CI.

These build a small tiled COG in memory, serve it through a fake transport, and
check the same properties: that values come back identical to what the file
actually contains, that restart points reproduce the stream, that the cost model
responds to its inputs, and that every refusal path fires.
"""
from __future__ import annotations

import io
import struct
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from georange_io import (ObjectChanged, SparseReader, Unsupported,  # noqa: E402
                         describe, sbx)
from georange_io.cog import CogIndex, RangeResult                   # noqa: E402
from georange_io.reader import _Job, _Pool                         # noqa: E402


class FakePool:
    """The transport contract is one method returning (body, total size)."""

    def __init__(self, blobs: dict[str, bytes]):
        self.blobs = blobs
        self.requests = 0
        self.bytes = 0

    def get_range(self, path, start, length):
        b = self.blobs[path]
        self.requests += 1
        chunk = b[start:start + length]
        self.bytes += len(chunk)
        return chunk, len(b)


def make_cog(shape=(1024, 1024), tile=256, dtype=np.uint16, predictor=2,
             seed=3):
    """A real tiled Deflate COG, in memory."""
    rng = np.random.default_rng(seed)
    if dtype == np.float32:
        data = (rng.random(shape) * 1000).astype(np.float32)
    else:
        # smooth-ish so the predictor has something to do
        base = rng.integers(0, 400, shape).astype(np.int64)
        data = np.cumsum(base % 7, axis=1).astype(dtype)
    buf = io.BytesIO()
    tifffile.imwrite(buf, data, tile=(tile, tile), compression="deflate",
                     predictor=predictor, photometric="minisblack")
    return data, buf.getvalue()


@pytest.fixture(scope="module")
def cog():
    data, blob = make_cog()
    pool = FakePool({"/b/x.tif": blob})
    return data, blob, pool


def reader(pool, **kw):
    return SparseReader(None, "", "b", pool=pool, **kw)


class FakeResponse:
    def __init__(self, status, body=b"", content_range=None, headers=None):
        self.status = status
        self._body = body
        self._content_range = content_range
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def read(self):
        return self._body

    def getheader(self, name):
        if name.lower() == "content-range":
            return self._content_range
        return self._headers.get(name.lower())


class FakeConnection:
    def __init__(self, response):
        self.responses = list(response) if isinstance(response, list) else [response]
        self.requests = []
        self.closed = False

    def request(self, method, path, headers=None):
        self.requests.append((method, path, headers or {}))

    def getresponse(self):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


# --- the tile map ----------------------------------------------------------

def test_describe_matches_the_file(cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    L = rec["levels"][0]
    assert rec["size"] == len(blob)
    assert (L["width"], L["height"]) == data.shape[::-1]
    assert (L["blockw"], L["blockh"]) == (256, 256)
    assert L["nbx"] * L["nby"] == len(L["offsets"])
    assert L["predictor"] == 2 and L["compression"] in (8, 32946)


def test_malformed_tiff_is_rejected():
    pool = FakePool({"/b/broken.tif": b"not a tiff"})
    with pytest.raises(Exception):
        describe(pool, "/b/broken.tif")


def test_http_ranges_are_validated(monkeypatch):
    pool = _Pool("https://example.invalid", retries=0)
    good = FakeConnection(FakeResponse(
        206, b"abcd", "bytes 4-7/10", {"ETag": '"abc"'}))
    monkeypatch.setattr(pool, "_conn", lambda: good)
    response = pool.get_range("/x", 4, 4)
    assert tuple(response) == (b"abcd", 10)
    assert response.object_identity == 'etag:"abc"'

    ignored = FakeConnection(FakeResponse(200, b"0123456789"))
    monkeypatch.setattr(pool, "_conn", lambda: ignored)
    with pytest.raises(RuntimeError, match="HTTP 200"):
        pool.get_range("/x", 4, 4)

    wrong = FakeConnection(FakeResponse(206, b"abcd", "bytes 5-8/10"))
    monkeypatch.setattr(pool, "_conn", lambda: wrong)
    with pytest.raises(RuntimeError, match="unexpected range"):
        pool.get_range("/x", 4, 4)


def test_http_retry_policy_and_custom_headers(monkeypatch):
    pool = _Pool("https://example.invalid/prefix", retries=1, backoff_s=0,
                 headers={"Authorization": "Bearer token"},
                 headers_for=lambda path, start, end: {
                     "X-Signed-Range": f"{path}:{start}-{end}"})
    conn = FakeConnection([
        FakeResponse(503),
        FakeResponse(206, b"abcd", "bytes 0-3/4"),
    ])
    monkeypatch.setattr(pool, "_conn", lambda: conn)
    response = pool.get_range("/bucket/x", 0, 4)
    assert response.attempts == 2
    assert len(conn.requests) == 2
    assert conn.requests[0][1] == "/prefix/bucket/x"
    assert conn.requests[0][2]["Authorization"] == "Bearer token"
    assert conn.requests[0][2]["X-Signed-Range"] == "/prefix/bucket/x:0-3"

    denied = FakeConnection(FakeResponse(403))
    monkeypatch.setattr(pool, "_conn", lambda: denied)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        pool.get_range("/bucket/x", 0, 4)
    assert len(denied.requests) == 1


def test_transient_description_failure_is_retried(monkeypatch):
    import georange_io.cog as cog_module

    calls = 0

    def flaky(_pool, path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return {"key": path, "levels": [{"width": 1}]}

    monkeypatch.setattr(cog_module, "describe", flaky)
    idx = CogIndex(pool=object())
    assert idx.get("x") is None
    assert isinstance(idx.errors["x"], TimeoutError)
    assert idx.get("x")["key"] == "/x"
    assert calls == 2 and idx.described == 1 and "x" not in idx.errors


# --- values ----------------------------------------------------------------

@pytest.mark.parametrize("predictor,dt", [(1, np.uint16), (2, np.uint16),
                                          (3, np.float32)])
def test_values_match_the_file(predictor, dt):
    try:
        data, blob = make_cog(dtype=dt, predictor=predictor)
    except KeyError as e:
        # tifffile cannot *write* the floating-point predictor without
        # imagecodecs, which is a heavy binary dependency to add for a test
        # fixture. The reader's predictor-3 path is exercised against real
        # Copernicus DEM data by experiments/verify.py on W2, where it matches
        # GDAL on 9,893 of 9,893 reads.
        pytest.skip(f"cannot build a predictor-{predictor} fixture here: {e}")
    pool = FakePool({"/b/x.tif": blob})
    rd = reader(pool)
    pts = [(300, 20), (1000, 1000), (5, 900), (255, 256), (1023, 1023)]
    got = rd.sample([("x.tif", x, y) for (x, y) in pts])
    assert np.array_equal(got, np.array([float(data[y, x]) for (x, y) in pts]))


def test_windows_match_the_file(cog):
    data, blob, pool = cog
    rd = reader(pool)
    wins = [(0, 0, 100, 100), (250, 250, 300, 12), (900, 900, 124, 124)]
    got = rd.read([("x.tif", 0, x, y, w, h) for (x, y, w, h) in wins])
    for (x, y, w, h), a in zip(wins, got):
        assert np.array_equal(a, data[y:y + h, x:x + w])


def test_fetches_less_than_the_whole_blocks(cog):
    data, blob, pool = cog
    rd = reader(pool, margin=0.02, bandwidth_mbps=0.0001)   # no coalescing
    rd.sample([("x.tif", 10, 5)])                            # top of a block
    st = rd.stats.as_dict()
    assert st["bytes_fetched"] < st["bytes_if_whole_blocks"]


# --- the cost model --------------------------------------------------------

def test_coalescing_trades_requests_for_bytes(cog):
    data, blob, pool = cog
    pts = [("x.tif", x, 700) for x in range(0, 1024, 64)]
    tight = reader(pool, rtt_s=0.05, bandwidth_mbps=0.01)
    tight.sample(pts)
    loose = reader(pool, rtt_s=0.05, bandwidth_mbps=0.0)     # unbounded budget
    loose.sample(pts)
    assert loose.stats.requests < tight.stats.requests
    assert loose.stats.bytes_fetched >= tight.stats.bytes_fetched


def test_workers_do_not_change_what_is_fetched(cog):
    data, blob, pool = cog
    pts = [("x.tif", x, 300) for x in range(0, 1024, 32)]
    one, many = reader(pool, workers=1), reader(pool, workers=4)
    a, b = one.sample(pts), many.sample(pts)
    assert np.array_equal(a, b)
    assert one.stats.bytes_fetched == many.stats.bytes_fetched
    assert one.stats.requests == many.stats.requests


def test_one_reader_is_safe_across_concurrent_callers(cog):
    data, blob, _ = cog
    pool = FakePool({"/b/x.tif": blob})
    rd = reader(pool, workers=2)
    points = [[("x.tif", i * 17, i * 13)] for i in range(8)]
    with ThreadPoolExecutor(4) as executor:
        got = list(executor.map(rd.sample, points))
    assert [a[0] for a in got] == [data[i * 13, i * 17] for i in range(8)]


def test_different_overview_levels_are_never_coalesced():
    rd = SparseReader({}, pool=FakePool({}), bandwidth_mbps=0.0)
    jobs = [
        _Job("x.tif", 0, 0, 100, 10, prefix=10, start=100),
        _Job("x.tif", 1, 0, 110, 10, prefix=10, start=110),
    ]
    assert len(rd.coalesce(jobs)) == 2


def test_stats_accumulate_across_reads(cog):
    _, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    rd = SparseReader({"x.tif": rec}, "", "b",
                      pool=FakePool({"/b/x.tif": blob}),
                      bandwidth_mbps=0.0001)
    rd.sample([("x.tif", 10, 5)])
    first = rd.stats.as_dict()
    rd.sample([("x.tif", 10, 5)])
    second = rd.stats.as_dict()
    for field in ("requests", "bytes_fetched", "blocks", "groups",
                  "bytes_if_whole_blocks"):
        assert second[field] == 2 * first[field]


def test_stats_include_on_demand_header_traffic(cog):
    _, blob, _ = cog
    pool = FakePool({"/b/x.tif": blob})
    rd = reader(pool, bandwidth_mbps=0.0001)
    rd.sample([("x.tif", 10, 5)])
    assert rd.stats.requests == pool.requests
    assert rd.stats.bytes_fetched == pool.bytes


def test_retry_attempts_are_visible_in_stats(cog):
    _, blob, pool = cog
    rec = describe(pool, "/b/x.tif")

    class RetriedPool(FakePool):
        def get_range(self, path, start, length):
            data, total = super().get_range(path, start, length)
            return RangeResult(data, total, attempts=3)

    rd = SparseReader({"x.tif": rec}, "", "b",
                      pool=RetriedPool({"/b/x.tif": blob}))
    rd.sample([("x.tif", 10, 5)])
    assert rd.stats.requests == 3 and rd.stats.retries == 2


def test_request_generators_and_encoded_object_keys(cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    rd = SparseReader({"a b.tif": rec}, "", "b",
                      pool=FakePool({"/b/a%20b.tif": blob}))
    requests = (("a b.tif", x, y, 1, 1) for x, y in [(10, 5), (20, 6)])
    got = rd.read(requests)
    assert [a[0, 0] for a in got] == [data[5, 10], data[6, 20]]
    any_got = rd.read_any((r for r in [("a b.tif", 30, 7)]))
    assert any_got[0][0, 0] == data[7, 30]


# --- restart points --------------------------------------------------------

def test_restart_reproduces_the_stream(cog):
    from georange_io.tile_index import build_index, read_from
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    L = rec["levels"][0]
    bi = int(np.argmax(L["bytecounts"]))
    off, cnt = L["offsets"][bi], L["bytecounts"][bi]
    comp = blob[off:off + cnt]
    idx, full = build_index(comp, 16384)
    assert idx.points, "no restart points were found"
    for p in idx.points:
        assert read_from(comp, idx, p.out, p.out + 512) == full[p.out:p.out + 512]


def test_sidecar_round_trip_and_staleness(tmp_path, cog):
    from georange_io.tile_index import build_index
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    L = rec["levels"][0]
    bi = int(np.argmax(L["bytecounts"]))
    comp = blob[L["offsets"][bi]:L["offsets"][bi] + L["bytecounts"][bi]]
    idx, _ = build_index(comp, 16384)
    rec["content_identity"] = "sha256:test-object"
    p = tmp_path / "x.sbx"
    sbx.write(p, 16384, {bi: idx.points}, rec["size"], sbx.layout_hash(L),
              rec["content_identity"])

    ok = sbx.open_for(p, rec)
    assert ok.best(bi, 10 ** 9) is not None
    ok.close()

    import copy
    wrong_size = copy.deepcopy(rec); wrong_size["size"] += 1
    with pytest.raises(sbx.StaleSidecar):
        sbx.open_for(p, wrong_size)

    retiled = copy.deepcopy(rec); retiled["levels"][0]["offsets"][0] += 8
    with pytest.raises(sbx.StaleSidecar):
        sbx.open_for(p, retiled)

    replaced = copy.deepcopy(rec)
    replaced["content_identity"] = "sha256:replacement"
    with pytest.raises(sbx.StaleSidecar, match="identity match False"):
        sbx.open_for(p, replaced)


def test_legacy_unbound_sidecar_fails_closed(tmp_path, cog):
    _, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    rec["content_identity"] = "sha256:test-object"
    p = tmp_path / "legacy.sbx"
    p.write_bytes(struct.pack("<4sIIQQ", b"SBX2", 0, 0,
                              len(blob), sbx.layout_hash(rec["levels"][0])))
    with pytest.raises(sbx.StaleSidecar, match="strong identity"):
        sbx.open_for(p, rec)


def test_invalid_checkpoint_is_disabled_and_reader_falls_back(tmp_path, cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    rec["content_identity"] = "sha256:test-object"
    L = rec["levels"][0]
    point = sbx.Point(in_byte=L["bytecounts"][0] + 1, bits=0,
                      out=1, window=b"")
    sbx.write(tmp_path / "x.tif.sbx", 0, {0: [point]}, rec["size"],
              sbx.layout_hash(L), rec["content_identity"])
    rd = SparseReader({"x.tif": rec}, "", "b", pool=pool,
                      sbx_dir=tmp_path)
    got = rd.sample([("x.tif", 10, 5)])
    assert got[0] == data[5, 10]
    assert rd.stats.invalid_sidecars == 1


@pytest.mark.parametrize("payload", [b"", b"SBX2", b"not a sidecar at all"])
def test_corrupt_sidecar_is_refused(tmp_path, payload):
    p = tmp_path / "broken.sbx"
    p.write_bytes(payload)
    with pytest.raises(ValueError):
        sbx.Sbx(p)


def test_sidecar_rejects_excessive_table_before_allocating(tmp_path):
    p = tmp_path / "hostile.sbx"
    identity = b"sha256:x"
    p.write_bytes(struct.pack("<4sIIQQH", b"SBX3", 0, 10_000_000,
                              1, 1, len(identity)) + identity)
    with pytest.raises(ValueError, match="table"):
        sbx.Sbx(p)


# --- refusals --------------------------------------------------------------

def test_refuses_unknown_file(cog):
    _, _, pool = cog
    with pytest.raises(Unsupported):
        reader(pool).sample([("nope.tif", 0, 0)])


def test_refuses_unreadable_encoding(cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    import copy
    bad = copy.deepcopy(rec)
    bad["levels"][0]["compression"] = 5              # LZW
    rd = SparseReader({"x.tif": bad}, "", "b", pool=pool)
    assert not rd.supports("x.tif")
    assert "Deflate" in rd.why_unsupported("x.tif")
    with pytest.raises(Unsupported):
        rd.read([("x.tif", 0, 0, 8, 8)])


def test_refuses_window_off_the_edge(cog):
    data, blob, pool = cog
    rd = reader(pool)
    with pytest.raises(Unsupported):
        rd.read([("x.tif", 1000, 1000, 64, 64)])
    part = rd.read([("x.tif", 1000, 1000, 64, 64)], allow_partial=True)
    assert part[0].shape == (64, 64)
    assert (part[0][:, 24:] == 0).all()


@pytest.mark.parametrize("window_request", [
    ("x.tif", -1, 0, 0, 1, 1),
    ("x.tif", 1, 0, 0, 1, 1),
    ("x.tif", 0, 0, 0, 1),
    ("x.tif", 0, 0, -1, 1),
])
def test_refuses_invalid_level_or_window_even_when_partial(cog, window_request):
    _, _, pool = cog
    with pytest.raises(Unsupported):
        reader(pool).read([window_request], allow_partial=True)


def test_checks_encoding_at_the_requested_level(cog):
    _, _, pool = cog
    import copy
    rec = describe(pool, "/b/x.tif")
    rec["levels"].append(copy.deepcopy(rec["levels"][0]))
    rec["levels"][1]["level"] = 1
    rec["levels"][1]["compression"] = 5
    rd = SparseReader({"x.tif": rec}, "", "b", pool=pool)
    assert rd.supports("x.tif", 0)
    assert not rd.supports("x.tif", 1)
    assert rd.split([("x.tif", 1, 0, 0, 1, 1)]) == ([], [0])
    with pytest.raises(Unsupported, match="compression 5"):
        rd.read([("x.tif", 1, 0, 0, 1, 1)])


def test_refuses_malformed_or_dangerous_index_metadata(cog):
    _, _, pool = cog
    import copy
    rec = describe(pool, "/b/x.tif")

    outside = copy.deepcopy(rec)
    outside["levels"][0]["offsets"][0] = outside["size"] + 1
    assert "outside" in SparseReader(
        {"x.tif": outside}, pool=pool).why_unsupported("x.tif")

    missing = copy.deepcopy(rec)
    del missing["levels"][0]["compression"]
    assert "compression None" in SparseReader(
        {"x.tif": missing}, pool=pool).why_unsupported("x.tif")

    huge = copy.deepcopy(rec)
    huge["levels"][0].update(width=1_000_000, height=1_000_000,
                             blockw=1_000_000, blockh=1_000_000,
                             nbx=1, nby=1, offsets=[1], bytecounts=[1])
    assert "max_block_bytes" in SparseReader(
        {"x.tif": huge}, pool=pool).why_unsupported("x.tif")


def test_refuses_a_planned_range_over_the_configured_limit(cog):
    _, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    rd = SparseReader({"x.tif": rec}, "", "b",
                      pool=FakePool({"/b/x.tif": blob}),
                      max_fetch_bytes=1024)
    with pytest.raises(Unsupported, match="max_fetch_bytes"):
        rd.sample([("x.tif", 10, 255)])


def test_refuses_an_index_describing_another_object(cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    import copy
    lying = copy.deepcopy(rec)
    lying["size"] = len(blob) + 12345
    rd = SparseReader({"x.tif": lying}, "", "b", pool=pool)
    with pytest.raises(Unsupported, match="do not belong"):
        rd.sample([("x.tif", 10, 10)])


def test_refuses_an_object_that_changes_after_indexing(cog):
    _, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    rec["object_identity"] = 'etag:"before"'

    class ChangedPool(FakePool):
        def get_range(self, path, start, length):
            data, total = super().get_range(path, start, length)
            return RangeResult(data, total, 'etag:"after"')

    rd = SparseReader({"x.tif": rec}, "", "b",
                      pool=ChangedPool({"/b/x.tif": blob}))
    with pytest.raises(ObjectChanged):
        rd.sample([("x.tif", 10, 10)])


def test_resource_limits_and_context_manager(cog):
    _, blob, pool = cog
    with SparseReader(None, "", "b", pool=pool,
                      max_output_bytes=1024) as rd:
        with pytest.raises(Unsupported, match="max_output_bytes"):
            rd.read([("x.tif", 0, 0, 100, 100)])
    with pytest.raises(RuntimeError, match="closed"):
        rd.sample([("x.tif", 0, 0)])


@pytest.mark.parametrize("kwargs", [
    {"margin": -1}, {"min_fetch": 0}, {"rtt_s": -1},
    {"bandwidth_mbps": -1}, {"max_fetch_bytes": 0},
    {"max_output_bytes": 0}, {"max_block_bytes": 0}, {"workers": 0},
])
def test_invalid_reader_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        SparseReader({}, pool=FakePool({}), **kwargs)


@pytest.mark.parametrize("url", ["", "ftp://example.com", "https://u:p@example.com",
                                  "https://example.com/?signature=secret"])
def test_invalid_base_urls_are_rejected(url):
    with pytest.raises(ValueError):
        SparseReader({}, base_url=url)


@pytest.mark.parametrize("invalid_request", [
    ("x.tif", 1.5, 0), ("", 0, 0), ("x.tif", True, 0),
])
def test_invalid_request_types_are_rejected(invalid_request):
    with pytest.raises(ValueError):
        SparseReader({}, pool=FakePool({})).read([invalid_request])


def test_package_and_metadata_versions_match():
    import tomllib
    import georange_io

    metadata = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())
    assert metadata["project"]["version"] == georange_io.__version__


def test_split_partitions_by_capability(cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    import copy
    bad = copy.deepcopy(rec); bad["levels"][0]["compression"] = 5
    rd = SparseReader({"a.tif": rec, "b.tif": bad}, "", "b", pool=pool)
    ok, no = rd.split([("a.tif", 0, 0, 1, 1), ("b.tif", 0, 0, 1, 1)])
    assert ok == [0] and no == [1]
