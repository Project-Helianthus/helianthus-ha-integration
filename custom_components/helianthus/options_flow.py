"""Options flow for Helianthus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL

from .const import (
    CONF_DHW_SCHEDULE_HELPER,
    CONF_PV_M2M_ASSET_REF,
    CONF_PV_M2M_CA_CERT_FILE,
    CONF_PV_M2M_CLIENT_CERT_FILE,
    CONF_PV_M2M_CLIENT_KEY_FILE,
    CONF_PV_M2M_DESCRIPTORS,
    CONF_PV_M2M_ENABLED,
    CONF_PV_M2M_ENDPOINT,
    CONF_USE_SUBSCRIPTIONS,
    CONF_ZONE_SCHEDULE_HELPERS,
    DEFAULT_DHW_SCHEDULE_HELPER,
    DEFAULT_PV_M2M_ASSET_REF,
    DEFAULT_PV_M2M_CA_CERT_FILE,
    DEFAULT_PV_M2M_CLIENT_CERT_FILE,
    DEFAULT_PV_M2M_CLIENT_KEY_FILE,
    DEFAULT_PV_M2M_ENABLED,
    DEFAULT_PV_M2M_ENDPOINT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SUBSCRIPTIONS,
    DEFAULT_ZONE_SCHEDULE_HELPERS,
)


class HelianthusOptionsFlow(config_entries.OptionsFlow):
    """Handle Helianthus options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            from .pv_m2m import validate_pv_m2m_options

            submitted = dict(user_input)
            for key in (
                CONF_PV_M2M_ENDPOINT,
                CONF_PV_M2M_ASSET_REF,
                CONF_PV_M2M_CA_CERT_FILE,
                CONF_PV_M2M_CLIENT_CERT_FILE,
                CONF_PV_M2M_CLIENT_KEY_FILE,
            ):
                value = submitted.get(key)
                if isinstance(value, str):
                    submitted[key] = value.strip()
            if validate_pv_m2m_options(submitted):
                stored_descriptors = self._config_entry.options.get(
                    CONF_PV_M2M_DESCRIPTORS
                )
                if stored_descriptors is not None:
                    submitted[CONF_PV_M2M_DESCRIPTORS] = stored_descriptors
                return self.async_create_entry(title="", data=submitted)
            errors["base"] = "pv_m2m_invalid"

        options = {
            **self._config_entry.options,
            **(user_input or {}),
        }
        scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        use_subscriptions = options.get(CONF_USE_SUBSCRIPTIONS, DEFAULT_USE_SUBSCRIPTIONS)
        zone_schedule_helpers = options.get(
            CONF_ZONE_SCHEDULE_HELPERS, DEFAULT_ZONE_SCHEDULE_HELPERS
        )
        dhw_schedule_helper = options.get(CONF_DHW_SCHEDULE_HELPER, DEFAULT_DHW_SCHEDULE_HELPER)
        pv_m2m_enabled = options.get(CONF_PV_M2M_ENABLED, DEFAULT_PV_M2M_ENABLED)
        pv_m2m_endpoint = options.get(CONF_PV_M2M_ENDPOINT, DEFAULT_PV_M2M_ENDPOINT)
        pv_m2m_asset_ref = options.get(CONF_PV_M2M_ASSET_REF, DEFAULT_PV_M2M_ASSET_REF)
        pv_m2m_ca_cert_file = options.get(
            CONF_PV_M2M_CA_CERT_FILE, DEFAULT_PV_M2M_CA_CERT_FILE
        )
        pv_m2m_client_cert_file = options.get(
            CONF_PV_M2M_CLIENT_CERT_FILE, DEFAULT_PV_M2M_CLIENT_CERT_FILE
        )
        pv_m2m_client_key_file = options.get(
            CONF_PV_M2M_CLIENT_KEY_FILE, DEFAULT_PV_M2M_CLIENT_KEY_FILE
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): int,
                vol.Required(CONF_USE_SUBSCRIPTIONS, default=use_subscriptions): bool,
                vol.Optional(
                    CONF_ZONE_SCHEDULE_HELPERS, default=str(zone_schedule_helpers)
                ): str,
                vol.Optional(CONF_DHW_SCHEDULE_HELPER, default=str(dhw_schedule_helper)): str,
                vol.Required(CONF_PV_M2M_ENABLED, default=bool(pv_m2m_enabled)): bool,
                vol.Optional(CONF_PV_M2M_ENDPOINT, default=str(pv_m2m_endpoint)): str,
                vol.Optional(CONF_PV_M2M_ASSET_REF, default=str(pv_m2m_asset_ref)): str,
                vol.Optional(
                    CONF_PV_M2M_CA_CERT_FILE, default=str(pv_m2m_ca_cert_file)
                ): str,
                vol.Optional(
                    CONF_PV_M2M_CLIENT_CERT_FILE,
                    default=str(pv_m2m_client_cert_file),
                ): str,
                vol.Optional(
                    CONF_PV_M2M_CLIENT_KEY_FILE,
                    default=str(pv_m2m_client_key_file),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"eebus_portal": "/portal/eebus"},
        )
