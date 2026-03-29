"""Data coordinator for Helianthus."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_EXTENDED_V3 = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    deviceId
    displayName
    productFamily
    productModel
    partNumber
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_EXTENDED_V3_NO_PART = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    deviceId
    displayName
    productFamily
    productModel
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_EXTENDED_V3_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    deviceId
    displayName
    productFamily
    productModel
    partNumber
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_EXTENDED_V3_NO_PART_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    deviceId
    displayName
    productFamily
    productModel
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_EXTENDED_V2 = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    deviceId
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_EXTENDED_V2_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    deviceId
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_BASE = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    deviceId
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_BASE_NO_ADDRESSES = """
query Devices {
  devices {
    address
    manufacturer
    deviceId
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_STATUS = """
query Status {
  daemonStatus {
    status
    firmwareVersion
    updatesAvailable
    initiatorAddress
  }
  adapterStatus {
    status
    firmwareVersion
    updatesAvailable
  }
}
"""

QUERY_STATUS_LEGACY = """
query Status {
  daemonStatus {
    status
    firmwareVersion
    updatesAvailable
  }
  adapterStatus {
    status
    firmwareVersion
    updatesAvailable
  }
}
"""

QUERY_SEMANTIC = """
query Semantic {
  zones {
    id
    name
    state {
      currentTempC
      currentHumidityPct
      hvacAction
      specialFunction
      heatingDemandPct
      valvePositionPct
    }
    config {
      operatingMode
      preset
      targetTempC
      allowedModes
      circuitType
      associatedCircuit
      roomTemperatureZoneMapping
      quickVeto
      quickVetoSetpoint
      quickVetoDuration
      quickVetoExpiry
      holidayStartDate
      holidayEndDate
      holidaySetpoint
      holidayStartTime
      holidayEndTime
    }
  }
  dhw {
    state {
      currentTempC
      specialFunction
      heatingDemandPct
    }
    config {
      operatingMode
      preset
      targetTempC
      holidayStartDate
      holidayEndDate
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
      currentTempC
      currentHumidityPct
      hvacAction
      specialFunction
      heatingDemandPct
      valvePositionPct
    }
    config {
      operatingMode
      preset
      targetTempC
      allowedModes
      circuitType
      associatedCircuit
      roomTemperatureZoneMapping
      quickVeto
      quickVetoSetpoint
      quickVetoDuration
      quickVetoExpiry
    }
  }
  dhw {
    state {
      currentTempC
      specialFunction
      heatingDemandPct
    }
    config {
      operatingMode
      preset
      targetTempC
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
      currentTempC
      currentHumidityPct
      hvacAction
      specialFunction
      heatingDemandPct
      valvePositionPct
    }
    config {
      operatingMode
      preset
      targetTempC
      allowedModes
      circuitType
      associatedCircuit
      roomTemperatureZoneMapping
    }
  }
  dhw {
    state {
      currentTempC
      specialFunction
      heatingDemandPct
    }
    config {
      operatingMode
      preset
      targetTempC
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
      currentTempC
      currentHumidityPct
      hvacAction
      specialFunction
      heatingDemandPct
      valvePositionPct
    }
    config {
      operatingMode
      preset
      targetTempC
      allowedModes
      circuitType
      associatedCircuit
    }
  }
  dhw {
    state {
      currentTempC
      specialFunction
      heatingDemandPct
    }
    config {
      operatingMode
      preset
      targetTempC
    }
  }
}
"""

_HOLIDAY_FIELDS = ["holidayStartDate", "holidayEndDate", "holidaySetpoint", "holidayStartTime", "holidayEndTime"]
_QV_FIELDS = ["quickVeto", "quickVetoSetpoint", "quickVetoDuration", "quickVetoExpiry"]
_SEMANTIC_RECOVERABLE_FIELDS = _HOLIDAY_FIELDS + _QV_FIELDS + ["roomTemperatureZoneMapping"]

QUERY_CIRCUITS = """
query Circuits {
  circuits {
    index
    circuitType
    hasMixer
    managingDevice {
      role
      deviceId
      address
    }
    state {
      pumpActive
      mixerPositionPct
      flowTemperatureC
      flowSetpointC
      calcFlowTempC
      circuitState
      humidity
      dewPoint
      pumpHours
      pumpStarts
    }
    config {
      heatingCurve
      flowTempMaxC
      flowTempMinC
      summerLimitC
      frostProtC
      roomTempControl
      coolingEnabled
    }
  }
}
"""

QUERY_RADIO_DEVICES = """
query RadioDevices {
  radioDevices {
    group
    instance
    slotMode
    deviceConnected
    deviceClassAddress
    deviceModel
    firmwareVersion
    hardwareIdentifier
    remoteControlAddress
    devicePaired
    receptionStrength
    zoneAssignment
    roomTemperatureC
    roomHumidityPct
  }
}
"""

QUERY_FM5 = """
query FM5Semantic {
  fm5SemanticMode
  solar {
    collectorTemperatureC
    returnTemperatureC
    pumpActive
    currentYield
    pumpHours
    solarEnabled
    functionMode
  }
  cylinders {
    index
    temperatureC
    maxSetpointC
    chargeHysteresisC
    chargeOffsetC
  }
}
"""

QUERY_SYSTEM = """
query System {
  system {
    state {
      systemWaterPressure
      systemFlowTemperature
      outdoorTemperature
      outdoorTemperatureAvg24h
      maintenanceDue
      hwcCylinderTemperatureTop
      hwcCylinderTemperatureBottom
    }
    config {
      adaptiveHeatingCurve
      heatingCircuitBivalencePoint
      dhwBivalencePoint
      hcEmergencyTemperature
      hwcMaxFlowTempDesired
      maxRoomHumidity
    }
    properties {
      systemScheme
      moduleConfigurationVR71
    }
  }
}
"""

QUERY_ENERGY = """
query Energy {
  energyTotals {
    gas { dhw { today yearly monthly } climate { today yearly monthly } }
    electric { dhw { today yearly monthly } climate { today yearly monthly } }
    solar { dhw { today yearly monthly } climate { today yearly monthly } }
  }
}
"""

QUERY_ENERGY_LEGACY = """
query Energy {
  energyTotals {
    gas { dhw { today yearly } climate { today yearly } }
    electric { dhw { today yearly } climate { today yearly } }
    solar { dhw { today yearly } climate { today yearly } }
  }
}
"""

QUERY_BOILER = """
query BoilerStatus {
  boilerStatus {
    state {
      flowTemperatureC
      returnTemperatureC
      centralHeatingPumpActive
      flameActive
      modulationPct
      gasValveActive
      fanSpeedRpm
      ionisationVoltageUa
      externalPumpActive
      circulationPumpActive
      storageLoadPumpPct
      diverterValvePositionPct
    }
    config {
      flowsetHcMaxC
      flowsetHwcMaxC
      partloadHcKW
      partloadHwcKW
    }
    diagnostics {
      heatingStatusRaw
      centralHeatingHours
      dhwHours
      centralHeatingStarts
      dhwStarts
      pumpHours
      fanHours
      deactivationsIFC
      deactivationsTemplimiter
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
                if _is_missing_field_error(exc.errors, ["serialNumber", "macAddress"]):
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
                if _is_missing_field_error(exc.errors, ["displayName", "productFamily", "productModel"]):
                    return await fetch_v2_devices()
                if _is_missing_field_error(exc.errors, ["serialNumber", "macAddress"]):
                    return await fetch_base_devices()
                raise UpdateFailed(str(exc)) from exc

        try:
            return await fetch_with_addresses(QUERY_EXTENDED_V3, QUERY_EXTENDED_V3_NO_ADDRESSES)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["partNumber"]):
                return await fetch_v3_no_part_devices()
            if _is_missing_field_error(exc.errors, ["displayName", "productFamily", "productModel"]):
                return await fetch_v2_devices()
            if _is_missing_field_error(exc.errors, ["serialNumber", "macAddress"]):
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
            if _is_missing_field_error(exc.errors, ["initiatorAddress"]):
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
            "daemon": payload.get("daemonStatus", {}) or {},
            "adapter": payload.get("adapterStatus", {}) or {},
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
                    "circuitType",
                    "hasMixer",
                    "state",
                    "config",
                    "pumpActive",
                    "mixerPositionPct",
                    "flowTemperatureC",
                    "flowSetpointC",
                    "calcFlowTempC",
                    "circuitState",
                    "humidity",
                    "dewPoint",
                    "pumpHours",
                    "pumpStarts",
                    "heatingCurve",
                    "flowTempMaxC",
                    "flowTempMinC",
                    "summerLimitC",
                    "frostProtC",
                    "roomTempControl",
                    "coolingEnabled",
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
        empty = {"radioDevices": [], "radioZoneCandidates": {}}
        missing_fields = [
            "radioDevices",
            "group",
            "instance",
            "slotMode",
            "deviceConnected",
            "deviceClassAddress",
            "deviceModel",
            "firmwareVersion",
            "hardwareIdentifier",
            "remoteControlAddress",
            "devicePaired",
            "receptionStrength",
            "zoneAssignment",
            "roomTemperatureC",
            "roomHumidityPct",
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
        devices = payload.get("radioDevices")
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
            current["staleCycles"] = 0
            current["radioBusKey"] = _radio_bus_key(slot[0], slot[1])
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
                carried["deviceConnected"] = False
            carried["staleCycles"] = stale
            carried["radioBusKey"] = _radio_bus_key(slot[0], slot[1])
            combined[slot] = carried
            next_stale_cycles[slot] = stale

        sorted_slots = sorted(combined.keys(), key=lambda item: (item[0], item[1]))
        radio_devices = [combined[slot] for slot in sorted_slots]
        candidates = _build_radio_zone_candidates(active_by_slot)

        self._last_by_slot = {
            slot: {
                key: value
                for key, value in combined[slot].items()
                if key not in {"staleCycles"}
            }
            for slot in sorted_slots
        }
        self._stale_cycles = dict(next_stale_cycles)
        return {
            "radioDevices": radio_devices,
            "radioZoneCandidates": candidates,
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
            "fm5SemanticMode": "ABSENT",
            "solar": None,
            "cylinders": [],
        }
        missing_fields = [
            "fm5SemanticMode",
            "solar",
            "cylinders",
            "collectorTemperatureC",
            "returnTemperatureC",
            "pumpActive",
            "currentYield",
            "pumpHours",
            "solarEnabled",
            "functionMode",
            "index",
            "temperatureC",
            "maxSetpointC",
            "chargeHysteresisC",
            "chargeOffsetC",
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

        mode = str(payload.get("fm5SemanticMode") or "ABSENT").strip().upper()
        if mode not in {"INTERPRETED", "GPIO_ONLY", "ABSENT"}:
            mode = "ABSENT"

        if mode != "INTERPRETED":
            return {
                "fm5SemanticMode": mode,
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
            "fm5SemanticMode": mode,
            "solar": solar,
            "cylinders": normalized_cylinders,
        }


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

    async def _async_update_data(self) -> dict[str, Any]:
        empty = {"state": {}, "config": {}, "properties": {}}
        missing_fields = [
            "system",
            "state",
            "config",
            "properties",
            "systemWaterPressure",
            "systemFlowTemperature",
            "outdoorTemperature",
            "outdoorTemperatureAvg24h",
            "maintenanceDue",
            "hwcCylinderTemperatureTop",
            "hwcCylinderTemperatureBottom",
            "adaptiveHeatingCurve",
            "heatingCircuitBivalencePoint",
            "dhwBivalencePoint",
            "hcEmergencyTemperature",
            "hwcMaxFlowTempDesired",
            "maxRoomHumidity",
            "systemScheme",
            "moduleConfigurationVR71",
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
        return {
            "state": state if isinstance(state, dict) else {},
            "config": config if isinstance(config, dict) else {},
            "properties": properties if isinstance(properties, dict) else {},
        }


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
            if _is_missing_field_error(exc.errors, ["energyTotals"]):
                return self._hold_last_valid_energy_totals()
            return self._hold_last_valid_energy_totals()
        except GraphQLClientError:
            return self._hold_last_valid_energy_totals()

        totals = _normalize_energy_totals_payload(payload)
        if totals is None:
            return self._hold_last_valid_energy_totals()

        self._last_valid_energy_totals = deepcopy(totals)
        return {"energyTotals": deepcopy(totals)}

    def _hold_last_valid_energy_totals(self) -> dict[str, Any]:
        totals = getattr(self, "_last_valid_energy_totals", None)
        if isinstance(totals, dict):
            return {"energyTotals": deepcopy(totals)}
        return {"energyTotals": None}


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

    @property
    def client(self) -> GraphQLClient:
        return self._client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_BOILER)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(
                exc.errors,
                [
                    "boilerStatus",
                    "flowTemperatureC",
                    "returnTemperatureC",
                    "centralHeatingPumpActive",
                    "flameActive",
                    "modulationPct",
                    "gasValveActive",
                    "fanSpeedRpm",
                    "ionisationVoltageUa",
                    "externalPumpActive",
                    "circulationPumpActive",
                    "storageLoadPumpPct",
                    "diverterValvePositionPct",
                    "flowsetHcMaxC",
                    "flowsetHwcMaxC",
                    "partloadHcKW",
                    "partloadHwcKW",
                    "heatingStatusRaw",
                    "centralHeatingHours",
                    "dhwHours",
                    "centralHeatingStarts",
                    "dhwStarts",
                    "pumpHours",
                    "fanHours",
                    "deactivationsIFC",
                    "deactivationsTemplimiter",
                ],
            ):
                self.boiler_supported = False
                return {"boilerStatus": None}
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            self.boiler_supported = False
            return {"boilerStatus": None}
        self.boiler_supported = True
        return {"boilerStatus": payload.get("boilerStatus")}


QUERY_SCHEDULES = """
query Schedules {
  schedules {
    programs {
      zone
      hc
      config {
        maxSlots
        timeResolution
        minDuration
        hasTemperature
        tempSlots
        minTempC
        maxTempC
      }
      slotsUsed
      days {
        weekday
        slots {
          startHour
          startMinute
          endHour
          endMinute
          temperatureC
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
        "slotMode": str(raw.get("slotMode") or "").strip() or "active",
    }

    connected = raw.get("deviceConnected")
    if isinstance(connected, bool):
        normalized["deviceConnected"] = connected
    class_address = _parse_optional_int(raw.get("deviceClassAddress"))
    if class_address is not None and 0 <= class_address <= 0xFF:
        normalized["deviceClassAddress"] = class_address

    device_model = str(raw.get("deviceModel") or "").strip()
    if device_model:
        normalized["deviceModel"] = device_model
    firmware = str(raw.get("firmwareVersion") or "").strip()
    if firmware:
        normalized["firmwareVersion"] = firmware

    hardware_identifier = _parse_optional_int(raw.get("hardwareIdentifier"))
    if hardware_identifier is not None and hardware_identifier >= 0:
        normalized["hardwareIdentifier"] = hardware_identifier
    remote_control_address = _parse_optional_int(raw.get("remoteControlAddress"))
    if remote_control_address is not None and remote_control_address >= 0:
        normalized["remoteControlAddress"] = remote_control_address
    paired = raw.get("devicePaired")
    if isinstance(paired, bool):
        normalized["devicePaired"] = paired
    reception_strength = _parse_optional_int(raw.get("receptionStrength"))
    if reception_strength is not None:
        normalized["receptionStrength"] = reception_strength
    zone_assignment = _parse_optional_int(raw.get("zoneAssignment"))
    if zone_assignment is not None and zone_assignment >= 0:
        normalized["zoneAssignment"] = zone_assignment
    room_temperature = _parse_optional_float(raw.get("roomTemperatureC"))
    if room_temperature is not None:
        normalized["roomTemperatureC"] = room_temperature
    room_humidity = _parse_optional_float(raw.get("roomHumidityPct"))
    if room_humidity is not None:
        normalized["roomHumidityPct"] = room_humidity

    return normalized


def _has_radio_identity_evidence(device: dict[str, Any]) -> bool:
    class_address = _parse_optional_int(device.get("deviceClassAddress"))
    if class_address == 0x26:
        return True
    if str(device.get("deviceModel") or "").strip():
        return True
    if str(device.get("firmwareVersion") or "").strip():
        return True
    if _parse_optional_int(device.get("hardwareIdentifier")) is not None:
        return True
    return False


def _is_active_radio_device(device: dict[str, Any]) -> bool:
    group = _parse_optional_int(device.get("group"))
    if group is None:
        return False
    connected = device.get("deviceConnected") is True
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
        if device.get("deviceConnected") is not True:
            continue
        zone_assignment = _parse_optional_int(device.get("zoneAssignment"))
        if zone_assignment is None or zone_assignment <= 0:
            continue
        zone_instance = zone_assignment - 1
        by_zone[zone_instance].append(
            {
                "group": group,
                "instance": instance,
                "remote_control_address": _parse_optional_int(device.get("remoteControlAddress")),
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
    totals = payload.get("energyTotals")
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
  adapterHardwareInfo {
    firmwareVersion
    firmwareChecksum
    bootloaderVersion
    bootloaderChecksum
    hardwareID
    hardwareConfig
    features
    jumpers
    jumperFlags
    isWifi
    isEthernet
    temperatureC
    supplyVoltageMv
    busVoltageMaxDv
    busVoltageMinDv
    resetCause
    resetCauseCode
    restartCount
    wifiRssiDbm
    lastIdentityQuery
    lastTelemetryQuery
    versionResponseLen
    infoSupported
  }
}
"""

QUERY_ADAPTER_HARDWARE_INFO_MINIMAL = """
query AdapterHardwareInfo {
  adapterHardwareInfo {
    firmwareVersion
    isWifi
    isEthernet
    infoSupported
    versionResponseLen
  }
}
"""


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

    async def _async_update_data(self) -> dict[str, Any] | None:
        if self._hardware_info_supported is False:
            return None

        try:
            payload = await self._client.execute(QUERY_ADAPTER_HARDWARE_INFO)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["adapterHardwareInfo"]):
                self._hardware_info_supported = False
                return None
            try:
                payload = await self._client.execute(QUERY_ADAPTER_HARDWARE_INFO_MINIMAL)
            except (GraphQLClientError, GraphQLResponseError):
                return None
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return None

        info = payload.get("adapterHardwareInfo")
        if not isinstance(info, dict):
            return None

        return info
