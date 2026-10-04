"""Focused business specs through VK's message and callback boundaries."""

from __future__ import annotations

import datetime
import json
import secrets
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from src import scheduler
from src.bot import setup_handlers
from src.cards import CardPublisher
from src.config import Config
from src.keyboard import build_inline_keyboard
from src.storage import RegistrationSnapshot, Storage
from vkbottle.bot import Bot
from vkbottle_types.objects import (
    MessagesDeleteFullResponseItem,
    MessagesSendUserIdsResponseItem,
)

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime.datetime(2026, 10, 4, 18, 0)  # noqa: DTZ001
_START = _NOW + datetime.timedelta(hours=1)
type Channel = Literal["text", "button"]


def _spellings(*commands: str) -> list[str]:
    variants: list[str] = []
    for command in commands:
        prefix, separator, rest = command.partition(" ")
        variants.extend(
            (
                command,
                command[0].upper() + command[1:],
                prefix.upper() + separator + rest,
                f"  {prefix.upper()}{separator}{rest}  ",
            )
        )
    return list(dict.fromkeys(variants))


@dataclass
class Chat:
    config: Config
    storage: Storage
    bot: Bot
    api: MagicMock
    path: Path
    now: datetime.datetime = _NOW

    async def open_event(self) -> None:
        assert await self.storage.begin_event(_START, "manual")
        await self.storage.activate_event(10, _START, conversation_message_id=20)

    async def saved(self) -> RegistrationSnapshot:
        return await Storage(self.path).snapshot()

    async def message(
        self, text: str, *, user_id: int = 123, peer_id: int | None = None
    ) -> None:
        await self.bot.router.route(
            {
                "type": "message_new",
                "group_id": 7,
                "object": {
                    "message": {
                        "id": 1,
                        "conversation_message_id": 1,
                        "date": 1,
                        "from_id": user_id,
                        "peer_id": self.config.chat_peer_id
                        if peer_id is None
                        else peer_id,
                        "out": 0,
                        "text": text,
                        "attachments": [],
                        "fwd_messages": [],
                        "version": 1,
                    }
                },
            },
            self.api,
        )

    async def press(self, label: str, *, peer_id: int | None = None) -> None:
        keyboard = json.loads(build_inline_keyboard())
        action = next(
            button["action"]
            for row in keyboard["buttons"]
            for button in row
            if label.casefold() in button["action"]["label"].casefold()
        )
        await self.bot.router.route(
            {
                "type": "message_event",
                "group_id": 7,
                "object": {
                    "event_id": "participant-click",
                    "user_id": 123,
                    "peer_id": self.config.chat_peer_id if peer_id is None else peer_id,
                    "conversation_message_id": 20,
                    "payload": action["payload"],
                },
            },
            self.api,
        )

    async def participant_action(self, label: str, channel: Channel) -> None:
        if channel == "button":
            await self.press(label)
        else:
            await self.message(label.lower())

    def feedback(self, channel: Channel) -> str:
        if channel == "button":
            assert self.api.messages.send_message_event_answer.await_count == 1
            answer = self.api.messages.send_message_event_answer.await_args.kwargs
            return str(json.loads(answer["event_data"])["text"])
        return str(self.api.messages.send.await_args.kwargs["message"])

    def updated_card(self, channel: Channel) -> str:
        operation = (
            self.api.messages.edit if channel == "button" else self.api.messages.send
        )
        return str(operation.await_args.kwargs["message"])


