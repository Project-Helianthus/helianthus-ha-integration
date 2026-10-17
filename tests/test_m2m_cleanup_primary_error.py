"""First-refresh ownership and primary-error precedence for M2M consumers."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture(params=("pv", "storage", "evse"))
def refresh_with_cleanup(request, monkeypatch):
    """Load each real consumer with a local HA import stub, without HA setup."""
    coordinator = ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    monkeypatch.setitem(sys.modules, coordinator.__name__, coordinator)
    component = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
    name = f"custom_components.helianthus._cleanup_test_{request.param}_m2m"
    spec = importlib.util.spec_from_file_location(name, component / f"{request.param}_m2m.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module.async_first_refresh_with_cleanup


@pytest.mark.parametrize("primary_type", (asyncio.CancelledError, ValueError), ids=("cancelled", "failed"))
@pytest.mark.parametrize("close_fails", (False, True), ids=("close-ok", "close-fails"))
def test_primary_refresh_error_survives_cleanup(refresh_with_cleanup, primary_type, close_fails):
    calls = []
    primary = primary_type("synthetic refresh failure")
    cleanup = RuntimeError("synthetic cleanup failure")

    class Coordinator:
        async def async_config_entry_first_refresh(self):
            calls.append("refresh")
            raise primary

    class Client:
        async def async_close(self):
            calls.append("close")
            if close_fails:
                raise cleanup

    async def exercise():
        with pytest.raises(primary_type) as caught:
            await refresh_with_cleanup(Coordinator(), Client())
        assert caught.value is primary
        assert type(caught.value) is primary_type
        assert calls == ["refresh", "close"]

    asyncio.run(exercise())


@pytest.mark.parametrize("primary_type", (asyncio.CancelledError, ValueError), ids=("cancelled", "failed"))
def test_missing_client_preserves_primary_refresh_error(refresh_with_cleanup, primary_type):
    calls = []
    primary = primary_type("synthetic refresh failure")

    class Coordinator:
        async def async_config_entry_first_refresh(self):
            calls.append("refresh")
            raise primary

    async def exercise():
        with pytest.raises(primary_type) as caught:
            await refresh_with_cleanup(Coordinator(), None)
        assert caught.value is primary
        assert type(caught.value) is primary_type
        assert calls == ["refresh"]

    asyncio.run(exercise())


@pytest.mark.parametrize("has_client", (False, True), ids=("no-client", "client"))
def test_successful_refresh_transfers_ownership_without_close(refresh_with_cleanup, has_client):
    calls = []

    class Coordinator:
        async def async_config_entry_first_refresh(self):
            calls.append("refresh")

    class Client:
        async def async_close(self):
            calls.append("close")
            raise RuntimeError("successful refresh must not close the client")

    async def exercise():
        result = await refresh_with_cleanup(Coordinator(), Client() if has_client else None)
        assert result is None
        assert calls == ["refresh"]

    asyncio.run(exercise())
