# Changelog

## 0.2.1

- Export `CorruptObject` from the package root. 0.2.0 listed it in `__all__`
  without importing it, so `from georange_io import CorruptObject` and
  `from georange_io import *` both failed.
- Replace the PyPI description, which still showed pre-audit measurements
  (1.61x / 2.88x) and pointed at documents that no longer exist. Current
  figures are 2.05x fewer bytes, 8.74x with a sidecar.
- Make the restart-point and fuzz tests pass on every zlib version.

## 0.2.0

- Bind SBX3 sidecars to SHA-256, object-version, or ETag identities.
- Validate source identity between header and pixel range reads.
- Add configurable timeout, bounded retries/backoff, authentication headers,
  TLS contexts, connection cleanup, and retry metrics.
- Add concurrency safety and output, fetch, block, index, and sidecar bounds.
- Expand malformed-input and lifecycle regression coverage.
- Document the supported API, compatibility contract, and operational defaults.

## 0.1.0

- Initial access-pattern-aware sparse COG reader, checkpoint sidecars,
  measurement harness, correctness gates, and package workflow.

## 0.2.1

- Header discovery reads 32 kB instead of 1 MB per object, which is what the
  corpus needs and 32x less than was being fetched and counted.
- `CorruptObject` (a `TransportError`) replaces bare `zlib.error` escaping from
  a damaged tile stream. Found by randomized fuzzing.
- A configured sidecar that is never used is now reported instead of silent, with
  `sidecars_loaded` / `sidecars_missing` counters; warnings go through `logging`.
- Benchmark harness: fixed a name shadowing that made it unrunnable, and a
  single transient network error no longer discards a whole run.
