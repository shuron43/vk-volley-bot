"""VK bot message handlers."""

import asyncio
import datetime
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Final, TypeVar

from vkbottle import GroupEventType, VKAPIError
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle.dispatch.rules.base import PayloadRule, RegexRule

from src.config import Config
from src.formatting import format_registration_card, help_text
from src.keyboard import build_inline_keyboard
from src.scheduler import open_manual_event
from src.storage import Storage

_LOGGER: Final = logging.getLogger(__name__)
_EventT = TypeVar("_EventT", Message, MessageEvent)


async def temporary_answer(msg: Message, message: str, *, keyboard: str) -> None:
    """Show feedback briefly, then delete only the bot's own reply for everyone."""
    sent = await msg.answer(message, keyboard=keyboard)
    message_id = sent.message_id
    cmid = sent.conversation_message_id
    has_cmid = type(cmid) is int and cmid > 0
    if not has_cmid and (type(message_id) is not int or message_id <= 0):
        return
    await asyncio.sleep(10)
    try:
        if type(cmid) is int and cmid > 0:
            _ = await msg.ctx_api.messages.delete(
                peer_id=msg.peer_id,
                cmids=[cmid],
                delete_for_all=True,
            )
        elif type(message_id) is int and message_id > 0:
            _ = await msg.ctx_api.messages.delete(
                message_ids=[message_id],
                delete_for_all=True,
            )
    except (VKAPIError, OSError, TimeoutError):
        _LOGGER.warning("Could not delete temporary bot reply %s", message_id)


def extract_friend_name(text: str) -> str:
    """Extract and trim the name following a friend command sign."""
    return text[1:].strip()


def parse_event_start(text: str) -> datetime.datetime:
    """Parse the date and time from an administrator event command."""
    value = text.removeprefix("создать событие").strip()
    try:
        return datetime.datetime.strptime(value, "%d.%m.%Y %H:%M")  # noqa: DTZ007
    except ValueError as exc:
        message = "Формат: создать событие ДД.ММ.ГГГГ ЧЧ:ММ"
        raise ValueError(message) from exc


def _admin_help_text() -> str:
    """Return the admin command help text."""
    return (
        "Админ-команды:\n"
        "очистить / сбросить — очистить список участников\n"
        "убрать Имя / удалить Имя — удалить участника по имени\n"
        "создать событие ДД.ММ.ГГГГ ЧЧ:ММ — открыть ручную запись\n"
        "админ помощь — показать эту справку"
    )


