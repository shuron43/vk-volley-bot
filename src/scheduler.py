"""Scheduler for weekly and administrator-created events."""

import datetime
import logging
import secrets
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Final, Literal, NoReturn

import anyio
from vkbottle import VKAPIError
from vkbottle.api import API

from src.config import Config
from src.formatting import format_entries, format_event_start, format_registration_card
from src.keyboard import build_inline_keyboard
from src.storage import Storage

_RETRY_DELAY_SECONDS: Final = 5 * 60
_LOGGER: Final = logging.getLogger(__name__)
ScheduleEvent = Literal["collect", "remind", "close"]


class _EventExpiredError(Exception):
    """Raised when an event reaches its start before opening completes."""


def _next_target(
    now: datetime.datetime, weekday: int, hour: int, minute: int
) -> datetime.datetime:
    """Return the next configured weekday and time that has not elapsed."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = weekday - now.weekday()
    if days_ahead < 0 or (days_ahead == 0 and now >= target):
        days_ahead += 7
    return target + datetime.timedelta(days=days_ahead)


def _weekly_event_after(
    announcement_at: datetime.datetime, config: Config
) -> datetime.datetime:
    """Return the weekly event start following an announcement."""
    return _next_target(
        announcement_at,
        config.event_weekday,
        config.event_hour,
        config.event_minute,
    )


def _announcement_message(event_starts_at: datetime.datetime) -> str:
    return (
        "🏐 Запись на волейбол открыта!\n"
        f"🗓 Начало: {format_event_start(event_starts_at)}\n\n"
        "Кто идёт? Нажми «Записаться» или добавь друга командой + Имя.\n"
        "Запись автоматически закроется в момент начала."
    )


async def _send_announcement(
    api: API,
    config: Config,
    inline_keyboard: str,
    event_starts_at: datetime.datetime | None = None,
    random_id: int | None = None,
) -> int | None:
    """Send an event announcement to the configured chat."""
    resolved_start = event_starts_at or _weekly_event_after(
        datetime.datetime.now(),  # noqa: DTZ005
        config,
    )
    message_id = await api.messages.send(
        peer_id=config.chat_peer_id,
        message=_announcement_message(resolved_start),
        keyboard=inline_keyboard,
        random_id=(
            random_id if random_id is not None else secrets.randbelow(2_147_483_646) + 1
        ),
    )
    return message_id if type(message_id) is int else None


async def _send_reminder(
    api: API, config: Config, inline_keyboard: str, storage: Storage
) -> None:
    """Send a reminder with the current participant list."""
    entries = await storage.list_entries()
    event_starts_at, _ = await storage.event_details()
    event_line = (
        f"\n🗓 Начало: {format_event_start(event_starts_at)}"
        if event_starts_at is not None
        else ""
    )
    message = (
        f"🏐 Напоминаем: сбор на волейбол!{event_line}\n\n{format_entries(entries)}"
    )
    _ = await api.messages.send(
        peer_id=config.chat_peer_id,
        message=message,
        keyboard=inline_keyboard,
        random_id=secrets.randbelow(2_147_483_647),
    )


def _pick_next_event(
    collect_target: datetime.datetime,
    remind_target: datetime.datetime | None,
    close_target: datetime.datetime | None = None,
) -> tuple[datetime.datetime, ScheduleEvent]:
    """Return the nearest scheduled action and its label."""
    candidates: list[tuple[datetime.datetime, ScheduleEvent]] = [
        (collect_target, "collect")
    ]
    if remind_target is not None:
        candidates.append((remind_target, "remind"))
    if close_target is not None:
        candidates.append((close_target, "close"))
    return min(candidates, key=lambda item: item[0])


async def _run_with_retry[T](
    label: str,
    operation: Callable[[], Awaitable[T]],
    *,
    deadline: datetime.datetime | None = None,
) -> T:
    """Run an operation with the scheduler's standard retry loop."""
    while True:
        if deadline is not None and _now_for(deadline) >= deadline:
            raise _EventExpiredError
        try:
            result = await operation()
        except anyio.get_cancelled_exc_class():
            raise
        except (OSError, TimeoutError, VKAPIError):
            _LOGGER.exception("%s failed; retrying in five minutes", label)
            delay = _RETRY_DELAY_SECONDS
            if deadline is not None:
                remaining = (deadline - _now_for(deadline)).total_seconds()
                if remaining <= 0:
                    raise _EventExpiredError from None
                delay = min(delay, remaining)
            await anyio.sleep(delay)
        else:
            _LOGGER.info("%s completed successfully", label)
            return result


def _now_for(reference: datetime.datetime) -> datetime.datetime:
    """Return current time with the same timezone awareness as *reference*."""
    return datetime.datetime.now(tz=reference.tzinfo)


