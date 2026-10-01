"""Scheduler behavior tests."""

from __future__ import annotations

import datetime
import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from src import scheduler
from src.cards import CardPublisher, MessageRef
from src.config import Config
from src.storage import Storage

if TYPE_CHECKING:
    from asyncio import Event
    from collections.abc import Awaitable, Callable
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
    return Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        collect_weekday=0,
        collect_time="10:00",
        event_weekday=0,
        event_time="11:00",
        remind_enabled=False,
    )


def _publisher(*, activate: bool = True) -> MagicMock:
    publisher = MagicMock(spec=CardPublisher)
    ref = MessageRef(message_id=123, conversation_message_id=45)
    publisher.edit_current = AsyncMock(return_value=ref)
    publisher.replace_current = AsyncMock(return_value=ref)

    async def publish_event(
        _starts_at: datetime.datetime,
        _random_id: int,
        callback: Callable[[MessageRef], Awaitable[None]],
    ) -> MessageRef:
        if activate:
            await callback(ref)
        return ref

    publisher.publish_event = AsyncMock(side_effect=publish_event)
    return publisher


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
    ],
)
def test_next_target_returns_upcoming_configured_time(
    now: datetime.datetime,
    weekday: int,
    hour: int,
    minute: int,
    expected: datetime.datetime,
) -> None:
    assert scheduler._next_target(now, weekday, hour, minute) == expected


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


def test_pick_next_event_includes_close_deadline() -> None:
    collect = datetime.datetime(2024, 1, 8, 10, 0)  # noqa: DTZ001
    remind = datetime.datetime(2024, 1, 2, 8, 0)  # noqa: DTZ001
    close = datetime.datetime(2024, 1, 1, 11, 0)  # noqa: DTZ001
    assert scheduler._pick_next_event(collect, remind, close) == (close, "close")


@pytest.mark.anyio
async def test_send_reminder_moves_same_card_last() -> None:
    publisher = _publisher()

    await scheduler._send_reminder(publisher)

    publisher.replace_current.assert_awaited_once_with(
        notice="Напоминание: проверьте список участников перед тренировкой."
    )


@pytest.mark.anyio
async def test_retry_reuses_operation_until_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = AsyncMock(side_effect=[OSError("offline"), 42])
    sleep = AsyncMock()
    monkeypatch.setattr(scheduler.anyio, "sleep", sleep)

    result = await scheduler._run_with_retry("test operation", operation)

    assert result == 42
    assert operation.await_count == 2
    sleep.assert_awaited_once_with(300)


@pytest.mark.anyio
async def test_finish_opening_activates_event_with_both_message_ids(
    config: Config,
    tmp_path: Path,
) -> None:
    starts_at = datetime.datetime.now() + datetime.timedelta(days=1)  # noqa: DTZ005
    storage = Storage(tmp_path / "participants.json")
    assert await storage.begin_event(starts_at, "weekly")
    publisher = _publisher()

    assert await scheduler._finish_opening(MagicMock(), config, storage, publisher)

    snapshot = await storage.snapshot()
    assert snapshot.state == "open"
    assert snapshot.status_message_id == 123
    assert snapshot.status_conversation_message_id == 45
    assert snapshot.event_starts_at == starts_at


@pytest.mark.anyio
async def test_finish_opening_closes_event_when_publication_reaches_deadline(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2024, 1, 1, 8, 0, tzinfo=datetime.UTC)
    assert await storage.begin_event(starts_at, "manual")
    publisher = _publisher()
    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )

    assert not await scheduler._finish_opening(MagicMock(), config, storage, publisher)

    assert await storage.registration_state() == "closed"
    publisher.publish_event.assert_not_awaited()
    publisher.edit_current.assert_awaited_once_with()


@pytest.mark.anyio
async def test_late_vk_response_cannot_reopen_event(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    starts_at = datetime.datetime(2024, 1, 1, 10, 0, tzinfo=datetime.UTC)
    moments = iter(
        (
            datetime.datetime(2024, 1, 1, 9, 59, tzinfo=datetime.UTC),
            starts_at,
        )
    )

    class TickingDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> datetime.datetime:
            value = next(moments)
            return value if tz is not None else value.replace(tzinfo=None)

    storage = Storage(tmp_path / "participants.json")
    assert await storage.begin_event(starts_at, "weekly")
    publisher = _publisher()
    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=TickingDateTime, timedelta=datetime.timedelta),
    )

    assert not await scheduler._finish_opening(MagicMock(), config, storage, publisher)

    assert await storage.registration_state() == "closed"
    publisher.publish_event.assert_awaited_once()
    publisher.edit_current.assert_awaited_once_with()


@pytest.mark.anyio
async def test_manual_event_opens_immediately(
    config: Config,
    tmp_path: Path,
) -> None:
    now = datetime.datetime.now(tz=datetime.UTC)
    starts_at = now + datetime.timedelta(minutes=30)
    storage = Storage(tmp_path / "participants.json")
    publisher = _publisher()

    await scheduler.open_manual_event(
        MagicMock(), config, storage, starts_at, cards=publisher, now=now
    )

    assert await storage.registration_state() == "open"
    assert await storage.event_details() == (starts_at, "manual")
    publisher.publish_event.assert_awaited_once()


