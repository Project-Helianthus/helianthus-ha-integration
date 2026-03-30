"""Text entities for Helianthus installer/maintenance fields."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError

_SET_SYSTEM_CONFIG_MUTATION = """
mutation SetSystemConfig($field: String!, $value: String!) {
  setSystemConfig(field: $field, value: $value) {
    success
    error
  }
}
"""

_SET_BOILER_CONFIG_MUTATION = """
mutation SetBoilerConfig($field: String!, $value: String!) {
  setBoilerConfig(field: $field, value: $value) {
    success
    error
  }
}
"""


@dataclass(frozen=True)
class InstallerTextField:
    key: str
    label: str
    max_length: int
    source: str  # "system" or "boiler"
    icon: str = "mdi:card-account-details-outline"


_SYSTEM_TEXT_FIELDS = [
    InstallerTextField(key="installerName", label="Installer Name", max_length=12, source="system"),
    InstallerTextField(key="installerPhone", label="Installer Phone", max_length=12, source="system", icon="mdi:phone-outline"),
]

_BOILER_TEXT_FIELDS = [
    InstallerTextField(key="phoneNumber", label="Boiler Installer Phone", max_length=16, source="boiler", icon="mdi:phone-outline"),
]


@dataclass(frozen=True)
class InstallerMenuCodeField:
    key: str
    label: str
    max_value: int
    digits: int
    source: str  # "system" or "boiler"
    icon: str = "mdi:key-variant"


_SYSTEM_MENU_CODE_FIELD = InstallerMenuCodeField(
    key="installerMenuCode", label="Installer Menu Code", max_value=999, digits=3, source="system",
)

_BOILER_MENU_CODE_FIELD = InstallerMenuCodeField(
    key="installerMenuCode", label="Boiler Installer Menu Code", max_value=255, digits=3, source="boiler",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Helianthus text entities."""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data is None:
        return

    system_coordinator = data.get("system_coordinator")
    boiler_coordinator = data.get("boiler_coordinator")
    manufacturer = data.get("manufacturer", "Vaillant")
    entry_id = entry.entry_id
    client = data.get("graphql_client")
    regulator_device_id = data.get("regulator_device_id")
    boiler_device_id = data.get("boiler_device_id")

    entities: list[TextEntity] = []

    if system_coordinator is not None and regulator_device_id is not None:
        for field in _SYSTEM_TEXT_FIELDS:
            entities.append(
                HelianthusSystemText(
                    coordinator=system_coordinator,
                    entry_id=entry_id,
                    manufacturer=manufacturer,
                    client=client,
                    device_id=regulator_device_id,
                    field=field,
                )
            )
        entities.append(
            HelianthusInstallerMenuCodeText(
                coordinator=system_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                client=client,
                device_id=regulator_device_id,
                field=_SYSTEM_MENU_CODE_FIELD,
                mutation=_SET_SYSTEM_CONFIG_MUTATION,
                mutation_key="setSystemConfig",
            )
        )

    if boiler_coordinator is not None and boiler_device_id is not None:
        for field in _BOILER_TEXT_FIELDS:
            entities.append(
                HelianthusBoilerText(
                    coordinator=boiler_coordinator,
                    entry_id=entry_id,
                    manufacturer=manufacturer,
                    client=client,
                    device_id=boiler_device_id,
                    field=field,
                )
            )
        entities.append(
            HelianthusInstallerMenuCodeText(
                coordinator=boiler_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                client=client,
                device_id=boiler_device_id,
                field=_BOILER_MENU_CODE_FIELD,
                mutation=_SET_BOILER_CONFIG_MUTATION,
                mutation_key="setBoilerConfig",
            )
        )

    async_add_entities(entities)


