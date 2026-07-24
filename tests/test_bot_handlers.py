"""Integration tests for functions registered with VKBottle."""

from __future__ import annotations

import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.bot import setup_handlers
from src.config import Config
from src.storage import Storage
from vkbottle import GroupEventType
from vkbottle.bot import Bot, Message, MessageEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    type _MessageHandler = Callable[[Message], Awaitable[None]]
    type _CallbackHandler = Callable[[MessageEvent], Awaitable[None]]


def _build_bot(storage: Storage, config: Config) -> Bot:
    bot = Bot(config.vk_token)
    api = MagicMock()
    api.users.get = AsyncMock(
        return_value=[SimpleNamespace(first_name="Alice")],
    )
    bot.api = api
    setup_handlers(bot, storage, config)
    return bot


def _message_handlers(bot: Bot) -> dict[str, _MessageHandler]:
    return {
        registered.handler.__name__: registered.handler
        for registered in bot.labeler.message_view.handlers
    }


def _callback_handlers(bot: Bot) -> dict[str, _CallbackHandler]:
    registered_handlers = bot.labeler.raw_event_view.handlers[
        GroupEventType.MESSAGE_EVENT
    ]
    return {
        registered.handler.handler.__name__: registered.handler.handler
        for registered in registered_handlers
    }


@pytest.mark.anyio
async def test_registered_participant_message_handlers_update_storage(
    tmp_path: Path,
) -> None:
    # Given: the registered handlers, a real store, and an in-scope message
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    handlers = _message_handlers(_build_bot(storage, config))
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.answer = AsyncMock()

    # When: a participant uses every regular text command
    await handlers["sign_up"](message)
    await handlers["sign_up"](message)
    message.text = "+ Bob"
    await handlers["add_friend"](message)
    message.text = "список"
    await handlers["show_list"](message)
    message.text = "помощь"
    await handlers["help_cmd"](message)
    message.text = "- Bob"
    await handlers["remove_friend"](message)
    await handlers["sign_off"](message)

    # Then: duplicate signup is idempotent and the final list is empty
    assert await storage.list_entries() == []
    assert message.answer.await_count == 7


@pytest.mark.anyio
async def test_registered_admin_handlers_enforce_membership_and_mutate_storage(
    tmp_path: Path,
) -> None:
    # Given: a populated store with one configured administrator
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(1, "Alice")
    await storage.add_friend("Bob")
    handlers = _message_handlers(_build_bot(storage, config))
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.answer = AsyncMock()

    # When: a non-admin tries all commands, then an admin performs them
    message.from_id = 999
    message.text = "очистить"
    await handlers["admin_clear"](message)
    message.text = "удалить Alice"
    await handlers["admin_remove"](message)
    message.text = "админ помощь"
    await handlers["admin_help"](message)
    assert [entry.name for entry in await storage.list_entries()] == ["Alice", "Bob"]

    message.from_id = 123
    message.text = "удалить Alice"
    await handlers["admin_remove"](message)
    message.text = "админ помощь"
    await handlers["admin_help"](message)
    message.text = "очистить"
    await handlers["admin_clear"](message)

    # Then: only authorized operations change participant state
    assert await storage.list_entries() == []
    assert message.answer.await_count == 6


@pytest.mark.anyio
async def test_registered_callback_handlers_drive_participant_flow(
    tmp_path: Path,
) -> None:
    # Given: the four registered callbacks and an in-scope event
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    bot = _build_bot(storage, config)
    handlers = _callback_handlers(bot)
    event = MagicMock(spec=MessageEvent)
    event.peer_id = config.chat_peer_id
    event.user_id = 1
    event.show_snackbar = AsyncMock()
    event.send_message = AsyncMock()

    # When: the participant joins twice, reads both views, and leaves twice
    await handlers["cb_join"](event)
    await handlers["cb_join"](event)
    await handlers["cb_list"](event)
    await handlers["cb_help"](event)
    await handlers["cb_leave"](event)
    await handlers["cb_leave"](event)

    # Then: callback state transitions are idempotent and responses are emitted
    assert await storage.list_entries() == []
    assert bot.api.users.get.await_count == 2
    assert event.show_snackbar.await_count == 4
    assert event.send_message.await_count == 2
