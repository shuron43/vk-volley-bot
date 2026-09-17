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
        event_weekday=0,
        event_time="11:00",
        remind_enabled=False,
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


def test_weekly_event_start_follows_announcement() -> None:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        collect_weekday=0,
        collect_time="08:00",
        event_weekday=1,
        event_time="19:30",
        remind_enabled=False,
    )
    announcement = datetime.datetime(2026, 9, 21, 8, 0)  # noqa: DTZ001
    expected = datetime.datetime(2026, 9, 22, 19, 30)  # noqa: DTZ001
    assert scheduler._weekly_event_after(announcement, config) == expected


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
@pytest.mark.parametrize(
    ("response", "expected_message_id"),
    [(123, 123), (None, None), ("unexpected", None)],
)
async def test_send_announcement_returns_message_id_when_available(
    config: Config,
    response: int | str | None,
    expected_message_id: int | None,
) -> None:
    # Given: an API whose message endpoint returns a message ID or mock value
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=response)

    # When: the scheduler sends an announcement
    message_id = await scheduler._send_announcement(api, config, "keyboard")

    # Then: it targets the configured chat and retains only valid message IDs
    sent = api.messages.send.await_args.kwargs
    assert sent["peer_id"] == config.chat_peer_id
    assert sent["keyboard"] == "keyboard"
    assert sent["message"].startswith("🏐 Запись на волейбол открыта!")
    assert message_id == expected_message_id


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
async def test_scheduler_opens_collection_after_successful_announcement(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: a previous collection and a successful announcement delivery
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=123)
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(vk_id=1, name="Alice")
    sleep_durations: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_durations.append(seconds)
        if len(sleep_durations) == 2:
            raise StopSchedulerError

    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )
    monkeypatch.setattr(scheduler.anyio, "sleep", fake_sleep)

    # When: the scheduler reaches the collection target
    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(api, config, storage)

    # Then: it clears the previous collection and opens the new one with its ID
    assert await storage.list_entries() == []
    assert await storage.registration_state() == "open"
    assert await storage.status_message_id() == 123


