"""Config flow for Helianthus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.zeroconf import ZeroconfServiceInfo
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_VERSION,
    DEFAULT_GRAPHQL_PATH,
    DEFAULT_GRAPHQL_TRANSPORT,
    DOMAIN,
)
from .discovery import normalize_transport, parse_mdns_service
from .graphql import (
    GraphQLRequestError,
    GraphQLResponseError,
    GraphQLTimeoutError,
    GraphQLClient,
    build_graphql_url,
)
from .options_flow import HelianthusOptionsFlow

_SCHEMA_QUERY = """
query HelianthusSchema {
  __schema {
    queryType { name }
  }
}
"""


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
            path = user_input.get(CONF_PATH) or DEFAULT_GRAPHQL_PATH
            transport = normalize_transport(user_input.get(CONF_TRANSPORT))
            version = (user_input.get(CONF_VERSION) or "").strip() or None
            user_input = {
                **user_input,
                CONF_PATH: path,
                CONF_TRANSPORT: transport,
            }
            if version:
                user_input[CONF_VERSION] = version
            else:
                user_input.pop(CONF_VERSION, None)

            unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            connection_error = await self._async_validate_connection(
                host=str(user_input[CONF_HOST]),
                port=int(user_input[CONF_PORT]),
                path=path,
                transport=transport,
            )
            if connection_error:
                errors["base"] = connection_error
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

            self._discovery = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_PATH: path,
                CONF_TRANSPORT: transport,
                CONF_VERSION: version or "",
            }

        default_host = self._discovery[CONF_HOST] if self._discovery else ""
        default_port = self._discovery[CONF_PORT] if self._discovery else 80
        default_path = (
            self._discovery[CONF_PATH] if self._discovery else DEFAULT_GRAPHQL_PATH
        )
        default_transport = (
            self._discovery[CONF_TRANSPORT]
            if self._discovery
            else DEFAULT_GRAPHQL_TRANSPORT
        )
        default_version = self._discovery.get(CONF_VERSION, "") if self._discovery else ""
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=default_host): str,
                vol.Required(CONF_PORT, default=default_port): int,
                vol.Optional(CONF_PATH, default=default_path): str,
                vol.Optional(CONF_TRANSPORT, default=default_transport): str,
                vol.Optional(CONF_VERSION, default=default_version): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def _async_validate_connection(
        self, host: str, port: int, path: str, transport: str
    ) -> str | None:
        url = build_graphql_url(host, port, path=path, transport=transport)
        client = GraphQLClient(
            session=async_get_clientsession(self.hass),
            url=url,
            timeout=5.0,
        )
        try:
            data = await client.execute(_SCHEMA_QUERY)
        except (GraphQLTimeoutError, GraphQLRequestError):
            return "cannot_connect"
        except GraphQLResponseError:
            return "invalid_response"
        except Exception:  # pragma: no cover - unexpected errors
            return "unknown"

        if not isinstance(data, dict) or "__schema" not in data:
            return "invalid_response"
        return None

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

        self._discovery = {
            CONF_HOST: record.host,
            CONF_PORT: record.port,
            CONF_PATH: record.path,
            CONF_TRANSPORT: record.transport,
            CONF_VERSION: record.version or "",
        }

        return await self.async_step_user()