@pytest.fixture
def chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Chat:
    config = Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
        admin_vk_ids=(123,),
        collect_weekday=0,
        collect_time="08:00",
        event_weekday=1,
        event_time="19:30",
        remind_enabled=False,
    )
    path = tmp_path / "participants.json"
    storage = Storage(path)
    api = MagicMock()
    api.users.get = AsyncMock(return_value=[SimpleNamespace(first_name="Alice")])
    api.messages.send = AsyncMock(
        return_value=[
            MessagesSendUserIdsResponseItem(
                peer_id=config.chat_peer_id,
                message_id=30,
                conversation_message_id=40,
            )
        ]
    )
    api.messages.edit = AsyncMock(return_value=1)
    api.messages.delete = AsyncMock(return_value=[])
    api.messages.send_message_event_answer = AsyncMock(return_value=1)
    bot = Bot(config.vk_token)
    bot.api = api
    setup_handlers(bot, storage, config, CardPublisher(api, config, storage))
    scenario = Chat(config, storage, bot, api, path)

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> datetime.datetime:
            return scenario.now.replace(tzinfo=tz)

    monkeypatch.setattr(
        scheduler,
        "datetime",
        SimpleNamespace(datetime=Clock, timedelta=datetime.timedelta),
    )
    monkeypatch.setattr(Storage, "_now", lambda _: scenario.now)
    return scenario


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("записаться"))
async def test_participant_signs_up_by_text(chat: Chat, command: str) -> None:
    # Given: registration is open and another participant is already listed.
    await chat.open_event()
    await chat.storage.add_friend("Bob")
    # When: the participant sends the signup command.
    await chat.message(command)
    # Then: their VK identity is saved and visible in one new last card.
    entries = (await chat.saved()).participants
    assert any(
        e.kind == "user" and e.vk_id == 123 and e.name == "Alice" for e in entries
    )
    chat.api.messages.send.assert_awaited_once()
    sent = chat.api.messages.send.await_args.kwargs
    assert sent["peer_ids"] == [chat.config.chat_peer_id]
    assert "Alice" in sent["message"]
    chat.api.messages.delete.assert_awaited_once_with(
        peer_id=chat.config.chat_peer_id, cmids=[20], delete_for_all=True
    )


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("отписаться", "-"))
async def test_registered_participant_leaves_by_text(chat: Chat, command: str) -> None:
    # Given: the participant is listed one second before the event starts.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    await chat.storage.add_friend("Bob")
    chat.now = _START - datetime.timedelta(seconds=1)
    # When: the participant sends a withdrawal command.
    await chat.message(command)
    # Then: only their row disappears from the saved list and the new card.
    assert [e.name for e in (await chat.saved()).participants] == ["Bob"]
    card = chat.updated_card("text")
    assert "Bob" in card
    assert "Alice" not in card


@pytest.mark.anyio
async def test_participant_presses_signup(chat: Chat) -> None:
    # Given: the participant can see an open event's inline keyboard.
    await chat.open_event()
    # When: they press the signup action from the actual keyboard.
    await chat.press("Записаться")
    # Then: their identity is saved; the card updates with snackbar feedback.
    assert any(
        e.kind == "user" and e.vk_id == 123 for e in (await chat.saved()).participants
    )
    edited = chat.api.messages.edit.await_args.kwargs
    assert edited["conversation_message_id"] == 20
    assert "Alice" in edited["message"]
    assert "записал" in chat.feedback("button")
    chat.api.messages.send.assert_not_awaited()


@pytest.mark.anyio
async def test_registered_participant_presses_withdrawal(chat: Chat) -> None:
    # Given: the participant is listed just before the event starts.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    chat.now = _START - datetime.timedelta(seconds=1)
    # When: they press the withdrawal action from the actual keyboard.
    await chat.press("Отписаться")
    # Then: storage and the existing card exclude them, with snackbar feedback.
    assert (await chat.saved()).participants == ()
    assert "Alice" not in chat.updated_card("button")
    assert "отписал" in chat.feedback("button")
    chat.api.messages.send.assert_not_awaited()


@pytest.mark.anyio
async def test_participant_presses_help(chat: Chat) -> None:
    # Given: an event card already exists and contains a participant.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    before = await chat.saved()
    # When: the participant presses help from the actual keyboard.
    await chat.press("Помощь")
    # Then: snackbar guidance is shown without changing or republishing the card.
    assert "список" in chat.feedback("button")
    assert await chat.saved() == before
    chat.api.messages.send.assert_not_awaited()
    chat.api.messages.edit.assert_not_awaited()


@pytest.mark.anyio
async def test_listed_participant_presses_signup_again(chat: Chat) -> None:
    # Given: the participant already belongs to this event.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    before = (await chat.saved()).participants
    # When: they press signup again.
    await chat.press("Записаться")
    # Then: membership feedback is shown without a duplicate or new chat message.
    assert (await chat.saved()).participants == before
    assert "уже" in chat.feedback("button")
    chat.api.messages.send.assert_not_awaited()
    chat.api.messages.edit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("+"))
