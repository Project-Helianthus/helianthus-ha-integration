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
  devices again. Before this change, setting up an entry failed with
  `NameError` whenever the registry already held devices of this
  integration, and the first trusted setup failed after its platforms were
  set up. These paths run again: the start-of-setup registry clean-up, the
  trusted clean-up of bus, cylinder and retired boiler rows, the
  duplicate-entry handling, and the automatic disabling of sparse entities
  whose values are null. Unique IDs and device identifiers are unchanged. The
  readability baseline no longer accepts an undefined name in shipped code.
  The start-of-setup clean-up keeps every device of an active entry,
  including the eeBUS administration and the semantic-snapshot, PV, storage
  and EVSE asset devices. The trusted clean-up removes cylinder rows beyond
  the published count only while the FM5 mode is interpreted, both when the
  count is read and when the clean-up runs.
- Bound the entry reloads caused by automatically enabling and disabling
  sparse entities. A sparse entity whose value is null at the trusted
  clean-up is still disabled once, now without a reload, because Home
  Assistant removes an entity disabled in the registry by itself. When a
  value appears, the integration enables the entity and schedules a reload
  of the entry; Home Assistant itself reloads the entry once more after an
  entity is enabled. When the daemon admission repair code appears and its
  sensor is missing, the integration schedules a reload. The integration
  schedules at most one reload per sparse entity per Home Assistant run,
  and the allowance is spent when that reload is scheduled. Once it is
  spent, the entity is not disabled or removed again during that run, and
  the sensor platform creates it whether or not its value is null at that
  moment; this covers the repair-code sensor and the optional radio
  metadata sensors, which the platform otherwise creates only while their
  value is present. While the value is null the sensor shows unknown. A
  sparse sensor that has not had its reload keeps the earlier rule, so a
  system that never reports a repair code gets no repair-code sensor. An
  entity whose value alternates between null and a value therefore settles
  enabled. The record of these entities is kept in the integration's
  domain data, which survives a reload of the entry and is cleared when
  Home Assistant restarts; the record of an entry that is no longer
  configured is dropped when an entry of the integration sets up. The
  sensor platform reads the record through a public, documented helper of
  entry setup. Unique IDs, entity IDs, device identifiers and names are
  unchanged.
- Declare the `invoke` parameters of zone, hot-water and schedule-helper writes
  with the gateway's `json` scalar so the gateway accepts these writes again.


- Share byte-address parsing: reject boolean and out-of-range addresses, use
  trimmed base-zero text consistently, and preserve non-address identity parsing.
- Share the radio grace constant while preserving availability below three stale
  cycles and cleanup after three; retain existing entity unique IDs.

- Define the integration release identity and development placeholder policy.
