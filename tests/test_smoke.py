"""Import and entry-point smoke tests."""

from __future__ import annotations

import importlib
import secrets
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from src import main as main_module


@pytest.mark.parametrize(
    "module_name",
    ["src.bot", "src.config", "src.main", "src.scheduler", "src.storage"],
)
def test_module_imports_without_error(module_name: str) -> None:
    """Given an application module, importing it succeeds."""
    # Given: an importable application module name.
    # When / Then: its import completes without raising.
    importlib.import_module(module_name)


@pytest.mark.anyio
async def test_main_starts_bot_and_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given mocked dependencies, main starts both long-running tasks."""
    # Given: constructors and services that do not touch VK or the filesystem.
    config = MagicMock(vk_token=secrets.token_urlsafe(), data_path="participants.json")
    config.path = Path(config.data_path)
    storage = MagicMock()
    api = MagicMock()
    bot = MagicMock(api=api)
    bot.run_polling = AsyncMock()
    config_factory = MagicMock(return_value=config)
    storage_factory = MagicMock(return_value=storage)
    bot_factory = MagicMock(return_value=bot)
    cards = MagicMock()
    card_publisher_factory = MagicMock(return_value=cards)
    setup_handlers = MagicMock()
    run_scheduler = AsyncMock()
    monkeypatch.setattr(main_module, "Config", config_factory)
    monkeypatch.setattr(main_module, "Storage", storage_factory)
    monkeypatch.setattr(main_module, "Bot", bot_factory)
    monkeypatch.setattr(main_module, "CardPublisher", card_publisher_factory)
    monkeypatch.setattr(main_module, "setup_handlers", setup_handlers)
    monkeypatch.setattr(main_module, "run_scheduler", run_scheduler)

    # When: the entry point runs its task group.
    await main_module.main()

    # Then: dependencies are wired into the scheduler, handlers, and polling loop.
    config_factory.assert_called_once_with()
    storage_factory.assert_called_once_with(Path(config.data_path))
    bot_factory.assert_called_once_with(config.vk_token)
    card_publisher_factory.assert_called_once_with(api, config, storage)
    setup_handlers.assert_called_once_with(bot, storage, config, cards)
    run_scheduler.assert_awaited_once_with(api, config, storage, cards)
    bot.run_polling.assert_awaited_once_with()