async def test_participant_requests_signup_guidance_with_bare_plus(
    chat: Chat, command: str
) -> None:
    # Given: registration is open but the participant has not joined.
    await chat.open_event()
    # When: they send a plus without a friend's name.
    await chat.message(command)
    # Then: the card explains signup while the participant list stays empty.
    assert (await chat.saved()).participants == ()
    assert "записаться" in chat.updated_card("text").lower()
    assert "+ Имя" in chat.updated_card("text")


@pytest.mark.anyio
async def test_participant_adds_friend_with_name_case_preserved(chat: Chat) -> None:
    # Given: an open event with no friends yet.
    await chat.open_event()
    # When: the participant adds a friend with outer spaces and mixed name case.
    await chat.message("  + АнНа ПЕТРОВА  ")
    # Then: the trimmed name is saved as a friend and displayed with its case intact.
    assert [(e.kind, e.name) for e in (await chat.saved()).participants] == [
        ("friend", "АнНа ПЕТРОВА")
    ]
    assert "АнНа ПЕТРОВА (друг)" in chat.updated_card("text")


@pytest.mark.anyio
async def test_participant_removes_named_friend_before_start(chat: Chat) -> None:
    # Given: two friends are listed before the event starts.
    await chat.open_event()
    await chat.storage.add_friend("АнНа ПЕТРОВА")
    await chat.storage.add_friend("Bob")
    # When: the participant removes one friend with outer whitespace.
    await chat.message("  - АнНа ПЕТРОВА  ")
    # Then: that friend alone disappears from storage and the card.
    assert [e.name for e in (await chat.saved()).participants] == ["Bob"]
    assert "АнНа" not in chat.updated_card("text")


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("список", "участники", "кто идёт"))
async def test_participant_requests_current_list(chat: Chat, command: str) -> None:
    # Given: a participant's card is buried in chat history.
    await chat.open_event()
    await chat.storage.add_friend("Друг")
    before = (await chat.saved()).participants
    # When: a participant requests the list using a supported spelling.
    await chat.message(command)
    # Then: one new last card shows the same participants and replaces the old card.
    chat.api.messages.send.assert_awaited_once()
    assert "Друг (друг)" in chat.updated_card("text")
    assert (await chat.saved()).participants == before
    chat.api.messages.delete.assert_awaited_once_with(
        peer_id=chat.config.chat_peer_id, cmids=[20], delete_for_all=True
    )


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("?", "help", "помощь", "команды"))
async def test_participant_requests_text_help(chat: Chat, command: str) -> None:
    # Given: an open event with a participant.
    await chat.open_event()
    await chat.storage.add_friend("Друг")
    before = (await chat.saved()).participants
    # When: a participant requests help using a supported spelling.
    await chat.message(command)
    # Then: one card includes signup/friend guidance and preserves attendance.
    chat.api.messages.send.assert_awaited_once()
    text = chat.updated_card("text")
    assert "записаться" in text
    assert "+ Имя" in text
    assert (await chat.saved()).participants == before


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["text", "button"])
async def test_participant_attempts_signup_before_announcement(
    chat: Chat, channel: Channel
) -> None:
    # Given: the event has not been announced and registration is closed.
    before = (await chat.saved()).participants
    # When: a participant tries to sign up.
    await chat.participant_action("Записаться", channel)
    # Then: registration is refused with visible guidance and no saved participant.
    assert (await chat.saved()).participants == before
    assert "Запись закрыта" in chat.feedback(channel)


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["text", "button"])
@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("seconds", [0, 1799])
async def test_registered_participant_withdraws_during_grace_period(
    chat: Chat, channel: Channel, closed: bool, seconds: int
) -> None:
    # Given: start has elapsed, with or without the scheduler's close transition.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    await chat.storage.add_user(456, "Bob")
    if closed:
        await chat.storage.close_registration()
    chat.now = _START + datetime.timedelta(seconds=seconds)
    # When: the listed participant withdraws during the first half hour.
    await chat.participant_action("Отписаться", channel)
    # Then: their saved row is marked at the bottom and the displayed count decreases.
    entries = (await chat.saved()).participants
    assert [e.name for e in entries] == ["Bob", "Alice"]
    assert entries[-1].withdrawn
    card = chat.updated_card(channel)
    assert "Alice (-)" in card
    assert "Участники: 1" in card


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["text", "button"])
async def test_withdrawn_participant_returns_during_grace_period(
    chat: Chat, channel: Channel
) -> None:
    # Given: two participants withdrew after start while a friend stayed active.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    await chat.storage.add_user(456, "Bob")
    await chat.storage.add_friend("Друг")
    await chat.storage.close_registration()
    chat.now = _START + datetime.timedelta(minutes=10)
    assert await chat.storage.withdraw_user(123)
    assert await chat.storage.withdraw_user(456)
    # When: the withdrawn participant signs up again within the grace period.
    await chat.participant_action("Записаться", channel)
    # Then: the same identity is active above withdrawn rows and the count is restored.
    entries = (await chat.saved()).participants
    assert [e.name for e in entries] == ["Друг", "Alice", "Bob"]
    assert [e.withdrawn for e in entries] == [False, False, True]
    assert entries[1].kind == "user"
    assert entries[1].vk_id == 123
    assert "Alice (-)" not in chat.updated_card(channel)
    assert "Участники: 2" in chat.updated_card(channel)


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["text", "button"])
@pytest.mark.parametrize("seconds", [1800, 1801, 86400])
async def test_registered_participant_cannot_withdraw_after_grace_period(
    chat: Chat, channel: Channel, seconds: int
) -> None:
    # Given: registration is closed and the withdrawal window has ended.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    await chat.storage.close_registration()
    chat.now = _START + datetime.timedelta(seconds=seconds)
    before = (await chat.saved()).participants
    # When: the participant attempts to withdraw.
    await chat.participant_action("Отписаться", channel)
    # Then: visible refusal preserves their final attendance row.
    assert (await chat.saved()).participants == before
    assert "30 минут" in chat.feedback(channel)


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["text", "button"])
@pytest.mark.parametrize("seconds", [1800, 1801, 86400])
async def test_withdrawn_participant_cannot_return_after_grace_period(
    chat: Chat, channel: Channel, seconds: int
) -> None:
    # Given: a withdrawn row and an expired grace period.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    await chat.storage.close_registration()
    chat.now = _START
    assert await chat.storage.withdraw_user(123)
    chat.now = _START + datetime.timedelta(seconds=seconds)
    before = (await chat.saved()).participants
    # When: the participant attempts to return.
    await chat.participant_action("Записаться", channel)
    # Then: the minus remains saved and the participant receives an explicit refusal.
    assert (await chat.saved()).participants == before
    assert "30 минут" in chat.feedback(channel)


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["text", "button"])
async def test_new_participant_cannot_join_started_event(
    chat: Chat, channel: Channel
) -> None:
    # Given: the event started but its durable state has not yet been closed.
    await chat.open_event()
    chat.now = _START
    # When: a new participant attempts to join through one interface.
    await chat.participant_action("Записаться", channel)
    # Then: the grace period cannot add a new identity and refusal is visible.
    assert (await chat.saved()).participants == ()
    assert "Запись закрыта" in chat.feedback(channel)


