"""Options flow for Helianthus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL

from .const import CONF_USE_SUBSCRIPTIONS, DEFAULT_SCAN_INTERVAL, DEFAULT_USE_SUBSCRIPTIONS


class HelianthusOptionsFlow(config_entries.OptionsFlow):
    """Handle Helianthus options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
        scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        use_subscriptions = options.get(CONF_USE_SUBSCRIPTIONS, DEFAULT_USE_SUBSCRIPTIONS)

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): int,
                vol.Required(CONF_USE_SUBSCRIPTIONS, default=use_subscriptions): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
