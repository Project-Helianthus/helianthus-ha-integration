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
from .coordinator import is_missing_field_error as _is_missing_field_error
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

QUERY_STATUS_NO_INITIATOR = """
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


_STATUS_SCHEMA_FIELDS = [
    "bus_summary",
    "status",
    "bus_admission",
    "source_selection",
    "state",
    "mode",
    "outcome",
    "reason",
    "selected_source",
    "failed_source",
    "companion_target",
    "active_probe",
    "target",
    "opcode",
    "retryable",
    "next_action",
    "last_successful_source",
    "automatic_retry_scheduled",
    "rejected_candidates",
    "source",
    "occupancy_state",
    "evidence_provenance",
]


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
        force_schema_incompatible = False
        try:
            payload = await self._client.execute(QUERY_STATUS)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["initiator_address"]):
                try:
                    payload = await self._client.execute(QUERY_STATUS_NO_INITIATOR)
                except GraphQLResponseError as nested:
                    if not _is_missing_field_error(
                        nested.errors,
                        _STATUS_SCHEMA_FIELDS,
                    ):
                        raise UpdateFailed(str(nested)) from nested
                    try:
                        payload = await self._client.execute(QUERY_STATUS_MINIMAL)
                        force_schema_incompatible = True
                    except GraphQLResponseError as legacy:
                        raise UpdateFailed(str(legacy)) from legacy
                    except GraphQLClientError as legacy:
                        raise UpdateFailed(str(legacy)) from legacy
                except GraphQLClientError as nested:
                    raise UpdateFailed(str(nested)) from nested
            elif _is_missing_field_error(
                exc.errors,
                _STATUS_SCHEMA_FIELDS,
            ):
                try:
                    payload = await self._client.execute(QUERY_STATUS_MINIMAL)
                    force_schema_incompatible = True
                except GraphQLResponseError as nested:
                    raise UpdateFailed(str(nested)) from nested
                except GraphQLClientError as nested:
                    raise UpdateFailed(str(nested)) from nested
            else:
                raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            admission = schema_incompatible_admission()
            return {
                "daemon": daemon_status_with_admission({}, admission),
                "adapter": {},
                "admission": admission,
                "raw_admission": admission,
            }
        raw_admission = (
            schema_incompatible_admission()
            if force_schema_incompatible
            else source_selection_from_status_payload(payload)
        )
        daemon = daemon_status_with_admission(
            payload.get("daemon_status", {}) or {}, raw_admission
        )
        return {
            "daemon": daemon,
            "adapter": payload.get("adapter_status", {}) or {},
            "admission": raw_admission,
            "raw_admission": raw_admission,
        }
