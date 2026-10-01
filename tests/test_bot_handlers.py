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
from vkbottle_types.objects import MessagesDeleteFullResponseItem

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
    publisher.delete_event = AsyncMock(return_value=True)
    publisher.diagnostic_notice = AsyncMock(return_value="Состояние: open")
    return publisher


def _build_bot(storage: Storage, config: Config) -> tuple[Bot, MagicMock]:
    bot = Bot(config.vk_token)
    api = MagicMock()
    api.users.get = AsyncMock(
        return_value=[SimpleNamespace(first_name="Alice")],
    )
    api.messages.send = AsyncMock(return_value=1)
    api.messages.delete = AsyncMock()
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
    message.conversation_message_id = 1
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
@pytest.mark.parametrize("callback", [False, True])
async def test_closed_event_withdrawal_and_return_follow_half_hour_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    callback: bool,
) -> None:
    # Given: registration is closed and the event started ten minutes ago.
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2026, 9, 30, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "manual")
    await storage.activate_event(10, starts_at)
    await storage.add_user(1, "Alice")
    await storage.add_user(2, "Bob")
    await storage.close_registration()
    monkeypatch.setattr(
        Storage,
        "_now",
        lambda _: starts_at + datetime.timedelta(minutes=10),
    )
    bot, publisher = _build_bot(storage, config)
    event = _event(config)
    messages = _message_handlers(bot)
    callbacks = _callback_handlers(bot)

    # When: the participant withdraws, then signs up again via the same interface.
    if callback:
        await callbacks["cb_leave"](event)
    else:
        await messages["sign_off"](_message(config, text="отписаться"))
    entries = await storage.list_entries()
    assert [entry.name for entry in entries] == ["Bob", "Alice"]
    assert entries[-1].withdrawn
    if callback:
        await callbacks["cb_join"](event)
    else:
        await messages["sign_up"](_message(config, text="записаться"))

    # Then: the existing name is restored without a new profile lookup or entry.
    assert all(not entry.withdrawn for entry in await storage.list_entries())
    bot.api.users.get.assert_not_awaited()
    if callback:
        assert publisher.edit_current.await_count == 2
        publisher.replace_current.assert_not_awaited()
    else:
        assert publisher.replace_current.await_count == 2
        publisher.edit_current.assert_not_awaited()

    # When: withdrawal is attempted at exactly thirty minutes after start.
    before = await storage.snapshot()
    monkeypatch.setattr(
        Storage,
        "_now",
        lambda _: starts_at + datetime.timedelta(minutes=30),
    )
    if callback:
        await callbacks["cb_leave"](event)
        assert "30 минут" in event.show_snackbar.await_args.args[0]
        assert publisher.edit_current.await_count == 2
    else:
        await messages["sign_off"](_message(config, text="отписаться"))
        assert "30 минут" in publisher.replace_current.await_args.kwargs["notice"]
    # Then: the final participant list is preserved and the refusal is visible.
    assert await storage.snapshot() == before


@pytest.mark.anyio
async def test_friend_commands_restore_existing_row_after_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2026, 9, 30, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "manual")
    await storage.activate_event(10, starts_at)
    await storage.add_friend("Друг")
    await storage.close_registration()
    monkeypatch.setattr(Storage, "_now", lambda _: starts_at)
    bot, _publisher_mock = _build_bot(storage, config)
    handlers = _message_handlers(bot)

    await handlers["remove_friend"](_message(config, text="- Друг"))
    assert (await storage.list_entries())[0].withdrawn
    await handlers["add_friend"](_message(config, text="+ Друг"))
    entries = await storage.list_entries()
    assert len(entries) == 1
    assert not entries[0].withdrawn


