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
