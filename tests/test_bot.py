"""Unit tests for bot presentation helpers."""

from __future__ import annotations

import json
import secrets
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.bot import (
    _admin_help_text,
    build_inline_keyboard,
    extract_friend_name,
    format_entries,
    help_text,
    setup_handlers,
)
from src.config import Config
from src.storage import FriendEntry, Storage, UserEntry
from vkbottle import GroupEventType
from vkbottle.bot import Bot, Message, MessageEvent


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], "Пока никто не записался."),
        (
            [UserEntry(kind="user", vk_id=1, name="Алексей")],
            "Список участников:\n1. Алексей",
        ),
        (
            [FriendEntry(kind="friend", name="Андрей")],
            "Список участников:\n1. Андрей (друг)",
        ),
        (
            [
                UserEntry(kind="user", vk_id=1, name="Алексей"),
                FriendEntry(kind="friend", name="Андрей"),
            ],
            "Список участников:\n1. Алексей\n2. Андрей (друг)",
        ),
    ],
)
def test_format_entries_when_entries_vary(
    entries: list[UserEntry | FriendEntry], expected: str
) -> None:
    # Given: stored participant entries of each supported kind.
    # When: the list presentation is built.
    actual = format_entries(entries)

    # Then: the user-visible list text matches the existing contract.
    assert actual == expected


def test_help_text_when_requested() -> None:
    # Given: no external dependencies.
    # When: the help presentation is built.
    text = help_text()

    # Then: it gives lifecycle-aware guidance without treating bare + as signup.
    signup_line = next(line for line in text.splitlines() if "Для себя" in line)
    assert "+" not in signup_line
    assert "записаться" in signup_line
    assert "после анонса" in text
    assert "+ Имя" in text
    assert "пока запись открыта" in text
    assert "не добавит вас" in text
    assert "отписаться" in text
    assert "- Имя" in text
    assert "список" in text
    assert "помощь" in text
    assert help_text(compact=True) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+ Name", "Name"),
        ("+   Name  ", "Name"),
        ("- Name", "Name"),
        ("-   Name  ", "Name"),
        ("+", ""),
    ],
)
def test_extract_friend_name_when_command_contains_sign(
    text: str, expected: str
) -> None:
    # Given: a friend command with a plus or minus sign.
    # When: the name is extracted.
    actual = extract_friend_name(text)

    # Then: whitespace and the command sign are excluded.
    assert actual == expected


def test_build_inline_keyboard_when_serialized() -> None:
    # Given: no external dependencies.
    # When: the inline keyboard is built.
    keyboard = json.loads(build_inline_keyboard())

    # Then: VK receives readable labels, stable commands, and neutral join controls.
    buttons = [button for row in keyboard["buttons"] for button in row]
    actions = [button["action"] for button in buttons]
    assert [(action["label"], action["payload"]["cmd"]) for action in actions] == [
        ("✅ Записаться", "join"),
        ("↩️ Отписаться", "leave"),
        ("📋 Список", "list"),
        ("❓ Помощь", "help"),
    ]
    assert [button.get("color") for button in buttons] == [
        "secondary",
        "secondary",
        None,
        None,
    ]


def test_admin_help_text_contains_commands() -> None:
    """Admin help lists every documented admin command."""
    text = _admin_help_text()
    assert "очистить" in text
    assert "сбросить" in text
    assert "убрать" in text
    assert "удалить" in text


@pytest.mark.anyio
async def test_message_handlers_ignore_events_from_other_peers() -> None:
    # Given: every registered message handler receives an event from another peer.
    peer_id = 2_000_000_001
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=peer_id,
        admin_vk_ids=(123,),
    )
    storage = MagicMock(spec=Storage)
    bot = Bot(config.vk_token)
    api = MagicMock()
    api.users.get = AsyncMock(return_value=[])
    bot.api = api
    setup_handlers(bot, storage, config)
    message = MagicMock(spec=Message)
    message.peer_id = peer_id + 1
    message.from_id = 123
    message.text = "+ OutsidePeer"
    message.answer = AsyncMock()

    # When: handlers are called through the functions registered with VKBottle.
    for registered in bot.labeler.message_view.handlers:
        await registered.handler(message)

    # Then: no data, VK API, or response surface is touched.
    assert storage.mock_calls == []
    api.users.get.assert_not_awaited()
    message.answer.assert_not_awaited()


@pytest.mark.anyio
async def test_callback_handlers_ignore_events_from_other_peers() -> None:
    # Given: every callback handler receives an event from another peer.
    peer_id = 2_000_000_001
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=peer_id,
    )
    storage = MagicMock(spec=Storage)
    bot = Bot(config.vk_token)
    api = MagicMock()
    api.users.get = AsyncMock(return_value=[])
    bot.api = api
    setup_handlers(bot, storage, config)
    event = MagicMock(spec=MessageEvent)
    event.peer_id = peer_id + 1
    event.user_id = 123
    event.show_snackbar = AsyncMock()
    event.send_message = AsyncMock()

    # When: handlers are called through the functions registered with VKBottle.
    callbacks = bot.labeler.raw_event_view.handlers[GroupEventType.MESSAGE_EVENT]
    for registered in callbacks:
        await registered.handler.handler(event)

    # Then: no data, VK API, or response surface is touched.
    assert storage.mock_calls == []
    api.users.get.assert_not_awaited()
    event.show_snackbar.assert_not_awaited()
    event.send_message.assert_not_awaited()
