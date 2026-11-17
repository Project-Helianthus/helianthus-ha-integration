"""Service status coordinator and its GraphQL documents."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .admission import (
    daemon_status_with_admission,
    schema_incompatible_admission,
    source_selection_from_status_payload,
)
from .gateway_capabilities import GatewayCapabilities, query_field_paths
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_STATUS = """
query Status {
  bus_summary {
    status {
      bus_admission {
        source_selection {
          state
          mode
          outcome
          reason
          selected_source
          failed_source
          companion_target
          active_probe {
            target
            opcode
            status
          }
          retryable
          next_action
          last_successful_source
          automatic_retry_scheduled
          rejected_candidates {
            source
            reason
            occupancy_state
            evidence_provenance
          }
        }
      }
    }
  }
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

QUERY_STATUS_MINIMAL = """
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


class HelianthusStatusCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator fetching GraphQL service status."""

    def __init__(
        self,
        hass,
        client: GraphQLClient,
        scan_interval: int,
        capabilities: GatewayCapabilities | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_status",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._capabilities = capabilities

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        capabilities = getattr(self, "_capabilities", None)
        query = QUERY_STATUS
        untrusted = False
        missing = False
        if isinstance(capabilities, GatewayCapabilities):
            for path in query_field_paths(QUERY_STATUS):
                if path not in capabilities.paths:
                    missing = True
                    break
        if missing:
            query = QUERY_STATUS_MINIMAL
            untrusted = True
        try:
            payload = await self._client.execute(query)
        except GraphQLResponseError as exc:
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc
        if untrusted:
            admission = schema_incompatible_admission()
        elif not isinstance(payload, dict):
            admission = schema_incompatible_admission()
        else:
            admission = source_selection_from_status_payload(payload)
        daemon_status: object = {}
        adapter_status: object = {}
        if isinstance(payload, dict):
            daemon_status = payload.get("daemon_status", {}) or {}
            adapter_status = payload.get("adapter_status", {}) or {}
        if not isinstance(daemon_status, dict):
            daemon_status = {}
        if not isinstance(adapter_status, dict):
            adapter_status = {}
        daemon = daemon_status_with_admission(daemon_status, admission)
        return {
            "daemon": daemon,
            "adapter": adapter_status,
            "admission": admission,
            "raw_admission": admission,
        }
