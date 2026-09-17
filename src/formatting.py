"""Formatting helpers for bot responses."""

import datetime
from typing import Final, assert_never

from src.storage import Entry, FriendEntry, UserEntry

_MAX_NAME_LENGTH: Final = 100
_HELP_TEXT: Final = (
    "Запись открывается после анонса и закрывается в момент начала события.\n"
    "Для себя — «записаться» или кнопка «✅ Записаться».\n"
    "Чтобы отписаться — «отписаться» или кнопка «↩️ Отписаться».\n"
    "`+` без имени подскажет, как записаться, но не добавит вас.\n"
    "+ Имя — записать друга, пока запись открыта.\n"
    "- Имя — убрать друга.\n"
    "📋 список — кто идёт.\n"
    "❓ ? — помощь"
)
_WEEKDAYS: Final = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


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
    """Return lifecycle-aware help for text and callback messages."""
    _ = compact
    return _HELP_TEXT


def format_event_start(value: datetime.datetime) -> str:
    """Format a local event start for chat messages."""
    return f"{_WEEKDAYS[value.weekday()]}, {value:%d.%m.%Y в %H:%M}"


def format_registration_card(
    entries: list[Entry],
    state: str,
    event_starts_at: datetime.datetime | None,
) -> str:
    """Build the canonical registration card."""
    label = "Запись открыта" if state == "open" else "Запись закрыта"
    event_line = (
        f"\n🗓 Начало: {format_event_start(event_starts_at)}"
        if event_starts_at is not None
        else ""
    )
    return (
        f"🏐 {label} · Участников: {len(entries)}{event_line}\n\n"
        f"{format_entries(entries)}"
    )