def setup_handlers(bot: Bot, storage: Storage, config: Config) -> None:  # noqa: C901,PLR0915
    """Register message handlers on the bot instance."""
    inline_keyboard = build_inline_keyboard()
    status_lock = asyncio.Lock()
    registration_closed_message = (
        "Запись закрыта. Дождись следующего анонса или нажми «Помощь»."
    )
    bare_plus_instruction = (
        "Чтобы записаться, нажми «Записаться» или напиши «записаться»"
    )
    bare_plus_message = f"{bare_plus_instruction} после анонса. Для друга: + Имя."

    async def join_user(vk_id: int) -> tuple[str, bool]:
        """Register a VK user and return feedback with its mutation result."""
        collection = await storage.active_collection()
        if collection is None:
            return registration_closed_message, False
        try:
            _users = await bot.api.users.get(user_ids=[vk_id])
        except VKAPIError:
            _LOGGER.exception("VK API error fetching user %s", vk_id)
            return "Не удалось получить данные из VK. Попробуй позже.", False
        name = (_users[0].first_name or "Unknown") if _users else "Unknown"
        try:
            added = await storage.add_user(vk_id, name, expected_collection=collection)
        except ValueError as exc:
            return str(exc), False
        if added:
            await refresh_canonical_status()
            _LOGGER.info("User %s (%s) signed up", vk_id, name)
            return "Ты записался!", True
        return "Ты уже в списке.", False

    async def leave_user(vk_id: int) -> tuple[str, bool]:
        """Remove a VK user and return feedback with its mutation result."""
        if await storage.remove_user(vk_id):
            await refresh_canonical_status()
            _LOGGER.info("User %s signed off", vk_id)
            return "Ты отписался.", True
        return "Тебя не было в списке.", False

    async def add_friend_by_name(name: str) -> str:
        """Register a friend and return the result message."""
        collection = await storage.active_collection()
        if collection is None:
            return registration_closed_message
        try:
            await storage.add_friend(name, expected_collection=collection)
        except ValueError as exc:
            return str(exc)
        await refresh_canonical_status()
        _LOGGER.info("Friend added: %s", name)
        return f"{name} записан(а) как друг."

    async def remove_friend_by_name(name: str) -> str:
        """Remove a friend and return the result message."""
        if await storage.remove_friend(name):
            await refresh_canonical_status()
            _LOGGER.info("Friend removed: %s", name)
            return f"{name} убран(а) из списка."
        return "Такого друга не нашлось."

    async def refresh_canonical_status() -> None:
        """Refresh the persisted registration status message when it is known."""
        async with status_lock:
            await edit_canonical_status()

    async def card_text() -> str:
        """Build a visible registration card, including the empty state."""
        entries = await storage.list_entries()
        state = await storage.registration_state()
        event_starts_at, _ = await storage.event_details()
        return format_registration_card(entries, state, event_starts_at)

    async def delete_previous_card(message_id: int | None, cmid: int | None) -> None:
        """Remove the previous bot card only after its replacement is durable."""
        try:
            if cmid is not None and cmid > 0:
                _ = await bot.api.messages.delete(
                    peer_id=config.chat_peer_id,
                    cmids=[cmid],
                    delete_for_all=True,
                )
            elif message_id is not None and message_id > 0:
                _ = await bot.api.messages.delete(
                    message_ids=[message_id],
                    delete_for_all=True,
                )
        except (VKAPIError, OSError, TimeoutError):
            _LOGGER.warning("Could not delete previous participant card")

    async def edit_canonical_status() -> None:
        """Read and publish the latest list while holding the status lock."""
        message_id = await storage.status_message_id()
        cmid = await storage.status_conversation_message_id()
        if not message_id and not cmid:
            return
        try:
            _ = await bot.api.messages.edit(
                peer_id=config.chat_peer_id,
                message_id=message_id if not cmid else None,
                conversation_message_id=cmid,
                message=await card_text(),
                keyboard=inline_keyboard,
            )
        except (VKAPIError, OSError, TimeoutError):
            _LOGGER.exception("Failed to refresh canonical status message")

    def target_peer_only(
        handler: Callable[[_EventT], Awaitable[None]],
    ) -> Callable[[_EventT], Awaitable[None]]:
        @wraps(handler)
        async def guarded(event: _EventT) -> None:
            if event.peer_id != config.chat_peer_id:
                _LOGGER.info(
                    "Ignored %s from unexpected peer_id=%s (configured %s)",
                    type(event).__name__,
                    event.peer_id,
                    config.chat_peer_id,
                )
                return
            _LOGGER.debug(
                "Received %s from peer_id=%s",
                type(event).__name__,
                event.peer_id,
            )
            await handler(event)

        return guarded

    @bot.on.message(text=["записаться"])
    @target_peer_only
    async def sign_up(msg: Message) -> None:
        # Shares join_user with cb_join.
        response, _ = await join_user(msg.from_id)
        await temporary_answer(
            msg,
            response,
            keyboard=inline_keyboard,
        )

    @bot.on.message(text=["+"])
    @bot.on.message(RegexRule(r"^\+\s+$"))
    @target_peer_only
    async def bare_plus(msg: Message) -> None:
        await temporary_answer(
            msg,
            bare_plus_message,
            keyboard=inline_keyboard,
        )

    @bot.on.message(text=["-", "отписаться"])
    @target_peer_only
    async def sign_off(msg: Message) -> None:
        # Shares leave_user with cb_leave.
        response, _ = await leave_user(msg.from_id)
        await temporary_answer(
            msg,
            response,
            keyboard=inline_keyboard,
        )

    @bot.on.message(RegexRule(r"^\+\s*(.+)$"))
    @target_peer_only
    async def add_friend(msg: Message) -> None:
        name = extract_friend_name(msg.text or "")
        if not name:
            await temporary_answer(
                msg,
                "Укажи имя друга: + Имя",
                keyboard=inline_keyboard,
            )
            return
        await temporary_answer(
            msg,
            await add_friend_by_name(name),
            keyboard=inline_keyboard,
        )

    @bot.on.message(RegexRule(r"^-\s*(.+)$"))
    @target_peer_only
    async def remove_friend(msg: Message) -> None:
        name = extract_friend_name(msg.text or "")
        await temporary_answer(
            msg,
            await remove_friend_by_name(name),
            keyboard=inline_keyboard,
        )

    @bot.on.message(text=["список", "участники", "кто идёт"])
    @target_peer_only
    async def show_list(msg: Message) -> None:
        async with status_lock:
            old_id = await storage.status_message_id()
            old_cmid = await storage.status_conversation_message_id()
            sent = await msg.answer(await card_text(), keyboard=inline_keyboard)
            message_id = sent.message_id if type(sent.message_id) is int else None
            cmid = sent.conversation_message_id
            cmid = cmid if type(cmid) is int else None
            if not message_id and not cmid:
                return
            await storage.set_status_message_id(
                message_id, conversation_message_id=cmid
            )
            if (old_id, old_cmid) != (message_id, cmid):
                await delete_previous_card(old_id, old_cmid)

    @bot.on.message(text=["?", "help", "помощь", "команды"])
    @target_peer_only
    async def help_cmd(msg: Message) -> None:
        await temporary_answer(msg, help_text(), keyboard=inline_keyboard)

    # --- Callback handlers for inline keyboard ---

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "join"}),
    )
    @target_peer_only
    async def cb_join(event: MessageEvent) -> None:
        # Shares join_user with sign_up.
        response, _ = await join_user(event.user_id)
        _ = await event.show_snackbar(response)

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "leave"}),
    )
    @target_peer_only
    async def cb_leave(event: MessageEvent) -> None:
        # Shares leave_user with sign_off.
        response, _ = await leave_user(event.user_id)
        _ = await event.show_snackbar(response)

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "list"}),
    )
    @target_peer_only
    async def cb_list(event: MessageEvent) -> None:
        async with status_lock:
            try:
                _ = await event.edit_message(
                    message=await card_text(),
                    keyboard=inline_keyboard,
                )
            except (VKAPIError, OSError, TimeoutError):
                _ = await event.show_snackbar("Напиши список — покажу новую карточку.")
                return
        _ = await event.show_snackbar("Участники показаны в этом сообщении.")

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "help"}),
    )
    @target_peer_only
    async def cb_help(event: MessageEvent) -> None:
        _ = await event.show_snackbar(
            "Запись — кнопкой; друг: + Имя; выход: -; убрать: - Имя. Справка: помощь."
        )

    # --- Admin handlers ---

    @bot.on.message(RegexRule(r"^создать событие(?:\s+.*)?$"))
    @target_peer_only
    async def admin_create_event(msg: Message) -> None:
        if not config.is_admin(msg.from_id):
            _LOGGER.warning("Unauthorized create_event attempt from %s", msg.from_id)
            await temporary_answer(
                msg,
                "Только администраторы могут использовать эту команду.",
                keyboard=inline_keyboard,
            )
            return
        try:
            event_starts_at = parse_event_start(msg.text or "")
            await open_manual_event(bot.api, config, storage, event_starts_at)
        except ValueError as exc:
            await temporary_answer(msg, str(exc), keyboard=inline_keyboard)
            return
        _LOGGER.info("Manual event created by admin %s", msg.from_id)
        await temporary_answer(
            msg,
            "Событие создано, запись открыта до начала.",
            keyboard=inline_keyboard,
        )

    @bot.on.message(text=["очистить", "сбросить"])
    @target_peer_only
    async def admin_clear(msg: Message) -> None:
        if not config.is_admin(msg.from_id):
            _LOGGER.warning("Unauthorized clear attempt from %s", msg.from_id)
            await temporary_answer(
                msg,
                "Только администраторы могут использовать эту команду.",
                keyboard=inline_keyboard,
            )
            return
        await storage.clear()
        await refresh_canonical_status()
        _LOGGER.info("List cleared by admin %s", msg.from_id)
        await temporary_answer(
            msg,
            "Список участников очищен.",
            keyboard=inline_keyboard,
        )

    @bot.on.message(RegexRule(r"^(?:убрать|удалить)\s+(.+)$"))
    @target_peer_only
    async def admin_remove(msg: Message) -> None:
        if not config.is_admin(msg.from_id):
            _LOGGER.warning("Unauthorized remove attempt from %s", msg.from_id)
            await temporary_answer(
                msg,
                "Только администраторы могут использовать эту команду.",
                keyboard=inline_keyboard,
            )
            return
        name = (msg.text or "").split(maxsplit=1)[1].strip()
        if await storage.remove_by_name(name):
            await refresh_canonical_status()
            _LOGGER.info("Admin %s removed %s", msg.from_id, name)
            await temporary_answer(
                msg,
                f"{name} убран(а) из списка.",
                keyboard=inline_keyboard,
            )
        else:
            await temporary_answer(
                msg,
                "Такого участника не нашлось.",
                keyboard=inline_keyboard,
            )

    @bot.on.message(text=["админ помощь", "admin help"])
    @target_peer_only
    async def admin_help(msg: Message) -> None:
        if not config.is_admin(msg.from_id):
            _LOGGER.warning("Unauthorized admin_help attempt from %s", msg.from_id)
            await temporary_answer(
                msg,
                "Только администраторы могут использовать эту команду.",
                keyboard=inline_keyboard,
            )
            return
        await temporary_answer(
            msg,
            _admin_help_text(),
            keyboard=inline_keyboard,
        )
