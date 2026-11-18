# Changelog

## Unreleased

- The system update, the energy update, and the boiler update each send one
  query. The hot-water bound is part of the system query. A rejected bound
  fails that update. The installer query, the sensitive query, and the gateway
  metadata query stay separate. Each is sent when the recorded schema offers
  every path it selects. A resolver error on one of those queries leaves the
  system or boiler update in place and does not change which entities stay
  available. The energy update keeps the last accepted totals when a read
  fails, and returns no totals before the first accepted sample.

- The status update and the zones update choose one query from the paths
  discovery recorded for the connection. When every status path is present,
  the status update sends the full status query once and reads admission
  from the payload. When a status path is absent, it sends the shorter
  status query once and marks admission untrusted. A rejected status field
  fails that update. When every zones path is present, the zones update
  sends its one query. When a zones path is absent, the update returns no
  zones and no hot water, and one warning names the path. A later poll does
  not warn again. A rejected zones field fails that update.

- After the gateway connection is up, setup reads the query schema once and
  builds the dotted field paths of the query root. A domain whose query needs
  a path the schema does not offer is unavailable for that connection: setup
  logs one warning and raises `ConfigEntryNotReady` naming the path. The
  device inventory sends one device query. A later rejection of a path that
  discovery offered fails that update and reloads the entry once. A connection
  failure whose resource release fails, with the platforms confirmed unloaded,
  raises that release failure. While the platforms of a failed setup stay
  loaded, the entry stays retryable and keeps them.