@pytest.mark.anyio
async def test_participant_cannot_add_new_friend_after_start(chat: Chat) -> None:
    # Given: an event that has just started.
    await chat.open_event()
    chat.now = _START
    # When: a participant attempts to add a new friend.
    await chat.message("+ Новый")
    # Then: the friend is not saved and the card explains that registration is closed.
    assert (await chat.saved()).participants == ()
    assert "Запись закрыта" in chat.updated_card("text")


@pytest.mark.anyio
async def test_participant_withdraws_friend_during_grace_period(chat: Chat) -> None:
    # Given: a friend and a VK participant are listed in a started event.
    await chat.open_event()
    await chat.storage.add_friend("Друг")
    await chat.storage.add_user(456, "Bob")
    await chat.storage.close_registration()
    chat.now = _START + datetime.timedelta(minutes=5)
    # When: a participant withdraws the friend by name.
    await chat.message("- Друг")
    # Then: the saved friend moves to the bottom with a minus, excluded from count.
    entries = (await chat.saved()).participants
    assert [e.name for e in entries] == ["Bob", "Друг"]
    assert entries[-1].withdrawn
    assert "Друг (-) (друг)" in chat.updated_card("text")
    assert "Участники: 1" in chat.updated_card("text")


@pytest.mark.anyio
async def test_participant_restores_withdrawn_friend_during_grace_period(
    chat: Chat,
) -> None:
    # Given: a friend's row was marked after the event started.
    await chat.open_event()
    await chat.storage.add_friend("Друг")
    await chat.storage.close_registration()
    chat.now = _START + datetime.timedelta(minutes=5)
    assert await chat.storage.withdraw_friend("Друг")
    # When: the participant adds that friend again.
    await chat.message("+ Друг")
    # Then: the existing row is active without a duplicate or minus in the card.
    entries = (await chat.saved()).participants
    assert len(entries) == 1
    assert entries[0].name == "Друг"
    assert not entries[0].withdrawn
    assert "Друг (-)" not in chat.updated_card("text")
    assert "Участники: 1" in chat.updated_card("text")


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["- Друг", "+ Друг"])
@pytest.mark.parametrize("seconds", [1800, 1801, 86400])
async def test_participant_cannot_change_friend_after_grace_period(
    chat: Chat, command: str, seconds: int
) -> None:
    # Given: a friend's final row and an expired withdrawal/return window.
    await chat.open_event()
    await chat.storage.add_friend("Друг")
    await chat.storage.close_registration()
    chat.now = _START
    if command.startswith("+"):
        assert await chat.storage.withdraw_friend("Друг")
    before = (await chat.saved()).participants
    chat.now = _START + datetime.timedelta(seconds=seconds)
    # When: a participant attempts one late change to the friend.
    await chat.message(command)
    # Then: the final row stays saved and the card explains the time limit.
    assert (await chat.saved()).participants == before
    assert "30 минут" in chat.updated_card("text")


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["text", "button"])
async def test_withdrawn_participant_repeats_withdrawal(
    chat: Chat, channel: Channel
) -> None:
    # Given: the participant already withdrew during the grace period.
    await chat.open_event()
    await chat.storage.add_user(123, "Alice")
    await chat.storage.close_registration()
    chat.now = _START
    assert await chat.storage.withdraw_user(123)
    before = (await chat.saved()).participants
    # When: they repeat the withdrawal request.
    await chat.participant_action("Отписаться", channel)
    # Then: the existing withdrawn row retains its order and identity.
    assert (await chat.saved()).participants == before