async def _finish_opening(api: API, config: Config, storage: Storage) -> bool:
    """Resume delivery and durable activation of an interrupted event."""
    event_starts_at, event_source = await storage.event_details()
    if event_starts_at is None:
        event_starts_at = _weekly_event_after(datetime.datetime.now(), config)  # noqa: DTZ005
        event_source = "weekly"
        await storage.mark_opening(event_starts_at, event_source)
    if await storage.announcement_random_id() is None:
        await storage.mark_opening(event_starts_at, event_source or "weekly")
    random_id = await storage.announcement_random_id()
    message_id: int | None = None
    try:
        message_id = await _run_with_retry(
            "Weekly announcement",
            partial(
                _send_announcement,
                api,
                config,
                build_inline_keyboard(),
                event_starts_at,
                random_id,
            ),
            deadline=event_starts_at,
        )
        await _run_with_retry(
            "Registration activation",
            partial(storage.activate_event, message_id, event_starts_at),
            deadline=event_starts_at,
        )
    except _EventExpiredError:
        if message_id is not None:
            await storage.set_status_message_id(message_id)
        await _close_event(api, config, storage, build_inline_keyboard())
        return False
    return True


async def _refresh_status_card(
    api: API, config: Config, storage: Storage, inline_keyboard: str
) -> None:
    """Edit the canonical card after automatic closure when possible."""
    message_id = await storage.status_message_id()
    conversation_message_id = await storage.status_conversation_message_id()
    if not message_id and not conversation_message_id:
        return
    entries = await storage.list_entries()
    state = await storage.registration_state()
    event_starts_at, _ = await storage.event_details()
    try:
        _ = await api.messages.edit(
            peer_id=config.chat_peer_id,
            message_id=message_id if not conversation_message_id else None,
            conversation_message_id=conversation_message_id,
            message=format_registration_card(entries, state, event_starts_at),
            keyboard=inline_keyboard,
        )
    except (OSError, TimeoutError, VKAPIError):
        _LOGGER.exception("Could not refresh registration card after closure")


async def _close_event(
    api: API, config: Config, storage: Storage, inline_keyboard: str
) -> None:
    """Close registration and update the existing card without chat spam."""
    if await storage.close_registration():
        _LOGGER.info("Registration closed at event start")
        await _refresh_status_card(api, config, storage, inline_keyboard)


async def open_manual_event(
    api: API,
    config: Config,
    storage: Storage,
    event_starts_at: datetime.datetime,
    *,
    now: datetime.datetime | None = None,
) -> None:
    """Open an administrator-created event after overlap checks."""
    current = now or datetime.datetime.now()  # noqa: DTZ005
    if event_starts_at <= current:
        message = "Время начала события должно быть в будущем."
        raise ValueError(message)
    next_weekly_announcement = _next_target(
        current,
        config.collect_weekday,
        config.collect_hour,
        config.collect_minute,
    )
    if event_starts_at >= next_weekly_announcement:
        message = (
            "Событие пересекается со следующим еженедельным анонсом "
            f"{next_weekly_announcement:%d.%m.%Y %H:%M}."
        )
        raise ValueError(message)
    if not await storage.begin_event(event_starts_at, "manual"):
        message = "Уже есть активное событие с открытой записью."
        raise ValueError(message)
    if not await _finish_opening(api, config, storage):
        message = "Событие уже началось: запись осталась закрытой."
        raise ValueError(message)


async def run_scheduler(api: API, config: Config, storage: Storage) -> NoReturn:
    """Run weekly announcements, reminders, and automatic closures forever."""
    inline_keyboard = build_inline_keyboard()
    _LOGGER.info(
        "Scheduler started: announcement=%d %s, event=%d %s",
        config.collect_weekday,
        config.collect_time,
        config.event_weekday,
        config.event_time,
    )

    if await storage.registration_state() == "opening":
        event_starts_at, _ = await storage.event_details()
        now = datetime.datetime.now()  # noqa: DTZ005
        if event_starts_at is not None and event_starts_at <= now:
            _LOGGER.info("Closing an interrupted event whose start has elapsed")
            await _close_event(api, config, storage, inline_keyboard)
        else:
            _LOGGER.info("Resuming interrupted event announcement")
            _ = await _finish_opening(api, config, storage)

    while True:
        now = datetime.datetime.now()  # noqa: DTZ005
        state = await storage.registration_state()
        event_starts_at, _ = await storage.event_details()
        if (
            state in {"opening", "open"}
            and event_starts_at is not None
            and event_starts_at <= now
        ):
            await _close_event(api, config, storage, inline_keyboard)
            continue

        collect_target = _next_target(
            now,
            config.collect_weekday,
            config.collect_hour,
            config.collect_minute,
        )
        remind_target = (
            _next_target(
                now,
                config.remind_weekday,
                config.remind_hour,
                config.remind_minute,
            )
            if config.remind_enabled and state == "open"
            else None
        )
        close_target = (
            event_starts_at
            if state in {"opening", "open"}
            and event_starts_at is not None
            and event_starts_at > now
            else None
        )
        target, action = _pick_next_event(collect_target, remind_target, close_target)
        await anyio.sleep((target - now).total_seconds())

        if action == "collect":
            event_start = _weekly_event_after(target, config)
            if await storage.begin_event(event_start, "weekly"):
                _ = await _finish_opening(api, config, storage)
            else:
                _LOGGER.warning("Weekly announcement skipped: another event is active")
        elif action == "close":
            await _close_event(api, config, storage, inline_keyboard)
        else:
            await _run_with_retry(
                "Weekly reminder",
                partial(_send_reminder, api, config, inline_keyboard, storage),
            )