@pytest.mark.anyio
async def test_scheduler_preserves_participants_while_announcement_is_retried(
    caplog: pytest.LogCaptureFixture,
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the first API delivery fails and the next one succeeds
    api = MagicMock()
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(vk_id=1, name="Alice")
    failed_attempt_participants: list[str] = []
    failed_attempt_states: list[str] = []
    attempts = 0

    async def send_message(**_kwargs: int | str) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failed_attempt_participants.extend(
                entry.name for entry in await storage.list_entries()
            )
            failed_attempt_states.append(await storage.registration_state())
            error = OSError("VK unavailable")
            raise error
        return 456

    api.messages.send = AsyncMock(side_effect=send_message)
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

    # Then: it preserves the prior collection while opening is retried
    assert api.messages.send.await_count == 2
    assert (
        api.messages.send.await_args_list[0].kwargs["random_id"]
        == api.messages.send.await_args_list[1].kwargs["random_id"]
    )
    assert sleep_durations == [3600.0, 300.0, 3600.0]
    assert "Weekly announcement failed; retrying in five minutes" in caplog.text
    assert failed_attempt_participants == ["Alice"]
    assert failed_attempt_states == ["opening"]
    assert await storage.list_entries() == []
    assert await storage.registration_state() == "open"
    assert await storage.status_message_id() == 456


@pytest.mark.anyio
async def test_scheduler_starts_new_collection_once_when_announcement_is_retried(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a collection delivery that fails once before succeeding
    api = MagicMock()
    api.messages.send = AsyncMock(side_effect=[OSError("VK unavailable"), 1])
    storage = MagicMock(spec=Storage)
    storage.registration_state = AsyncMock(return_value="closed")
    storage.event_details = AsyncMock(
        return_value=(
            datetime.datetime(2024, 1, 1, 11, 0, tzinfo=datetime.UTC),
            "weekly",
        )
    )
    storage.announcement_random_id = AsyncMock(return_value=42)
    storage.begin_event = AsyncMock(return_value=True)
    storage.mark_opening = AsyncMock()
    storage.start_new_collection = AsyncMock()
    storage.activate_event = AsyncMock()
    storage.clear = AsyncMock()
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

    # When: the announcement send is retried
    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(api, config, storage)

    # Then: the opening transition is attempted once and collection starts once
    storage.begin_event.assert_awaited_once_with(
        datetime.datetime(2024, 1, 1, 11, 0, tzinfo=datetime.UTC), "weekly"
    )
    storage.mark_opening.assert_not_awaited()
    storage.activate_event.assert_awaited_once_with(
        1, datetime.datetime(2024, 1, 1, 11, 0, tzinfo=datetime.UTC)
    )
    storage.start_new_collection.assert_not_awaited()
    storage.clear.assert_not_awaited()


@pytest.mark.anyio
async def test_scheduler_resumes_pending_opening_before_sleep(
    config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "participants.json"
    storage = Storage(path)
    await storage.add_friend("Previous participant")
    await storage.mark_opening()
    random_id = await storage.announcement_random_id()
    restored = Storage(path)
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=123)
    monkeypatch.setattr(
        scheduler.anyio, "sleep", AsyncMock(side_effect=StopSchedulerError)
    )
    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(api, config, restored)
    api.messages.send.assert_awaited_once()
    assert api.messages.send.await_args.kwargs["random_id"] == random_id
    assert await Storage(path).registration_state() == "open"
    assert await restored.status_message_id() == 123
    assert await restored.list_entries() == []


@pytest.mark.anyio
async def test_activation_retry_does_not_resend_announcement(
    config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.mark_opening()
    activate = storage.activate_event
    attempts = 0

    async def fail_once(
        message_id: int | None, expected_start: datetime.datetime
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message = "disk unavailable"
            raise OSError(message)
        await activate(message_id, expected_start)

    monkeypatch.setattr(storage, "activate_event", fail_once)
    monkeypatch.setattr(scheduler.anyio, "sleep", AsyncMock())
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=123)
    await scheduler._finish_opening(api, config, storage)
    api.messages.send.assert_awaited_once()
    assert attempts == 2
    assert await storage.is_registration_open()


@pytest.mark.anyio
async def test_scheduler_closes_registration_at_event_start(
    config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2024, 1, 1, 9, 0, tzinfo=datetime.UTC)
    assert await storage.begin_event(starts_at, "weekly")
    await storage.start_new_collection(123)
    await storage.add_friend("Bob")
    api = MagicMock()
    api.messages.edit = AsyncMock()
    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )
    monkeypatch.setattr(
        scheduler.anyio, "sleep", AsyncMock(side_effect=StopSchedulerError)
    )

    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(api, config, storage)

    assert await storage.registration_state() == "closed"
    assert [entry.name for entry in await storage.list_entries()] == ["Bob"]
    assert "Запись закрыта" in api.messages.edit.await_args.kwargs["message"]


@pytest.mark.anyio
async def test_expired_opening_closes_after_restart_without_late_announcement(
    config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC)
    assert await storage.begin_event(starts_at, "manual")
    restored = Storage(tmp_path / "participants.json")
    api = MagicMock()
    api.messages.send = AsyncMock()
    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )
    monkeypatch.setattr(
        scheduler.anyio, "sleep", AsyncMock(side_effect=StopSchedulerError)
    )

    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(api, config, restored)

    assert await restored.registration_state() == "closed"
    api.messages.send.assert_not_awaited()


@pytest.mark.anyio
async def test_manual_event_opens_now_and_must_end_before_weekly_announcement(
    config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.datetime(2024, 1, 1, 9, 0, tzinfo=datetime.UTC)
    storage = Storage(tmp_path / "participants.json")
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=321)
    starts_at = now + datetime.timedelta(minutes=30)
    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )

    await scheduler.open_manual_event(api, config, storage, starts_at, now=now)

    assert await storage.registration_state() == "open"
    assert await storage.event_details() == (starts_at, "manual")
    assert "01.01.2024 в 09:30" in api.messages.send.await_args.kwargs["message"]

    await storage.close_registration()
    next_announcement = datetime.datetime(2024, 1, 8, 10, 0, tzinfo=datetime.UTC)
    with pytest.raises(ValueError, match="пересекается"):
        await scheduler.open_manual_event(
            api, config, storage, next_announcement, now=now
        )


@pytest.mark.anyio
async def test_manual_event_rejects_active_registration(
    config: Config, tmp_path: Path
) -> None:
    now = datetime.datetime(2024, 1, 1, 9, 0, tzinfo=datetime.UTC)
    storage = Storage(tmp_path / "participants.json")
    assert await storage.begin_event(now + datetime.timedelta(minutes=15), "weekly")
    api = MagicMock()
    api.messages.send = AsyncMock()
    with pytest.raises(ValueError, match="активное событие"):
        await scheduler.open_manual_event(
            api,
            config,
            storage,
            now + datetime.timedelta(minutes=30),
            now=now,
        )
    api.messages.send.assert_not_awaited()
