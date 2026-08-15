"""RED service-registry contract for eeBUS operator-admin v1."""

from __future__ import annotations

import asyncio
import importlib
import re
from pathlib import Path
from typing import Any

import pytest


SKI = "0123456789abcdef0123456789abcdef01234567"
FIXED_SERVICES = {
    "snapshot": "eebus_admin_snapshot",
    "spine_root": "eebus_admin_spine_root",
    "spine_children": "eebus_admin_spine_children",
    "spine_continue": "eebus_admin_spine_continue",
    "open_pairing_window": "eebus_admin_open_pairing_window",
    "close_pairing_window": "eebus_admin_close_pairing_window",
    "select_observation": "eebus_admin_select_observation",
    "connect_selection": "eebus_admin_connect_selection",
    "confirm_candidate": "eebus_admin_confirm_candidate",
    "cancel_candidate": "eebus_admin_cancel_candidate",
    "retry_trusted_partner": "eebus_admin_retry_trusted_partner",
    "untrust_partner": "eebus_admin_untrust_partner",
}
REQUEST_FIELDS = {
    "snapshot": {"entry_id", "view"},
    "spine_root": {"entry_id", "partner_id"},
    "spine_children": {"entry_id", "partner_id", "snapshot_id", "parent_node_id"},
    "spine_continue": {"entry_id", "partner_id", "snapshot_id", "parent_node_id", "cursor"},
    "open_pairing_window": {"entry_id", "expected_state_revision", "idempotency_key", "duration_seconds"},
    "close_pairing_window": {"entry_id", "expected_state_revision", "idempotency_key"},
    "select_observation": {"entry_id", "expected_state_revision", "idempotency_key", "observation_id", "expected_ski"},
    "connect_selection": {"entry_id", "expected_state_revision", "idempotency_key", "selection_id"},
    "confirm_candidate": {"entry_id", "expected_state_revision", "idempotency_key", "expected_ski"},
    "cancel_candidate": {"entry_id", "expected_state_revision", "idempotency_key"},
    "retry_trusted_partner": {"entry_id", "expected_state_revision", "idempotency_key", "partner_id"},
    "untrust_partner": {"entry_id", "expected_state_revision", "idempotency_key", "partner_id"},
}


def _services() -> Any:
    return importlib.import_module("custom_components.helianthus.eebus_admin_services")


class _Registry:
    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], tuple[Any, Any, Any]] = {}

    def async_register(self, domain: str, name: str, handler: Any, *, schema: Any, supports_response: Any) -> None:
        self.registered[(domain, name)] = (handler, schema, supports_response)

    def async_remove(self, domain: str, name: str) -> None:
        self.registered.pop((domain, name))


class _Hass:
    def __init__(self) -> None:
        self.services = _Registry()


class _Client:
    def __init__(self, identity: str) -> None:
        self.identity = identity

    async def fetch_status(self) -> Any:
        return type("Result", (), {"state_revision": 7, "data": {"client": self.identity}})()

    async def fetch_partners(self, view: str) -> Any:
        return type("Result", (), {"state_revision": 7, "data": {"view": view, "client": self.identity}})()


def test_fixed_documented_response_only_services_register_once_and_unload_after_last_entry() -> None:
    services = _services()
    hass = _Hass()
    one = services.register_eebus_admin_services(hass, entry_id="one", client=_Client("one"))
    two = services.register_eebus_admin_services(hass, entry_id="two", client=_Client("two"))
    assert one is not two
    assert services.SERVICE_NAMES == FIXED_SERVICES
    assert {name for _, name in hass.services.registered} == set(FIXED_SERVICES.values())
    assert all(supports_response == services.SupportsResponse.ONLY for _, _, supports_response in hass.services.registered.values())
    assert services.unregister_eebus_admin_services(hass, entry_id="one") is True
    assert len(hass.services.registered) == len(FIXED_SERVICES)
    assert services.unregister_eebus_admin_services(hass, entry_id="two") is True
    assert hass.services.registered == {}
    documentation = (Path(__file__).parents[1] / "custom_components" / "helianthus" / "services.yaml").read_text()
    assert set(FIXED_SERVICES.values()) <= {line.split(":", 1)[0] for line in documentation.splitlines() if line and not line.startswith(" ")}


def test_service_validation_is_closed_requires_entry_and_40_char_lowercase_ski() -> None:
    services = _services()
    valid = {"entry_id": "one", "expected_state_revision": 65_536, "idempotency_key": "key-1234567890", "expected_ski": SKI}
    assert services.validate_service_call("confirm_candidate", valid) == valid
    for bad in (
        {key: value for key, value in valid.items() if key != "entry_id"},
        {**valid, "expected_state_revision": 0},
        {**valid, "expected_state_revision": 18_446_744_073_709_551_616},
        {**valid, "expected_ski": SKI.upper()},
        {**valid, "route": "/anything"},
        {**valid, "endpoint": "192.0.2.1:4712"},
    ):
        assert services.validate_service_call("confirm_candidate", bad) is None
    assert services.validate_service_call("spine_children", {"entry_id": "one", "partner_id": "p-opaque", "snapshot_id": "s-opaque", "parent_node_id": "n-opaque"}) == {"entry_id": "one", "partner_id": "p-opaque", "snapshot_id": "s-opaque", "parent_node_id": "n-opaque"}
    assert services.validate_service_call("spine_continue", {"entry_id": "one", "partner_id": "p-opaque", "snapshot_id": "s-opaque", "parent_node_id": "n-opaque", "cursor": "c-opaque"}) is not None
    assert services.validate_service_call("open_pairing_window", {"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": 1}) is not None
    assert services.validate_service_call("open_pairing_window", {"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": 300}) is not None
    for duration in (0, 301, True):
        assert services.validate_service_call("open_pairing_window", {"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": duration}) is None


