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
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from georange_io import SparseReader, Unsupported, describe, sbx   # noqa: E402
from georange_io.reader import _Pool                               # noqa: E402


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
        # Copernicus DEM data by georange_io/verify.py on W2, where it matches
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


# --- restart points --------------------------------------------------------

def test_restart_reproduces_the_stream(cog):
    from tile_index import build_index, read_from
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
    from tile_index import build_index
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    L = rec["levels"][0]
    bi = int(np.argmax(L["bytecounts"]))
    comp = blob[L["offsets"][bi]:L["offsets"][bi] + L["bytecounts"][bi]]
    idx, _ = build_index(comp, 16384)
    p = tmp_path / "x.sbx"
    sbx.write(p, 16384, {bi: idx.points}, rec["size"], sbx.layout_hash(L))

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


def test_refuses_an_index_describing_another_object(cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    import copy
    lying = copy.deepcopy(rec)
    lying["size"] = len(blob) + 12345
    rd = SparseReader({"x.tif": lying}, "", "b", pool=pool)
    with pytest.raises(Unsupported, match="do not belong"):
        rd.sample([("x.tif", 10, 10)])


def test_split_partitions_by_capability(cog):
    data, blob, pool = cog
    rec = describe(pool, "/b/x.tif")
    import copy
    bad = copy.deepcopy(rec); bad["levels"][0]["compression"] = 5
    rd = SparseReader({"a.tif": rec, "b.tif": bad}, "", "b", pool=pool)
    ok, no = rd.split([("a.tif", 0, 0, 1, 1), ("b.tif", 0, 0, 1, 1)])
    assert ok == [0] and no == [1]
