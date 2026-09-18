"""Integration tests for functions registered with VKBottle."""

from __future__ import annotations

import datetime
import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.bot import setup_handlers
from src.cards import CardPublisher, MessageRef
from src.config import Config
from src.storage import Storage
from vkbottle import GroupEventType
from vkbottle.bot import Bot, Message, MessageEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    type _MessageHandler = Callable[[Message], Awaitable[None]]
    type _CallbackHandler = Callable[[MessageEvent], Awaitable[None]]


def _publisher() -> MagicMock:
    publisher = MagicMock(spec=CardPublisher)
    ref = MessageRef(message_id=10, conversation_message_id=20)
    publisher.replace_current = AsyncMock(return_value=ref)
    publisher.edit_current = AsyncMock(return_value=ref)
    publisher.diagnostic_notice = AsyncMock(return_value="Состояние: open")
    return publisher


def _build_bot(storage: Storage, config: Config) -> tuple[Bot, MagicMock]:
    bot = Bot(config.vk_token)
    api = MagicMock()
    api.users.get = AsyncMock(
        return_value=[SimpleNamespace(first_name="Alice")],
    )
    bot.api = api
    publisher = _publisher()
    setup_handlers(bot, storage, config, publisher)
    return bot, publisher


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


def _message(config: Config, *, text: str = "", user_id: int = 1) -> MagicMock:
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = user_id
    message.text = text
    message.answer = AsyncMock()
    return message


def _event(config: Config, *, user_id: int = 1) -> MagicMock:
    event = MagicMock(spec=MessageEvent)
    event.peer_id = config.chat_peer_id
    event.user_id = user_id
    event.show_snackbar = AsyncMock()
    return event


@pytest.mark.anyio
async def test_text_signup_updates_storage_and_moves_card_last(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(None)
    bot, publisher = _build_bot(storage, config)
    message = _message(config, text="записаться")

    await _message_handlers(bot)["sign_up"](message)

    assert [entry.name for entry in await storage.list_entries()] == ["Alice"]
    publisher.replace_current.assert_awaited_once_with(notice=None)
    message.answer.assert_not_awaited()


@pytest.mark.anyio
async def test_text_failure_is_shown_in_republished_card(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    bot, publisher = _build_bot(storage, config)

    await _message_handlers(bot)["sign_up"](_message(config, text="записаться"))

    publisher.replace_current.assert_awaited_once_with(
        notice="Запись закрыта. Дождись следующего анонса или нажми «Помощь»."
    )
    bot.api.users.get.assert_not_awaited()


@pytest.mark.anyio
async def test_signup_rejects_collection_changed_during_vk_lookup(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(1)
    bot, publisher = _build_bot(storage, config)

    async def change_collection(**_kwargs: object) -> list[SimpleNamespace]:
        await storage.mark_opening()
        await storage.start_new_collection(2)
        return [SimpleNamespace(first_name="Alice")]

    bot.api.users.get = AsyncMock(side_effect=change_collection)
    await _message_handlers(bot)["sign_up"](_message(config, text="записаться"))

    assert await storage.list_entries() == []
    notice = publisher.replace_current.await_args.kwargs["notice"]
    assert "сбор изменился" in notice


@pytest.mark.anyio
async def test_friend_commands_and_views_keep_one_card(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(None)
    bot, publisher = _build_bot(storage, config)
    handlers = _message_handlers(bot)

    await handlers["add_friend"](_message(config, text="+ Bob"))
    await handlers["show_list"](_message(config, text="список"))
    await handlers["help_cmd"](_message(config, text="помощь"))
    await handlers["remove_friend"](_message(config, text="- Bob"))

    assert await storage.list_entries() == []
    assert publisher.replace_current.await_count == 4
    assert publisher.replace_current.await_args.kwargs == {"notice": None}


@pytest.mark.anyio
async def test_callback_updates_card_and_uses_snackbar(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(None)
    bot, publisher = _build_bot(storage, config)
    handlers = _callback_handlers(bot)
    event = _event(config)

    await handlers["cb_join"](event)
    await handlers["cb_list"](event)
    await handlers["cb_help"](event)
    await handlers["cb_leave"](event)

    assert await storage.list_entries() == []
    assert publisher.edit_current.await_count == 3
    assert event.show_snackbar.await_count == 4


@pytest.mark.anyio
async def test_duplicate_callback_does_not_edit_unchanged_card(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(None)
    bot, publisher = _build_bot(storage, config)
    handler = _callback_handlers(bot)["cb_join"]
    event = _event(config)

    await handler(event)
    await handler(event)

    publisher.edit_current.assert_awaited_once_with()
    assert event.show_snackbar.await_args_list[-1].args == ("Ты уже в списке.",)


@pytest.mark.anyio
async def test_admin_creates_manual_event_with_shared_publisher(
    tmp_path: Path,
) -> None:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    bot, publisher = _build_bot(storage, config)
    message = _message(
        config,
        text="создать событие 22.09.2099 19:30",
        user_id=123,
    )

    with patch("src.bot.open_manual_event", new_callable=AsyncMock) as open_event:
        await _message_handlers(bot)["admin_create_event"](message)

    open_event.assert_awaited_once_with(
        bot.api,
        config,
        storage,
        datetime.datetime(2099, 9, 22, 19, 30),  # noqa: DTZ001
        cards=publisher,
    )
    publisher.replace_current.assert_not_awaited()


@pytest.mark.anyio
async def test_admin_status_is_rendered_in_card(tmp_path: Path) -> None:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    bot, publisher = _build_bot(storage, config)

    await _message_handlers(bot)["admin_event_status"](
        _message(config, text="статус события", user_id=123)
    )

    publisher.diagnostic_notice.assert_awaited_once_with()
    publisher.replace_current.assert_awaited_once_with(notice="Состояние: open")


@pytest.mark.anyio
async def test_admin_commands_reject_non_admin_without_mutation(
    tmp_path: Path,
) -> None:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_friend("Bob")
    bot, publisher = _build_bot(storage, config)

    await _message_handlers(bot)["admin_clear"](
        _message(config, text="очистить", user_id=999)
    )

    assert [entry.name for entry in await storage.list_entries()] == ["Bob"]
    publisher.replace_current.assert_awaited_once_with(
        notice="Только администраторы могут использовать эту команду."
    )


@pytest.mark.anyio
async def test_handlers_ignore_other_peer_before_any_action(tmp_path: Path) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    bot, publisher = _build_bot(storage, config)
    message = _message(config, text="записаться")
    message.peer_id += 1
    event = _event(config)
    event.peer_id += 1

    await _message_handlers(bot)["sign_up"](message)
    await _callback_handlers(bot)["cb_join"](event)

    assert await storage.list_entries() == []
    bot.api.users.get.assert_not_awaited()
    publisher.replace_current.assert_not_awaited()
    publisher.edit_current.assert_not_awaited()
    event.show_snackbar.assert_not_awaited()
