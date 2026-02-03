"""Config flow for Helianthus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.zeroconf import ZeroconfServiceInfo
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .discovery import parse_mdns_service
from .options_flow import HelianthusOptionsFlow


class HelianthusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Helianthus."""

    VERSION = 1
    _discovery: dict[str, Any] | None = None

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return HelianthusOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=user_input[CONF_HOST], data=user_input
            )

        default_host = self._discovery[CONF_HOST] if self._discovery else ""
        default_port = self._discovery[CONF_PORT] if self._discovery else 80
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=default_host): str,
                vol.Required(CONF_PORT, default=default_port): int,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        try:
            record = parse_mdns_service(discovery_info)
        except ValueError:
            return self.async_abort(reason="invalid_discovery_info")

        unique_id = f"{record.host}:{record.port}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {"name": record.name or record.host}

        self._discovery = {CONF_HOST: record.host, CONF_PORT: record.port}

        return await self.async_step_user()