- Read the heating circuit, zone and hot-water state and configuration fields
  under the names the gateway publishes, the docs-ebus register names, with no
  alias, no fallback query of a former name and no unique-ID migration. Units
  leave the field names and stay on the entities. The circuit query and its
  missing-field list, the zone and hot-water queries, their step-down field
  lists and subscriptions, the circuit sensor descriptors and the read keys of
  the circuit numbers, the room temperature control select, the mixing valve,
  the circuit pump binary sensor, the circuit pump fan, the cooling switch, the
  zone climate, the zone valve sensors and valve, the hot-water heater and the
  hot-water status sensor use the published names; the integration's copies of
  the gateway's register-derived name table, its GraphQL schema and its
  circuit snapshot equal the gateway's files. Nine entities whose unique ID
  carried a renamed field are recreated under their new unique IDs; their
  display names, state attribute names, devices, categories, units, bounds and
  icons are unchanged. The numbers, the select and the cooling switch keep the
  unique ID of their write key, and the write keys are unchanged.
  `calc_flow_temp_c`, `circuit_state` and the derived zone and hot-water fields
  (among them the zone `operating_mode`, `preset` and `target_temp_c`) keep
  their names.

  | Former field | Field read |
  |---|---|
  | `circuits.state.pump_active` | `circuits.state.circuit_pump_unknown_8` |
  | `circuits.state.mixer_position_pct` | `circuits.state.circuit_mixer_position` |
  | `circuits.state.flow_temperature_c` | `circuits.state.circuit_flow_temperature` |
  | `circuits.state.flow_setpoint_c` | `circuits.state.circuit_flow_temperature_setpoint` |
  | `circuits.state.humidity` | `circuits.state.circuit_room_humidity` |
  | `circuits.state.dew_point` | `circuits.state.circuit_dew_point_temperature` |
  | `circuits.state.pump_hours` | `circuits.state.circuit_pump_operating_time_total` |
  | `circuits.state.pump_starts` | `circuits.state.circuit_pump_start_count_total` |
  | `circuits.config.heating_curve` | `circuits.config.circuit_heating_curve_factor` |
  | `circuits.config.flow_temp_max_c` | `circuits.config.heating_circuit_flow_temperature_setpoint_maximum` |
  | `circuits.config.flow_temp_min_c` | `circuits.config.circuit_flow_temperature_setpoint_minimum` |
  | `circuits.config.summer_limit_c` | `circuits.config.heating_circuit_outdoor_temperature_limit_maximum` |
  | `circuits.config.room_temp_control` | `circuits.config.circuit_room_temperature_influence_mode` |
  | `circuits.config.cooling_enabled` | `circuits.config.circuit_cooling_enabled` |
  | `zones.state.special_function` | `zones.state.zone_special_function_mode` |
  | `zones.state.valve_position_pct` | `zones.state.zone_valve_position` |
  | `zones.config.quick_veto_setpoint` | `zones.config.zone_room_temperature_setpoint_at_quick_veto` |
  | `zones.config.quick_veto_duration` | `zones.config.zone_quick_veto_time` |
  | `zones.config.holiday_start_date` | `zones.config.zone_holiday_start_date` |
  | `zones.config.holiday_end_date` | `zones.config.zone_holiday_end_date` |
  | `zones.config.holiday_setpoint` | `zones.config.zone_room_temperature_setpoint_at_holiday` |
  | `zones.config.holiday_start_time` | `zones.config.zone_holiday_start_clock_time` |
  | `zones.config.holiday_end_time` | `zones.config.zone_holiday_end_clock_time` |
  | `dhw.state.current_temp_c` | `dhw.state.dhw_temperature` |
  | `dhw.state.special_function` | `dhw.state.dhw_special_function_mode` |
  | `dhw.config.operating_mode` | `dhw.config.dhw_operating_mode` |
  | `dhw.config.target_temp_c` | `dhw.config.dhw_temperature_setpoint` |
  | `dhw.config.holiday_start_date` | `dhw.config.dhw_holiday_start_date` |
  | `dhw.config.holiday_end_date` | `dhw.config.dhw_holiday_end_date` |

  | Former unique ID (after `<entry_id>-`) | New unique ID |
  |---|---|
  | `circuit-<index>-binary-pump_active` | `circuit-<index>-binary-circuit_pump_unknown_8` |
  | `circuit-<index>-sensor-flow_temperature_c` | `circuit-<index>-sensor-circuit_flow_temperature` |
  | `circuit-<index>-sensor-flow_setpoint_c` | `circuit-<index>-sensor-circuit_flow_temperature_setpoint` |
  | `circuit-<index>-sensor-mixer_position_pct` | `circuit-<index>-sensor-circuit_mixer_position` |
  | `circuit-<index>-sensor-humidity` | `circuit-<index>-sensor-circuit_room_humidity` |
  | `circuit-<index>-sensor-dew_point` | `circuit-<index>-sensor-circuit_dew_point_temperature` |
  | `circuit-<index>-sensor-pump_hours` | `circuit-<index>-sensor-circuit_pump_operating_time_total` |
  | `circuit-<index>-sensor-pump_starts` | `circuit-<index>-sensor-circuit_pump_start_count_total` |
  | `zone-<zone_id>-sensor-valve_position_pct` | `zone-<zone_id>-sensor-zone_valve_position` |

  The integration does not rewrite registry entries; an existing installation
  keeps the former unique IDs as orphaned registry entries until they are
  moved outside the integration.
