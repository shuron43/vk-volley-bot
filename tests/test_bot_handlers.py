"""Integration tests for functions registered with VKBottle."""

from __future__ import annotations

import asyncio
import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest
from src.bot import setup_handlers
from src.config import Config
from src.storage import Storage
from vkbottle import GroupEventType, VKAPIError
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
    api.messages.edit = AsyncMock()
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
async def test_signup_rejects_collection_changed_during_vk_lookup(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(1)
    bot = _build_bot(storage, config)

    async def change_collection(**_kwargs: object) -> list[SimpleNamespace]:
        await storage.mark_opening()
        await storage.start_new_collection(2)
        return [SimpleNamespace(first_name="Alice")]

    bot.api.users.get = AsyncMock(side_effect=change_collection)
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.answer = AsyncMock()
    await _message_handlers(bot)["sign_up"](message)
    assert await storage.list_entries() == []
    assert "сбор изменился" in message.answer.await_args.args[0]
    bot.api.messages.edit.assert_not_awaited()


@pytest.mark.anyio
async def test_all_text_mutations_refresh_status(tmp_path: Path) -> None:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(1,),
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(321)
    bot = _build_bot(storage, config)
    handlers = _message_handlers(bot)
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.answer = AsyncMock()
    for handler, text in [
        ("sign_up", "записаться"),
        ("add_friend", "+ Bob"),
        ("remove_friend", "- Bob"),
        ("sign_off", "отписаться"),
        ("add_friend", "+ Bob"),
        ("admin_remove", "удалить Bob"),
        ("add_friend", "+ Carol"),
        ("admin_clear", "очистить"),
    ]:
        message.text = text
        before = bot.api.messages.edit.await_count
        await handlers[handler](message)
        assert bot.api.messages.edit.await_count == before + 1
        assert bot.api.messages.edit.await_args.kwargs["message_id"] == 321
    assert (
        bot.api.messages.edit.await_args.kwargs["message"] == "Пока никто не записался."
    )


@pytest.mark.anyio
async def test_concurrent_status_updates_publish_latest_list_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(321)
    bot = _build_bot(storage, config)
    first_edit_started = asyncio.Event()
    release_first_edit = asyncio.Event()
    second_saved = asyncio.Event()
    published: list[str] = []
    add_friend = storage.add_friend

    async def observe_add(name: str, *, expected_collection: int | None = None) -> None:
        await add_friend(name, expected_collection=expected_collection)
        if name == "Bob":
            second_saved.set()

    monkeypatch.setattr(storage, "add_friend", observe_add)

    async def edit_message(**kwargs: str | int) -> None:
        if not first_edit_started.is_set():
            first_edit_started.set()
            await release_first_edit.wait()
        published.append(str(kwargs["message"]))

    bot.api.messages.edit = AsyncMock(side_effect=edit_message)
    handler = _message_handlers(bot)["add_friend"]

    def message(name: str) -> MagicMock:
        msg = MagicMock(spec=Message)
        msg.peer_id = config.chat_peer_id
        msg.text = f"+ {name}"
        msg.answer = AsyncMock()
        return msg

    first = asyncio.create_task(handler(message("Alice")))
    await asyncio.wait_for(first_edit_started.wait(), timeout=2)
    second = asyncio.create_task(handler(message("Bob")))
    try:
        # Wait until the second mutation has reached publication, behind the first.
        await asyncio.wait_for(second_saved.wait(), timeout=2)
        assert bot.api.messages.edit.await_count == 1
    finally:
        release_first_edit.set()
        await asyncio.gather(first, second)
    assert "Alice" in published[-1]
    assert "Bob" in published[-1]


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
    await storage.start_new_collection(status_message_id=None)
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
async def test_sign_up_when_registration_is_closed_refuses_before_vk_or_storage(
    tmp_path: Path,
) -> None:
    # Given: a closed collection and an in-scope text registration command
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    bot = _build_bot(storage, config)
    handlers = _message_handlers(bot)
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.answer = AsyncMock()

    # When: the participant requests self-registration before the announcement
    await handlers["sign_up"](message)

    # Then: no external lookup or participant mutation occurs
    bot.api.users.get.assert_not_awaited()
    assert await storage.list_entries() == []
    message.answer.assert_awaited_once_with(
        "Запись ещё не открыта. Дождись анонса сбора или нажми «Помощь».",
        keyboard=ANY,
    )


@pytest.mark.anyio
async def test_callback_join_when_registration_is_closed_refuses_before_vk_or_storage(
    tmp_path: Path,
) -> None:
    # Given: a closed collection and an in-scope join callback
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

    # When: the participant presses join before the announcement
    await handlers["cb_join"](event)

    # Then: no external lookup or participant mutation occurs
    bot.api.users.get.assert_not_awaited()
    assert await storage.list_entries() == []
    event.show_snackbar.assert_awaited_once_with(
        "Запись ещё не открыта. Дождись анонса сбора или нажми «Помощь»."
    )


@pytest.mark.anyio
async def test_add_friend_when_registration_is_closed_refuses_without_mutation(
    tmp_path: Path,
) -> None:
    # Given: a closed collection and an in-scope friend registration command
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    handlers = _message_handlers(_build_bot(storage, config))
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.text = "+ Bob"
    message.answer = AsyncMock()

    # When: the participant tries to register a friend before the announcement
    with patch.object(storage, "add_friend", wraps=storage.add_friend) as add_friend:
        await handlers["add_friend"](message)

    # Then: the friend is not stored or added, and lifecycle guidance is returned
    add_friend.assert_not_awaited()
    assert await storage.list_entries() == []
    message.answer.assert_awaited_once_with(
        "Запись ещё не открыта. Дождись анонса сбора или нажми «Помощь».",
        keyboard=ANY,
    )


@pytest.mark.anyio
async def test_explicit_sign_up_when_registration_is_open_adds_participant(
    tmp_path: Path,
) -> None:
    # Given: an open collection and an in-scope text registration command
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(status_message_id=None)
    bot = _build_bot(storage, config)
    handlers = _message_handlers(bot)
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.text = "записаться"
    message.answer = AsyncMock()

    # When: the participant explicitly requests self-registration after the announcement
    await handlers["sign_up"](message)

    # Then: VK is queried and the participant is recorded
    bot.api.users.get.assert_awaited_once_with(user_ids=[message.from_id])
    assert [entry.name for entry in await storage.list_entries()] == ["Alice"]


@pytest.mark.anyio
async def test_add_friend_when_registration_is_open_adds_friend(
    tmp_path: Path,
) -> None:
    # Given: an open collection and an in-scope friend registration command
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(status_message_id=None)
    handlers = _message_handlers(_build_bot(storage, config))
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.text = "+ Bob"
    message.answer = AsyncMock()

    # When: the participant registers a friend after the announcement
    await handlers["add_friend"](message)

    # Then: the named friend is recorded
    assert [entry.name for entry in await storage.list_entries()] == ["Bob"]


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["+", "+   "])
async def test_bare_plus_when_registration_is_open_shows_guidance_without_mutation(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: an open collection and a plus command with no friend name
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(status_message_id=None)
    bot = _build_bot(storage, config)
    handlers = _message_handlers(bot)
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.text = command
    message.answer = AsyncMock()

    # When: the participant sends a bare plus command
    with (
        patch.object(storage, "add_user", wraps=storage.add_user) as add_user,
        patch.object(storage, "add_friend", wraps=storage.add_friend) as add_friend,
    ):
        await handlers["bare_plus"](message)

    # Then: guidance is returned without contacting VK or mutating the collection
    add_user.assert_not_awaited()
    add_friend.assert_not_awaited()
    bot.api.users.get.assert_not_awaited()
    expected_instruction = (
        "Чтобы записаться, нажми «Записаться» или напиши «записаться»"
    )
    message.answer.assert_awaited_once_with(
        f"{expected_instruction} после анонса. Для друга: + Имя.",
        keyboard=ANY,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("registration_open", [False, True])
async def test_non_registration_text_handlers_work_in_each_lifecycle_state(
    tmp_path: Path,
    registration_open: bool,
) -> None:
    # Given: a populated collection in either lifecycle state
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    if registration_open:
        await storage.start_new_collection(status_message_id=None)
    await storage.add_user(1, "Alice")
    await storage.add_friend("Bob")
    handlers = _message_handlers(_build_bot(storage, config))
    message = MagicMock(spec=Message)
    message.peer_id = config.chat_peer_id
    message.from_id = 1
    message.answer = AsyncMock()

    # When: the participant leaves, removes a friend, reads the list, and asks for help
    await handlers["sign_off"](message)
    message.text = "- Bob"
    await handlers["remove_friend"](message)
    await handlers["show_list"](message)
    await handlers["help_cmd"](message)

    # Then: non-registration actions remain available regardless of lifecycle state
    assert await storage.list_entries() == []
    assert message.answer.await_count == 4


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
    await storage.start_new_collection(status_message_id=None)
    bot = _build_bot(storage, config)
    handlers = _callback_handlers(bot)
    event = MagicMock(spec=MessageEvent)
    event.peer_id = config.chat_peer_id
    event.user_id = 1
    event.show_snackbar = AsyncMock()
    event.send_message = AsyncMock()
    event.edit_message = AsyncMock()

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
    assert event.show_snackbar.await_count == 6
    assert event.edit_message.await_count == 2
    event.send_message.assert_not_awaited()


@pytest.mark.anyio
async def test_callback_list_edits_the_originating_conversation_message(
    tmp_path: Path,
) -> None:
    # Given: an in-scope list callback with distinct conversation and message IDs
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(1, "Alice")
    bot = _build_bot(storage, config)
    event = MagicMock(spec=MessageEvent)
    event.peer_id = config.chat_peer_id
    event.conversation_message_id = 42
    event.message_id = 999
    event.edit_message = AsyncMock()
    event.send_message = AsyncMock()
    event.show_snackbar = AsyncMock()

    # When: the participant requests the list through the inline button
    await _callback_handlers(bot)["cb_list"](event)

    # Then: VKBottle edits the event's conversation message, never a guessed ID
    event.edit_message.assert_awaited_once_with(
        message="Список участников:\n1. Alice",
        keyboard=ANY,
    )
    bot.api.messages.edit.assert_not_called()
    event.send_message.assert_not_awaited()
    event.show_snackbar.assert_awaited_once_with("Список обновлён в сообщении бота.")


@pytest.mark.anyio
async def test_callback_list_replaces_message_once_when_edit_fails(
    tmp_path: Path,
) -> None:
    # Given: an in-scope list callback whose event message is no longer editable
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    bot = _build_bot(storage, config)
    bot.api.messages.send = AsyncMock(return_value=321)
    event = MagicMock(spec=MessageEvent)
    event.peer_id = config.chat_peer_id
    event.edit_message = AsyncMock(
        side_effect=VKAPIError(error_msg="message is not editable"),
    )
    event.send_message = AsyncMock()
    event.show_snackbar = AsyncMock()

    # When: the participant requests the list through the inline button
    await _callback_handlers(bot)["cb_list"](event)

    # Then: exactly one replacement is sent and becomes the canonical status
    event.edit_message.assert_awaited_once_with(
        message="Пока никто не записался.",
        keyboard=ANY,
    )
    bot.api.messages.send.assert_awaited_once_with(
        peer_id=config.chat_peer_id,
        message="Пока никто не записался.",
        keyboard=ANY,
        random_id=ANY,
    )
    event.send_message.assert_not_awaited()
    assert await storage.status_message_id() == 321


@pytest.mark.anyio
async def test_callback_join_and_leave_refresh_the_canonical_status_message(
    tmp_path: Path,
) -> None:
    # Given: an open collection with a persisted canonical message ID
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(status_message_id=321)
    bot = _build_bot(storage, config)
    bot.api.messages.edit = AsyncMock()
    event = MagicMock(spec=MessageEvent)
    event.peer_id = config.chat_peer_id
    event.user_id = 1
    event.show_snackbar = AsyncMock()

    # When: the participant joins and then leaves through the inline buttons
    handlers = _callback_handlers(bot)
    await handlers["cb_join"](event)
    await handlers["cb_leave"](event)

    # Then: each successful mutation refreshes the stored canonical message
    assert bot.api.messages.edit.await_args_list == [
        call(
            peer_id=config.chat_peer_id,
            message_id=321,
            message="Список участников:\n1. Alice",
            keyboard=ANY,
        ),
        call(
            peer_id=config.chat_peer_id,
            message_id=321,
            message="Пока никто не записался.",
            keyboard=ANY,
        ),
    ]
