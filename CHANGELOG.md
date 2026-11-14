# Changelog

## Unreleased

- Map the fifteen EVSE pack facts of the shared semantic snapshot to read-only
  sensors on an `EV charger` device, so EVSE readings reach Home Assistant
  through the ordinary GraphQL read. Energy counters use the `total` state
  class and the allocated current limit has none. On the asset bound to the
  EVSE consumer the configured and allocated current limits stay with that
  consumer and the other EVSE facts join its device. Existing unique IDs are
  unchanged.
- Send the canonical config-write keys of the system and boiler setters and
  read the renamed system fields, with no alias, no fallback to a former name
  and no unique-ID migration. Sixteen entities are recreated under their new
  unique IDs; their display names, devices, categories, units, bounds and
  icons are unchanged. Writes before this change failed because the gateway
  accepted neither the former values nor a second spelling.

  | Setter | Control | Former key | Key sent |
  |---|---|---|---|
  | `set_system_config` | HC Bivalence Point | `hc_bivalence_point_c` | `heating_outdoor_bivalence_temperature_limit` |
  | `set_system_config` | DHW Bivalence Point | `dhw_bivalence_point_c` | `dhw_outdoor_bivalence_temperature_limit` |
  | `set_system_config` | HC Emergency Temperature | `hc_emergency_temp_c` | `heating_flow_temperature_setpoint_maximum_at_heat_pump_fault` |
  | `set_system_config` | HWC Max Flow Temperature | `hwc_max_flow_temp_c` | `dhw_flow_temperature_setpoint_maximum` |
  | `set_system_config` | Max Room Humidity | `max_room_humidity_pct` | `room_humidity_limit_maximum` |
  | `set_system_config` | Installer Phone | `installer_phone` | `installer_phone_number` |
  | `set_system_config` | Installer Menu Code | `installer_menu_code` | `installer_access_code` |
  | `set_boiler_config` | CH Max Flow Setpoint | `flowset_hc_max_c` | `maximum_heating_flow_temperature_setpoint` |
  | `set_boiler_config` | DHW Max Flow Setpoint | `flowset_hwc_max_c` | `maximum_dhw_flow_temperature_setpoint` |
  | `set_boiler_config` | CH Partload | `partload_hc_kw` | `heating_partial_load_power` |
  | `set_boiler_config` | DHW Partload | `partload_hwc_kw` | `dhw_partial_load_power` |
  | `set_boiler_config` | Boiler Installer Phone | `phone_number` | `installer_phone_number` |

  `maintenance_date` and `installer_name` were already canonical. The Boiler
  Installer Menu Code control keeps sending `installer_menu_code`: the meaning
  of its register (B509 selector 0x4904) is open, so the gateway keeps its
  current key for it and this write keeps failing as before.

  | Published system field (former) | Field read |
  |---|---|
  | `system.state.system_water_pressure` | `system.state.water_pressure` |
  | `system.state.system_flow_temperature` | `system.state.flow_temperature` |
  | `system.config.adaptive_heating_curve` | `system.config.adaptive_heating_curve_enabled` |
  | `system.config.heating_circuit_bivalence_point` | `system.config.heating_outdoor_bivalence_temperature_limit` |
  | `system.config.max_room_humidity` | `system.config.room_humidity_limit_maximum` |
  | `system.config.installer_menu_code` | `system.config.installer_access_code` |
  | `system.properties.system_scheme` | `system.properties.hydraulic_scheme_code` |
  | `system.properties.module_configuration_vr71` | `system.properties.functional_module_configuration_code` |

  The boiler numbers and the two installer phone texts read their
  unchanged fields (`flowset_hc_max_c`, `flowset_hwc_max_c`,
  `partload_hc_kw`, `partload_hwc_kw`, `phone_number`, `installer_phone`)
  and write the keys above.

  | Former unique ID (after `<entry_id>-`) | New unique ID |
  |---|---|
  | `system-sensor-system_water_pressure` | `system-sensor-water_pressure` |
  | `system-sensor-system_flow_temperature` | `system-sensor-flow_temperature` |
  | `system-sensor-system_scheme` | `system-sensor-hydraulic_scheme_code` |
  | `system-binary-adaptive_heating_curve` | `system-binary-adaptive_heating_curve_enabled` |
  | `system-number-hc_bivalence_point_c` | `system-number-heating_outdoor_bivalence_temperature_limit` |
  | `system-number-dhw_bivalence_point_c` | `system-number-dhw_outdoor_bivalence_temperature_limit` |
  | `system-number-hc_emergency_temp_c` | `system-number-heating_flow_temperature_setpoint_maximum_at_heat_pump_fault` |
  | `system-number-hwc_max_flow_temp_c` | `system-number-dhw_flow_temperature_setpoint_maximum` |
  | `system-number-max_room_humidity_pct` | `system-number-room_humidity_limit_maximum` |
  | `system-text-installer_phone` | `system-text-installer_phone_number` |
  | `system-text-installer_menu_code` | `system-text-installer_access_code` |
  | `boiler-number-flowset_hc_max_c` | `boiler-number-maximum_heating_flow_temperature_setpoint` |
  | `boiler-number-flowset_hwc_max_c` | `boiler-number-maximum_dhw_flow_temperature_setpoint` |
  | `boiler-number-partload_hc_kw` | `boiler-number-heating_partial_load_power` |
  | `boiler-number-partload_hwc_kw` | `boiler-number-dhw_partial_load_power` |
  | `boiler-text-phone_number` | `boiler-text-installer_phone_number` |

  `system-text-installer_name`, `system-date-maintenance_date`,
  `boiler-text-installer_menu_code` and every other unique ID are unchanged.
  The registry entries under the former unique IDs are no longer provided.
  The automatic enabling and disabling of sparse entities follows the new
  unique IDs of the system and boiler installer texts and of the boiler
  numbers.
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
- Respect a user's enable of a sparse entity and bound the FM5 mode and
  zone-parent mapping reloads. The automatic disable of a null sparse entity
  now acts only on an enabled entity that has not been seen disabled during
  the Home Assistant run: an entity that was disabled, by the integration,
  the user or anything else, and is then enabled again stays enabled across
  trusted clean-ups and reloads. Setup records that enable before forwarding
  platforms, so optional sensors with null values are created on that reload.
  The integration still enables only entities
  it disabled itself, so a user-disabled entity stays disabled. The reloads
  for an FM5 mode that enters or leaves the interpreted state and for a
  changed zone-parent mapping are each limited to one per trigger and entry
  per ten minutes, ten polls at the default scan interval. A lasting change
  still schedules exactly one reload; when the input still differs at a poll
  after the ten minutes, one further reload follows and is built from the
  value observed then, so a flapping input settles on its last observed value
  at that reload. These records live in the integration's domain data next
  to the sparse-entity record, hold across reloads, are cleared when Home
  Assistant restarts and are dropped with it for an entry that is no longer
  configured; an enable made before a restart is therefore not known to the
  next run. Unique IDs, entity IDs, device identifiers and names are
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
