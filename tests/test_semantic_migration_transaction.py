"""Synthetic ownership barriers, failures, cancellation and independent identities."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from custom_components.helianthus.semantic_migration_transaction import (
    CallbackToken, Callbacks, EntityPair, MigrationTransaction, Owner,
    RecoveryRequired, RecoveryState, recovery_owner,
)


PAIR = EntityPair("entry:synthetic", "saved:synthetic", "mapping:synthetic", 7)
DRAIN = ["fence_legacy", "join_legacy", "fence_shared", "join_shared"]


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 3))


class Fake:
    def __init__(self, *, failure=None, cancel=False, blocked=None):
        self.events = []
        self.failure = failure
        self.cancel = cancel
        self.blocked = blocked
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.writers = {"legacy"}
        self.pending = {"legacy"}
        self.durable = False
        self.state = RecoveryState(PAIR, Owner.UNRESOLVED)
        self.tokens = []
        self.allowed = True
        self.admissions = []
        self.metadata = {"disabled_by": "user", "hidden_by": "user", "unit": "W"}
        self.last_good = {"value": 0, "evidence": ("public:synthetic",)}
        self.tx = MigrationTransaction(PAIR, self, Callbacks(
            self.fence_legacy, self.join_legacy, self.fence_shared, self.join_shared,
            self.restore_legacy, self.attach_shared, self.admission,
        ), legacy_established=True)

    async def step(self, name, pair):
        assert pair == PAIR
        self.events.append(name)
        if self.blocked == name:
            self.entered.set()
            await self.release.wait()
        if self.failure == name:
            self.failure = None  # cleanup can succeed independently
            if self.cancel:
                raise asyncio.CancelledError
            raise RuntimeError(name)

    def admission(self, pair, owner, value):
        self.admissions.append((pair, owner, value))
        if self.failure == "before_intent":
            self.failure = None
            if self.cancel:
                raise asyncio.CancelledError
            raise RuntimeError("before_intent")
        return self.allowed

    async def intent(self, pair):
        await self.step("intent", pair)

    async def fence_legacy(self, pair):
        self.writers.discard("legacy")
        await self.step("fence_legacy", pair)

    async def join_legacy(self, pair):
        self.pending.discard("legacy")
        await self.step("join_legacy", pair)

    async def fence_shared(self, pair):
        self.writers.discard("shared")
        await self.step("fence_shared", pair)

    async def join_shared(self, pair):
        self.pending.discard("shared")
        await self.step("join_shared", pair)

    async def commit_shared(self, pair):
        assert not self.writers and not self.pending
        assert self.events[-4:] == DRAIN
        self.durable = True  # error here deliberately has a durable outcome
        self.state = RecoveryState(pair, Owner.SHARED)
        await self.step("commit", pair)

    async def abort_intent(self, pair):
        assert not self.writers and not self.pending and not self.durable
        await self.step("abort", pair)

    async def restore_legacy(self, pair, token):
        assert not self.writers and not self.pending
        assert self.events[-1] in ("abort", "read")
        self.tokens.append(token)
        self.writers.add("legacy")
        await self.step("restore", pair)

    async def attach_shared(self, pair, token):
        assert self.durable
        assert not self.writers and not self.pending
        assert self.events[-1] in ("commit", "read")
        # Physical listener creation may happen before attachment fails.
        self.writers.add("shared")
        self.pending.add("shared")
        self.tokens.append(token)
        assert not self.tx.admit(token, 0)  # no publication during attachment
        await self.step("attach", pair)

    async def read_recovery(self, pair):
        assert not self.writers and not self.pending
        await self.step("read", pair)
        return self.state


async def successful():
    fake = Fake()
    old = fake.tx.token
    assert fake.tx.admit(old, 0)
    assert await fake.tx.migrate(0) is Owner.SHARED
    assert fake.events == ["intent", *DRAIN, "commit", "attach"]
    assert fake.writers == {"shared"}
    assert not fake.tx.admit(old, 0)
    assert fake.tx.admit(fake.tokens[-1], 0)
    fake.allowed = False
    assert not fake.tx.admit(fake.tx.token, 0)
    assert fake.last_good == {"value": 0, "evidence": ("public:synthetic",)}
    assert fake.metadata == {"disabled_by": "user", "hidden_by": "user", "unit": "W"}
    assert await fake.tx.migrate(0) is Owner.SHARED
    assert await fake.tx.recover() is Owner.SHARED
    assert fake.events.count("attach") == 1


def test_order_one_writer_admission_and_metadata_preservation():
    run(successful())


@pytest.mark.parametrize("boundary", [
    "before_intent", "intent", "fence_legacy", "join_legacy", "fence_shared",
    "join_shared", "commit", "attach",
])
@pytest.mark.parametrize("cancel", [False, True])
def test_failure_or_cancel_at_each_boundary(boundary, cancel):
    async def exercise():
        fake = Fake(failure=boundary, cancel=cancel)
        old = fake.tx.token
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await fake.tx.migrate(0)
        if boundary == "before_intent":
            assert fake.events == []
            assert fake.tx.token is old
        elif boundary in ("commit", "attach"):
            assert fake.tx.owner is Owner.UNRESOLVED
            assert not fake.tx.admit(old, 0)
            assert all(not fake.tx.admit(token, 0) for token in fake.tokens)
            assert "abort" not in fake.events and "restore" not in fake.events
            with pytest.raises(RecoveryRequired):
                await fake.tx.migrate(0)
            assert await fake.tx.recover() is Owner.SHARED
            assert await fake.tx.recover() is Owner.SHARED
            assert fake.events.count("read") == 1
            assert fake.events.count("attach") == (2 if boundary == "attach" else 1)
        else:
            assert fake.events[-6:] == [*DRAIN, "abort", "restore"]
            assert not fake.tx.admit(old, 0)
            assert fake.tx.admit(fake.tx.token, 0)
        assert fake.tx.owner is (Owner.SHARED if boundary in ("commit", "attach") else Owner.LEGACY)
        assert len(fake.writers) == 1
    run(exercise())


@pytest.mark.parametrize("boundary", ["intent", "fence_legacy", "join_legacy", "commit", "attach"])
def test_real_task_cancellation_and_concurrent_retry(boundary):
    async def exercise():
        fake = Fake(blocked=boundary)
        old = fake.tx.token
        task = asyncio.create_task(fake.tx.migrate(0))
        await fake.entered.wait()
        assert not fake.tx.admit(old, 0)
        task.cancel()
        fake.blocked = None
        with pytest.raises(asyncio.CancelledError):
            await task
        if boundary in ("commit", "attach"):
            assert fake.tx.owner is Owner.UNRESOLVED
            assert "restore" not in fake.events
        else:
            assert fake.tx.owner is Owner.LEGACY
            assert fake.events[-6:] == [*DRAIN, "abort", "restore"]
    run(exercise())


def test_concurrent_calls_wait_for_join_and_attach_once():
    async def exercise():
        fake = Fake(blocked="join_legacy")
        first = asyncio.create_task(fake.tx.migrate(0))
        await fake.entered.wait()
        second = asyncio.create_task(fake.tx.migrate(0))
        await asyncio.sleep(0)
        assert "commit" not in fake.events and "attach" not in fake.events
        assert not second.done()
        fake.release.set()
        assert await first is Owner.SHARED
        assert await second is Owner.SHARED
        assert fake.events == ["intent", *DRAIN, "commit", "attach"]
    run(exercise())


AXES = [
    ("entry_id", "entry:other"),
    ("saved_unique_id", "saved:other"),
    ("mapping_identity", "mapping:other"),
    ("generation", 8),
]


@pytest.mark.parametrize("field,value", AXES)
def test_recovery_identity_axes_independently(field, value):
    changed = replace(PAIR, **{field: value})
    assert recovery_owner(PAIR, RecoveryState(changed, Owner.SHARED)) is Owner.UNRESOLVED
    assert recovery_owner(changed, RecoveryState(PAIR, Owner.LEGACY)) is Owner.UNRESOLVED
    assert recovery_owner(PAIR, RecoveryState(PAIR, Owner.SHARED)) is Owner.SHARED


@pytest.mark.parametrize("field,value", AXES)
def test_callback_identity_axes_independently(field, value):
    async def exercise():
        fake = Fake()
        await fake.tx.migrate(0)
        token = fake.tx.token
        # Keep the capability itself to isolate the full pair comparison.
        object.__setattr__(token, "pair", replace(PAIR, **{field: value}))
        calls = len(fake.admissions)
        assert not fake.tx.admit(token, 0)
        assert len(fake.admissions) == calls
    run(exercise())


def test_callback_token_identity_and_owner_are_independent_fences():
    async def exercise():
        fake = Fake()
        await fake.tx.migrate(0)
        token = fake.tx.token
        assert not fake.tx.admit(CallbackToken(PAIR, Owner.SHARED), 0)
        object.__setattr__(token, "owner", Owner.LEGACY)
        assert not fake.tx.admit(token, 0)
    run(exercise())


@pytest.mark.parametrize("selected", [Owner.LEGACY, Owner.SHARED, Owner.UNRESOLVED])
def test_explicit_reload_recovery_is_read_only_and_idempotent(selected):
    async def exercise():
        fake = Fake()
        fake.tx = MigrationTransaction(PAIR, fake, fake.tx._callbacks)
        fake.state = RecoveryState(PAIR, selected)
        fake.durable = selected is Owner.SHARED
        assert not fake.tx.admit(fake.tx.token, 0)
        assert await fake.tx.recover() is selected
        assert fake.events == [*DRAIN, "read", *(
            ["attach"] if selected is Owner.SHARED else ["restore"] if selected is Owner.LEGACY else []
        )]
        await fake.tx.recover()
        assert fake.events.count("attach") <= 1
        assert fake.events.count("restore") <= 1
        assert "intent" not in fake.events and "abort" not in fake.events
    run(exercise())


@pytest.mark.parametrize("boundary", [*DRAIN, "abort", "restore"])
def test_failed_precommit_cleanup_never_enables_publication(boundary):
    async def exercise():
        fake = Fake(failure="intent")
        original = fake.step
        async def step(name, pair):
            if name == boundary:
                fake.events.append(name)
                raise RuntimeError("cleanup")
            await original(name, pair)
        fake.step = step
        with pytest.raises(RuntimeError):
            await fake.tx.migrate(0)
        assert fake.tx.owner is Owner.UNRESOLVED
        assert not fake.tx.admit(fake.tx.token, 0)
        assert all(not fake.tx.admit(token, 0) for token in fake.tokens)
        assert "commit" not in fake.events and "attach" not in fake.events
    run(exercise())


def test_denied_preflight_does_not_record_intent_or_change_legacy_token():
    async def exercise():
        fake = Fake()
        fake.allowed = False
        token = fake.tx.token
        assert await fake.tx.migrate(0) is Owner.LEGACY
        assert fake.tx.token is token
        assert fake.events == []
    run(exercise())


@pytest.mark.parametrize("state", [None, RecoveryState(PAIR, Owner.UNRESOLVED),
                                   RecoveryState(replace(PAIR, generation=9), Owner.SHARED)])
def test_unresolved_or_foreign_recovery_never_attaches(state):
    async def exercise():
        fake = Fake()
        fake.tx = MigrationTransaction(PAIR, fake, fake.tx._callbacks)
        fake.state = state
        assert await fake.tx.recover() is Owner.UNRESOLVED
        assert fake.events == [*DRAIN, "read"]
        assert fake.writers == set()
    run(exercise())


@pytest.mark.parametrize("boundary", [*DRAIN, "read", "restore", "attach"])
@pytest.mark.parametrize("cancel", [False, True])
def test_recovery_failure_or_cancel_retains_fence(boundary, cancel):
    async def exercise():
        fake = Fake(failure=boundary, cancel=cancel)
        fake.tx = MigrationTransaction(PAIR, fake, fake.tx._callbacks)
        chosen = Owner.LEGACY if boundary == "restore" else Owner.SHARED
        fake.state = RecoveryState(PAIR, chosen)
        fake.durable = chosen is Owner.SHARED
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await fake.tx.recover()
        assert fake.tx.owner is Owner.UNRESOLVED
        assert all(not fake.tx.admit(token, 0) for token in fake.tokens)
        assert "abort" not in fake.events and "intent" not in fake.events
        assert await fake.tx.recover() is chosen
        active = fake.tx.token
        assert fake.tx.admit(active, 0)
        assert all(not fake.tx.admit(token, 0) for token in fake.tokens if token is not active)
    run(exercise())


def test_second_cancellation_during_cleanup_leaves_fenced_without_detached_restore():
    async def exercise():
        fake = Fake(failure="intent", blocked="join_legacy")
        task = asyncio.create_task(fake.tx.migrate(0))
        await fake.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        fake.release.set()
        await asyncio.sleep(0)
        assert fake.tx.owner is Owner.UNRESOLVED
        assert "restore" not in fake.events and "abort" not in fake.events
        assert fake.events == ["intent", "fence_legacy", "join_legacy"]
    run(exercise())
