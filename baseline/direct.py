#!/usr/bin/env python3
"""Direct reads against MinIO, bypassing the logging proxy.

Used only while *generating* the data-dependent workloads (W4, W5) and while
computing the W5 oracle. Nothing here is ever measured; routing it around the
proxy is what keeps generation out of the numbers.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache

ENDPOINT = os.environ.get("MINIO_DIRECT", "http://127.0.0.1:9100")
BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "georange-io")

_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="1",
    GDAL_CACHEMAX="512",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="268435456",
)


def url(key: str) -> str:
    return f"/vsicurl/{ENDPOINT}/{BUCKET}/{key}"


@contextmanager
def env():
    import rasterio
    with rasterio.Env(**_ENV):
        yield


class Reader:
    """Small caching opener. Datasets stay open across reads, which is what a
    real consumer would do and keeps generation from re-reading headers."""

    def __init__(self):
        self._ds: dict[str, object] = {}
        self._env = None

    def __enter__(self):
        import rasterio
        self._env = rasterio.Env(**_ENV)
        self._env.__enter__()
        return self

    def __exit__(self, *a):
        for d in self._ds.values():
            try:
                d.close()
            except Exception:  # noqa: BLE001
                pass
        self._ds.clear()
        if self._env:
            self._env.__exit__(*a)

    def open(self, key: str):
        if key not in self._ds:
            import rasterio
            self._ds[key] = rasterio.open(url(key))
        return self._ds[key]

    def read(self, key: str, level: int, x: int, y: int, w: int, h: int):
        """Read a window at an IFD level. level 0 is native; level n reads the
        n-th overview by decimating the native window, which is exactly what a
        reader asking for that overview does."""
        import numpy as np
        from rasterio.windows import Window
        ds = self.open(key)
        if level == 0:
            return ds.read(1, window=Window(x, y, w, h), boundless=True,
                           fill_value=ds.nodata if ds.nodata is not None else 0)
        dec = ds.overviews(1)
        factor = dec[level - 1]
        win = Window(x * factor, y * factor, w * factor, h * factor)
        return ds.read(1, window=win, out_shape=(h, w), boundless=True,
                       fill_value=ds.nodata if ds.nodata is not None else 0)
