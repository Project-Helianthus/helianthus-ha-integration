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
    DOMAIN,
)


def _pin_selector() -> Any:
    try:
        from homeassistant.helpers import selector

        return selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="new-password",
            )
        )
    except (ImportError, AttributeError, TypeError):
        return str


class HelianthusOptionsFlow(config_entries.OptionsFlow):
    """Handle Helianthus options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._eebus_controller: Any = None
        self._eebus_observations: dict[str, dict[str, Any]] = {}
        self._eebus_observation: dict[str, Any] | None = None
        self._eebus_candidate_ski: str | None = None
        self._eebus_trusted: dict[str, dict[str, Any]] = {}
        self._eebus_partner_id: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return await self.async_step_settings(user_input)
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "eebus_pairing"]
        )

    async def async_step_settings(
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
            step_id="settings",
            data_schema=schema,
            errors=errors,
        )

    def _pairing_controller(self) -> Any:
        if self._eebus_controller is not None:
            return self._eebus_controller
        from .eebus_admin_services import services_for_entry
        from .eebus_pairing import (
            EEBusActionTerminalBroker,
            EEBusPairingController,
        )

        hass = getattr(self, "hass", None)
        entry_id = getattr(self._config_entry, "entry_id", None)
        if hass is None or not isinstance(entry_id, str):
            return None
        services = services_for_entry(hass, entry_id)
        if services is None:
            return None
        domain_data = getattr(hass, "data", {}).get(DOMAIN, {})
        entry_data = domain_data.get(entry_id) if isinstance(domain_data, dict) else None
        broker = (
            entry_data.get("eebus_admin_action_broker")
            if isinstance(entry_data, dict)
            else None
        )
        if not isinstance(broker, EEBusActionTerminalBroker):
            return None
        self._eebus_controller = EEBusPairingController(
            services.client, action_broker=broker
        )
        return self._eebus_controller

    def _eebus_error(self, step_id: str, code: str) -> config_entries.FlowResult:
        del step_id
        return self.async_show_form(
            step_id="eebus_result",
            data_schema=vol.Schema({}),
            errors={"base": code},
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        from .eebus_admin import EEBusAdminV1Error

        return error.code if isinstance(error, EEBusAdminV1Error) else "invalid_request"

    async def async_step_eebus_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        del user_input
        controller = self._pairing_controller()
        if controller is None:
            return self._eebus_error(
                "eebus_pairing", "admin_boundary_unavailable"
            )
        try:
            status = await controller.async_refresh_status()
        except Exception as error:
            return self._eebus_error("eebus_pairing", self._error_code(error))
        menu = [
            "eebus_close_window"
            if status.get("pairing_window") == "open"
            else "eebus_open_window"
        ]
        if controller.has_active_action:
            menu.append("eebus_action")
        if status.get("discovered_count", 0) > 0:
            menu.append("eebus_discovered")
        if status.get("candidate_count", 0) > 0:
            menu.append("eebus_candidate")
        if status.get("trusted_count", 0) > 0:
            menu.append("eebus_trusted")
        menu.extend(("eebus_refresh", "eebus_finish"))
        active = status.get("active_action")
        placeholders = {
            "readiness": status["readiness"]["eebus_readiness"],
            "pairing_window": status["pairing_window"],
            "trusted_count": str(status["trusted_count"]),
            "connected_count": str(status["connected_count"]),
            "discovered_count": str(status["discovered_count"]),
            "candidate_count": str(status["candidate_count"]),
            "active_action_state": active.get("state", "none")
            if isinstance(active, dict)
            else "none",
        }
        return self.async_show_menu(
            step_id="eebus_pairing",
            menu_options=menu,
            description_placeholders=placeholders,
        )

    async def async_step_eebus_refresh(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        return await self.async_step_eebus_pairing(user_input)

    async def async_step_eebus_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        del user_input
        if self._eebus_controller is not None:
            self._eebus_controller.abort()
        self._clear_eebus_flow()
        return self.async_create_entry(
            title="", data=dict(getattr(self._config_entry, "options", {}))
        )

    async def async_step_eebus_open_window(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="eebus_open_window",
                data_schema=vol.Schema(
                    {vol.Required("duration_seconds", default=120): vol.All(int, vol.Range(min=1, max=300))}
                ),
                errors={},
            )
        try:
            await self._pairing_controller().async_open_pairing_window(
                user_input["duration_seconds"]
            )
            return await self.async_step_eebus_pairing()
        except Exception as error:
            return self._eebus_error("eebus_open_window", self._error_code(error))

    async def async_step_eebus_close_window(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="eebus_close_window",
                data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
                errors={},
            )
        if not user_input.get("confirm"):
            return await self.async_step_eebus_pairing()
        try:
            await self._pairing_controller().async_close_pairing_window()
            self._clear_eebus_flow()
            return await self.async_step_eebus_pairing()
        except Exception as error:
            return self._eebus_error("eebus_close_window", self._error_code(error))

    async def async_step_eebus_discovered(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        try:
            if user_input is None:
                rows = await self._pairing_controller().async_load_partners(
                    "discovered"
                )
                self._eebus_observations = {
                    row["observation_id"]: row
                    for row in rows
                    if isinstance(row.get("observation_id"), str)
                }
                choices = {
                    key: " / ".join(
                        value
                        for value in (
                            row.get("brand"),
                            row.get("model"),
                            row.get("remote_ski"),
                        )
                        if isinstance(value, str)
                    )
                    for key, row in self._eebus_observations.items()
                }
                return self.async_show_form(
                    step_id="eebus_discovered",
                    data_schema=vol.Schema(
                        {vol.Required("observation_id"): vol.In(choices)}
                    ),
                    errors={},
                )
            observation_id = user_input.get("observation_id")
            self._eebus_observation = self._eebus_observations.get(observation_id)
            self._eebus_observations.clear()
            if self._eebus_observation is None:
                raise ValueError("unknown observation")
            return await self.async_step_eebus_compare_observation()
        except Exception as error:
            self._eebus_observations.clear()
            self._eebus_observation = None
            return self._eebus_error("eebus_discovered", self._error_code(error))

    async def async_step_eebus_compare_observation(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        row = self._eebus_observation
        if row is None:
            return self._eebus_error("eebus_discovered", "observation_stale")
        if user_input is None:
            return self.async_show_form(
                step_id="eebus_compare_observation",
                data_schema=vol.Schema({vol.Required("expected_ski"): str}),
                errors={},
                description_placeholders={"remote_ski": row["remote_ski"]},
            )
        observation_id = row["observation_id"]
        expected_ski = user_input.get("expected_ski")
        self._eebus_observation = None
        try:
            await self._pairing_controller().async_select_discovered(
                observation_id=observation_id, expected_ski=expected_ski
            )
            return await self.async_step_eebus_connect()
        except Exception as error:
            return self._eebus_error(
                "eebus_compare_observation", self._error_code(error)
            )

    async def async_step_eebus_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="eebus_connect",
                data_schema=vol.Schema({vol.Optional("pin"): _pin_selector()}),
                errors={},
            )
        pin = user_input.pop("pin", None)
        if pin == "":
            pin = None
        try:
            await self._pairing_controller().async_connect_selection(pin=pin)
        except Exception as error:
            pin = None
            return self._eebus_error("eebus_connect", self._error_code(error))
        pin = None
        return await self.async_step_eebus_action()

    async def async_step_eebus_action(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        del user_input
        try:
            active = await self._pairing_controller().async_poll_active_action(
                max_attempts=4, interval=0.5
            )
        except Exception as error:
            controller = self._pairing_controller()
            if controller is not None and controller.has_active_action:
                return self.async_show_form(
                    step_id="eebus_action",
                    data_schema=vol.Schema({}),
                    errors={"base": self._error_code(error)},
                    description_placeholders={"action_state": "retry"},
                )
            return self._eebus_error("eebus_action", self._error_code(error))
        if active is None:
            return self._eebus_error("eebus_action", "unknown_state")
        if active.get("state") == "pending":
            return self.async_show_form(
                step_id="eebus_action",
                data_schema=vol.Schema({}),
                errors={},
                description_placeholders={"action_state": "pending"},
            )
        outcome = active.get("outcome", "unknown_state")
        return self._eebus_error(
            "eebus_result", outcome if isinstance(outcome, str) else "unknown_state"
        )

    async def async_step_eebus_result(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        del user_input
        return await self.async_step_eebus_pairing()

    async def async_step_eebus_candidate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        del user_input
        try:
            candidate = await self._pairing_controller().async_load_candidate()
            self._eebus_candidate_ski = candidate.remote_ski
            return self.async_show_menu(
                step_id="eebus_candidate",
                menu_options=["eebus_confirm_candidate", "eebus_cancel_candidate"],
                description_placeholders={"remote_ski": candidate.remote_ski or ""},
            )
        except Exception as error:
            self._eebus_candidate_ski = None
            return self._eebus_error("eebus_candidate", self._error_code(error))

    async def async_step_eebus_confirm_candidate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="eebus_confirm_candidate",
                data_schema=vol.Schema({vol.Required("expected_ski"): str}),
                errors={},
                description_placeholders={"remote_ski": self._eebus_candidate_ski or ""},
            )
        try:
            await self._pairing_controller().async_confirm_candidate(
                expected_ski=user_input.get("expected_ski")
            )
            self._eebus_candidate_ski = None
            return await self.async_step_eebus_pairing()
        except Exception as error:
            self._eebus_candidate_ski = None
            return self._eebus_error(
                "eebus_confirm_candidate", self._error_code(error)
            )

    async def async_step_eebus_cancel_candidate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="eebus_cancel_candidate",
                data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
                errors={},
            )
        if not user_input.get("confirm"):
            return await self.async_step_eebus_pairing()
        try:
            await self._pairing_controller().async_cancel_candidate()
            self._eebus_candidate_ski = None
            return await self.async_step_eebus_pairing()
        except Exception as error:
            self._eebus_candidate_ski = None
            return self._eebus_error(
                "eebus_cancel_candidate", self._error_code(error)
            )

    async def async_step_eebus_trusted(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        try:
            if user_input is None:
                rows = await self._pairing_controller().async_load_partners("trusted")
                self._eebus_trusted = {
                    row["partner_id"]: row
                    for row in rows
                    if isinstance(row.get("partner_id"), str)
                }
                choices = {
                    key: row.get("remote_ski", key)
                    for key, row in self._eebus_trusted.items()
                }
                return self.async_show_form(
                    step_id="eebus_trusted",
                    data_schema=vol.Schema({vol.Required("partner_id"): vol.In(choices)}),
                    errors={},
                )
            partner_id = user_input.get("partner_id")
            if partner_id not in self._eebus_trusted:
                raise ValueError("unknown partner")
            self._eebus_partner_id = partner_id
            self._eebus_trusted.clear()
            return self.async_show_menu(
                step_id="eebus_trusted_action",
                menu_options=["eebus_retry_trusted", "eebus_untrust"],
            )
        except Exception as error:
            self._eebus_trusted.clear()
            self._eebus_partner_id = None
            return self._eebus_error("eebus_trusted", self._error_code(error))

    async def async_step_eebus_retry_trusted(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        del user_input
        partner_id = self._eebus_partner_id
        self._eebus_partner_id = None
        if partner_id is None:
            return self._eebus_error("eebus_trusted", "snapshot_expired")
        try:
            await self._pairing_controller().async_retry_trusted(partner_id)
            return await self.async_step_eebus_pairing()
        except Exception as error:
            return self._eebus_error("eebus_trusted", self._error_code(error))

    async def async_step_eebus_untrust(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="eebus_untrust",
                data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
                errors={},
            )
        partner_id = self._eebus_partner_id
        self._eebus_partner_id = None
        if not user_input.get("confirm"):
            return await self.async_step_eebus_pairing()
        if partner_id is None:
            return self._eebus_error("eebus_trusted", "snapshot_expired")
        try:
            await self._pairing_controller().async_untrust(partner_id)
            return await self.async_step_eebus_pairing()
        except Exception as error:
            return self._eebus_error("eebus_trusted", self._error_code(error))

    def _clear_eebus_flow(self) -> None:
        self._eebus_observations.clear()
        self._eebus_observation = None
        self._eebus_candidate_ski = None
        self._eebus_trusted.clear()
        self._eebus_partner_id = None
        self._eebus_controller = None
