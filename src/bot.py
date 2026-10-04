"""VK bot message handlers."""

import datetime
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Final, TypeVar

from vkbottle import GroupEventType, VKAPIError
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle.dispatch.rules.base import PayloadRule, RegexRule

from src.cards import CardPublisher, check_delete_response
from src.config import Config
from src.formatting import help_text
from src.scheduler import open_manual_event
from src.storage import Storage

_LOGGER: Final = logging.getLogger(__name__)
_EventT = TypeVar("_EventT", Message, MessageEvent)


def extract_friend_name(text: str) -> str:
    """Extract and trim the name following a friend command sign."""
    return text.strip()[1:].strip()


def parse_event_start(text: str) -> datetime.datetime:
    """Parse the date and time from an administrator event command."""
    value = re.sub(
        r"^\s*создать\s+событие\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip()
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
        "удалить событие — отменить событие и удалить карточку\n"
        "статус события — показать состояние и ID карточки\n"
        "админ помощь — показать эту справку"
    )


def setup_handlers(  # noqa: C901,PLR0915
    bot: Bot,
    storage: Storage,
    config: Config,
    cards: CardPublisher,
) -> None:
    """Register message handlers on the bot instance."""
    publisher = cards
    registration_closed_message = (
        "Запись закрыта. Дождись следующего анонса или нажми «Помощь»."
    )
    bare_plus_instruction = (
        "Чтобы записаться, нажми «Записаться» или напиши «записаться»"
    )
    bare_plus_message = f"{bare_plus_instruction} после анонса. Для друга: + Имя."

    async def join_user(vk_id: int) -> tuple[str, bool]:
        """Register a VK user and return feedback with its mutation result."""
        try:
            if await storage.restore_user(vk_id):
                return "Ты снова в списке! Отметка (-) снята.", True
        except ValueError as exc:
            return str(exc), False
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
            _LOGGER.info("User %s (%s) signed up", vk_id, name)
        return ("Ты записался!" if added else "Ты уже в списке."), added

    async def leave_user(vk_id: int) -> tuple[str, bool]:
        """Remove a VK user and return feedback with its mutation result."""
        try:
            removed = await storage.withdraw_user(vk_id)
        except ValueError as exc:
            return str(exc), False
        if removed:
            _LOGGER.info("User %s signed off", vk_id)
            return "Ты отписался.", True
        return "Тебя нет среди записанных участников.", False

    async def add_friend_by_name(name: str) -> tuple[str, bool]:
        """Register a friend and return the result message."""
        try:
            if await storage.restore_friend(name):
                return f"{name} снова в списке. Отметка (-) снята.", True
        except ValueError as exc:
            return str(exc), False
        collection = await storage.active_collection()
        if collection is None:
            return registration_closed_message, False
        try:
            await storage.add_friend(name, expected_collection=collection)
        except ValueError as exc:
            return str(exc), False
        _LOGGER.info("Friend added: %s", name)
        return f"{name} записан(а) как друг.", True

    async def remove_friend_by_name(name: str) -> tuple[str, bool]:
        """Remove a friend and return the result message."""
        try:
            removed = await storage.withdraw_friend(name)
        except ValueError as exc:
            return str(exc), False
        if removed:
            _LOGGER.info("Friend removed: %s", name)
            return f"{name} убран(а) из списка.", True
        return "Такого друга нет среди записанных участников.", False

    async def publish_text_result(response: str, *, changed: bool) -> None:
        """Move the only card last; show errors inside it without extra replies."""
        _ = await publisher.replace_current(notice=None if changed else response)

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

    @bot.on.message(RegexRule(re.compile(r"^\s*записаться\s*$", re.IGNORECASE)))
    @target_peer_only
    async def sign_up(msg: Message) -> None:
        # Shares join_user with cb_join.
        response, changed = await join_user(msg.from_id)
        await publish_text_result(response, changed=changed)

    @bot.on.message(RegexRule(r"^\s*\+\s*$"))
    @target_peer_only
    async def bare_plus(msg: Message) -> None:
        _ = msg
        await publish_text_result(bare_plus_message, changed=False)

    @bot.on.message(RegexRule(re.compile(r"^\s*(?:-|отписаться)\s*$", re.IGNORECASE)))
    @target_peer_only
    async def sign_off(msg: Message) -> None:
        # Shares leave_user with cb_leave.
        response, changed = await leave_user(msg.from_id)
        await publish_text_result(response, changed=changed)

    @bot.on.message(RegexRule(r"^\s*\+\s*(\S.*?)\s*$"))
    @target_peer_only
    async def add_friend(msg: Message) -> None:
        name = extract_friend_name(msg.text or "")
        if not name:
            await publish_text_result("Укажи имя друга: + Имя", changed=False)
            return
        response, changed = await add_friend_by_name(name)
        await publish_text_result(response, changed=changed)

    @bot.on.message(RegexRule(r"^\s*-\s*(\S.*?)\s*$"))
    @target_peer_only
    async def remove_friend(msg: Message) -> None:
        name = extract_friend_name(msg.text or "")
        response, changed = await remove_friend_by_name(name)
        await publish_text_result(response, changed=changed)

    @bot.on.message(
        RegexRule(re.compile(r"^\s*(?:список|участники|кто\s+идёт)\s*$", re.IGNORECASE))
    )
    @target_peer_only
    async def show_list(msg: Message) -> None:
        _ = msg
        _ = await publisher.replace_current()

    @bot.on.message(
        RegexRule(re.compile(r"^\s*(?:\?|help|помощь|команды)\s*$", re.IGNORECASE))
    )
    @target_peer_only
    async def help_cmd(msg: Message) -> None:
        _ = msg
        await publish_text_result(help_text(), changed=False)

    # --- Callback handlers for inline keyboard ---

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "join"}),
    )
    @target_peer_only
    async def cb_join(event: MessageEvent) -> None:
        # Shares join_user with sign_up.
        response, changed = await join_user(event.user_id)
        if changed:
            _ = await publisher.edit_current()
        _ = await event.show_snackbar(response)

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "leave"}),
    )
    @target_peer_only
    async def cb_leave(event: MessageEvent) -> None:
        # Shares leave_user with sign_off.
        response, changed = await leave_user(event.user_id)
        if changed:
            _ = await publisher.edit_current()
        _ = await event.show_snackbar(response)

    @bot.on.raw_event(
        GroupEventType.MESSAGE_EVENT,
        MessageEvent,
        PayloadRule({"cmd": "help"}),
    )
    @target_peer_only
    async def cb_help(event: MessageEvent) -> None:
        _ = await event.show_snackbar(
            "список — показать актуальную карточку в конце чата. Справка: помощь."
        )

    # --- Admin handlers ---

    async def private_admin_reply(msg: Message, text: str) -> None:
        """Keep administrative feedback out of the conversation card."""
        try:
            _ = await bot.api.messages.send(
                peer_id=msg.from_id,
                message=text,
                random_id=secrets.randbelow(2_147_483_646) + 1,
            )
        except (OSError, TimeoutError, VKAPIError):
            _LOGGER.exception("Could not send private admin reply to %s", msg.from_id)

    def admin_command(
        handler: Callable[[Message], Awaitable[None]],
    ) -> Callable[[Message], Awaitable[None]]:
        @wraps(handler)
        async def handled(msg: Message) -> None:
            try:
                result = await bot.api.messages.delete(
                    peer_id=msg.peer_id,
                    cmids=[msg.conversation_message_id],
                    delete_for_all=True,
                )
                check_delete_response(result)
            except (OSError, TimeoutError, VKAPIError):
                _LOGGER.exception("Could not delete admin command in %s", msg.peer_id)
                await private_admin_reply(
                    msg,
                    "Команда не удалена. Выдай боту права администратора беседы.",
                )
            if not config.is_admin(msg.from_id):
                _LOGGER.warning(
                    "Unauthorized %s attempt from %s", handler.__name__, msg.from_id
                )
                await private_admin_reply(
                    msg, "Только администраторы могут использовать эту команду."
                )
                return
            try:
                await handler(msg)
            except (OSError, TimeoutError, VKAPIError) as exc:
                _LOGGER.exception("Admin command %s failed", handler.__name__)
                await private_admin_reply(msg, f"Не удалось выполнить команду: {exc}")

        return handled

    @bot.on.message(
        RegexRule(re.compile(r"^\s*создать\s+событие(?:\s+.*)?\s*$", re.IGNORECASE))
    )
    @target_peer_only
    @admin_command
    async def admin_create_event(msg: Message) -> None:
        try:
            event_starts_at = parse_event_start(msg.text or "")
            await open_manual_event(
                config,
                storage,
                event_starts_at,
                cards=publisher,
            )
        except ValueError as exc:
            await private_admin_reply(msg, str(exc))
            return
        _LOGGER.info("Manual event created by admin %s", msg.from_id)

    @bot.on.message(
        RegexRule(re.compile(r"^\s*(?:очистить|сбросить)\s*$", re.IGNORECASE))
    )
    @target_peer_only
    @admin_command
    async def admin_clear(msg: Message) -> None:
        await storage.clear()
        _LOGGER.info("List cleared by admin %s", msg.from_id)
        _ = await publisher.edit_current()

    @bot.on.message(RegexRule(re.compile(r"^\s*удалить\s+событие\s*$", re.IGNORECASE)))
    @target_peer_only
    @admin_command
    async def admin_delete_event(msg: Message) -> None:
        existed = await publisher.delete_event()
        await private_admin_reply(
            msg, "Событие удалено." if existed else "Нет события для удаления."
        )

    @bot.on.message(
        RegexRule(re.compile(r"^\s*(?:убрать|удалить)\s+(.+?)\s*$", re.IGNORECASE))
    )
    @target_peer_only
    @admin_command
    async def admin_remove(msg: Message) -> None:
        name = (msg.text or "").split(maxsplit=1)[1].strip()
        if await storage.remove_by_name(name):
            _LOGGER.info("Admin %s removed %s", msg.from_id, name)
            _ = await publisher.edit_current()
        else:
            await private_admin_reply(msg, "Такого участника не нашлось.")

    @bot.on.message(RegexRule(re.compile(r"^\s*статус\s+события\s*$", re.IGNORECASE)))
    @target_peer_only
    @admin_command
    async def admin_event_status(msg: Message) -> None:
        await private_admin_reply(msg, await publisher.diagnostic_notice())

    @bot.on.message(
        RegexRule(re.compile(r"^\s*(?:админ\s+помощь|admin\s+help)\s*$", re.IGNORECASE))
    )
    @target_peer_only
    @admin_command
    async def admin_help(msg: Message) -> None:
        await private_admin_reply(msg, _admin_help_text())