@pytest.mark.anyio
async def test_new_signup_is_refused_after_start_even_before_scheduler_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a started event whose durable state has not yet switched to closed.
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2026, 9, 30, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "manual")
    await storage.activate_event(10, starts_at)
    monkeypatch.setattr(Storage, "_now", lambda _: starts_at)
    bot, publisher = _build_bot(storage, config)

    # When: a new user or friend tries to join during the late-change window.
    await _callback_handlers(bot)["cb_join"](_event(config, user_id=3))
    await _message_handlers(bot)["add_friend"](_message(config, text="+ Новый"))

    # Then: the grace period cannot be used for first-time registrations.
    assert await storage.list_entries() == []
    bot.api.users.get.assert_not_awaited()
    publisher.edit_current.assert_not_awaited()
    assert "Запись закрыта" in publisher.replace_current.await_args.kwargs["notice"]


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
    await handlers["cb_help"](event)
    await handlers["cb_leave"](event)

    assert await storage.list_entries() == []
    assert publisher.edit_current.await_count == 2
    assert event.show_snackbar.await_count == 3


@pytest.mark.anyio
async def test_three_callbacks_keep_card_in_place_and_help_in_snackbar(
    tmp_path: Path,
) -> None:
    # Given: a card with three inline actions and open registration.
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(None)
    bot, publisher = _build_bot(storage, config)
    handlers = _callback_handlers(bot)
    assert set(handlers) == {"cb_join", "cb_leave", "cb_help"}
    event = _event(config)

    # When: a participant joins, asks for help, and leaves via the buttons.
    await handlers["cb_join"](event)
    assert [entry.name for entry in await storage.list_entries()] == ["Alice"]
    publisher.edit_current.assert_awaited_once_with()
    publisher.edit_current.reset_mock()
    await handlers["cb_help"](event)

    # Then: help explains how to bring the card last without republishing it.
    assert (
        "список — показать актуальную карточку в конце чата"
        in (event.show_snackbar.await_args.args[0])
    )
    publisher.edit_current.assert_not_awaited()
    await handlers["cb_leave"](event)
    assert await storage.list_entries() == []
    publisher.edit_current.assert_awaited_once_with()
    publisher.replace_current.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["список", "участники", "кто идёт"])
async def test_list_command_republishes_card_with_current_participants(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: an existing card with a participant, buried in the chat history.
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(10)
    await storage.add_friend("Друг")
    before = await storage.list_entries()
    bot = Bot(config.vk_token)
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=20)
    api.messages.delete = AsyncMock()
    api.messages.edit = AsyncMock()
    bot.api = api
    setup_handlers(bot, storage, config)
    message = _message(config, text=command)

    # When: the participant requests the list using a supported text alias.
    registered = next(
        handler
        for handler in bot.labeler.message_view.handlers
        if handler.handler.__name__ == "show_list"
    )
    assert all([await rule.check(message) is not False for rule in registered.rules])
    await registered.handler(message)

    # Then: a fresh card contains the list and replaces the previous message.
    api.messages.send.assert_awaited_once()
    assert "1. Друг (друг)" in api.messages.send.await_args.kwargs["message"]
    api.messages.delete.assert_awaited_once_with(
        message_ids=[10],
        delete_for_all=True,
    )
    assert (await storage.snapshot()).status_message_id == 20
    assert await storage.list_entries() == before
    api.messages.edit.assert_not_awaited()
    message.answer.assert_not_awaited()


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
@pytest.mark.parametrize("variant", ["exact", "capitalized", "padded_upper"])
@pytest.mark.parametrize(
    ("command", "action"),
    [
        ("записаться", "join"),
        ("отписаться", "leave"),
        ("-", "leave"),
        ("+", "hint"),
        ("список", "list"),
        ("участники", "list"),
        ("кто идёт", "list"),
        ("?", "help"),
        ("help", "help"),
        ("помощь", "help"),
        ("команды", "help"),
    ],
)
async def test_user_commands_accept_case_and_outer_spaces_through_router(
    tmp_path: Path,
    command: str,
    action: str,
    variant: str,
) -> None:
    # Given: an open collection and a VK participant sending a documented command.
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(10)
    if action == "leave":
        await storage.add_user(123, "Alice")
    bot, publisher = _build_bot(storage, config)
    if variant == "capitalized":
        command = command[0].upper() + command[1:]
    elif variant == "padded_upper":
        command = f"  {command.upper()}  "

    # When: the real router handles the message rather than a direct handler call.
    await _route_admin_message(bot, config, command)

    # Then: the command performs its action and publishes exactly one card.
    publisher.replace_current.assert_awaited_once()
    entries = await storage.list_entries()
    if action == "join":
        assert [entry.name for entry in entries] == ["Alice"]
    else:
        assert entries == []
    if action == "help":
        assert "список —" in publisher.replace_current.await_args.kwargs["notice"]
    elif action == "hint":
        assert (
            "Чтобы записаться" in publisher.replace_current.await_args.kwargs["notice"]
        )


