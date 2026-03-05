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
