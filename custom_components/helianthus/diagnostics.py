"""Config-entry diagnostics download.

One on-demand read of the gateway's bus observability and runtime status,
together with each coordinator's last update state. The download polls
nothing, creates no entity and triggers no reload. Bus message items,
periodicity items and identification evidence are never requested.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CONF_EVSE_M2M_ASSET_REF,
    CONF_EVSE_M2M_DESCRIPTORS,
    CONF_EVSE_M2M_CA_CERT_FILE,
    CONF_EVSE_M2M_CLIENT_CERT_FILE,
    CONF_EVSE_M2M_CLIENT_KEY_FILE,
    CONF_EVSE_M2M_ENDPOINT,
    CONF_HOST_ALIASES,
    CONF_INSTANCE_GUID,
    CONF_PV_M2M_ASSET_REF,
    CONF_PV_M2M_DESCRIPTORS,
    CONF_PV_M2M_CA_CERT_FILE,
    CONF_PV_M2M_CLIENT_CERT_FILE,
    CONF_PV_M2M_CLIENT_KEY_FILE,
    CONF_PV_M2M_ENDPOINT,
    CONF_STORAGE_M2M_ASSET_REF,
    CONF_STORAGE_M2M_DESCRIPTORS,
    CONF_STORAGE_M2M_CA_CERT_FILE,
    CONF_STORAGE_M2M_CLIENT_CERT_FILE,
    CONF_STORAGE_M2M_CLIENT_KEY_FILE,
    CONF_STORAGE_M2M_ENDPOINT,
    DOMAIN,
)

TO_REDACT = frozenset({
    "host",
    CONF_HOST_ALIASES,
    CONF_INSTANCE_GUID,
    "serial_number",
    "mac_address",
    "asset_ref",
    CONF_PV_M2M_ENDPOINT,
    CONF_PV_M2M_ASSET_REF,
    CONF_PV_M2M_CA_CERT_FILE,
    CONF_PV_M2M_CLIENT_CERT_FILE,
    CONF_PV_M2M_CLIENT_KEY_FILE,
    CONF_STORAGE_M2M_ENDPOINT,
    CONF_STORAGE_M2M_ASSET_REF,
    CONF_STORAGE_M2M_CA_CERT_FILE,
    CONF_STORAGE_M2M_CLIENT_CERT_FILE,
    CONF_STORAGE_M2M_CLIENT_KEY_FILE,
    CONF_EVSE_M2M_ENDPOINT,
    CONF_EVSE_M2M_ASSET_REF,
    CONF_EVSE_M2M_CA_CERT_FILE,
    CONF_EVSE_M2M_CLIENT_CERT_FILE,
    CONF_EVSE_M2M_CLIENT_KEY_FILE,
    CONF_PV_M2M_DESCRIPTORS,
    CONF_STORAGE_M2M_DESCRIPTORS,
    CONF_EVSE_M2M_DESCRIPTORS,
})

DIAGNOSTICS_QUERY = """
query Diagnostics {
  bus_summary {
    status {
      capability {
        passive_supported
        passive_available
        passive_state
        passive_reason
        active_supported
        broadcast_supported
        endpoint_state
        tap_connected
      }
      warmup {
        state
        completed_transactions
        required_transactions
        completion_mode
        elapsed_seconds
        blocker
      }
      degraded {
        active
        reasons
      }
      timing_quality {
        passive
        busy
        active
        periodicity
      }
      feature_flags {
        observe_first_enabled
        passive_config_direct_apply
        passive_state_direct_apply
        external_write_policy
        normalizations
        last_updated_at
      }
      startup {
        phase
        reason
        cache_epoch
        live_epoch
        last_updated_at
      }
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
      transport_class
      publisher_cadence_sec
      publisher_cadence_source
      last_updated_at
    }
    counters {
      periodicity_budget_overflow_total
      series_budget_overflow_total
    }
    messages {
      count
      capacity
    }
    periodicity {
      count
      capacity
    }
    reconstructor_invalid_src_class_skipped_total
    reconstructor_prefix_resync_skipped_total
    last_updated_at
  }
  watch_summary {
    activation_counts {
      active_keys
      catalog_descriptors
      source_classes {
        class
        count
      }
    }
    freshness_classes {
      class
      count
    }
    direct_apply_eligibility_classes {
      class
      count
    }
    inventory {
      total_entries
      pinned_entries
      evictable_entries
      static_pinned_footprint
      write_confirm_pinned_active
      pin_classes {
        class
        count
      }
      state_classes {
        class
        count
      }
    }
    degraded {
      active
      reasons
      compactor_degraded
      pinned_budget_degraded
      shadowing_enabled
    }
    last_updated_at
  }
  fm5_interpretation {
    mode
    degraded_reason
    evidence_revision
  }
  vaillant_regulator_capability
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
    initiator_address
  }
}
"""

_COORDINATOR_SUFFIX = "_coordinator"


def _coordinator_states(runtime: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Last update state per published coordinator; an error is named by its type only."""
    states: dict[str, dict[str, Any]] = {}
    for key, coordinator in runtime.items():
        if not key.endswith(_COORDINATOR_SUFFIX) or coordinator is None:
            continue
        exception = getattr(coordinator, "last_exception", None)
        states[key] = {
            "last_update_success": bool(getattr(coordinator, "last_update_success", False)),
            "last_exception": type(exception).__name__ if exception is not None else None,
        }
    return states


async def _gateway_read(runtime: dict[str, Any]) -> dict[str, Any]:
    client = runtime.get("graphql_client")
    if client is None:
        return {"error": "no client"}
    try:
        data = await client.query(DIAGNOSTICS_QUERY)
    except Exception as exc:  # noqa: BLE001 - a download reports, never raises
        return {"error": type(exc).__name__}
    if not isinstance(data, dict):
        return {"error": "no data"}
    return data


async def async_get_config_entry_diagnostics(hass: Any, entry: Any) -> dict[str, Any]:
    """Return the redacted diagnostics download of one config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        coordinators: dict[str, dict[str, Any]] = {}
        gateway: dict[str, Any] = {"error": "entry not loaded"}
    else:
        coordinators = _coordinator_states(runtime)
        gateway = await _gateway_read(runtime)
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "coordinators": coordinators,
            "gateway": gateway,
        },
        TO_REDACT,
    )
