# GeoRange IO API and compatibility contract

## Supported fast path

GeoRange IO 0.2 supports tiled, little-endian, single-sample, contiguous COGs
using Deflate or Adobe Deflate. Predictor 1 is accepted directly, predictor 2
is accepted for integer data, and predictor 3 is accepted for floating-point
data. Point reads, windows, and overview levels are supported.

Files outside this contract are reported by `supports` / `why_unsupported`,
partitioned by `split`, or delegated to rasterio/GDAL by `read_any`.

## Stable public surface

The public names exported from `georange_io` are:

- `SparseReader`, `Stats`, and `Unsupported`
- `TransportError` and `ObjectChanged`
- `CogIndex`, `RangeResult`, and `describe`
- `Sbx`, `StaleSidecar`, and `open_for`

Names beginning with an underscore and the internals of `tile_index` are not
public APIs.

`SparseReader` accepts either a prebuilt index mapping/path or discovers each
COG over range requests. It is safe to call from multiple threads; calls on one
instance are serialized while its configured worker pool parallelizes the
planned ranges. Use it as a context manager or call `close()`.

Custom transports implement:

```python
get_range(path: str, start: int, length: int) -> RangeResult
```

Returning the legacy `(body, total_size)` tuple remains supported, but it cannot
provide object-version validation. `RangeResult.object_identity` should be a
stable namespaced value such as `version-id:...` or `etag:...`.

## Sidecar identity

SBX3 sidecars require an exact identity match, preferring
`content_identity` (for example `sha256:...`) and otherwise using the transport
object version or ETag. SBX2 files can be inspected by `Sbx`, but `open_for`
rejects them because they cannot prove payload identity.

For byte-identical objects copied between stores, pass a trusted mapping:

```python
SparseReader(..., content_identities={"scene.tif": "sha256:..."})
```

## Versioning

The project follows semantic versioning. Before 1.0, a minor release may change
APIs or file formats and a patch release will not. Deprecations will remain for
at least one minor release where a safe compatibility path exists. The SBX
magic/version is independent of the Python package version.

## Operational defaults

- timeout: 30 seconds per socket operation
- retries: 3 after the initial attempt
- backoff: 0.25 seconds, doubled after each retry
- maximum coalesced range: 128 MiB
- maximum returned arrays per call: 1 GiB
- maximum uncompressed tile: 64 MiB

Tune these for the deployment and catch `Unsupported` for capability/resource
limits, `TransportError` for remote protocol failures, and `ObjectChanged` for
source-version races.
