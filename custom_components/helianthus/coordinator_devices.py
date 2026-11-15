"""Device inventory coordinator and its GraphQL documents."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .coordinator import is_missing_field_error as _is_missing_field_error
from .coordinator_fetch import fetch_device_inventory
from .graphql import GraphQLClient


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
        return await fetch_device_inventory(
            self._client,
            query_v3=QUERY_EXTENDED_V3,
            query_v3_no_addresses=QUERY_EXTENDED_V3_NO_ADDRESSES,
            query_v3_no_part=QUERY_EXTENDED_V3_NO_PART,
            query_v3_no_part_no_addresses=QUERY_EXTENDED_V3_NO_PART_NO_ADDRESSES,
            query_v2=QUERY_EXTENDED_V2,
            query_v2_no_addresses=QUERY_EXTENDED_V2_NO_ADDRESSES,
            query_base=QUERY_BASE,
            query_base_no_addresses=QUERY_BASE_NO_ADDRESSES,
            is_missing_field_error=_is_missing_field_error,
        )