@pytest.mark.anyio
async def test_participant_signup_cannot_leak_into_next_event(chat: Chat) -> None:
    # Given: VK responds to a profile lookup after the event has changed.
    await chat.open_event()

    async def change_event(**_kwargs: object) -> list[SimpleNamespace]:
        await chat.storage.close_registration()
        assert await chat.storage.begin_event(_START, "weekly")
        await chat.storage.activate_event(10, _START, conversation_message_id=20)
        return [SimpleNamespace(first_name="Alice")]

    chat.api.users.get.side_effect = change_event
    # When: the participant signs up while the delayed lookup is in flight.
    await chat.message("записаться")
    # Then: the new event stays empty and the participant learns the collection changed.
    assert (await chat.saved()).participants == ()
    assert "сбор изменился" in chat.updated_card("text")


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("создать событие 04.10.2026 19:00"))
async def test_admin_creates_manual_event(chat: Chat, command: str) -> None:
    # Given: prior attendance and a requested start before Monday's announcement.
    await chat.storage.add_friend("Previous")
    # When: the administrator creates the event through Long Poll.
    await chat.message(command)
    # Then: an empty open manual event and its displayed deadline are durably saved.
    saved = await chat.saved()
    assert saved.state == "open"
    assert saved.event_source == "manual"
    assert saved.event_starts_at == _START
    assert saved.participants == ()
    assert saved.status_conversation_message_id == 40
    chat.api.messages.send.assert_awaited_once()
    sent = chat.api.messages.send.await_args.kwargs
    assert sent["peer_ids"] == [chat.config.chat_peer_id]
    assert "19:00" in sent["message"]
    assert "запись открыта" in sent["message"]


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("очистить", "сбросить"))
async def test_admin_clears_event_participants(chat: Chat, command: str) -> None:
    # Given: an open event with multiple participants.
    await chat.open_event()
    await chat.storage.add_friend("Bob")
    await chat.storage.add_friend("Друг")
    # When: the administrator clears the participant list.
    await chat.message(command)
    # Then: the event stays open while attendance is empty in storage and its card.
    saved = await chat.saved()
    assert saved.participants == ()
    assert saved.state == "open"
    assert saved.event_starts_at == _START
    assert "Участники: 0" in chat.updated_card("button")
    chat.api.messages.send.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("убрать Bob", "удалить Bob"))