@pytest.mark.anyio
async def test_friend_commands_trim_edges_without_changing_name_case(
    tmp_path: Path,
) -> None:
    config = Config(vk_token=secrets.token_urlsafe(), chat_peer_id=2_000_000_001)
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(10)
    bot, publisher = _build_bot(storage, config)
    await _route_admin_message(bot, config, "  + АнНа ПЕТРОВА  ")
    assert [entry.name for entry in await storage.list_entries()] == ["АнНа ПЕТРОВА"]
    await _route_admin_message(bot, config, "  - АнНа ПЕТРОВА  ")
    assert await storage.list_entries() == []
    assert publisher.replace_current.await_count == 2


async def _route_admin_message(
    bot: Bot,
    config: Config,
    text: str,
    *,
    user_id: int = 123,
    peer_id: int | None = None,
) -> None:
    """Deliver the same event shape as Bots Long Poll to the actual router."""
    await bot.router.route(
        {
            "type": "message_new",
            "group_id": 7,
            "object": {
                "message": {
                    "id": 1,
                    "version": 1,
                    "conversation_message_id": 1,
                    "date": 1,
                    "from_id": user_id,
                    "peer_id": config.chat_peer_id if peer_id is None else peer_id,
                    "out": 0,
                    "text": text,
                    "attachments": [],
                    "fwd_messages": [],
                }
            },
        },
        bot.api,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("variant", ["exact", "capitalized", "upper_prefix", "padded"])
@pytest.mark.parametrize(
    ("command", "action"),
    [
        ("очистить", "clear"),
        ("сбросить", "clear"),
        ("убрать Bob", "remove"),
        ("удалить Bob", "remove"),
        ("админ помощь", "help"),
        ("admin help", "help"),
        ("статус события", "status"),
        ("удалить событие", "delete_event"),
        ("создать событие 22.09.2099 19:30", "create"),
    ],
)
async def test_admin_commands_route_from_long_poll_and_perform_action(
    tmp_path: Path,
    command: str,
    action: str,
    variant: str,
) -> None:
    # Given: an allowed administrator in the configured chat and one participant.
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_friend("Bob")
    bot, publisher = _build_bot(storage, config)

    if variant == "capitalized":
        command = command[0].upper() + command[1:]
    elif variant == "upper_prefix":
        prefix, separator, rest = command.partition(" ")
        command = prefix.upper() + separator + rest
    elif variant == "padded":
        command = f"  {command}  "

    # When: VK delivers a documented command through the real routing pipeline.
    with patch("src.bot.open_manual_event", new_callable=AsyncMock) as open_event:
        await _route_admin_message(bot, config, command)

    # Then: the command is deleted for everyone and feedback stays private.
    bot.api.messages.delete.assert_awaited_once_with(
        peer_id=config.chat_peer_id,
        cmids=[1],
        delete_for_all=True,
    )
    publisher.replace_current.assert_not_awaited()
    if action == "create":
        open_event.assert_awaited_once()
    elif action in {"clear", "remove"}:
        publisher.edit_current.assert_awaited_once()
        assert await storage.list_entries() == []
    else:
        bot.api.messages.send.assert_awaited_once()
        assert bot.api.messages.send.await_args.kwargs["peer_id"] == 123
        text = bot.api.messages.send.await_args.kwargs["message"]
        if action == "help":
            assert "Админ-команды" in text
            assert "удалить событие" in text
        elif action == "delete_event":
            publisher.delete_event.assert_awaited_once()
            assert text == "Событие удалено."
        else:
            publisher.diagnostic_notice.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    [
        "очистить",
        "убрать Bob",
        "админ помощь",
        "статус события",
        "удалить событие",
        "создать событие 22.09.2099 19:30",
    ],
)
@pytest.mark.parametrize(("admin_ids", "user_id"), [((123,), 999), ((), 123)])
async def test_all_admin_actions_deny_unlisted_user_through_router(
    tmp_path: Path,
    command: str,
    admin_ids: tuple[int, ...],
    user_id: int,
) -> None:
    # Given: a chat participant who is not listed in ADMIN_VK_IDS_RAW.
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=admin_ids,
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_friend("Bob")
    before = await storage.snapshot()
    bot, publisher = _build_bot(storage, config)
    # When: any admin command is delivered by VK from that participant.
    with patch("src.bot.open_manual_event", new_callable=AsyncMock) as open_event:
        await _route_admin_message(bot, config, command, user_id=user_id)
    # Then: it produces an explicit denial and leaves the event untouched.
    publisher.replace_current.assert_not_awaited()
    bot.api.messages.delete.assert_awaited_once()
    assert "Только администраторы" in bot.api.messages.send.await_args.kwargs["message"]
    publisher.delete_event.assert_not_awaited()
    open_event.assert_not_awaited()
    publisher.diagnostic_notice.assert_not_awaited()
    assert await storage.snapshot() == before


