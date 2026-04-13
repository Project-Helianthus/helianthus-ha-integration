"""Data coordinator for Helianthus."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_EXTENDED_V3 = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    device_id
    display_name
    product_family
    product_model
    part_number
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""

QUERY_EXTENDED_V3_NO_PART = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    device_id
    display_name
    product_family
    product_model
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""

QUERY_EXTENDED_V3_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    device_id
    display_name
    product_family
    product_model
    part_number
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""

QUERY_EXTENDED_V3_NO_PART_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    device_id
    display_name
    product_family
    product_model
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""

QUERY_EXTENDED_V2 = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    device_id
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""

QUERY_EXTENDED_V2_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    device_id
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""

QUERY_BASE = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    device_id
    software_version
    hardware_version
  }
}
"""

QUERY_BASE_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    device_id
    software_version
    hardware_version
  }
}
"""

QUERY_STATUS = """
query Status {
  daemon_status {
    status
    firmware_version
    updates_available
    initiator_address
  }
  adapter_status {
    status
    firmware_version
    updates_available
  }
}
"""

QUERY_STATUS_LEGACY = """
query Status {
  daemon_status {
    status
    firmware_version
    updates_available
  }
  adapter_status {
    status
    firmware_version
    updates_available
  }
}
"""

QUERY_SEMANTIC = """
query Semantic {
  zones {
    id
    name
    state {
      current_temp_c
      current_humidity_pct
      hvac_action
      special_function
      heating_demand_pct
      valve_position_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
      room_temperature_zone_mapping
      quick_veto
      quick_veto_setpoint
      quick_veto_duration
      quick_veto_expiry
      holiday_start_date
      holiday_end_date
      holiday_setpoint
      holiday_start_time
      holiday_end_time
    }
  }
  dhw {
    state {
      current_temp_c
      special_function
      heating_demand_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
      holiday_start_date
      holiday_end_date
    }
  }
}
"""

QUERY_SEMANTIC_NO_HOLIDAY = """
query Semantic {
  zones {
    id
    name
    state {
      current_temp_c
      current_humidity_pct
      hvac_action
      special_function
      heating_demand_pct
      valve_position_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
      room_temperature_zone_mapping
      quick_veto
      quick_veto_setpoint
      quick_veto_duration
      quick_veto_expiry
    }
  }
  dhw {
    state {
      current_temp_c
      special_function
      heating_demand_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
    }
  }
}
"""

QUERY_SEMANTIC_NO_QV = """
query Semantic {
  zones {
    id
    name
    state {
      current_temp_c
      current_humidity_pct
      hvac_action
      special_function
      heating_demand_pct
      valve_position_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
      room_temperature_zone_mapping
    }
  }
  dhw {
    state {
      current_temp_c
      special_function
      heating_demand_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
    }
  }
}
"""

QUERY_SEMANTIC_LEGACY = """
query Semantic {
  zones {
    id
    name
    state {
      current_temp_c
      current_humidity_pct
      hvac_action
      special_function
      heating_demand_pct
      valve_position_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
    }
  }
  dhw {
    state {
      current_temp_c
      special_function
      heating_demand_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
    }
  }
}
"""

_HOLIDAY_FIELDS = ["holiday_start_date", "holiday_end_date", "holiday_setpoint", "holiday_start_time", "holiday_end_time"]
_QV_FIELDS = ["quick_veto", "quick_veto_setpoint", "quick_veto_duration", "quick_veto_expiry"]
_SEMANTIC_RECOVERABLE_FIELDS = _HOLIDAY_FIELDS + _QV_FIELDS + ["room_temperature_zone_mapping"]

QUERY_CIRCUITS = """
query Circuits {
  circuits {
    index
    circuit_type
    has_mixer
    managing_device {
      role
      device_id
      address
    }
    state {
      pump_active
      mixer_position_pct
      flow_temperature_c
      flow_setpoint_c
      calc_flow_temp_c
      circuit_state
      humidity
      dew_point
      pump_hours
      pump_starts
    }
    config {
      heating_curve
      flow_temp_max_c
      flow_temp_min_c
      summer_limit_c
      frost_prot_c
      room_temp_control
      cooling_enabled
    }
  }
}
"""

QUERY_RADIO_DEVICES = """
query RadioDevices {
  radio_devices {
    group
    instance
    slot_mode
    device_connected
    device_class_address
    device_model
    firmware_version
    hardware_identifier
    remote_control_address
    device_paired
    reception_strength
    zone_assignment
    room_temperature_c
    room_humidity_pct
  }
}
"""

QUERY_FM5 = """
query FM5Semantic {
  fm5_semantic_mode
  solar {
    collector_temperature_c
    return_temperature_c
    pump_active
    current_yield
    pump_hours
    solar_enabled
    function_mode
  }
  cylinders {
    index
    temperature_c
    max_setpoint_c
    charge_hysteresis_c
    charge_offset_c
  }
}
"""

QUERY_SYSTEM = """
query System {
  system {
    state {
      system_water_pressure
      system_flow_temperature
      outdoor_temperature
      outdoor_temperature_avg24h
      maintenance_due
      hwc_cylinder_temperature_top
      hwc_cylinder_temperature_bottom
    }
    config {
      adaptive_heating_curve
      heating_circuit_bivalence_point
      dhw_bivalence_point
      hc_emergency_temperature
      hwc_max_flow_temp_desired
      max_room_humidity
    }
    properties {
      system_scheme
      module_configuration_vr71
    }
  }
}
"""

QUERY_ENERGY = """
query Energy {
  energy_totals {
    gas { dhw { today yearly monthly } climate { today yearly monthly } }
    electric { dhw { today yearly monthly } climate { today yearly monthly } }
    solar { dhw { today yearly monthly } climate { today yearly monthly } }
  }
}
"""

QUERY_ENERGY_LEGACY = """
query Energy {
  energy_totals {
    gas { dhw { today yearly } climate { today yearly } }
    electric { dhw { today yearly } climate { today yearly } }
    solar { dhw { today yearly } climate { today yearly } }
  }
}
"""

QUERY_BOILER = """
query BoilerStatus {
  boiler_status {
    state {
      flow_temperature_c
      return_temperature_c
      central_heating_pump_active
      flame_active
      modulation_pct
      gas_valve_active
      fan_speed_rpm
      ionisation_voltage_ua
      external_pump_active
      circulation_pump_active
      storage_load_pump_pct
      diverter_valve_position_pct
    }
    config {
      flowset_hc_max_c
      flowset_hwc_max_c
      partload_hc_kw
      partload_hwc_kw
    }
    diagnostics {
      heating_status_raw
      central_heating_hours
      dhw_hours
      central_heating_starts
      dhw_starts
      pump_hours
      fan_hours
      deactivations_ifc
      deactivations_templimiter
    }
  }
}
"""

_RADIO_GROUP_ZONE_VRC = 0x09
_RADIO_GROUP_ZONE_VR92 = 0x0A
_RADIO_GROUP_INVENTORY = 0x0C
_RADIO_STALE_GRACE_CYCLES = 3

class HelianthusCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Coordinator fetching GraphQL device inventory."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_devices",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client

    async def _async_update_data(self) -> list[dict[str, Any]]:
        async def fetch(query: str) -> list[dict[str, Any]]:
            payload = await self._client.execute(query)
            if isinstance(payload, dict):
                return list(payload.get("devices", []))
            return []

        async def fetch_with_addresses(
            query_with_addresses: str, query_without_addresses: str
        ) -> list[dict[str, Any]]:
            try:
                return await fetch(query_with_addresses)
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["addresses"]):
                    return await fetch(query_without_addresses)
                raise

        async def fetch_base_devices() -> list[dict[str, Any]]:
            try:
                return await fetch_with_addresses(QUERY_BASE, QUERY_BASE_NO_ADDRESSES)
            except GraphQLClientError as exc:
                raise UpdateFailed(str(exc)) from exc
            except GraphQLResponseError as exc:
                raise UpdateFailed(str(exc)) from exc

        async def fetch_v2_devices() -> list[dict[str, Any]]:
            try:
                return await fetch_with_addresses(QUERY_EXTENDED_V2, QUERY_EXTENDED_V2_NO_ADDRESSES)
            except GraphQLClientError as exc:
                raise UpdateFailed(str(exc)) from exc
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["serial_number", "mac_address"]):
                    return await fetch_base_devices()
                raise UpdateFailed(str(exc)) from exc

        async def fetch_v3_no_part_devices() -> list[dict[str, Any]]:
            try:
                return await fetch_with_addresses(
                    QUERY_EXTENDED_V3_NO_PART,
                    QUERY_EXTENDED_V3_NO_PART_NO_ADDRESSES,
                )
            except GraphQLClientError as exc:
                raise UpdateFailed(str(exc)) from exc
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["display_name", "product_family", "product_model"]):
                    return await fetch_v2_devices()
                if _is_missing_field_error(exc.errors, ["serial_number", "mac_address"]):
                    return await fetch_base_devices()
                raise UpdateFailed(str(exc)) from exc

        try:
            return await fetch_with_addresses(QUERY_EXTENDED_V3, QUERY_EXTENDED_V3_NO_ADDRESSES)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["part_number"]):
                return await fetch_v3_no_part_devices()
            if _is_missing_field_error(exc.errors, ["display_name", "product_family", "product_model"]):
                return await fetch_v2_devices()
            if _is_missing_field_error(exc.errors, ["serial_number", "mac_address"]):
                return await fetch_base_devices()
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc


class HelianthusStatusCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator fetching GraphQL service status."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_status",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            payload = await self._client.execute(QUERY_STATUS)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["initiator_address"]):
                try:
                    payload = await self._client.execute(QUERY_STATUS_LEGACY)
                except GraphQLClientError as nested:
                    raise UpdateFailed(str(nested)) from nested
                except GraphQLResponseError as nested:
                    raise UpdateFailed(str(nested)) from nested
            else:
                raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return {"daemon": {}, "adapter": {}}
        return {
            "daemon": payload.get("daemon_status", {}) or {},
            "adapter": payload.get("adapter_status", {}) or {},
        }


class HelianthusSemanticCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching semantic zone/DHW data."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_semantic",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, Any]:
        queries = [QUERY_SEMANTIC, QUERY_SEMANTIC_NO_HOLIDAY, QUERY_SEMANTIC_NO_QV, QUERY_SEMANTIC_LEGACY]
        payload = None
        for query in queries:
            try:
                payload = await self._client.execute(query)
                break
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["zones", "dhw"]):
                    return {"zones": [], "dhw": None}
                if _is_missing_field_error(exc.errors, _SEMANTIC_RECOVERABLE_FIELDS):
                    continue
                raise UpdateFailed(str(exc)) from exc
            except GraphQLClientError as exc:
                raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return {"zones": [], "dhw": None}
        return {
            "zones": payload.get("zones", []) or [],
            "dhw": payload.get("dhw"),
        }


class HelianthusCircuitCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching semantic heating circuit data."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_circuits",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_CIRCUITS)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(
                exc.errors,
                [
                    "circuits",
                    "index",
                    "circuit_type",
                    "has_mixer",
                    "state",
                    "config",
                    "pump_active",
                    "mixer_position_pct",
                    "flow_temperature_c",
                    "flow_setpoint_c",
                    "calc_flow_temp_c",
                    "circuit_state",
                    "humidity",
                    "dew_point",
                    "pump_hours",
                    "pump_starts",
                    "heating_curve",
                    "flow_temp_max_c",
                    "flow_temp_min_c",
                    "summer_limit_c",
                    "frost_prot_c",
                    "room_temp_control",
                    "cooling_enabled",
                ],
            ):
                return {"circuits": []}
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return {"circuits": []}
        circuits = payload.get("circuits")
        if not isinstance(circuits, list):
            return {"circuits": []}
        return {
            "circuits": [
                circuit
                for circuit in circuits
                if isinstance(circuit, dict)
            ]
        }


class HelianthusRadioDeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching remote-slot radio device snapshots."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_radio_devices",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._last_by_slot: dict[tuple[int, int], dict[str, Any]] = {}
        self._stale_cycles: dict[tuple[int, int], int] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        empty = {"radio_devices": [], "radio_zone_candidates": {}}
        missing_fields = [
            "radio_devices",
            "group",
            "instance",
            "slot_mode",
            "device_connected",
            "device_class_address",
            "device_model",
            "firmware_version",
            "hardware_identifier",
            "remote_control_address",
            "device_paired",
            "reception_strength",
            "zone_assignment",
            "room_temperature_c",
            "room_humidity_pct",
        ]
        try:
            payload = await self._client.execute(QUERY_RADIO_DEVICES)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, missing_fields):
                self._last_by_slot = {}
                self._stale_cycles = {}
                return empty
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            self._last_by_slot = {}
            self._stale_cycles = {}
            return empty
        devices = payload.get("radio_devices")
        if not isinstance(devices, list):
            self._last_by_slot = {}
            self._stale_cycles = {}
            return empty
        return self._materialize_snapshot(devices)

    def apply_radio_update(self, devices: Any) -> None:
        if not isinstance(devices, list):
            return
        self.async_set_updated_data(self._materialize_snapshot(devices))

    def _materialize_snapshot(self, devices: list[Any]) -> dict[str, Any]:
        observed_by_slot: dict[tuple[int, int], dict[str, Any]] = {}
        active_by_slot: dict[tuple[int, int], dict[str, Any]] = {}
        for raw_device in devices:
            if not isinstance(raw_device, dict):
                continue
            normalized = _normalize_radio_device(raw_device)
            if normalized is None:
                continue
            slot = (normalized["group"], normalized["instance"])
            observed_by_slot[slot] = normalized
            if _is_active_radio_device(normalized):
                active_by_slot[slot] = normalized

        combined: dict[tuple[int, int], dict[str, Any]] = {}
        next_stale_cycles: dict[tuple[int, int], int] = {}
        for slot, device in active_by_slot.items():
            current = dict(device)
            current["stale_cycles"] = 0
            current["radio_bus_key"] = _radio_bus_key(slot[0], slot[1])
            combined[slot] = current
            next_stale_cycles[slot] = 0

        for slot, previous in self._last_by_slot.items():
            if slot in combined:
                continue
            stale = self._stale_cycles.get(slot, 0) + 1
            if stale > _RADIO_STALE_GRACE_CYCLES:
                continue
            carried = dict(previous)
            observed = observed_by_slot.get(slot)
            if isinstance(observed, dict):
                carried.update(observed)
            if observed is None or not _is_active_radio_device(observed):
                carried["device_connected"] = False
            carried["stale_cycles"] = stale
            carried["radio_bus_key"] = _radio_bus_key(slot[0], slot[1])
            combined[slot] = carried
            next_stale_cycles[slot] = stale

        sorted_slots = sorted(combined.keys(), key=lambda item: (item[0], item[1]))
        radio_devices = [combined[slot] for slot in sorted_slots]
        candidates = _build_radio_zone_candidates(active_by_slot)

        self._last_by_slot = {
            slot: {
                key: value
                for key, value in combined[slot].items()
                if key not in {"stale_cycles"}
            }
            for slot in sorted_slots
        }
        self._stale_cycles = dict(next_stale_cycles)
        return {
            "radio_devices": radio_devices,
            "radio_zone_candidates": candidates,
        }


class HelianthusFM5Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching FM5 mode plus interpreted solar/cylinder semantics."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_fm5",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, Any]:
        empty = {
            "fm5_semantic_mode": "ABSENT",
            "solar": None,
            "cylinders": [],
        }
        missing_fields = [
            "fm5_semantic_mode",
            "solar",
            "cylinders",
            "collector_temperature_c",
            "return_temperature_c",
            "pump_active",
            "current_yield",
            "pump_hours",
            "solar_enabled",
            "function_mode",
            "index",
            "temperature_c",
            "max_setpoint_c",
            "charge_hysteresis_c",
            "charge_offset_c",
        ]
        try:
            payload = await self._client.execute(QUERY_FM5)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, missing_fields):
                return empty
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return empty

        mode = str(payload.get("fm5_semantic_mode") or "ABSENT").strip().upper()
        if mode not in {"INTERPRETED", "GPIO_ONLY", "ABSENT"}:
            mode = "ABSENT"

        if mode != "INTERPRETED":
            return {
                "fm5_semantic_mode": mode,
                "solar": None,
                "cylinders": [],
            }

        solar = payload.get("solar")
        if not isinstance(solar, dict):
            solar = None
        cylinders = payload.get("cylinders")
        normalized_cylinders: list[dict[str, Any]] = []
        if isinstance(cylinders, list):
            for cylinder in cylinders:
                if not isinstance(cylinder, dict):
                    continue
                index = _parse_optional_int(cylinder.get("index"))
                if index is None or index < 0:
                    continue
                normalized = dict(cylinder)
                normalized["index"] = index
                normalized_cylinders.append(normalized)
        normalized_cylinders.sort(key=lambda item: int(item.get("index") or 0))

        return {
            "fm5_semantic_mode": mode,
            "solar": solar,
            "cylinders": normalized_cylinders,
        }


QUERY_SYSTEM_INSTALLER = """
query SystemInstaller {
  system {
    config {
      maintenance_date
      installer_name
      installer_phone
    }
  }
}
"""

QUERY_SYSTEM_SENSITIVE = """
query SystemSensitive {
  system {
    config {
      installer_menu_code
    }
  }
}
"""


class HelianthusSystemCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching semantic system data."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_system",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._system_installer_available: bool | None = None
        self._system_sensitive_available: bool | None = None

    @property
    def system_installer_available(self) -> bool:
        return self._system_installer_available is not False

    @property
    def system_sensitive_available(self) -> bool:
        return self._system_sensitive_available is not False

    async def _async_update_data(self) -> dict[str, Any]:
        empty = {"state": {}, "config": {}, "properties": {}}
        missing_fields = [
            "system",
            "state",
            "config",
            "properties",
            "system_water_pressure",
            "system_flow_temperature",
            "outdoor_temperature",
            "outdoor_temperature_avg24h",
            "maintenance_due",
            "hwc_cylinder_temperature_top",
            "hwc_cylinder_temperature_bottom",
            "adaptive_heating_curve",
            "heating_circuit_bivalence_point",
            "dhw_bivalence_point",
            "hc_emergency_temperature",
            "hwc_max_flow_temp_desired",
            "max_room_humidity",
            "system_scheme",
            "module_configuration_vr71",
        ]

        try:
            payload = await self._client.execute(QUERY_SYSTEM)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, missing_fields):
                return empty
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return empty

        system = payload.get("system")
        if not isinstance(system, dict):
            return empty

        state = system.get("state")
        config = system.get("config")
        properties = system.get("properties")
        result = {
            "state": state if isinstance(state, dict) else {},
            "config": config if isinstance(config, dict) else {},
            "properties": properties if isinstance(properties, dict) else {},
        }

        # Optional installer query (backward-compat with older gateways).
        if self._system_installer_available is not False:
            try:
                inst_payload = await self._client.execute(QUERY_SYSTEM_INSTALLER)
                inst_sys = inst_payload.get("system", {}) if isinstance(inst_payload, dict) else {}
                inst_cfg = inst_sys.get("config", {}) if isinstance(inst_sys, dict) else {}
                if isinstance(inst_cfg, dict):
                    result["config"].update(inst_cfg)
                if self._system_installer_available is None:
                    self._system_installer_available = True
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["maintenance_date", "installer_name", "installer_phone"]):
                    self._system_installer_available = False
            except GraphQLClientError:
                pass  # transient — retry next cycle

        # Optional sensitive query (independent of installer query).
        if self._system_sensitive_available is not False:
            try:
                sens_payload = await self._client.execute(QUERY_SYSTEM_SENSITIVE)
                sens_sys = sens_payload.get("system", {}) if isinstance(sens_payload, dict) else {}
                sens_cfg = sens_sys.get("config", {}) if isinstance(sens_sys, dict) else {}
                if isinstance(sens_cfg, dict):
                    result["config"].update(sens_cfg)
                if self._system_sensitive_available is None:
                    self._system_sensitive_available = True
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["installer_menu_code"]):
                    self._system_sensitive_available = False
            except GraphQLClientError:
                pass  # transient — retry next cycle

        return result


class HelianthusEnergyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching energy totals."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_energy",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._last_valid_energy_totals: dict[str, Any] | None = None
        self._monthly_supported: bool = True

    async def _async_update_data(self) -> dict[str, Any]:
        query = QUERY_ENERGY if self._monthly_supported else QUERY_ENERGY_LEGACY
        try:
            payload = await self._client.execute(query)
        except GraphQLResponseError as exc:
            if self._monthly_supported and _is_missing_field_error(
                exc.errors, ["monthly"]
            ):
                self._monthly_supported = False
                return await self._async_update_data()
            if _is_missing_field_error(exc.errors, ["energy_totals"]):
                return self._hold_last_valid_energy_totals()
            return self._hold_last_valid_energy_totals()
        except GraphQLClientError:
            return self._hold_last_valid_energy_totals()

        totals = _normalize_energy_totals_payload(payload)
        if totals is None:
            return self._hold_last_valid_energy_totals()

        self._last_valid_energy_totals = deepcopy(totals)
        return {"energy_totals": deepcopy(totals)}

    def _hold_last_valid_energy_totals(self) -> dict[str, Any]:
        totals = getattr(self, "_last_valid_energy_totals", None)
        if isinstance(totals, dict):
            return {"energy_totals": deepcopy(totals)}
        return {"energy_totals": None}


QUERY_BOILER_INSTALLER = """
query BoilerInstaller {
  boiler_status {
    config {
      phone_number
      hours_till_service
    }
  }
}
"""

QUERY_BOILER_SENSITIVE = """
query BoilerSensitive {
  boiler_status {
    config {
      installer_menu_code
    }
  }
}
"""


class HelianthusBoilerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching boiler semantic status."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_boiler",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self.boiler_supported = True
        self._boiler_installer_available: bool | None = None
        self._boiler_sensitive_available: bool | None = None

    @property
    def client(self) -> GraphQLClient:
        return self._client

    @property
    def boiler_installer_available(self) -> bool:
        return self._boiler_installer_available is not False

    @property
    def boiler_sensitive_available(self) -> bool:
        return self._boiler_sensitive_available is not False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_BOILER)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(
                exc.errors,
                [
                    "boiler_status",
                    "flow_temperature_c",
                    "return_temperature_c",
                    "central_heating_pump_active",
                    "flame_active",
                    "modulation_pct",
                    "gas_valve_active",
                    "fan_speed_rpm",
                    "ionisation_voltage_ua",
                    "external_pump_active",
                    "circulation_pump_active",
                    "storage_load_pump_pct",
                    "diverter_valve_position_pct",
                    "flowset_hc_max_c",
                    "flowset_hwc_max_c",
                    "partload_hc_kw",
                    "partload_hwc_kw",
                    "heating_status_raw",
                    "central_heating_hours",
                    "dhw_hours",
                    "central_heating_starts",
                    "dhw_starts",
                    "pump_hours",
                    "fan_hours",
                    "deactivations_ifc",
                    "deactivations_templimiter",
                ],
            ):
                self.boiler_supported = False
                return {"boiler_status": None}
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            self.boiler_supported = False
            return {"boiler_status": None}
        self.boiler_supported = True
        result = {"boiler_status": payload.get("boiler_status")}

        boiler_status = result.get("boiler_status")
        if not isinstance(boiler_status, dict):
            return result
        config = boiler_status.get("config")
        if not isinstance(config, dict):
            config = {}
            boiler_status["config"] = config

        # Optional installer query (backward-compat).
        if self._boiler_installer_available is not False:
            try:
                inst_payload = await self._client.execute(QUERY_BOILER_INSTALLER)
                inst_boiler = inst_payload.get("boiler_status", {}) if isinstance(inst_payload, dict) else {}
                inst_cfg = inst_boiler.get("config", {}) if isinstance(inst_boiler, dict) else {}
                if isinstance(inst_cfg, dict):
                    config.update(inst_cfg)
                if self._boiler_installer_available is None:
                    self._boiler_installer_available = True
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["phone_number", "hours_till_service"]):
                    self._boiler_installer_available = False
            except GraphQLClientError:
                pass

        # Optional sensitive query (independent).
        if self._boiler_sensitive_available is not False:
            try:
                sens_payload = await self._client.execute(QUERY_BOILER_SENSITIVE)
                sens_boiler = sens_payload.get("boiler_status", {}) if isinstance(sens_payload, dict) else {}
                sens_cfg = sens_boiler.get("config", {}) if isinstance(sens_boiler, dict) else {}
                if isinstance(sens_cfg, dict):
                    config.update(sens_cfg)
                if self._boiler_sensitive_available is None:
                    self._boiler_sensitive_available = True
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["installer_menu_code"]):
                    self._boiler_sensitive_available = False
            except GraphQLClientError:
                pass

        return result


QUERY_SCHEDULES = """
query Schedules {
  schedules {
    programs {
      zone
      hc
      config {
        max_slots
        time_resolution
        min_duration
        has_temperature
        temp_slots
        min_temp_c
        max_temp_c
      }
      slots_used
      days {
        weekday
        slots {
          start_hour
          start_minute
          end_hour
          end_minute
          temperature_c
        }
      }
    }
  }
}
"""


class HelianthusScheduleCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching B555 timer schedule data."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_schedule",
            update_interval=timedelta(seconds=max(scan_interval, 300)),
        )
        self._client = client
        self.schedule_supported = True

    async def _async_update_data(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"programs": []}
        if not self.schedule_supported:
            return empty

        try:
            payload = await self._client.execute(QUERY_SCHEDULES)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["schedules", "programs"]):
                self.schedule_supported = False
                return empty
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return empty

        schedules = payload.get("schedules")
        if not isinstance(schedules, dict):
            return empty

        programs = schedules.get("programs")
        if not isinstance(programs, list):
            return empty

        return {"programs": programs}


def _parse_optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_radio_device(raw: dict[str, Any]) -> dict[str, Any] | None:
    group = _parse_optional_int(raw.get("group"))
    instance = _parse_optional_int(raw.get("instance"))
    if group is None or instance is None:
        return None
    if group < 0 or group > 0xFF or instance < 0 or instance > 0xFF:
        return None

    normalized: dict[str, Any] = {
        "group": group,
        "instance": instance,
        "slot_mode": str(raw.get("slot_mode") or "").strip() or "active",
    }

    connected = raw.get("device_connected")
    if isinstance(connected, bool):
        normalized["device_connected"] = connected
    class_address = _parse_optional_int(raw.get("device_class_address"))
    if class_address is not None and 0 <= class_address <= 0xFF:
        normalized["device_class_address"] = class_address

    device_model = str(raw.get("device_model") or "").strip()
    if device_model:
        normalized["device_model"] = device_model
    firmware = str(raw.get("firmware_version") or "").strip()
    if firmware:
        normalized["firmware_version"] = firmware

    hardware_identifier = _parse_optional_int(raw.get("hardware_identifier"))
    if hardware_identifier is not None and hardware_identifier >= 0:
        normalized["hardware_identifier"] = hardware_identifier
    remote_control_address = _parse_optional_int(raw.get("remote_control_address"))
    if remote_control_address is not None and remote_control_address >= 0:
        normalized["remote_control_address"] = remote_control_address
    paired = raw.get("device_paired")
    if isinstance(paired, bool):
        normalized["device_paired"] = paired
    reception_strength = _parse_optional_int(raw.get("reception_strength"))
    if reception_strength is not None:
        normalized["reception_strength"] = reception_strength
    zone_assignment = _parse_optional_int(raw.get("zone_assignment"))
    if zone_assignment is not None and zone_assignment >= 0:
        normalized["zone_assignment"] = zone_assignment
    room_temperature = _parse_optional_float(raw.get("room_temperature_c"))
    if room_temperature is not None:
        normalized["room_temperature_c"] = room_temperature
    room_humidity = _parse_optional_float(raw.get("room_humidity_pct"))
    if room_humidity is not None:
        normalized["room_humidity_pct"] = room_humidity

    return normalized


def _has_radio_identity_evidence(device: dict[str, Any]) -> bool:
    class_address = _parse_optional_int(device.get("device_class_address"))
    if class_address == 0x26:
        return True
    if str(device.get("device_model") or "").strip():
        return True
    if str(device.get("firmware_version") or "").strip():
        return True
    if _parse_optional_int(device.get("hardware_identifier")) is not None:
        return True
    return False


def _is_active_radio_device(device: dict[str, Any]) -> bool:
    group = _parse_optional_int(device.get("group"))
    if group is None:
        return False
    connected = device.get("device_connected") is True
    if group in (_RADIO_GROUP_ZONE_VRC, _RADIO_GROUP_ZONE_VR92):
        return connected
    if group == _RADIO_GROUP_INVENTORY:
        return _has_radio_identity_evidence(device)
    return connected or _has_radio_identity_evidence(device)


def _radio_bus_key(group: int, instance: int) -> str:
    return f"g{group:02x}-i{instance:02d}"


def _build_radio_zone_candidates(
    active_by_slot: dict[tuple[int, int], dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    by_zone: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for (_, _), device in active_by_slot.items():
        group = _parse_optional_int(device.get("group"))
        instance = _parse_optional_int(device.get("instance"))
        if group not in (_RADIO_GROUP_ZONE_VRC, _RADIO_GROUP_ZONE_VR92):
            continue
        if instance is None:
            continue
        if device.get("device_connected") is not True:
            continue
        zone_assignment = _parse_optional_int(device.get("zone_assignment"))
        if zone_assignment is None or zone_assignment <= 0:
            continue
        zone_instance = zone_assignment - 1
        by_zone[zone_instance].append(
            {
                "group": group,
                "instance": instance,
                "remote_control_address": _parse_optional_int(device.get("remote_control_address")),
                "radio_bus_key": _radio_bus_key(group, instance),
            }
        )

    out: dict[int, list[dict[str, Any]]] = {}
    for zone_instance, candidates in by_zone.items():
        candidates.sort(
            key=lambda item: (
                int(item.get("group") or 0),
                (
                    int(item["remote_control_address"])
                    if isinstance(item.get("remote_control_address"), int)
                    else 255
                ),
                int(item.get("instance") or 0),
            )
        )
        out[zone_instance] = candidates
    return out


def _is_missing_field_error(errors: object, fields: list[str]) -> bool:
    if not isinstance(errors, list):
        return False
    for error in errors:
        message = ""
        if isinstance(error, dict):
            message = str(error.get("message", ""))
        else:
            message = str(error)
        for field in fields:
            if f'Cannot query field "{field}"' in message:
                return True
    return False


def _normalize_energy_totals_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    totals = payload.get("energy_totals")
    if not isinstance(totals, dict):
        return None

    for channel_name in ("gas", "electric", "solar"):
        channel = totals.get(channel_name)
        if not isinstance(channel, dict):
            return None
        for usage in ("dhw", "climate"):
            series = channel.get(usage)
            if not isinstance(series, dict):
                return None
            if "today" not in series or "yearly" not in series:
                return None
    return totals


QUERY_ADAPTER_HARDWARE_INFO = """
query AdapterHardwareInfo {
  adapter_hardware_info {
    firmware_version
    firmware_checksum
    bootloader_version
    bootloader_checksum
    hardware_id
    hardware_config
    features
    jumpers
    jumper_flags
    is_wifi
    is_ethernet
    temperature_c
    supply_voltage_mv
    bus_voltage_max_dv
    bus_voltage_min_dv
    reset_cause
    reset_cause_code
    restart_count
    wifi_rssi_dbm
    last_identity_query
    last_telemetry_query
    version_response_len
    info_supported
  }
}
"""

QUERY_ADAPTER_HARDWARE_INFO_MINIMAL = """
query AdapterHardwareInfo {
  adapter_hardware_info {
    firmware_version
    is_wifi
    is_ethernet
    info_supported
    version_response_len
  }
}
"""

_ADAPTER_HARDWARE_INFO_DETAILED_ONLY_FIELDS = [
    "firmware_checksum",
    "bootloader_version",
    "bootloader_checksum",
    "hardware_id",
    "hardware_config",
    "features",
    "jumpers",
    "jumper_flags",
    "temperature_c",
    "supply_voltage_mv",
    "bus_voltage_max_dv",
    "bus_voltage_min_dv",
    "reset_cause",
    "reset_cause_code",
    "restart_count",
    "wifi_rssi_dbm",
    "last_identity_query",
    "last_telemetry_query",
]

_ADAPTER_HARDWARE_INFO_REPROBE_INITIAL_DELAY_S = 300.0
_ADAPTER_HARDWARE_INFO_REPROBE_MAX_DELAY_S = 3600.0


class HelianthusAdapterInfoCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator fetching adapter hardware telemetry via GraphQL."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_adapter_info",
            update_interval=timedelta(seconds=max(scan_interval, 60)),
        )
        self._client = client
        self._hardware_info_supported: bool | None = None
        self._hardware_info_reprobe_at: float | None = None
        self._hardware_info_reprobe_delay_s = _ADAPTER_HARDWARE_INFO_REPROBE_INITIAL_DELAY_S

    async def _async_update_data(self) -> dict[str, Any] | None:
        now = time.monotonic()
        if self._hardware_info_reprobe_at is not None and now < self._hardware_info_reprobe_at:
            return None

        query = (
            QUERY_ADAPTER_HARDWARE_INFO_MINIMAL
            if self._hardware_info_supported is False
            else QUERY_ADAPTER_HARDWARE_INFO
        )
        try:
            payload = await self._client.execute(query)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["adapter_hardware_info"]):
                self._schedule_hardware_info_reprobe(now)
                return None
            if query == QUERY_ADAPTER_HARDWARE_INFO:
                if _is_missing_field_error(
                    exc.errors,
                    _ADAPTER_HARDWARE_INFO_DETAILED_ONLY_FIELDS,
                ):
                    self._hardware_info_supported = False
                try:
                    payload = await self._client.execute(QUERY_ADAPTER_HARDWARE_INFO_MINIMAL)
                except GraphQLResponseError as minimal_exc:
                    if _is_missing_field_error(minimal_exc.errors, ["adapter_hardware_info"]):
                        self._schedule_hardware_info_reprobe(now)
                    return None
                except GraphQLClientError:
                    return None
                self._reset_hardware_info_reprobe()
            else:
                return None
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return None

        info = payload.get("adapter_hardware_info")
        if not isinstance(info, dict):
            return None

        self._reset_hardware_info_reprobe()
        return info

    def _schedule_hardware_info_reprobe(self, now: float) -> None:
        delay_s = self._hardware_info_reprobe_delay_s
        self._hardware_info_reprobe_at = now + delay_s
        self._hardware_info_reprobe_delay_s = min(
            delay_s * 2,
            _ADAPTER_HARDWARE_INFO_REPROBE_MAX_DELAY_S,
        )

    def _reset_hardware_info_reprobe(self) -> None:
        self._hardware_info_reprobe_at = None
        self._hardware_info_reprobe_delay_s = _ADAPTER_HARDWARE_INFO_REPROBE_INITIAL_DELAY_S