class HelianthusSystemText(CoordinatorEntity, TextEntity):
    """Writable B524 controller text field."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT

    def __init__(self, *, coordinator, entry_id, manufacturer, client, device_id, field: InstallerTextField) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._client = client
        self._device_id = device_id
        self._field = field
        self._attr_unique_id = f"{entry_id}-system-text-{field.key}"
        self._attr_name = field.label
        self._attr_native_max = field.max_length
        self._attr_icon = field.icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._device_id}, manufacturer=self._manufacturer)

    @property
    def available(self) -> bool:
        return super().available and getattr(self.coordinator, "system_installer_available", True)

    @property
    def native_value(self) -> str | None:
        payload = self.coordinator.data or {}
        config = payload.get("config", {})
        value = config.get(self._field.key)
        return str(value) if value is not None else None

    async def async_set_value(self, value: str) -> None:
        if self._field.key == "installerPhone":
            allowed = set("0123456789+() ")
            for i, ch in enumerate(value):
                if ch not in allowed:
                    raise HomeAssistantError(f"Invalid character '{ch}' at position {i} (allowed: digits, +, (, ), space)")
        else:
            for i, ch in enumerate(value.encode("utf-8")):
                if ch < 0x20 or ch > 0x7E:
                    raise HomeAssistantError(f"Non-printable character at position {i}")
        if len(value) > self._field.max_length:
            raise HomeAssistantError(f"Value length {len(value)} exceeds max {self._field.max_length}")
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")

        variables = {"field": self._field.key, "value": value}
        try:
            payload = await self._client.mutation(_SET_SYSTEM_CONFIG_MUTATION, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get("setSystemConfig") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = result.get("error", "unknown error") if isinstance(result, dict) else "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {error}")


class HelianthusBoilerText(CoordinatorEntity, TextEntity):
    """Writable B509 boiler text field."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT

    def __init__(self, *, coordinator, entry_id, manufacturer, client, device_id, field: InstallerTextField) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._client = client
        self._device_id = device_id
        self._field = field
        self._attr_unique_id = f"{entry_id}-boiler-text-{field.key}"
        self._attr_name = field.label
        self._attr_native_max = field.max_length
        self._attr_icon = field.icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._device_id}, manufacturer=self._manufacturer)

    @property
    def available(self) -> bool:
        return super().available and getattr(self.coordinator, "boiler_installer_available", True)

    @property
    def native_value(self) -> str | None:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boilerStatus") if isinstance(payload, dict) else None
        config = boiler_status.get("config", {}) if isinstance(boiler_status, dict) else {}
        value = config.get(self._field.key)
        return str(value) if value is not None else None

    async def async_set_value(self, value: str) -> None:
        # Accept formatting characters but strip them for BCD encoding.
        value_stripped = value.strip()
        allowed = set("0123456789+() ")
        for i, ch in enumerate(value_stripped):
            if ch not in allowed:
                raise HomeAssistantError(f"Invalid character '{ch}' at position {i} (allowed: digits, +, (, ), space)")
        # Strip formatting — only digits go to BCD wire encoding.
        value_clean = "".join(c for c in value_stripped if c.isdigit())
        if len(value_clean) > self._field.max_length:
            raise HomeAssistantError(f"Digit count {len(value_clean)} exceeds max {self._field.max_length}")
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")

        variables = {"field": self._field.key, "value": value_clean}
        try:
            payload = await self._client.mutation(_SET_BOILER_CONFIG_MUTATION, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get("setBoilerConfig") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = result.get("error", "unknown error") if isinstance(result, dict) else "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {error}")


class HelianthusInstallerMenuCodeText(CoordinatorEntity, TextEntity):
    """Installer menu code as zero-padded 3-digit text field."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_entity_registry_enabled_default = False
    _attr_native_max = 3
    _attr_pattern = r"^\d{1,3}$"

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        client: GraphQLClient | None,
        device_id: tuple[str, str],
        field: InstallerMenuCodeField,
        mutation: str,
        mutation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._client = client
        self._device_id = device_id
        self._field = field
        self._mutation = mutation
        self._mutation_key = mutation_key
        self._attr_unique_id = f"{entry_id}-{field.source}-text-{field.key}"
        self._attr_name = field.label
        self._attr_icon = field.icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._device_id}, manufacturer=self._manufacturer)

    @property
    def available(self) -> bool:
        source = self._field.source
        if source == "system":
            return super().available and getattr(self.coordinator, "system_sensitive_available", True)
        return super().available and getattr(self.coordinator, "boiler_sensitive_available", True)

    @property
    def native_value(self) -> str | None:
        if self._field.source == "boiler":
            payload = self.coordinator.data or {}
            boiler_status = payload.get("boilerStatus") if isinstance(payload, dict) else None
            config = boiler_status.get("config", {}) if isinstance(boiler_status, dict) else {}
        else:
            payload = self.coordinator.data or {}
            config = payload.get("config", {})
        value = config.get(self._field.key)
        if value is None:
            return None
        try:
            return f"{int(value):0{self._field.digits}d}"
        except (TypeError, ValueError):
            return str(value)

    async def async_set_value(self, value: str) -> None:
        stripped = value.strip()
        if not stripped.isdigit():
            raise HomeAssistantError("Installer menu code must contain only digits")
        numeric = int(stripped)
        if numeric < 0 or numeric > self._field.max_value:
            raise HomeAssistantError(
                f"Value {numeric} outside allowed range [0, {self._field.max_value}]"
            )
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")

        variables = {"field": self._field.key, "value": str(numeric)}
        try:
            payload = await self._client.mutation(self._mutation, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get(self._mutation_key) if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = result.get("error", "unknown error") if isinstance(result, dict) else "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {error}")
