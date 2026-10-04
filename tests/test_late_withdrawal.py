"""Storage failure and legacy-state contracts for late withdrawals.

Business rules are exercised through VK routing in test_bot_handlers.py.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from src.storage import Storage

if TYPE_CHECKING:
    from pathlib import Path

_START = datetime.datetime(2026, 9, 30, 19, 30)  # noqa: DTZ001


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["withdraw", "restore"])
async def test_failed_late_change_preserves_live_and_durable_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    # Given: a started event and a disk that refuses the next write.
    path = tmp_path / "participants.json"
    storage = Storage(path)
    assert await storage.begin_event(_START, "manual")
    await storage.activate_event(1, _START)
    await storage.add_user(1, "Alice")
    await storage.add_user(2, "Bob")
    await storage.close_registration()
    monkeypatch.setattr(Storage, "_now", lambda _: _START)
    if operation == "restore":
        assert await storage.withdraw_user(1)
    before = await storage.snapshot()
    monkeypatch.setattr(storage, "_save", AsyncMock(side_effect=OSError("disk")))
    action = storage.withdraw_user if operation == "withdraw" else storage.restore_user

    # When: a late change fails to persist and storage is reopened.
    with pytest.raises(OSError, match="disk"):
        await action(1)
    reopened = Storage(path)

    # Then: both live and restarted storage retain the complete previous state.
    assert await storage.snapshot() == before
    assert await reopened.snapshot() == before


@pytest.mark.anyio
async def test_closed_legacy_state_without_start_refuses_withdrawal(
    tmp_path: Path,
) -> None:
    # Given: a closed legacy list with no event deadline.
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(1, "Alice")
    before = await storage.snapshot()

    # When: a withdrawal is requested without an event to calculate grace from.
    with pytest.raises(ValueError, match="Запись закрыта"):
        await storage.withdraw_user(1)

    # Then: the missing deadline cannot enable changes to the saved list.
    assert await storage.snapshot() == before
    assert await Storage(tmp_path / "participants.json").snapshot() == before
