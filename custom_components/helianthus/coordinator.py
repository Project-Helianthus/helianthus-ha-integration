"""Data coordinator for Helianthus."""

from __future__ import annotations

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

QUERY_CIRCUITS = """
query Circuits {
  circuits {
    index
    circuitType
    hasMixer
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
      vr71CircuitStartIndex
    }
  }
}
"""

QUERY_SYSTEM_LEGACY = """
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
  devices {
    address
    role
    energyTotals {
      gas { dhw { today yearly } climate { today yearly } }
      electric { dhw { today yearly } climate { today yearly } }
      solar { dhw { today yearly } climate { today yearly } }
    }
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
    }
    diagnostics {
      heatingStatusRaw
    }
  }
}
"""

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
        try:
            payload = await self._client.execute(QUERY_SEMANTIC)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["zones", "dhw"]):
                return {"zones": [], "dhw": None}
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
            if _is_missing_field_error(exc.errors, ["vr71CircuitStartIndex"]):
                try:
                    payload = await self._client.execute(QUERY_SYSTEM_LEGACY)
                except GraphQLResponseError as nested:
                    if _is_missing_field_error(nested.errors, missing_fields):
                        return empty
                    raise UpdateFailed(str(nested)) from nested
                except GraphQLClientError as nested:
                    raise UpdateFailed(str(nested)) from nested
            elif _is_missing_field_error(exc.errors, missing_fields):
                return empty
            else:
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

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_ENERGY)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["energyTotals", "devices"]):
                return {"energyTotals": None}
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return {"energyTotals": None}
        devices = payload.get("devices") or []
        totals = next(
            (d.get("energyTotals") for d in devices if d.get("role") == "Regulator"),
            None,
        )
        return {"energyTotals": totals}


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
                    "heatingStatusRaw",
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
