"""VK bot message handlers."""

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Final, TypeVar

from vkbottle import GroupEventType, VKAPIError
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle.dispatch.rules.base import PayloadRule, RegexRule

from src.config import Config
from src.formatting import format_entries, help_text
from src.keyboard import build_inline_keyboard
from src.storage import Storage

_LOGGER: Final = logging.getLogger(__name__)
_EventT = TypeVar("_EventT", Message, MessageEvent)


def extract_friend_name(text: str) -> str:
    """Extract and trim the name following a friend command sign."""
    return text[1:].strip()


def _admin_help_text() -> str:
    """Return the admin command help text."""
    return (
        "Админ-команды:\n"
        "очистить / сбросить — очистить список участников\n"
        "убрать Имя / удалить Имя — удалить участника по имени\n"
        "админ помощь — показать эту справку"
    )


def setup_handlers(bot: Bot, storage: Storage, config: Config) -> None:  # noqa: C901,PLR0915
    """Register message handlers on the bot instance."""
    inline_keyboard = build_inline_keyboard()

    def target_peer_only(
        handler: Callable[[_EventT], Awaitable[None]],
    ) -> Callable[[_EventT], Awaitable[None]]:
        @wraps(handler)
        async def guarded(event: _EventT) -> None:
            if event.peer_id == config.chat_peer_id:
                await handler(event)

        return guarded

    @bot.on.message(text=["+", "записаться"])
    @target_peer_only
    async def sign_up(msg: Message) -> None:
        # Duplicates cb_join — keep in sync.
        try:
            _users = await bot.api.users.get(user_ids=[msg.from_id])
        except VKAPIError:
            _LOGGER.exception("VK API error fetching user %s", msg.from_id)
            _ = await msg.answer(
                "Не удалось получить данные из VK. Попробуй позже.",
                keyboard=inline_keyboard,
            )
            return
        name = (_users[0].first_name or "Unknown") if _users else "Unknown"
        try:
            if await storage.add_user(msg.from_id, name):
                _ = await msg.answer("Ты записался!", keyboard=inline_keyboard)
            else:
                _ = await msg.answer("Ты уже в списке.", keyboard=inline_keyboard)
        except ValueError as exc:
            _ = await msg.answer(str(exc), keyboard=inline_keyboard)

    @bot.on.message(text=["-", "отписаться"])
    @target_peer_only
    async def sign_off(msg: Message) -> None:
        # Duplicates cb_leave — keep in sync.
        if await storage.remove_user(msg.from_id):
            _ = await msg.answer("Ты отписался.", keyboard=inline_keyboard)
        else:
            _ = await msg.answer("Тебя не было в списке.", keyboard=inline_keyboard)

    @bot.on.message(RegexRule(r"^\+\s*(.+)$"))
    @target_peer_only
    async def add_friend(msg: Message) -> None:
        name = extract_friend_name(msg.text or "")
        if not name:
            _ = await msg.answer(
                "Укажи имя друга: + Имя",
                keyboard=inline_keyboard,
            )
            return
        try:
            await storage.add_friend(name)
            _LOGGER.debug("Friend added: %s", name)
            _ = await msg.answer(
                f"{name} записан(а) как друг.",
                keyboard=inline_keyboard,
            )
        except ValueError as exc:
            _ = await msg.answer(str(exc), keyboard=inline_keyboard)

    @bot.on.message(RegexRule(r"^-\s*(.+)$"))
    @target_peer_only
    async def remove_friend(msg: Message) -> None:
        name = extract_friend_name(msg.text or "")
        if await storage.remove_friend(name):
            _LOGGER.debug("Friend removed: %s", name)
            _ = await msg.answer(
                f"{name} убран(а) из списка.",
                keyboard=inline_keyboard,
            )
        else:
            _ = await msg.answer(
                "Такого друга не нашлось.",
                keyboard=inline_keyboard,
            )

    @bot.on.message(text=["список", "участники", "кто идёт"])
    @target_peer_only
    async def show_list(msg: Message) -> None:
        # Duplicates cb_list — keep in sync.
        entries = await storage.list_entries()
        _LOGGER.debug("List requested, %d entries", len(entries))
        _ = await msg.answer(
            format_entries(entries),
            keyboard=inline_keyboard,
        )

    @bot.on.message(text=["?", "help", "помощь", "команды"])
    @target_peer_only
    async def help_cmd(msg: Message) -> None:
        _ = await msg.answer(help_text(), keyboard=inline_keyboard)

    # --- Callback handlers for inline keyboard ---

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "join"}),
    )
    @target_peer_only
    async def cb_join(event: MessageEvent) -> None:
        # Duplicates sign_up — keep in sync.
        try:
            _users = await bot.api.users.get(user_ids=[event.user_id])
        except VKAPIError:
            _LOGGER.exception("VK API error fetching user %s", event.user_id)
            _ = await event.show_snackbar(
                "Не удалось получить данные из VK. Попробуй позже."
            )
            return
        name = (_users[0].first_name or "Unknown") if _users else "Unknown"
        try:
            if await storage.add_user(event.user_id, name):
                _ = await event.show_snackbar("Ты записался!")
            else:
                _ = await event.show_snackbar("Ты уже в списке.")
        except ValueError as exc:
            _ = await event.show_snackbar(str(exc))

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "leave"}),
    )
    @target_peer_only
    async def cb_leave(event: MessageEvent) -> None:
        # Duplicates sign_off — keep in sync.
        if await storage.remove_user(event.user_id):
            _ = await event.show_snackbar("Ты отписался.")
        else:
            _ = await event.show_snackbar("Тебя не было в списке.")

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "list"}),
    )
    @target_peer_only
    async def cb_list(event: MessageEvent) -> None:
        # Duplicates show_list — keep in sync.
        entries = await storage.list_entries()
        _ = await event.send_message(
            message=format_entries(entries),
            keyboard=inline_keyboard,
        )

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "help"}),
    )
    @target_peer_only
    async def cb_help(event: MessageEvent) -> None:
        _ = await event.send_message(
            message=help_text(compact=True),
            keyboard=inline_keyboard,
        )

    # --- Admin handlers ---

    @bot.on.message(text=["очистить", "сбросить"])
    @target_peer_only
    async def admin_clear(msg: Message) -> None:
        if not config.is_admin(msg.from_id):
            _LOGGER.warning("Unauthorized clear attempt from %s", msg.from_id)
            _ = await msg.answer(
                "Только администраторы могут использовать эту команду.",
                keyboard=inline_keyboard,
            )
            return
        await storage.clear()
        _LOGGER.info("List cleared by admin %s", msg.from_id)
        _ = await msg.answer(
            "Список участников очищен.",
            keyboard=inline_keyboard,
        )

    @bot.on.message(RegexRule(r"^(?:убрать|удалить)\s+(.+)$"))
    @target_peer_only
    async def admin_remove(msg: Message) -> None:
        if not config.is_admin(msg.from_id):
            _LOGGER.warning("Unauthorized remove attempt from %s", msg.from_id)
            _ = await msg.answer(
                "Только администраторы могут использовать эту команду.",
                keyboard=inline_keyboard,
            )
            return
        name = (msg.text or "").split(maxsplit=1)[1].strip()
        if await storage.remove_by_name(name):
            _LOGGER.info("Admin %s removed %s", msg.from_id, name)
            _ = await msg.answer(
                f"{name} убран(а) из списка.",
                keyboard=inline_keyboard,
            )
        else:
            _ = await msg.answer(
                "Такого участника не нашлось.",
                keyboard=inline_keyboard,
            )

    @bot.on.message(text=["админ помощь", "admin help"])
    @target_peer_only
    async def admin_help(msg: Message) -> None:
        if not config.is_admin(msg.from_id):
            _LOGGER.warning("Unauthorized admin_help attempt from %s", msg.from_id)
            _ = await msg.answer(
                "Только администраторы могут использовать эту команду.",
                keyboard=inline_keyboard,
            )
            return
        _ = await msg.answer(
            _admin_help_text(),
            keyboard=inline_keyboard,
        )
