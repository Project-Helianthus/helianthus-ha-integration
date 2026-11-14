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
- Give one asset one Home Assistant device across the PV, Storage and EVSE
  consumers and the shared semantic path. When several consumers bind the
  same asset, the first of PV, Storage, EVSE owns the device, whatever the
  order in which the consumers were set up, and every entity of the asset
  reports that device's identifier, model and name. Before this change an
  asset bound by two consumers was two devices, the shared path ignored the
  EVSE consumer, and shared entities wrote their own model and name onto a
  consumer's device. Entity unique IDs and the formats of every device
  identifier are unchanged; so are the device identifier and metadata of
  each consumer's entities when one consumer binds the asset alone, and the
  device of an asset that only the shared path shows. Home Assistant device
  grouping changes for assets bound by more than one consumer, and for an
  asset bound by the EVSE consumer that the shared path also shows. For such
  an asset the moved entities take the owning device's name in their
  displayed name and the owning device's area; a custom name, area or labels
  of the device they leave do not carry over; entity IDs and unique IDs do
  not change. On an installation that already had several devices for one
  asset, the entities move to the owning device and the device they leave
  stays in the device registry without entities; removing it is an operator
  action outside the integration.
- Fix the two hassfest findings: the manifest keys are now in hassfest's
  order (domain, name, then alphabetical) with every value unchanged, and
  `services.yaml` no longer declares the invalid `response` option under the
  twelve eeBUS administration services; each keeps its returned keys as a
  comment, and the code still registers it as response only. No runtime code
  changes. The hassfest job now blocks.


- Share byte-address parsing: reject boolean and out-of-range addresses, use
  trimmed base-zero text consistently, and preserve non-address identity parsing.
- Share the radio grace constant while preserving availability below three stale
  cycles and cleanup after three; retain existing entity unique IDs.

- Define the integration release identity and development placeholder policy.
