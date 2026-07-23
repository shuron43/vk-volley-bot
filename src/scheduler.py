"""Scheduler: weekly event announcement and list reset."""

import datetime
import logging
import secrets
from typing import Final, NoReturn

import anyio
from vkbottle import VKAPIError
from vkbottle.api import API

from src.config import Config
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


async def _send_announcement(
    api: API, config: Config, inline_keyboard: str
) -> None:
    """Send the weekly collection announcement to the configured chat."""
    _ = await api.messages.send(
        peer_id=config.chat_peer_id,
        message=_ANNOUNCEMENT_MESSAGE,
        keyboard=inline_keyboard,
        random_id=secrets.randbelow(2_147_483_647),
    )


async def run_scheduler(api: API, config: Config, storage: Storage) -> NoReturn:
    """Loop forever, waiting for the configured weekday/time."""
    weekday = config.collect_weekday
    hour = config.collect_hour
    minute = config.collect_minute

    inline_keyboard = build_inline_keyboard()

    while True:
        now = datetime.datetime.now()  # noqa: DTZ005
        target = _next_target(now, weekday, hour, minute)
        sleep_seconds = (target - now).total_seconds()
        _LOGGER.info(
            "Next announcement at %s (sleep %.0f seconds)",
            target.isoformat(),
            sleep_seconds,
        )
        await anyio.sleep(sleep_seconds)

        while True:
            try:
                await storage.clear()
                await _send_announcement(api, config, inline_keyboard)
                _LOGGER.info("Weekly announcement sent to peer %s", config.chat_peer_id)
            except anyio.get_cancelled_exc_class():
                raise
            except (OSError, TimeoutError, VKAPIError):
                _LOGGER.exception(
                    "Scheduled announcement failed; retrying in five minutes"
                )
                await anyio.sleep(_RETRY_DELAY_SECONDS)
            else:
                break