@pytest.mark.anyio
async def test_admin_status_is_sent_privately(tmp_path: Path) -> None:
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
    publisher.replace_current.assert_not_awaited()
    assert bot.api.messages.send.await_args.kwargs["message"] == "Состояние: open"
    assert bot.api.messages.send.await_args.kwargs["peer_id"] == 123


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    [
        "Очистить",
        "Убрать Bob",
        "Админ помощь",
        "Статус события",
        "Удалить событие",
        "Создать событие 22.09.2099 19:30",
    ],
)
async def test_admin_commands_from_another_chat_do_not_touch_data(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: an authorized admin sending a command from an unconfigured chat.
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_friend("Bob")
    before = await storage.snapshot()
    bot, publisher = _build_bot(storage, config)
    # When: the actual router receives an admin command from that chat.
    with patch("src.bot.open_manual_event", new_callable=AsyncMock) as open_event:
        await _route_admin_message(
            bot, config, command, peer_id=config.chat_peer_id + 1
        )
    # Then: no admin action or visible response is performed.
    bot.api.messages.delete.assert_not_awaited()
    bot.api.messages.send.assert_not_awaited()
    publisher.delete_event.assert_not_awaited()
    open_event.assert_not_awaited()
    publisher.replace_current.assert_not_awaited()
    publisher.diagnostic_notice.assert_not_awaited()
    assert await storage.snapshot() == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("command", "notice"),
    [
        ("Создать событие", "Формат:"),
        ("Создать событие завтра", "Формат:"),
        ("Создать событие 01.01.2000 00:00", "должно быть в будущем"),
    ],
)
async def test_invalid_admin_event_command_shows_error_instead_of_silence(
    tmp_path: Path,
    command: str,
    notice: str,
) -> None:
    # Given: an administrator with existing participant data.
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_friend("Bob")
    before = await storage.snapshot()
    bot, publisher = _build_bot(storage, config)
    # When: VK delivers an incomplete, malformed, or expired event command.
    await _route_admin_message(bot, config, command)
    # Then: the error is visible and the stored event is preserved.
    publisher.replace_current.assert_not_awaited()
    bot.api.messages.delete.assert_awaited_once()
    assert notice in bot.api.messages.send.await_args.kwargs["message"]
    assert await storage.snapshot() == before


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
    publisher.replace_current.assert_not_awaited()
    assert "Только администраторы" in bot.api.messages.send.await_args.kwargs["message"]


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


