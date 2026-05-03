"""Tests for TurnRegistry — the in-memory turn_id → asyncio.Task map."""

from __future__ import annotations

import asyncio

import pytest

from hara.api.turns import TurnRegistry


async def test_register_and_get() -> None:
    reg = TurnRegistry()

    async def _slow() -> int:
        await asyncio.sleep(0.05)
        return 42

    task = asyncio.create_task(_slow())
    reg.register("trn_x", task)
    assert reg.get("trn_x") is task
    await task


async def test_get_returns_none_for_unknown() -> None:
    reg = TurnRegistry()
    assert reg.get("trn_nope") is None


async def test_cancel_in_flight_returns_true() -> None:
    reg = TurnRegistry()

    async def _hang() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(_hang())
    reg.register("trn_x", task)
    assert reg.cancel("trn_x") is True
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancel_unknown_returns_false() -> None:
    reg = TurnRegistry()
    assert reg.cancel("trn_nope") is False


async def test_cancel_already_done_returns_false() -> None:
    """Idempotent: spec §6 says DELETE on completed turn returns 204
    without effect. Registry-level: cancel() returns False so the route
    can decide based on the DB record's status."""
    reg = TurnRegistry()

    async def _quick() -> int:
        return 1

    task = asyncio.create_task(_quick())
    await task
    reg.register("trn_x", task)
    assert reg.cancel("trn_x") is False


async def test_unregister_drops_completed() -> None:
    reg = TurnRegistry()

    async def _q() -> int:
        return 1

    task = asyncio.create_task(_q())
    reg.register("trn_x", task)
    await task
    reg.unregister("trn_x")
    assert reg.get("trn_x") is None
