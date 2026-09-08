"""GeoRange IO: a sparse reader for COG archives.

Reads points and windows from tiled, Deflate-compressed COGs while fetching a
fraction of what a general-purpose reader must, by knowing the whole request
set before it fetches anything:

    from georange_io import SparseReader

    rd = SparseReader(base_url="https://example.s3.amazonaws.com",
                      bucket="my-bucket")
    values = rd.sample([("scene/B04.tif", 5000, 3000), ...])
    windows = rd.read([("scene/B04.tif", 0, 100, 100, 512, 512), ...])

No index has to be built in advance; each file is described from its own header
on first use. Anything the fast path cannot decode correctly is refused, and
`read_any` delegates it to GDAL instead.
"""
from georange_io.cog import CogIndex, RangeResult, describe
from georange_io.reader import (ObjectChanged, SparseReader, Stats,
                                TransportError, Unsupported)
from georange_io.sbx import Sbx, StaleSidecar, open_for

__all__ = ["SparseReader", "Stats", "Unsupported", "TransportError",
           "ObjectChanged", "CogIndex", "RangeResult", "describe", "Sbx",
           "StaleSidecar", "open_for"]
__version__ = "0.2.0"