@pytest.mark.anyio
async def test_delete_event_through_router_removes_command_before_event(
    tmp_path: Path,
) -> None:
    # Given: an open event and a friend literally named "событие".
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2099, 9, 22, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "manual")
    await storage.activate_event(10, starts_at, conversation_message_id=20)
    await storage.add_friend("событие")
    bot = Bot(config.vk_token)
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=1)
    deletions: list[list[int]] = []

    async def delete(**kwargs: object) -> list[object]:
        cmids = kwargs["cmids"]
        assert isinstance(cmids, list)
        deletions.append(cmids)
        if cmids == [1]:
            assert (await storage.snapshot()).state == "open"
            assert len(await storage.list_entries()) == 1
        else:
            assert (await storage.snapshot()).state == "closed"
        return []

    api.messages.delete = AsyncMock(side_effect=delete)
    bot.api = api
    setup_handlers(bot, storage, config)

    # When: the command arrives through Long Poll with case and outer spaces.
    await _route_admin_message(bot, config, "  УДАЛИТЬ СОБЫТИЕ  ")

    # Then: the command disappears first, followed by the entire event card.
    assert deletions == [[1], [20]]
    snapshot = await storage.snapshot()
    assert snapshot.state == "closed"
    assert snapshot.event_starts_at is None
    assert not snapshot.participants
    assert snapshot.status_message_id is None
    api.messages.send.assert_awaited_once()
    assert api.messages.send.await_args.kwargs["peer_id"] == 123
    assert api.messages.send.await_args.kwargs["message"] == "Событие удалено."


@pytest.mark.anyio
async def test_private_help_failure_does_not_expose_help_in_card(
    tmp_path: Path,
) -> None:
    # Given: VK forbids the community from messaging the administrator privately.
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    bot, publisher = _build_bot(storage, config)
    bot.api.messages.send.side_effect = OSError("private messages disabled")

    # When: the admin requests help.
    await _route_admin_message(bot, config, "Админ помощь")

    # Then: the command is deleted and private help never leaks into the chat.
    bot.api.messages.delete.assert_awaited_once()
    bot.api.messages.send.assert_awaited_once()
    publisher.replace_current.assert_not_awaited()
    publisher.edit_current.assert_not_awaited()


@pytest.mark.anyio
async def test_command_delete_failure_warns_privately_and_still_runs_action(
    tmp_path: Path,
) -> None:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    await storage.add_friend("Друг")
    bot, publisher = _build_bot(storage, config)
    bot.api.messages.delete.side_effect = OSError("missing chat administrator rights")
    await _route_admin_message(bot, config, "Очистить")
    assert not await storage.list_entries()
    publisher.edit_current.assert_awaited_once()
    assert "права администратора" in bot.api.messages.send.await_args.kwargs["message"]
    assert bot.api.messages.send.await_args.kwargs["peer_id"] == 123
    publisher.replace_current.assert_not_awaited()


@pytest.mark.anyio
async def test_vk_individual_command_delete_refusal_warns_privately(
    tmp_path: Path,
) -> None:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
    )
    storage = Storage(tmp_path / "participants.json")
    bot, publisher = _build_bot(storage, config)
    bot.api.messages.delete.return_value = [
        MessagesDeleteFullResponseItem(conversation_message_id=1, response=False)
    ]
    await _route_admin_message(bot, config, "админ помощь")
    assert bot.api.messages.send.await_count == 2
    assert (
        "права администратора"
        in bot.api.messages.send.await_args_list[0].kwargs["message"]
    )
    assert "Админ-команды" in bot.api.messages.send.await_args_list[1].kwargs["message"]
    publisher.replace_current.assert_not_awaited()
    publisher.edit_current.assert_not_awaited()
