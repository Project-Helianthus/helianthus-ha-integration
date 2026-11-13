# Changelog

## Unreleased

- Consume snake_case GraphQL roots, identity/provenance keys and the canonical
  zone room temperature and humidity fields; preserve unknown/zero values,
  revision vectors and partial last-known-good data.
- Use canonical circuit setter keys and unique-ID components for renamed
  controls. These entities are recreated with no migration or compatibility aliases.
- Withdraw the writable Frost Protection number without a replacement enum;
  the public special-function value table remains pending. Raw reads are unchanged.
- Remove the historical unique-ID registry rewrite. Unrelated identifiers remain.
- Keep `target_temp_c` provisional until source-selection semantics are resolved.
- Renew affected security, hardware and candidate evidence before release.
- Import the names that entry setup and six sensor classes used without
  importing them. Circuit, cylinder, DHW and energy sensors attach to their
  devices again; the duplicate-entry refusal, the start-of-setup and trusted
  registry clean-ups and the first trusted setup no longer raise `NameError`.
  Unique IDs and device identifiers are unchanged. The readability baseline
  no longer accepts an undefined name in shipped code.


- Share byte-address parsing: reject boolean and out-of-range addresses, use
  trimmed base-zero text consistently, and preserve non-address identity parsing.
- Share the radio grace constant while preserving availability below three stale
  cycles and cleanup after three; retain existing entity unique IDs.

- Define the integration release identity and development placeholder policy.
