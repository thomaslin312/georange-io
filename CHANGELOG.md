# Changelog

## 0.2.0

- Bind SBX3 sidecars to SHA-256, object-version, or ETag identities.
- Validate source identity between header and pixel range reads.
- Add configurable timeout, bounded retries/backoff, authentication headers,
  TLS contexts, connection cleanup, and retry metrics.
- Add concurrency safety and output, fetch, block, index, and sidecar bounds.
- Expand malformed-input and lifecycle regression coverage.
- Document the supported API, compatibility contract, and security policy.

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
