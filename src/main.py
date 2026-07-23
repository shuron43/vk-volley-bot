"""Entry point for the volleyball VK bot."""

import logging

import anyio
from vkbottle.bot import Bot

from src.bot import setup_handlers
from src.config import Config
from src.scheduler import run_scheduler
from src.storage import Storage

_LOGGER = logging.getLogger(__name__)


async def main() -> None:
    """Initialize and run the bot together with the weekly scheduler."""
    config = Config()
    _LOGGER.info("Config loaded: peer_id=%s", config.chat_peer_id)
    storage = Storage(config.path)
    _LOGGER.info("Storage initialized at %s", config.data_path)
    bot = Bot(config.vk_token)

    setup_handlers(bot, storage, config)
    _LOGGER.info("Bot handlers registered")

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_scheduler, bot.api, config, storage)
        tg.start_soon(bot.run_polling)


if __name__ == "__main__":
    anyio.run(main)
