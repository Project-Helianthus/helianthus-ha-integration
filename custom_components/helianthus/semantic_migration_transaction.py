"""Isolated ownership handoff; no persistence, registry or runtime integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


@dataclass(frozen=True)
class EntityPair:
    """Caller-approved identity; mapping identity is opaque and never decoded."""

    entry_id: str
    saved_unique_id: str
    mapping_identity: str
    generation: int

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value for value in (
            self.entry_id, self.saved_unique_id, self.mapping_identity
        )):
            raise ValueError("identity components must be nonempty strings")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation must be a nonnegative integer")


class Owner(Enum):
    LEGACY = "legacy"
    SHARED = "shared"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, eq=False)
class CallbackToken:
    """An instance capability, compared by identity rather than field equality."""

    pair: EntityPair
    owner: Owner


@dataclass(frozen=True)
class RecoveryState:
    pair: EntityPair
    owner: Owner


def recovery_owner(pair: EntityPair, state: RecoveryState | None) -> Owner:
    """Read-only selection; unknown, mismatched or incomplete state has no owner."""
    if not isinstance(state, RecoveryState) or state.pair != pair:
        return Owner.UNRESOLVED
    if state.owner in (Owner.LEGACY, Owner.SHARED):
        return state.owner
    return Owner.UNRESOLVED


class Journal(Protocol):
    """Successful writes are durable; failed commit has an unknown outcome."""

    async def intent(self, pair: EntityPair) -> None: ...

    async def commit_shared(self, pair: EntityPair) -> None: ...

    async def abort_intent(self, pair: EntityPair) -> None: ...

    async def read_recovery(self, pair: EntityPair) -> RecoveryState | None: ...


@dataclass(frozen=True)
class Callbacks:
    """Fences are idempotent; joins drain all work, including partial attachment."""

    fence_legacy: Callable[[EntityPair], Awaitable[None]]
    join_legacy: Callable[[EntityPair], Awaitable[None]]
    fence_shared: Callable[[EntityPair], Awaitable[None]]
    join_shared: Callable[[EntityPair], Awaitable[None]]
    restore_legacy: Callable[[EntityPair, CallbackToken], Awaitable[None]]
    attach_shared: Callable[[EntityPair, CallbackToken], Awaitable[None]]
    admission: Callable[[EntityPair, Owner, object], bool]


class RecoveryRequired(RuntimeError):
    """The pair remains fenced until an explicit recovery decision is supplied."""


class MigrationTransaction:
    """One serialized lifetime per reserved pair, on one asyncio event loop.

    The caller must reserve the pair across instances/discovery. Callbacks must
    use admit immediately before writing and must not suspend between admission
    and the write. Retained values/evidence and registry metadata stay external.
    """

    def __init__(
        self, pair: EntityPair, journal: Journal, callbacks: Callbacks,
        *, legacy_established: bool = False,
    ) -> None:
        self.pair = pair
        self._journal = journal
        self._callbacks = callbacks
        self._lock = asyncio.Lock()
        self._owner = Owner.LEGACY if legacy_established else Owner.UNRESOLVED
        self._token = CallbackToken(pair, self._owner)

    @property
    def owner(self) -> Owner:
        return self._owner

    @property
    def token(self) -> CallbackToken:
        return self._token

    def admit(self, token: CallbackToken, value: object) -> bool:
        """Fence late/foreign callbacks without changing last-known-good state."""
        if (token is not self._token or token.pair != self.pair
                or token.owner is not self._owner or self._owner is Owner.UNRESOLVED):
            return False
        return self._callbacks.admission(self.pair, self._owner, value) is True

    def _fence(self) -> None:
        self._owner = Owner.UNRESOLVED
        self._token = CallbackToken(self.pair, Owner.UNRESOLVED)

    async def _drain(self) -> None:
        await self._callbacks.fence_legacy(self.pair)
        await self._callbacks.join_legacy(self.pair)
        await self._callbacks.fence_shared(self.pair)
        await self._callbacks.join_shared(self.pair)

    async def _attach(self, owner: Owner) -> None:
        token = CallbackToken(self.pair, owner)
        callback = (self._callbacks.attach_shared if owner is Owner.SHARED
                    else self._callbacks.restore_legacy)
        await callback(self.pair, token)
        self._token = token
        self._owner = owner

    async def migrate(self, value: object) -> Owner:
        """Intent, quiesce/join, durable commit, then attach exactly once."""
        async with self._lock:
            if self._owner is Owner.SHARED:
                return self._owner
            if self._owner is not Owner.LEGACY:
                raise RecoveryRequired("explicit recovery required")
            if self._callbacks.admission(self.pair, Owner.SHARED, value) is not True:
                return self._owner
            self._fence()
            committing = False
            try:
                await self._journal.intent(self.pair)
                await self._drain()
                committing = True
                await self._journal.commit_shared(self.pair)
                await self._attach(Owner.SHARED)
            except BaseException:
                self._fence()
                if not committing:
                    # A failed intent may still have staged state. Restore only
                    # after all partial work is drained and abort is durable.
                    await self._drain()
                    await self._journal.abort_intent(self.pair)
                    await self._attach(Owner.LEGACY)
                # Commit failure/cancellation may have persisted: never guess.
                raise
            return self._owner

    async def recover(self) -> Owner:
        """Explicit read-only journal reconciliation, with no journal mutation.

        An established owner is idempotent. A fenced pair drains both sides,
        reads authoritative state, and attaches only the exact selected owner.
        A LEGACY recovery decision must incorporate any explicit rollback policy.
        """
        async with self._lock:
            if self._owner is not Owner.UNRESOLVED:
                return self._owner
            self._fence()
            try:
                await self._drain()
                state = await self._journal.read_recovery(self.pair)
                owner = recovery_owner(self.pair, state)
                if owner is not Owner.UNRESOLVED:
                    await self._attach(owner)
            except BaseException:
                self._fence()
                raise
            return self._owner
