"""BDD scenarios for preserving withdrawals after the event starts."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from src.formatting import format_registration_card
from src.storage import Storage

if TYPE_CHECKING:
    from pathlib import Path

_START = datetime.datetime(2026, 9, 30, 19, 30)  # noqa: DTZ001


async def _event(tmp_path: Path) -> Storage:
    storage = Storage(tmp_path / "participants.json")
    assert await storage.begin_event(_START, "manual")
    await storage.activate_event(1, _START)
    await storage.add_user(1, "Alice")
    await storage.add_user(2, "Bob")
    await storage.add_friend("Друг")
    return storage


def _clock(monkeypatch: pytest.MonkeyPatch, now: datetime.datetime) -> None:
    monkeypatch.setattr(Storage, "_now", lambda _: now)


@pytest.mark.anyio
@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("seconds", [0, 1799])
async def test_withdrawal_in_first_half_hour_keeps_marked_name_at_bottom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closed: bool,
    seconds: int,
) -> None:
    # Given: the event has started, even if the scheduler has not closed it yet.
    storage = await _event(tmp_path)
    if closed:
        await storage.close_registration()
    _clock(monkeypatch, _START + datetime.timedelta(seconds=seconds))

    # When: a participant withdraws during the first half hour.
    assert await storage.withdraw_user(1)

    # Then: the name remains in the persisted list, marked and moved below others.
    entries = await Storage(tmp_path / "participants.json").list_entries()
    assert [entry.name for entry in entries] == ["Bob", "Друг", "Alice"]
    assert entries[-1].withdrawn
    text = format_registration_card(entries, "closed", _START)
    assert "3. Alice (-)" in text
    assert "Участники: 2" in text


@pytest.mark.anyio
async def test_rejoin_removes_minus_and_returns_name_to_active_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two participants withdrew after the event started.
    storage = await _event(tmp_path)
    await storage.close_registration()
    _clock(monkeypatch, _START + datetime.timedelta(minutes=10))
    assert await storage.withdraw_user(1)
    assert await storage.withdraw_user(2)

    # When: one returns within the allowed half hour.
    assert await storage.restore_user(1)

    # Then: their minus disappears and withdrawn names remain at the bottom.
    entries = await Storage(tmp_path / "participants.json").list_entries()
    assert [entry.name for entry in entries] == ["Друг", "Alice", "Bob"]
    assert [entry.withdrawn for entry in entries] == [False, False, True]


@pytest.mark.anyio
@pytest.mark.parametrize("seconds", [1800, 1801, 86400])
@pytest.mark.parametrize("operation", ["withdraw", "restore"])
async def test_after_half_hour_the_final_list_cannot_be_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seconds: int,
    operation: str,
) -> None:
    # Given: a closed event, with a withdrawal if a return is being attempted.
    storage = await _event(tmp_path)
    await storage.close_registration()
    _clock(monkeypatch, _START)
    if operation == "restore":
        assert await storage.withdraw_user(1)
    before = await storage.snapshot()
    _clock(monkeypatch, _START + datetime.timedelta(seconds=seconds))

    # When: the 30-minute boundary has been reached or passed.
    # Then: withdrawal and return are refused without changing live/durable state.
    action = storage.withdraw_user if operation == "withdraw" else storage.restore_user
    with pytest.raises(ValueError, match="30 минут"):
        await action(1)
    assert await storage.snapshot() == before
    assert await Storage(tmp_path / "participants.json").snapshot() == before


@pytest.mark.anyio
async def test_withdrawal_before_start_still_removes_the_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = await _event(tmp_path)
    _clock(monkeypatch, _START - datetime.timedelta(seconds=1))
    assert await storage.withdraw_user(1)
    assert [entry.name for entry in await storage.list_entries()] == ["Bob", "Друг"]


@pytest.mark.anyio
async def test_friend_can_withdraw_and_return_without_creating_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = await _event(tmp_path)
    await storage.close_registration()
    _clock(monkeypatch, _START + datetime.timedelta(minutes=5))
    assert await storage.withdraw_friend("Друг")
    assert "Друг (-) (друг)" in format_registration_card(
        await storage.list_entries(),
        "closed",
        _START,
    )
    assert await storage.restore_friend("Друг")
    entries = await storage.list_entries()
    assert len(entries) == 3
    assert all(not entry.withdrawn for entry in entries)


@pytest.mark.anyio
async def test_duplicate_withdrawal_does_not_change_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = await _event(tmp_path)
    await storage.close_registration()
    _clock(monkeypatch, _START)
    assert await storage.withdraw_user(1)
    before = await storage.snapshot()
    assert not await storage.withdraw_user(1)
    assert await storage.snapshot() == before


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["withdraw", "restore"])
async def test_failed_late_withdrawal_preserves_live_and_durable_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    storage = await _event(tmp_path)
    await storage.close_registration()
    _clock(monkeypatch, _START)
    if operation == "restore":
        assert await storage.withdraw_user(1)
    before = await storage.snapshot()
    monkeypatch.setattr(storage, "_save", AsyncMock(side_effect=OSError("disk")))
    action = storage.withdraw_user if operation == "withdraw" else storage.restore_user
    with pytest.raises(OSError, match="disk"):
        await action(1)
    assert await storage.snapshot() == before
    assert await Storage(tmp_path / "participants.json").snapshot() == before


@pytest.mark.anyio
async def test_closed_event_without_start_cannot_accept_withdrawal(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(1, "Alice")
    with pytest.raises(ValueError, match="Запись закрыта"):
        await storage.withdraw_user(1)