- Read the remaining system fields and the boiler state, configuration and
  diagnostics fields under the names the gateway publishes, the docs-ebus
  register names, with no alias, no fallback query of a former name and no
  unique-ID migration. Units leave the field names and stay on the entities.
  The system and boiler queries, their missing-field lists, the boiler
  subscription, the sensor descriptors, the read keys of the numbers, texts,
  binary sensors, the burner fan and the diverter valve, and the sparse
  unique-ID patterns use the published names; the integration's copy of the
  gateway's register-derived name table equals the gateway's file, and the
  recorded gateway schema that every GraphQL document of the integration is
  validated against is a new recording with these names. Nineteen entities
  whose unique ID carried a renamed field are recreated under their new
  unique IDs; their display names, devices, categories, units, bounds and
  icons are unchanged. Numbers and texts keep the unique ID of their write
  key, and the write keys are unchanged.

  | Former field | Field read |
  |---|---|
  | `system.state.outdoor_temperature_avg24h` | `system.state.outdoor_temperature_daily_average` |
  | `system.state.maintenance_due` | `system.state.maintenance_active` |
  | `system.state.hwc_cylinder_temperature_top` | `system.state.dhw_cylinder_top_temperature` |
  | `system.state.hwc_cylinder_temperature_bottom` | `system.state.dhw_cylinder_bottom_temperature` |
  | `system.config.dhw_bivalence_point` | `system.config.dhw_outdoor_bivalence_temperature_limit` |
  | `system.config.dhw_bivalence_point_bound` | `system.config.dhw_outdoor_bivalence_temperature_limit_bound` |
  | `system.config.hc_emergency_temperature` | `system.config.heating_flow_temperature_setpoint_maximum_at_heat_pump_fault` |
  | `system.config.hwc_max_flow_temp_desired` | `system.config.dhw_flow_temperature_setpoint_maximum` |
  | `system.config.installer_phone` | `system.config.installer_phone_number` |
  | `boiler_status.state.flow_temperature_c` | `boiler_status.state.flow_temperature` |
  | `boiler_status.state.modulation_pct` | `boiler_status.state.modulation_setpoint` |
  | `boiler_status.state.fan_speed_rpm` | `boiler_status.state.fan_speed` |
  | `boiler_status.state.ionisation_voltage_ua` | `boiler_status.state.flame_ionization_current` |
  | `boiler_status.state.storage_load_pump_pct` | `boiler_status.state.storage_loading_pump_level` |
  | `boiler_status.state.diverter_valve_position_pct` | `boiler_status.state.diverter_valve_position` |
  | `boiler_status.config.flowset_hc_max_c` | `boiler_status.config.maximum_heating_flow_temperature_setpoint` |
  | `boiler_status.config.flowset_hwc_max_c` | `boiler_status.config.maximum_dhw_flow_temperature_setpoint` |
  | `boiler_status.config.partload_hc_kw` | `boiler_status.config.heating_partial_load_power` |
  | `boiler_status.config.partload_hwc_kw` | `boiler_status.config.dhw_partial_load_power` |
  | `boiler_status.config.phone_number` | `boiler_status.config.installer_phone_number` |
  | `boiler_status.config.hours_till_service` | `boiler_status.config.service_time_remaining` |
  | `boiler_status.config.installer_menu_code` | `boiler_status.config.central_heating_pump_mode` |
  | `boiler_status.diagnostics.central_heating_hours` | `boiler_status.diagnostics.central_heating_operating_time` |
  | `boiler_status.diagnostics.dhw_hours` | `boiler_status.diagnostics.dhw_operating_time` |
  | `boiler_status.diagnostics.central_heating_starts` | `boiler_status.diagnostics.central_heating_start_count` |
  | `boiler_status.diagnostics.dhw_starts` | `boiler_status.diagnostics.dhw_start_count` |
  | `boiler_status.diagnostics.pump_hours` | `boiler_status.diagnostics.pump_operating_time` |
  | `boiler_status.diagnostics.fan_hours` | `boiler_status.diagnostics.fan_operating_time` |
  | `boiler_status.diagnostics.deactivations_ifc` | `boiler_status.diagnostics.fire_control_deactivation_count` |
  | `boiler_status.diagnostics.deactivations_templimiter` | `boiler_status.diagnostics.temperature_limiter_deactivation_count` |
  | `boiler_status_update.state.flow_temperature_c` (subscription) | `boiler_status_update.state.flow_temperature` |

  | Former unique ID (after `<entry_id>-`) | New unique ID |
  |---|---|
  | `system-sensor-outdoor_temperature_avg24h` | `system-sensor-outdoor_temperature_daily_average` |
  | `system-sensor-hwc_cylinder_temperature_top` | `system-sensor-dhw_cylinder_top_temperature` |
  | `system-sensor-hwc_cylinder_temperature_bottom` | `system-sensor-dhw_cylinder_bottom_temperature` |
  | `system-binary-maintenance_due` | `system-binary-maintenance_active` |
  | `boiler-flow_temperature_c` | `boiler-flow_temperature` |
  | `boiler-sensor-modulation_pct` | `boiler-sensor-modulation_setpoint` |
  | `boiler-sensor-fan_speed_rpm` | `boiler-sensor-fan_speed` |
  | `boiler-sensor-ionisation_voltage_ua` | `boiler-sensor-flame_ionization_current` |
  | `boiler-sensor-storage_load_pump_pct` | `boiler-sensor-storage_loading_pump_level` |
  | `boiler-sensor-diverter_valve_position_pct` | `boiler-sensor-diverter_valve_position` |
  | `boiler-sensor-hours_till_service` | `boiler-sensor-service_time_remaining` |
  | `boiler-diag-central_heating_hours` | `boiler-diag-central_heating_operating_time` |
  | `boiler-diag-dhw_hours` | `boiler-diag-dhw_operating_time` |
  | `boiler-diag-pump_hours` | `boiler-diag-pump_operating_time` |
  | `boiler-diag-fan_hours` | `boiler-diag-fan_operating_time` |
  | `boiler-diag-central_heating_starts` | `boiler-diag-central_heating_start_count` |
  | `boiler-diag-dhw_starts` | `boiler-diag-dhw_start_count` |
  | `boiler-diag-deactivations_ifc` | `boiler-diag-fire_control_deactivation_count` |
  | `boiler-diag-deactivations_templimiter` | `boiler-diag-temperature_limiter_deactivation_count` |

  `system_off` keeps its published name. The integration does not rewrite
  registry entries; an existing installation keeps the former unique IDs as
  orphaned registry entries until they are moved outside the integration.
