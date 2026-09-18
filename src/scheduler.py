"""Scheduler for weekly and administrator-created events."""

import datetime
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Final, Literal, NoReturn

import anyio
from vkbottle import VKAPIError
from vkbottle.api import API

from src.cards import CardPublisher, MessageRef
from src.config import Config
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


async def _send_reminder(
    cards: CardPublisher,
) -> None:
    """Move the single card last and mark it as a reminder."""
    _ = await cards.replace_current(
        notice="Напоминание: проверьте список участников перед тренировкой."
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


async def _finish_opening(
    api: API,
    config: Config,
    storage: Storage,
    cards: CardPublisher | None = None,
) -> bool:
    """Resume delivery and durable activation of an interrupted event."""
    publisher = cards or CardPublisher(api, config, storage)
    event_starts_at, event_source = await storage.event_details()
    if event_starts_at is None:
        event_starts_at = _weekly_event_after(datetime.datetime.now(), config)  # noqa: DTZ005
        event_source = "weekly"
        await storage.mark_opening(event_starts_at, event_source)
    if await storage.announcement_random_id() is None:
        await storage.mark_opening(event_starts_at, event_source or "weekly")
    random_id = await storage.announcement_random_id()
    if random_id is None:
        message = "Pending event has no announcement_random_id"
        raise RuntimeError(message)
    _LOGGER.info(
        "Publishing event card: source=%s start=%s random_id=%s",
        event_source,
        event_starts_at,
        random_id,
    )

    async def activate(ref: MessageRef) -> None:
        if _now_for(event_starts_at) >= event_starts_at:
            _LOGGER.warning(
                "Rejecting late event activation: start=%s message_id=%s cmid=%s",
                event_starts_at,
                ref.message_id,
                ref.conversation_message_id,
            )
            raise _EventExpiredError
        await storage.activate_event(
            ref.message_id,
            event_starts_at,
            conversation_message_id=ref.conversation_message_id,
        )

    try:
        _ = await _run_with_retry(
            "Event card publication",
            partial(publisher.publish_event, event_starts_at, random_id, activate),
            deadline=event_starts_at,
        )
    except _EventExpiredError:
        await _close_event(storage, publisher)
        return False
    return True


async def _close_event(
    storage: Storage,
    cards: CardPublisher,
) -> None:
    """Close registration and update the existing card without chat spam."""
    if await storage.close_registration():
        event_starts_at, source = await storage.event_details()
        _LOGGER.info(
            "Registration transition open/opening -> closed: start=%s source=%s",
            event_starts_at,
            source,
        )
        _ = await cards.edit_current()


async def open_manual_event(  # noqa: PLR0913
    api: API,
    config: Config,
    storage: Storage,
    event_starts_at: datetime.datetime,
    *,
    cards: CardPublisher | None = None,
    now: datetime.datetime | None = None,
) -> None:
    """Open an administrator-created event after overlap checks."""
    current = now or datetime.datetime.now()  # noqa: DTZ005
    if event_starts_at <= current:
        _LOGGER.warning(
            "Manual event rejected because start elapsed: start=%s now=%s",
            event_starts_at,
            current,
        )
        message = "Время начала события должно быть в будущем."
        raise ValueError(message)
    next_weekly_announcement = _next_target(
        current,
        config.collect_weekday,
        config.collect_hour,
        config.collect_minute,
    )
    if event_starts_at >= next_weekly_announcement:
        _LOGGER.warning(
            "Manual event rejected: start=%s next_announcement=%s",
            event_starts_at,
            next_weekly_announcement,
        )
        message = (
            "Событие пересекается со следующим еженедельным анонсом "
            f"{next_weekly_announcement:%d.%m.%Y %H:%M}."
        )
        raise ValueError(message)
    if not await storage.begin_event(event_starts_at, "manual"):
        _LOGGER.warning("Manual event rejected because another event is active")
        message = "Уже есть активное событие с открытой записью."
        raise ValueError(message)
    _LOGGER.info(
        "Manual event transition closed -> opening: start=%s",
        event_starts_at,
    )
    if not await _finish_opening(api, config, storage, cards):
        message = "Событие уже началось: запись осталась закрытой."
        raise ValueError(message)


async def run_scheduler(
    api: API,
    config: Config,
    storage: Storage,
    cards: CardPublisher | None = None,
) -> NoReturn:
    """Run weekly announcements, reminders, and automatic closures forever."""
    publisher = cards or CardPublisher(api, config, storage)
    _LOGGER.info(
        "Scheduler started: announcement=%d %s, event=%d %s",
        config.collect_weekday,
        config.collect_time,
        config.event_weekday,
        config.event_time,
    )

    if await storage.close_missing_deadline():
        _LOGGER.warning("Recovered active registration without a deadline")
        _ = await publisher.edit_current(
            notice="Предыдущее событие закрыто: время начала не было сохранено."
        )

    if await storage.registration_state() == "opening":
        event_starts_at, _ = await storage.event_details()
        now = datetime.datetime.now()  # noqa: DTZ005
        if event_starts_at is not None and event_starts_at <= now:
            _LOGGER.info("Closing an interrupted event whose start has elapsed")
            await _close_event(storage, publisher)
        else:
            _LOGGER.info("Resuming interrupted event announcement")
            _ = await _finish_opening(api, config, storage, publisher)

    while True:
        now = datetime.datetime.now()  # noqa: DTZ005
        state = await storage.registration_state()
        event_starts_at, _ = await storage.event_details()
        if (
            state in {"opening", "open"}
            and event_starts_at is not None
            and event_starts_at <= now
        ):
            await _close_event(storage, publisher)
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
        _LOGGER.info(
            "Next scheduler action=%s target=%s state=%s start=%s",
            action,
            target.isoformat(),
            state,
            event_starts_at,
        )
        await anyio.sleep((target - now).total_seconds())

        if action == "collect":
            event_start = _weekly_event_after(target, config)
            if await storage.begin_event(event_start, "weekly"):
                _LOGGER.info(
                    "Weekly opening: announcement=%s start=%s",
                    target,
                    event_start,
                )
                _ = await _finish_opening(api, config, storage, publisher)
            else:
                _LOGGER.warning("Weekly announcement skipped: another event is active")
        elif action == "close":
            await _close_event(storage, publisher)
        else:
            await _run_with_retry(
                "Weekly reminder",
                partial(_send_reminder, publisher),
            )
