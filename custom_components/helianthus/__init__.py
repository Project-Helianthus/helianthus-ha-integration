"""Helianthus Home Assistant integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import DOMAIN as DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Helianthus config entry through the setup orchestration."""
    from .entry_setup import async_setup_entry as setup_entry

    return await setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one Helianthus config entry through the cleanup orchestration."""
    from .entry_cleanup import async_unload_entry as unload_entry

    return await unload_entry(hass, entry)