def test_fixed_service_dispatch_isolated_by_entry_and_unknown_data_rejected_before_client_call() -> None:
    services = _services()
    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=_Client("one"))
    services.register_eebus_admin_services(hass, entry_id="two", client=_Client("two"))
    handler, schema, _ = hass.services.registered[("helianthus", FIXED_SERVICES["snapshot"])]
    result = asyncio.run(handler({"entry_id": "two"}))
    assert result == {"state_revision": 7, "data": {"client": "two"}}
    with pytest.raises(ValueError):
        asyncio.run(handler({"entry_id": "two", "candidate": "must-not-persist"}))
    for invalid in ({}, {"entry_id": 2}, {"entry_id": "two", "candidate": "must-not-persist"}):
        with pytest.raises(Exception):
            schema(invalid)


def test_direct_snapshot_services_keep_all_identity_views_response_only() -> None:
    services = _services()
    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=_Client("one"))
    handler, _, _ = hass.services.registered[("helianthus", FIXED_SERVICES["snapshot"])]
    for view in ("trusted", "connected", "discovered", "candidate"):
        assert asyncio.run(handler({"entry_id": "one", "view": view})) == {"state_revision": 7, "data": {"view": view, "client": "one"}}
    assert services.services_for_entry(hass, "one").client.identity == "one"


def test_real_schema_strict_int_validator_rejects_bool_before_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    services = _services()
    strict_int = services._strict_int
    assert strict_int(1, minimum=1, maximum=300) == 1
    for invalid in (True, False, 0, 301, "1"):
        with pytest.raises(Exception):
            strict_int(invalid, minimum=1, maximum=300)

    class FakeVol:
        PREVENT_EXTRA = object()

        class Invalid(ValueError):
            pass

        @staticmethod
        def Required(name: str) -> str:
            return name

        @staticmethod
        def Optional(name: str, default: Any = None) -> str:
            return name

        @staticmethod
        def All(*validators: Any) -> Any:
            def validate(value: Any) -> Any:
                for validator in validators:
                    value = validator(value)
                return value
            return validate

        @staticmethod
        def Length(*, min: int, max: int) -> Any:
            return lambda value: value if isinstance(value, str) and min <= len(value) <= max else (_ for _ in ()).throw(FakeVol.Invalid())

        @staticmethod
        def Range(*, min: int, max: int) -> Any:
            return lambda value: value if min <= value <= max else (_ for _ in ()).throw(FakeVol.Invalid())

        @staticmethod
        def In(values: set[str]) -> Any:
            return lambda value: value if value in values else (_ for _ in ()).throw(FakeVol.Invalid())

        @staticmethod
        def Schema(fields: dict[str, Any], *, extra: object) -> Any:
            def validate(data: dict[str, Any]) -> dict[str, Any]:
                if set(data) != set(fields):
                    raise FakeVol.Invalid()
                return {key: validator(data[key]) for key, validator in fields.items()}
            return validate

    monkeypatch.setattr(services, "vol", FakeVol)
    schema = services._service_schema("open_pairing_window")
    assert schema({"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": 300})["duration_seconds"] == 300
    with pytest.raises(FakeVol.Invalid):
        schema({"entry_id": "one", "expected_state_revision": True, "idempotency_key": "key-1234567890", "duration_seconds": 60})


def test_services_yaml_documents_all_closed_requests_and_response_shapes() -> None:
    document = (Path(__file__).parents[1] / "custom_components" / "helianthus" / "services.yaml").read_text()
    assert not re.search(r"credential|auth|password|route|endpoint", document, re.IGNORECASE)
    for operation, name in FIXED_SERVICES.items():
        section = re.search(rf"(?ms)^{name}:\n(.*?)(?=^[A-Za-z0-9_]+:|\Z)", document)
        assert section is not None, name
        text = section.group(1)
        assert "response:" in text and "state_revision:" in text
        documented_fields = set(re.findall(r"(?m)^    ([a-z_]+):\n      required:", text))
        assert documented_fields == REQUEST_FIELDS[operation]
        assert "entry_id:\n      required: true\n      selector:\n        text:" in text
    open_section = re.search(r"(?ms)^eebus_admin_open_pairing_window:\n(.*?)(?=^[A-Za-z0-9_]+:|\Z)", document).group(1)
    assert "duration_seconds:\n      required: true\n      selector:\n        number:\n          min: 1\n          max: 300" in open_section
    mutation_names = {FIXED_SERVICES[key] for key in ("open_pairing_window", "close_pairing_window", "select_observation", "connect_selection", "confirm_candidate", "cancel_candidate", "retry_trusted_partner", "untrust_partner")}
    for name in mutation_names:
        section = re.search(rf"(?ms)^{name}:\n(.*?)(?=^[A-Za-z0-9_]+:|\Z)", document).group(1)
        assert "expected_state_revision:" in section and "idempotency_key:" in section
