"""One entry-owned polling lifecycle for the detached public SemReg read."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from .const import DEFAULT_SCAN_INTERVAL
from .coordinator_availability import _is_missing_field_error, next_optional_query_state
from .graphql import GraphQLResponseError
from .semantic_snapshot import fetch_semantic_snapshot

CONF_SEMANTIC_ASSET_IDS = "semantic_asset_ids"
_LOGGER = logging.getLogger(__name__)


def semantic_asset_ids(entry: object) -> tuple[str, ...]:
    """Validate consumer-known opaque IDs without interpreting their contents."""
    raw = entry.options.get(
        CONF_SEMANTIC_ASSET_IDS, entry.data.get(CONF_SEMANTIC_ASSET_IDS, [])
    )
    error = "semantic snapshot: invalid configured asset selection"
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError(error)
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(error)
        try:
            size = len(item.encode("utf-8"))
        except UnicodeError:
            raise ValueError(error) from None
        if not 1 <= size <= 256:
            raise ValueError(error)
    if len(set(raw)) != len(raw):
        raise ValueError(error)
    return tuple(raw)


class SemanticSnapshotCoordinator:
    """Retain immutable whole reads; transport health grants no entity admission."""

    def __init__(self, hass: object, client: object, asset_ids: tuple[str, ...],
                 scan_interval: int = DEFAULT_SCAN_INTERVAL) -> None:
        self.hass = hass
        self.client = client
        self.asset_ids = asset_ids
        self.update_interval = max(1, int(scan_interval))
        self.data = None
        self.last_update_success = False
        self.last_exception = None
        self.supported: bool | None = None
        self._reported_state = None
        self._listeners: list[Callable[[], None]] = []
        self._poll_task = None
        self._refresh_task = None
        self._closed = False
        self._lifecycle_lock = asyncio.Lock()

    def async_add_listener(self, listener: Callable[[], None], context=None):
        self._listeners.append(listener)

        def remove():
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Semantic snapshot consumer notification failed")

    async def _refresh(self) -> None:
        try:
            value = await fetch_semantic_snapshot(self.client, self.asset_ids)
        except Exception as exc:
            if self._closed:
                return
            self.last_update_success = False
            self.last_exception = exc
            self.supported = next_optional_query_state(
                self.supported,
                schema_field_missing=isinstance(exc, GraphQLResponseError)
                and _is_missing_field_error(exc.errors, ["semanticSnapshot"]),
            )
        else:
            if self._closed:
                return
            self.supported = next_optional_query_state(
                self.supported, response_received=True
            )
            self.data = value
            self.last_update_success = True
            self.last_exception = None
        state = (self.supported, self.last_update_success)
        if state != self._reported_state:
            self._reported_state = state
            if self.supported is False:
                _LOGGER.warning("Semantic snapshot root unsupported; reload entry to retry")
            elif not self.last_update_success:
                _LOGGER.warning("Semantic snapshot refresh failed (%s)",
                                type(self.last_exception).__name__)
            else:
                _LOGGER.info("Semantic snapshot refresh available")
        self._notify()

    async def async_request_refresh(self) -> None:
        """Coalesce overlapping requests into one read and one notification."""
        if self._closed or self.supported is False:
            return
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self.hass.async_create_task(self._refresh())
        await asyncio.shield(self._refresh_task)

    async def _poll(self) -> None:
        while not self._closed and self.supported is not False:
            await asyncio.sleep(self.update_interval)
            await self.async_request_refresh()

    async def async_start(self) -> None:
        """Refresh once then start exactly one timer; repeated starts are inert."""
        async with self._lifecycle_lock:
            if self._poll_task is not None and not self._poll_task.done():
                return
            self._closed = False
            try:
                await self.async_request_refresh()
                if self.supported is not False:
                    self._poll_task = asyncio.create_task(self._poll())
            except BaseException:
                await self._close()
                raise

    async def _close(self) -> None:
        self._closed = True
        tasks = [task for task in (self._poll_task, self._refresh_task)
                 if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._poll_task = self._refresh_task = None
        self.last_update_success = False
        self._notify()

    async def async_close(self) -> None:
        """Cancel and join timer and in-flight read before releasing the entry."""
        async with self._lifecycle_lock:
            await self._close()


async def async_setup_semantic_snapshot(hass: object, entry: object, client: object,
                                        scan_interval: int = DEFAULT_SCAN_INTERVAL):
    """Create no consumer or task when the optional selection is absent/invalid."""
    try:
        ids = semantic_asset_ids(entry)
    except ValueError:
        _LOGGER.warning("Semantic snapshot: invalid configured asset selection for entry %s",
                        entry.entry_id)
        return None
    if not ids:
        return None
    coordinator = SemanticSnapshotCoordinator(hass, client, ids, scan_interval)
    await coordinator.async_start()
    return coordinator
