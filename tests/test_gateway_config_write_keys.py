"""Every system and boiler config write sends a key the gateway accepts.

The accepted keys come from the gateway's published setter-key file, copied
verbatim to ``fixtures/gateway_config_setter_keys.json``. Each write is
identified by its mutation root and its ``field`` value; the tests pin both.
"""
from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
import re

import pytest

from test_system import (
    _build_payload,
    _FakeCoordinator,
    _FakeEntry,
    _FakeHass,
    date_platform,
    number_platform,
    text_platform,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gateway_config_setter_keys.json"
SETTERS = json.loads(FIXTURE.read_text(encoding="utf-8"))["setters"]
ACCEPTED = {
    setter: {entry["key"] for entry in spec["keys"]} for setter, spec in SETTERS.items()
}

# The boiler installer menu code (B509 selector 0x4904) keeps its control
# unchanged while the meaning of its register is open; it is the single write
# whose key is not an accepted key of its setter.
OPEN_EXCEPTION = ("set_boiler_config", "installer_menu_code")

# Former values the integration sent, from the issue's config-write table.
FORMER_VALUES = {
    "hc_bivalence_point_c",
    "dhw_bivalence_point_c",
    "hc_emergency_temp_c",
    "hwc_max_flow_temp_c",
    "max_room_humidity_pct",
    "installer_phone",
    "flowset_hc_max_c",
    "flowset_hwc_max_c",
    "partload_hc_kw",
    "partload_hwc_kw",
    "phone_number",
}

# (control kind, display label) -> (mutation root, field value sent).
EXPECTED_WRITES = {
    ("system_number", "HC Bivalence Point"): ("set_system_config", "heating_outdoor_bivalence_temperature_limit"),
    ("system_number", "DHW Bivalence Point"): ("set_system_config", "dhw_outdoor_bivalence_temperature_limit"),
    ("system_number", "HC Emergency Temperature"): (
        "set_system_config",
        "heating_flow_temperature_setpoint_maximum_at_heat_pump_fault",
    ),
    ("system_number", "HWC Max Flow Temperature"): ("set_system_config", "dhw_flow_temperature_setpoint_maximum"),
    ("system_number", "Max Room Humidity"): ("set_system_config", "room_humidity_limit_maximum"),
    ("system_date", "Maintenance Date"): ("set_system_config", "maintenance_date"),
    ("system_text", "Installer Name"): ("set_system_config", "installer_name"),
    ("system_text", "Installer Phone"): ("set_system_config", "installer_phone_number"),
    ("menu_code", "Installer Menu Code"): ("set_system_config", "installer_access_code"),
    ("boiler_number", "CH Max Flow Setpoint"): ("set_boiler_config", "maximum_heating_flow_temperature_setpoint"),
    ("boiler_number", "DHW Max Flow Setpoint"): ("set_boiler_config", "maximum_dhw_flow_temperature_setpoint"),
    ("boiler_number", "CH Partload"): ("set_boiler_config", "heating_partial_load_power"),
    ("boiler_number", "DHW Partload"): ("set_boiler_config", "dhw_partial_load_power"),
    ("boiler_text", "Boiler Installer Phone"): ("set_boiler_config", "installer_phone_number"),
    ("menu_code", "Boiler Installer Menu Code"): OPEN_EXCEPTION,
}

_ROOT = re.compile(r"^\s*(set_[a-z]+_config)\(field: \$field, value: \$value\)", re.MULTILINE)


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def mutation(self, query: str, variables: dict):  # noqa: ANN201
        self.calls.append((query, dict(variables)))
        root = _ROOT.search(query)
        assert root is not None, query
        return {root.group(1): {"success": True, "error": None}}


def _mutation_root(query: str) -> str:
    roots = _ROOT.findall(query)
    assert len(roots) == 1, query
    return roots[0]


def _write_identity(query: str, variables: dict) -> tuple[str, str]:
    assert set(variables) == {"field", "value"}
    return _mutation_root(query), variables["field"]


def _check_write(identity: tuple[str, str]) -> None:
    """Fail unless the write is accepted by the fixture or is the open exception."""
    root, field = identity
    assert root in ACCEPTED, root
    assert not re.search(r"[A-Z]", field), field
    assert field not in FORMER_VALUES, field
    if identity == OPEN_EXCEPTION:
        return
    assert field in ACCEPTED[root], identity


def _control_kind(entity) -> str:  # noqa: ANN001
    if isinstance(entity, number_platform.HelianthusSystemNumber):
        return "system_number"
    if isinstance(entity, number_platform.HelianthusBoilerNumber):
        return "boiler_number"
    if isinstance(entity, text_platform.HelianthusInstallerMenuCodeText):
        return "menu_code"
    if isinstance(entity, text_platform.HelianthusSystemText):
        return "system_text"
    if isinstance(entity, text_platform.HelianthusBoilerText):
        return "boiler_text"
    if isinstance(entity, date_platform.HelianthusMaintenanceDate):
        return "system_date"
    raise AssertionError(type(entity))


def _write_controls():
    payload, _system, _client = _build_payload()
    client = _RecordingClient()
    payload["graphql_client"] = client
    payload["boiler_coordinator"] = _FakeCoordinator(
        {
            "boiler_status": {
                "config": {
                    "flowset_hc_max_c": 70.0,
                    "flowset_hwc_max_c": 55.0,
                    "partload_hc_kw": 12.0,
                    "partload_hwc_kw": 14.0,
                    "phone_number": "401234",
                    "installer_menu_code": 12,
                }
            }
        }
    )
    payload["boiler_device_id"] = ("helianthus", "entry-1-bus-BAI00-08")
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []
    for platform in (number_platform, text_platform, date_platform):
        asyncio.run(platform.async_setup_entry(hass, entry, entities.extend))
    controls = [
        entity
        for entity in entities
        if isinstance(
            entity,
            (
                number_platform.HelianthusSystemNumber,
                number_platform.HelianthusBoilerNumber,
                text_platform.HelianthusSystemText,
                text_platform.HelianthusBoilerText,
                text_platform.HelianthusInstallerMenuCodeText,
                date_platform.HelianthusMaintenanceDate,
            ),
        )
    ]
    return controls, client


def _write_once(entity) -> None:  # noqa: ANN001
    if isinstance(entity, (number_platform.HelianthusSystemNumber, number_platform.HelianthusBoilerNumber)):
        asyncio.run(entity.async_set_native_value(entity._field.minimum))
    elif isinstance(entity, text_platform.HelianthusInstallerMenuCodeText):
        asyncio.run(entity.async_set_value("123"))
    elif isinstance(entity, (text_platform.HelianthusSystemText, text_platform.HelianthusBoilerText)):
        asyncio.run(entity.async_set_value("401234"))
    else:
        asyncio.run(entity.async_set_value(datetime.date(2026, 5, 6)))


def test_every_config_write_control_sends_an_accepted_key_of_its_mutation() -> None:
    controls, client = _write_controls()
    sent = {}
    for entity in controls:
        before = len(client.calls)
        _write_once(entity)
        assert len(client.calls) == before + 1
        query, variables = client.calls[-1]
        identity = _write_identity(query, variables)
        _check_write(identity)
        sent[(_control_kind(entity), entity._attr_name)] = identity

    assert sent == EXPECTED_WRITES
    rejected = {
        identity for identity in sent.values() if identity[1] not in ACCEPTED[identity[0]]
    }
    assert rejected == {OPEN_EXCEPTION}


def test_fixture_is_the_gateway_setter_key_file_and_marks_the_open_boiler_key() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert document["format"] == "helianthus.ebusgateway.config_setter_keys.v1"
    assert set(SETTERS) == {
        "set_circuit_config",
        "set_system_config",
        "set_zone_config",
        "set_boiler_config",
    }
    open_keys = [
        (setter, entry["key"], entry["selector"]["registers"])
        for setter, spec in SETTERS.items()
        for entry in spec["keys"]
        if entry["open"]
    ]
    assert open_keys == [("set_boiler_config", "installerMenuCode", ["0x4904"])]


def test_every_canonical_system_and_boiler_write_key_is_accepted() -> None:
    for root, field in EXPECTED_WRITES.values():
        if (root, field) != OPEN_EXCEPTION:
            assert field in ACCEPTED[root], (root, field)


# Controls for the checker: each varies one component of the write identity.
@pytest.mark.parametrize(
    "identity",
    [
        # field: a former value, a camelCase value, an unknown value.
        ("set_system_config", "hc_bivalence_point_c"),
        ("set_system_config", "hcBivalencePointC"),
        ("set_boiler_config", "flowset_hc_max_c"),
        ("set_boiler_config", "phoneNumber"),
        ("set_system_config", "installer_phone"),
        ("set_system_config", "installer_menu_code"),
        # mutation root: an accepted key sent to the other setter.
        ("set_boiler_config", "heating_outdoor_bivalence_temperature_limit"),
        ("set_system_config", "maximum_heating_flow_temperature_setpoint"),
        ("set_zone_config", "installer_name"),
        ("set_unknown_config", "installer_name"),
    ],
)
def test_checker_rejects_former_camel_case_and_misrouted_keys(identity) -> None:  # noqa: ANN001
    with pytest.raises(AssertionError):
        _check_write(identity)


def test_a_boiler_number_with_a_former_write_key_is_caught() -> None:
    controls, client = _write_controls()
    entity = next(
        e for e in controls if isinstance(e, number_platform.HelianthusBoilerNumber)
    )
    entity._field = number_platform.BoilerNumberField(
        mutation_field="flowset_hc_max_c",
        config_key="flowset_hc_max_c",
        label="Synthetic",
        minimum=20.0,
        maximum=80.0,
        step=1.0,
    )
    _write_once(entity)
    with pytest.raises(AssertionError):
        _check_write(_write_identity(*client.calls[-1]))
