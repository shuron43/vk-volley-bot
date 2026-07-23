"""VK bot message handlers."""

import logging
from typing import Final, assert_never

from vkbottle import GroupEventType, VKAPIError
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle.dispatch.rules.base import PayloadRule, RegexRule

from src.keyboard import build_inline_keyboard
from src.storage import Entry, FriendEntry, Storage, UserEntry

_LOGGER: Final = logging.getLogger(__name__)


def format_entries(entries: list[Entry]) -> str:
    """Format the participant list for a bot response."""
    if not entries:
        return "Пока никто не записался."

    lines: list[str] = []
    for index, entry in enumerate(entries, 1):
        match entry:
            case UserEntry():
                lines.append(f"{index}. {entry.name}")
            case FriendEntry():
                lines.append(f"{index}. {entry.name} (друг)")
            case unreachable:  # type: ignore[reportUnnecessaryComparison]
                assert_never(unreachable)
    return "Список участников:\n" + "\n".join(lines)


def help_text(*, compact: bool = False) -> str:
    """Return the message-command help text."""
    if compact:
        return (
            "Команды:\n"
            "➕ — записаться\n"
            "➖ — отписаться\n"
            "+ Имя — записать друга\n"
            "- Имя — убрать друга\n"
            "📋 — список\n"
            "❓ — помощь"
        )
    return (
        "Команды:\n"
        "➕ или записаться — записаться\n"
        "➖ или отписаться — отписаться\n"
        "+ Имя — записать друга\n"
        "- Имя — убрать друга\n"
        "📋 список — кто идёт\n"
        "❓ ? — помощь"
    )


def extract_friend_name(text: str) -> str:
    """Extract and trim the name following a friend command sign."""
    return text[1:].strip()


def setup_handlers(bot: Bot, storage: Storage) -> None:  # noqa: C901,PLR0915
    """Register message handlers on the bot instance."""
    inline_keyboard = build_inline_keyboard()

    @bot.on.message(text=["+", "записаться"])
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
    async def sign_off(msg: Message) -> None:
        # Duplicates cb_leave — keep in sync.
        if await storage.remove_user(msg.from_id):
            _ = await msg.answer("Ты отписался.", keyboard=inline_keyboard)
        else:
            _ = await msg.answer("Тебя не было в списке.", keyboard=inline_keyboard)

    @bot.on.message(RegexRule(r"^\+\s*(.+)$"))
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
    async def show_list(msg: Message) -> None:
        # Duplicates cb_list — keep in sync.
        entries = await storage.list_entries()
        _LOGGER.debug("List requested, %d entries", len(entries))
        _ = await msg.answer(
            format_entries(entries),
            keyboard=inline_keyboard,
        )

    @bot.on.message(text=["?", "help", "помощь", "команды"])
    async def help_cmd(msg: Message) -> None:
        _ = await msg.answer(help_text(), keyboard=inline_keyboard)

    # --- Callback handlers for inline keyboard ---

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "join"}),
    )
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
    async def cb_help(event: MessageEvent) -> None:
        _ = await event.send_message(
            message=help_text(compact=True),
            keyboard=inline_keyboard,
        )