- Roll back a failed config-entry setup and release every resource on unload.
  Each setup phase registers what it acquires (the eeBUS admin session,
  services and lifecycle, the coordinator and helper listeners, the
  subscription task, the PV, storage and EVSE boundaries, the option-update
  listener, so a retried setup does not add a second one, the forwarded
  platforms and the semantic snapshot coordinator) with one release stack of
  the entry. A setup that fails or is cancelled releases what it acquired,
  newest first, withdraws the runtime it published and keeps the original
  error; a gateway connection failure is raised as `ConfigEntryNotReady` so
  Home Assistant retries. When the rollback's platform unload is refused, the
  platforms keep the resources they use and the runtime until the next setup
  releases them. Unload unloads the platforms first and releases
  nothing when that is refused; otherwise every resource is released once,
  a failing release no longer skips the later ones, and the first failure is
  raised. No entity or unique ID changes.
- Add the config-entry diagnostics download: one on-demand GraphQL read of
  `bus_summary`, `watch_summary`, `fm5_interpretation`,
  `vaillant_regulator_capability`, `daemon_status` and `adapter_status`, plus
  each coordinator's last update state, with the host, instance GUID, serial
  numbers, MAC addresses and M2M endpoints and file paths redacted. A failed
  read becomes an error entry. No entity, unique ID, coordinator or platform
  query changes.
- Stop selecting `frost_prot_c` in the circuit configuration query. The
  gateway no longer publishes that field, and a query that still selected it
  was refused, so the circuit coordinator returned no circuits and every
  circuit entity went unavailable. The writable frost-protection control was
  already withdrawn; no entity or unique ID changes.
- Withdraw the Boiler Installer Menu Code text control (B509 selector
  0x4904). The gateway names that register `central_heating_pump_mode` and
  marks the name provisional: its meaning is open (a pump mode or an
  installer access code), so a consumer control no longer writes it. Its
  writes already failed because the control sent a key the gateway does not
  accept. The boiler configuration is still read as before, and the system
  Installer Menu Code control is unchanged. On an existing installation the
  registry entry `<entry_id>-boiler-text-installer_menu_code` is left behind;
  the integration does not remove it.
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
