"""Resource release of one config entry: setup rollback and unload.

Every setup phase that acquires a resource registers its release with the
entry's one ``EntryResources``, an async exit stack. A setup that fails or is
cancelled releases everything registered so far; a successful setup publishes
the stack in ``entry.runtime_data.resources`` and unload releases it after the
platforms are unloaded. Each release runs once, newest first, even when an
earlier release fails. See docs/entry-runtime-lifecycle.md.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable
from contextlib import AsyncExitStack
from functools import partial
from typing import TYPE_CHECKING

from .const import DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .entry_runtime import HelianthusConfigEntry, HelianthusRuntime

_LOGGER = logging.getLogger(__package__)


class EntryResources:
    """The resources one entry has acquired, released once, newest first."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self._stack = AsyncExitStack()
        self._errors: list[Exception] = []
        self._forwarded: tuple[object, object, list[str]] | None = None
        # True while a refused platform unload keeps the platforms and the
        # older resources they use owned by this stack.
        self.platforms_retained = False

    def own(self, name: str, release: Callable[[], object]) -> None:
        """Register the release of one acquired resource; it may return an awaitable."""
        self._stack.push_async_callback(self._release, name, release)

    def own_unsubscribes(self, name: str, unsubscribes: list[Callable[[], None]]) -> None:
        """Register a list of unsubscribe callbacks, read when it is released.

        Callbacks appended to the list later, by the platforms, are released
        with it. A failing callback is logged and the later ones still run.
        """
        self.own(name, partial(_unsubscribe_all, self.entry_id, unsubscribes))

    def own_platforms(self, hass: object, entry: object, platforms: list[str]) -> None:
        """Register the forwarded platforms; unload marks them unloaded first."""
        self._forwarded = (hass, entry, platforms)
        self.own("forwarded platforms", self._async_unload_forwarded_platforms)

    def platforms_unloaded(self) -> None:
        """Record that the forwarded platforms were unloaded outside this stack."""
        self._forwarded = None
        self.platforms_retained = False

    async def _async_unload_forwarded_platforms(self) -> None:
        forwarded = self._forwarded
        if forwarded is None:
            return
        hass, entry, platforms = forwarded
        try:
            unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
        except BaseException:
            self.platforms_unloaded()
            raise
        if unloaded:
            self.platforms_unloaded()
            return
        # The platforms are still loaded and still use every older resource:
        # stop the release here and keep the platforms and those resources
        # owned, the platforms on top, for the next release.
        self.platforms_retained = True
        retained = self._stack.pop_all()
        retained.push_async_callback(
            self._release, "forwarded platforms", self._async_unload_forwarded_platforms
        )
        self._stack = retained
        _LOGGER.warning(
            "Helianthus platforms of entry %s did not unload during release; "
            "keeping the resources they use until the next release",
            self.entry_id,
        )

    async def _release(self, name: str, release: Callable[[], object]) -> None:
        try:
            result = release()
            if inspect.isawaitable(result):
                await result
        except Exception as err:
            self._errors.append(err)
            _LOGGER.warning(
                "Releasing the %s of Helianthus entry %s failed", name, self.entry_id,
                exc_info=err,
            )

    async def async_release(self) -> Exception | None:
        """Release every registered resource once; return the first failure.

        A release that fails is logged and the later ones still run. A
        cancellation is not caught: the remaining releases still run, then it
        propagates. The stack is empty afterwards, so a second call releases
        nothing. A forwarded-platform unload that returns ``False`` stops the
        release there: ``platforms_retained`` is set and the platforms and the
        older resources stay registered for the next call.
        """
        try:
            await self._stack.aclose()
        finally:
            errors, self._errors = self._errors, []
        return errors[0] if errors else None


def _unsubscribe_all(entry_id: str, unsubscribes: Iterable[Callable[[], None]]) -> None:
    for unsubscribe in list(unsubscribes):
        try:
            unsubscribe()
        except Exception as err:
            _LOGGER.warning(
                "An unsubscribe callback of Helianthus entry %s failed", entry_id, exc_info=err
            )


async def async_unload_entry(hass: HomeAssistant, entry: HelianthusConfigEntry) -> bool:
    """Unload the platforms, then release every resource of the entry once.

    ``False`` from the platform unload releases nothing. Otherwise the entry's
    admission clean-up count is dropped and every resource is released even
    when one release fails; the first failure is raised afterwards.
    """
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    cleanup_counts = hass.data.get(DOMAIN, {}).get("_admission_cleanup_counts")
    if isinstance(cleanup_counts, dict):
        cleanup_counts.pop(entry.entry_id, None)
    runtime: HelianthusRuntime | None = getattr(entry, "runtime_data", None)
    resources = None if runtime is None else runtime.resources
    if resources is None:
        return True
    resources.platforms_unloaded()
    error = await resources.async_release()
    if error is not None:
        raise error
    return True
