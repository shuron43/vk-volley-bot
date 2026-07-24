"""Scheduler: weekly event announcement and list reset."""

import datetime
import logging
import secrets
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Final, NoReturn

import anyio
from vkbottle import VKAPIError
from vkbottle.api import API

from src.config import Config
from src.formatting import format_entries
from src.keyboard import build_inline_keyboard
from src.storage import Storage

_RETRY_DELAY_SECONDS: Final = 5 * 60
_ANNOUNCEMENT_MESSAGE: Final = (
    "🏐 Сбор на волейбол!\n"
    "Кто идёт? Напиши + или имя друга через +\n"
    "список — посмотреть участников"
)
_LOGGER: Final = logging.getLogger(__name__)


def _next_target(
    now: datetime.datetime, weekday: int, hour: int, minute: int
) -> datetime.datetime:
    """Return the next configured weekday and time that has not elapsed."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = weekday - now.weekday()
    if days_ahead < 0 or (days_ahead == 0 and now >= target):
        days_ahead += 7
    return target + datetime.timedelta(days=days_ahead)


async def _send_announcement(api: API, config: Config, inline_keyboard: str) -> None:
    """Send the weekly collection announcement to the configured chat."""
    _ = await api.messages.send(
        peer_id=config.chat_peer_id,
        message=_ANNOUNCEMENT_MESSAGE,
        keyboard=inline_keyboard,
        random_id=secrets.randbelow(2_147_483_647),
    )


async def _send_reminder(
    api: API, config: Config, inline_keyboard: str, storage: Storage
) -> None:
    """Send the weekly reminder with current participant list."""
    entries = await storage.list_entries()
    body = format_entries(entries)
    message = f"🏐 Напоминаем: сбор на волейбол!\n\n{body}"
    _ = await api.messages.send(
        peer_id=config.chat_peer_id,
        message=message,
        keyboard=inline_keyboard,
        random_id=secrets.randbelow(2_147_483_647),
    )


def _pick_next_event(
    collect_target: datetime.datetime,
    remind_target: datetime.datetime | None,
) -> tuple[datetime.datetime, str]:
    """Return the nearer target and its label ('collect' or 'remind')."""
    if remind_target is None:
        return collect_target, "collect"
    if collect_target <= remind_target:
        return collect_target, "collect"
    return remind_target, "remind"


async def _run_with_retry(
    label: str,
    operation: Callable[[], Awaitable[None]],
) -> None:
    """Run an operation with the scheduler's standard retry loop."""
    while True:
        try:
            await operation()
            _LOGGER.info("%s sent successfully", label)
        except anyio.get_cancelled_exc_class():
            raise
        except (OSError, TimeoutError, VKAPIError):
            _LOGGER.exception(
                "%s failed; retrying in five minutes",
                label,
            )
            await anyio.sleep(_RETRY_DELAY_SECONDS)
        else:
            break


async def run_scheduler(api: API, config: Config, storage: Storage) -> NoReturn:
    """Loop forever, waiting for the configured weekday/time."""
    weekday = config.collect_weekday
    hour = config.collect_hour
    minute = config.collect_minute

    inline_keyboard = build_inline_keyboard()

    while True:
        now = datetime.datetime.now()  # noqa: DTZ005
        collect_target = _next_target(now, weekday, hour, minute)

        if config.remind_enabled:
            remind_target = _next_target(
                now,
                config.remind_weekday,
                config.remind_hour,
                config.remind_minute,
            )
        else:
            remind_target = None

        target, event = _pick_next_event(collect_target, remind_target)
        sleep_seconds = (target - now).total_seconds()
        _LOGGER.info(
            "Next event '%s' at %s (sleep %.0f seconds)",
            event,
            target.isoformat(),
            sleep_seconds,
        )
        await anyio.sleep(sleep_seconds)

        if event == "collect":
            await storage.clear()
            await _run_with_retry(
                "Weekly announcement",
                partial(_send_announcement, api, config, inline_keyboard),
            )
        else:
            await _run_with_retry(
                "Weekly reminder",
                partial(_send_reminder, api, config, inline_keyboard, storage),
            )