async def test_admin_removes_only_named_participant(chat: Chat, command: str) -> None:
    # Given: Bob and another participant share an open event.
    await chat.open_event()
    await chat.storage.add_friend("Bob")
    await chat.storage.add_user(456, "Alice")
    # When: the administrator removes Bob by name.
    await chat.message(command)
    # Then: Alice remains saved and visible while Bob alone is removed.
    assert [e.name for e in (await chat.saved()).participants] == ["Alice"]
    card = chat.updated_card("button")
    assert "Alice" in card
    assert "Bob" not in card


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("админ помощь", "admin help"))
async def test_admin_requests_private_help(chat: Chat, command: str) -> None:
    # Given: an existing event and an authorized administrator.
    await chat.open_event()
    before = await chat.saved()
    # When: the administrator requests command guidance.
    await chat.message(command)
    # Then: the command disappears and private guidance leaves the card unchanged.
    chat.api.messages.delete.assert_awaited_once_with(
        peer_id=chat.config.chat_peer_id, cmids=[1], delete_for_all=True
    )
    chat.api.messages.send.assert_awaited_once()
    reply = chat.api.messages.send.await_args.kwargs
    assert reply["peer_id"] == 123
    assert "Админ-команды" in reply["message"]
    assert await chat.saved() == before
    chat.api.messages.edit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("статус события"))
async def test_admin_requests_private_event_status(chat: Chat, command: str) -> None:
    # Given: a persisted manual event with a known deadline and card.
    await chat.open_event()
    before = await chat.saved()
    # When: the administrator requests its status.
    await chat.message(command)
    # Then: real event details arrive privately without changing the event.
    reply = chat.api.messages.send.await_args.kwargs
    assert reply["peer_id"] == 123
    assert "open" in reply["message"]
    assert "04.10.2026 19:00" in reply["message"]
    assert "cmid: 20" in reply["message"]
    assert await chat.saved() == before
    chat.api.messages.edit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("command", _spellings("удалить событие"))
async def test_admin_deletes_event_after_removing_command(
    chat: Chat, command: str
) -> None:
    # Given: an open event and a friend named exactly like the command's noun.
    await chat.open_event()
    await chat.storage.add_friend("событие")
    # When: the administrator deletes the event through the router.
    await chat.message(command)
    # Then: command and card are deleted in order and cancellation survives restart.
    assert [c.kwargs["cmids"] for c in chat.api.messages.delete.await_args_list] == [
        [1],
        [20],
    ]
    saved = await chat.saved()
    assert saved.state == "closed"
    assert saved.participants == ()
    assert saved.event_starts_at is None
    assert saved.status_message_id is None
    assert saved.status_conversation_message_id is None
    reply = chat.api.messages.send.await_args.kwargs
    assert reply["peer_id"] == 123
    assert "удалено" in reply["message"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    [
        "очистить",
        "убрать Bob",
        "админ помощь",
        "статус события",
        "удалить событие",
        "создать событие 04.10.2026 19:00",
    ],
)
@pytest.mark.parametrize(("admins", "requester"), [((123,), 999), ((), 123)])
async def test_unlisted_participant_is_denied_admin_command(
    chat: Chat, command: str, admins: tuple[int, ...], requester: int
) -> None:
    # Given: an event and a requester outside the configured admin list.
    chat.config.admin_vk_ids = admins
    await chat.open_event()
    await chat.storage.add_friend("Bob")
    before = await chat.saved()
    # When: that participant sends a protected command through the router.
    await chat.message(command, user_id=requester)
    # Then: private denial and command deletion leave the entire event untouched.
    assert await chat.saved() == before
    reply = chat.api.messages.send.await_args.kwargs
    assert reply["peer_id"] == requester
    assert "Только администраторы" in reply["message"]
    chat.api.messages.delete.assert_awaited_once_with(
        peer_id=chat.config.chat_peer_id, cmids=[1], delete_for_all=True
    )
    chat.api.messages.edit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    [
        "очистить",
        "убрать Bob",
        "админ помощь",
        "статус события",
        "удалить событие",
        "создать событие 04.10.2026 19:00",
        "записаться",
        "+ Друг",
        "список",
    ],
)
async def test_requester_in_another_chat_is_ignored(chat: Chat, command: str) -> None:
    # Given: an existing event in the configured chat.
    await chat.open_event()
    before = await chat.saved()
    # When: the same requester sends a command from another chat.
    await chat.message(command, peer_id=chat.config.chat_peer_id + 1)
    # Then: neither the saved event nor any VK response surface is touched.
    assert await chat.saved() == before
    assert chat.api.mock_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("label", ["Записаться", "Отписаться", "Помощь"])
