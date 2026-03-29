"""Config flow for Helianthus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # pragma: no cover - older HA versions
    ZeroconfServiceInfo = Any  # type: ignore[misc,assignment]
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_INSTANCE_GUID,
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_VERSION,
    DEFAULT_GRAPHQL_PATH,
    DEFAULT_GRAPHQL_TRANSPORT,
    DOMAIN,
)
from .discovery import normalize_transport, parse_mdns_service
from .identity import (
    GatewayIdentityVerificationError,
    VerifiedHelianthusEndpoint,
    configured_instance_guid,
    same_endpoint,
    updated_entry_data,
    verify_gateway_identity,
)
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

            verified_endpoint, connection_error = await self._async_validate_connection(
                host=str(user_input[CONF_HOST]),
                port=int(user_input[CONF_PORT]),
                path=path,
                transport=transport,
                version=version,
            )
            if connection_error:
                errors["base"] = connection_error
            else:
                return await self._async_finish_verified_entry(
                    verified_endpoint,
                    version=version,
                    title=str(user_input[CONF_HOST]),
                )

            self._discovery = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_PATH: path,
                CONF_TRANSPORT: transport,
                CONF_VERSION: version or "",
                CONF_INSTANCE_GUID: (
                    verified_endpoint.instance_guid if verified_endpoint is not None else ""
                ),
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
        self,
        host: str,
        port: int,
        path: str,
        transport: str,
        *,
        addresses: list[str] | None = None,
        expected_instance_guid: str | None = None,
        version: str | None = None,
    ) -> tuple[VerifiedHelianthusEndpoint | None, str | None]:
        try:
            verified = await verify_gateway_identity(
                session=async_get_clientsession(self.hass),
                host=host,
                port=port,
                path=path,
                transport=transport,
                addresses=addresses,
                expected_instance_guid=expected_instance_guid,
                version=version,
            )
            return verified, None
        except GatewayIdentityVerificationError as exc:
            return None, exc.reason
        except Exception:  # pragma: no cover - unexpected errors
            return None, "unknown"

    def _async_find_entry_by_guid(
        self, instance_guid: str
    ) -> config_entries.ConfigEntry | None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if configured_instance_guid(entry.data, entry.unique_id) == instance_guid:
                return entry
        return None

    async def _async_verify_existing_entry(
        self, entry: config_entries.ConfigEntry, instance_guid: str
    ) -> VerifiedHelianthusEndpoint | None:
        try:
            host = str(entry.data["host"])
            port = int(entry.data["port"])
        except (KeyError, TypeError, ValueError):
            return None
        path = entry.data.get(CONF_PATH) or DEFAULT_GRAPHQL_PATH
        transport = entry.data.get(CONF_TRANSPORT) or DEFAULT_GRAPHQL_TRANSPORT
        version = (entry.data.get(CONF_VERSION) or "").strip() or None
        verified, error = await self._async_validate_connection(
            host=host,
            port=port,
            path=path,
            transport=transport,
            expected_instance_guid=instance_guid,
            version=version,
        )
        if error:
            return None
        return verified

    async def _async_finish_verified_entry(
        self,
        verified_endpoint: VerifiedHelianthusEndpoint | None,
        *,
        version: str | None,
        title: str,
    ) -> FlowResult:
        if verified_endpoint is None:
            return self.async_abort(reason="invalid_response")

        instance_guid = verified_endpoint.instance_guid
        await self.async_set_unique_id(instance_guid)
        existing_entry = self._async_find_entry_by_guid(instance_guid)

        if existing_entry is not None:
            existing_data = updated_entry_data(
                existing_entry.data,
                verified_endpoint,
                version=version or verified_endpoint.version,
            )
            if same_endpoint(existing_entry.data, verified_endpoint):
                if (
                    existing_entry.unique_id != instance_guid
                    or existing_entry.data != existing_data
                ):
                    self.hass.config_entries.async_update_entry(
                        existing_entry,
                        data=existing_data,
                        unique_id=instance_guid,
                        title=existing_entry.title or title,
                    )
                return self.async_abort(reason="already_configured")

            current_verified = await self._async_verify_existing_entry(
                existing_entry, instance_guid
            )
            if current_verified is not None:
                return self.async_abort(reason="duplicate_instance_guid")

            self.hass.config_entries.async_update_entry(
                existing_entry,
                data=existing_data,
                unique_id=instance_guid,
                title=existing_entry.title or title,
            )
            await self.hass.config_entries.async_reload(existing_entry.entry_id)
            return self.async_abort(reason="reconfigured")

        data = updated_entry_data(
            {},
            verified_endpoint,
            version=version or verified_endpoint.version,
        )
        return self.async_create_entry(title=title, data=data)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        try:
            record = parse_mdns_service(discovery_info)
        except ValueError:
            return self.async_abort(reason="invalid_discovery_info")

        if not record.instance_guid:
            return self.async_abort(reason="missing_instance_guid")

        verified_endpoint, error = await self._async_validate_connection(
            host=record.host,
            port=record.port,
            path=record.path,
            transport=record.transport,
            addresses=list(record.addresses),
            expected_instance_guid=record.instance_guid,
            version=record.version,
        )
        if error:
            return self.async_abort(reason=error)

        self.context["title_placeholders"] = {"name": record.name or record.host}
        return await self._async_finish_verified_entry(
            verified_endpoint,
            version=record.version,
            title=record.name or verified_endpoint.host,
        )
