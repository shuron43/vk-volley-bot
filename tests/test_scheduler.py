"""Scheduler behavior tests."""

from __future__ import annotations

import datetime
import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from src import scheduler
from src.config import Config
from src.storage import Storage

if TYPE_CHECKING:
    from pathlib import Path


class StopSchedulerError(Exception):
    """Stop the infinite scheduler loop after its behavior is observed."""


class FrozenDateTime(datetime.datetime):
    """Provide a deterministic clock for scheduler tests."""

    @classmethod
    def now(cls, tz: datetime.tzinfo | None = None) -> datetime.datetime:
        return datetime.datetime(2024, 1, 1, 9, 0, tzinfo=tz or datetime.UTC)


@pytest.fixture
def config() -> Config:
    """Provide scheduler configuration with a Monday 10:00 collection time."""
    return Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        collect_weekday=0,
        collect_time="10:00",
    )


@pytest.mark.parametrize(
    ("now", "weekday", "hour", "minute", "expected"),
    [
        (
            datetime.datetime(2024, 1, 1, 9, 30, tzinfo=datetime.UTC),
            0,
            10,
            0,
            datetime.datetime(2024, 1, 1, 10, 0, tzinfo=datetime.UTC),
        ),
        (
            datetime.datetime(2024, 1, 1, 10, 0, tzinfo=datetime.UTC),
            0,
            10,
            0,
            datetime.datetime(2024, 1, 8, 10, 0, tzinfo=datetime.UTC),
        ),
        (
            datetime.datetime(2024, 1, 2, 10, 0, tzinfo=datetime.UTC),
            0,
            10,
            0,
            datetime.datetime(2024, 1, 8, 10, 0, tzinfo=datetime.UTC),
        ),
        (
            datetime.datetime(2024, 1, 7, 23, 59, tzinfo=datetime.UTC),
            0,
            0,
            0,
            datetime.datetime(2024, 1, 8, 0, 0, tzinfo=datetime.UTC),
        ),
    ],
)
def test_next_target_returns_upcoming_configured_time(
    now: datetime.datetime,
    weekday: int,
    hour: int,
    minute: int,
    expected: datetime.datetime,
) -> None:
    # Given: a current time and configured weekly collection time
    # When: the scheduler determines its next target
    target = scheduler._next_target(now, weekday, hour, minute)

    # Then: it returns the next occurrence, never an elapsed time
    assert target == expected


@pytest.mark.parametrize(
    ("collect_target", "remind_target", "expected_target", "expected_event"),
    [
        (
            datetime.datetime(2024, 1, 2, 10, 0, tzinfo=datetime.UTC),
            datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC),
            datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC),
            "remind",
        ),
        (
            datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC),
            datetime.datetime(2024, 1, 2, 8, 0, tzinfo=datetime.UTC),
            datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC),
            "collect",
        ),
        (
            datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC),
            None,
            datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC),
            "collect",
        ),
    ],
)
def test_pick_next_event_selects_nearer_target(
    collect_target: datetime.datetime,
    remind_target: datetime.datetime | None,
    expected_target: datetime.datetime,
    expected_event: str,
) -> None:
    target, event = scheduler._pick_next_event(collect_target, remind_target)
    assert target == expected_target
    assert event == expected_event


@pytest.mark.anyio
async def test_send_announcement_sends_configured_message(config: Config) -> None:
    # Given: an API whose message endpoint succeeds
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=1)

    # When: the scheduler sends an announcement
    await scheduler._send_announcement(api, config, "keyboard")

    # Then: it targets the configured chat with the supplied keyboard
    sent = api.messages.send.await_args.kwargs
    assert sent["peer_id"] == config.chat_peer_id
    assert sent["keyboard"] == "keyboard"
    assert sent["message"].startswith("🏐 Сбор на волейбол!")


@pytest.mark.anyio
async def test_send_reminder_shows_current_list(
    config: Config,
    tmp_path: Path,
) -> None:
    # Given: a storage with two entries and a mocked API
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(vk_id=1, name="Alice")
    await storage.add_friend(name="Bob")

    api = MagicMock()
    api.messages.send = AsyncMock(return_value=1)

    # When: the scheduler sends a reminder
    await scheduler._send_reminder(api, config, "keyboard", storage)

    # Then: the message contains the formatted participant list
    sent = api.messages.send.await_args.kwargs
    assert sent["peer_id"] == config.chat_peer_id
    assert sent["keyboard"] == "keyboard"
    assert "Напоминаем: сбор на волейбол!" in sent["message"]
    assert "1. Alice" in sent["message"]
    assert "2. Bob (друг)" in sent["message"]


@pytest.mark.anyio
async def test_scheduler_retries_announcement_after_failure(
    caplog: pytest.LogCaptureFixture,
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the first API delivery fails and the next one succeeds
    api = MagicMock()
    api.messages.send = AsyncMock(side_effect=[OSError("VK unavailable"), 1])
    storage = Storage(tmp_path / "participants.json")
    sleep_durations: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_durations.append(seconds)
        if len(sleep_durations) == 3:
            raise StopSchedulerError

    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )
    monkeypatch.setattr(scheduler.anyio, "sleep", fake_sleep)

    # When: the scheduler reaches its target time
    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(api, config, storage)

    # Then: it logs the failure, waits five minutes, and retries the delivery
    assert api.messages.send.await_count == 2
    assert sleep_durations == [3600.0, 300.0, 3600.0]
    assert "Weekly announcement failed; retrying in five minutes" in caplog.text