async def test_participant_callback_in_another_chat_is_ignored(
    chat: Chat, label: str
) -> None:
    # Given: an existing event in the configured chat.
    await chat.open_event()
    before = await chat.saved()
    # When: a real callback payload arrives from a different chat.
    await chat.press(label, peer_id=chat.config.chat_peer_id + 1)
    # Then: the event is unchanged with no VK response or profile lookup.
    assert await chat.saved() == before
    assert chat.api.mock_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("command", "notice"),
    [
        ("Создать событие", "Формат:"),
        ("Создать событие завтра", "Формат:"),
        ("Создать событие 01.01.2000 00:00", "должно быть в будущем"),
        ("Создать событие 05.10.2026 08:00", "пересекается"),
    ],
)
async def test_admin_invalid_event_request_preserves_existing_data(
    chat: Chat, command: str, notice: str
) -> None:
    # Given: prior attendance that must survive an invalid event request.
    await chat.storage.add_friend("Bob")
    before = await chat.saved()
    # When: the administrator requests an invalid or overlapping deadline.
    await chat.message(command)
    # Then: a private error explains rejection and durable data stays unchanged.
    assert await chat.saved() == before
    reply = chat.api.messages.send.await_args.kwargs
    assert reply["peer_id"] == 123
    assert notice in reply["message"]


@pytest.mark.anyio
async def test_admin_cannot_create_event_over_active_registration(chat: Chat) -> None:
    # Given: another event is already accepting participants.
    await chat.open_event()
    before = await chat.saved()
    # When: the administrator requests another otherwise valid event.
    await chat.message("создать событие 04.10.2026 19:15")
    # Then: private rejection preserves the current event instead of replacing it.
    assert await chat.saved() == before
    reply = chat.api.messages.send.await_args.kwargs
    assert reply["peer_id"] == 123
    assert "активное событие" in reply["message"]


@pytest.mark.anyio
async def test_admin_private_help_delivery_failure_keeps_chat_private(
    chat: Chat,
) -> None:
    # Given: VK refuses private delivery to the administrator.
    await chat.open_event()
    before = await chat.saved()
    chat.api.messages.send.side_effect = OSError("private messages disabled")
    # When: the administrator requests help.
    await chat.message("админ помощь")
    # Then: the command is deleted without guidance leaking into the public card.
    assert await chat.saved() == before
    chat.api.messages.delete.assert_awaited_once()
    chat.api.messages.edit.assert_not_awaited()
    chat.api.messages.send.assert_awaited_once()
    assert chat.api.messages.send.await_args.kwargs["peer_id"] == 123


@pytest.mark.anyio
@pytest.mark.parametrize("refusal", ["network", "individual"])
async def test_admin_command_deletion_failure_still_performs_requested_action(
    chat: Chat, refusal: str
) -> None:
    # Given: attendance and a VK refusal to delete the administrator's command.
    await chat.open_event()
    await chat.storage.add_friend("Друг")
    if refusal == "network":
        chat.api.messages.delete.side_effect = OSError("missing administrator rights")
    else:
        chat.api.messages.delete.return_value = [
            MessagesDeleteFullResponseItem(message_id=1, response=False)
        ]
    # When: the administrator clears attendance despite that refusal.
    await chat.message("очистить")
    # Then: attendance is cleared and the deletion warning is delivered privately.
    assert (await chat.saved()).participants == ()
    assert "Участники: 0" in chat.updated_card("button")
    reply = chat.api.messages.send.await_args.kwargs
    assert reply["peer_id"] == 123
    assert "права администратора" in reply["message"]