@pytest.mark.anyio
async def test_manual_event_rejects_overlap_and_active_event(
    config: Config,
    tmp_path: Path,
) -> None:
    now = datetime.datetime(2024, 1, 1, 9, 0, tzinfo=datetime.UTC)
    storage = Storage(tmp_path / "participants.json")
    publisher = _publisher()

    with pytest.raises(ValueError, match="пересекается"):
        await scheduler.open_manual_event(
            MagicMock(),
            config,
            storage,
            datetime.datetime(2024, 1, 1, 10, 0, tzinfo=datetime.UTC),
            cards=publisher,
            now=now,
        )

    assert await storage.begin_event(now + datetime.timedelta(minutes=10), "manual")
    with pytest.raises(ValueError, match="активное событие"):
        await scheduler.open_manual_event(
            MagicMock(),
            config,
            storage,
            now + datetime.timedelta(minutes=20),
            cards=publisher,
            now=now,
        )


@pytest.mark.anyio
async def test_scheduler_recovers_legacy_open_state_without_deadline(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(None)
    await storage.add_friend("Bob")
    publisher = _publisher()
    monkeypatch.setattr(
        scheduler,
        "_wait_for_schedule_change",
        AsyncMock(side_effect=StopSchedulerError),
    )

    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(MagicMock(), config, storage, publisher)

    assert await storage.registration_state() == "closed"
    assert [entry.name for entry in await storage.list_entries()] == ["Bob"]
    publisher.edit_current.assert_awaited_once_with(
        notice="Предыдущее событие закрыто: время начала не было сохранено."
    )


@pytest.mark.anyio
async def test_scheduler_closes_elapsed_event_and_preserves_participants(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2024, 1, 1, 9, 0, tzinfo=datetime.UTC)
    assert await storage.begin_event(starts_at, "weekly")
    await storage.activate_event(123, starts_at, conversation_message_id=45)
    await storage.add_friend("Bob")
    publisher = _publisher()
    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )
    monkeypatch.setattr(
        scheduler,
        "_wait_for_schedule_change",
        AsyncMock(side_effect=StopSchedulerError),
    )

    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(MagicMock(), config, storage, publisher)

    assert await storage.registration_state() == "closed"
    assert [entry.name for entry in await storage.list_entries()] == ["Bob"]
    publisher.edit_current.assert_awaited_once_with()


@pytest.mark.anyio
async def test_scheduler_opens_weekly_event_after_announcement_time(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.add_friend("Previous")
    publisher = _publisher()
    sleep = AsyncMock(side_effect=[False, StopSchedulerError])
    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=datetime.timedelta),
    )
    monkeypatch.setattr(scheduler, "_wait_for_schedule_change", sleep)

    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(MagicMock(), config, storage, publisher)

    assert await storage.registration_state() == "open"
    assert await storage.list_entries() == []
    assert await storage.event_details() == (
        datetime.datetime(2024, 1, 1, 11, 0, tzinfo=datetime.UTC),
        "weekly",
    )
    publisher.publish_event.assert_awaited_once()


@pytest.mark.anyio
async def test_manual_event_wakes_sleeping_scheduler_and_closes_actual_card_on_time(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: an idle scheduler waiting an hour for the weekly announcement.
    current = datetime.datetime(2024, 1, 1, 9, 0, tzinfo=datetime.UTC)
    starts_at = current + datetime.timedelta(minutes=1)

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> datetime.datetime:
            return current

    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(
            datetime=Clock,
            timedelta=datetime.timedelta,
        ),
    )
    storage = Storage(tmp_path / "participants.json")
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=123)
    api.messages.edit = AsyncMock()
    api.messages.delete = AsyncMock()
    publisher = CardPublisher(api, config, storage)
    original_wait = scheduler._wait_for_schedule_change
    waits: list[float] = []

    async def wait(changed: Event, delay: float) -> bool:
        nonlocal current
        waits.append(delay)
        if len(waits) == 1:
            # When: the admin creates a manual event while the scheduler sleeps.
            await scheduler.open_manual_event(
                api,
                config,
                storage,
                starts_at,
                cards=publisher,
                now=current,
            )
            await storage.add_friend("Bob")
            assert "запись открыта" in api.messages.send.await_args.kwargs["message"]
            assert changed.is_set()
            return await original_wait(changed, delay)
        if len(waits) == 2:
            # Advance the clock to the new deadline, without waiting in real time.
            current = starts_at
            return False
        raise StopSchedulerError

    monkeypatch.setattr(scheduler, "_wait_for_schedule_change", wait)
    with pytest.raises(StopSchedulerError):
        await scheduler.run_scheduler(api, config, storage, publisher)

    # Then: the new deadline replaces the weekly wait, and the card closes in place.
    assert waits[:2] == [3600, 60]
    api.messages.edit.assert_awaited_once()
    text = api.messages.edit.await_args.kwargs["message"]
    assert "запись закрыта" in text
    assert "закроется автоматически" not in text
    assert "1. Bob" in text
    api.messages.send.assert_awaited_once()
    assert await storage.registration_state() == "closed"
    assert (
        await Storage(tmp_path / "participants.json").registration_state() == "closed"
    )


@pytest.mark.anyio
async def test_schedule_wait_distinguishes_deadline_from_change(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "participants.json")
    changed = await storage.schedule_change_event()
    assert not await scheduler._wait_for_schedule_change(changed, 0)
    await storage.add_friend("Bob")
    assert not changed.is_set()
    assert await storage.begin_event(datetime.datetime.now(), "manual")  # noqa: DTZ005
    assert await scheduler._wait_for_schedule_change(changed, 3600)
