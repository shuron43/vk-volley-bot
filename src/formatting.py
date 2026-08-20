"""Formatting helpers for bot responses."""

from typing import Final, assert_never

from src.storage import Entry, FriendEntry, UserEntry

_MAX_NAME_LENGTH: Final = 100
_HELP_TEXT: Final = (
    "Запись открывается после анонса сбора.\n"
    "Для себя — «записаться» или кнопка «✅ Записаться».\n"
    "Чтобы отписаться — «отписаться» или кнопка «↩️ Отписаться».\n"
    "`+` без имени подскажет, как записаться, но не добавит вас.\n"
    "+ Имя — записать друга, пока запись открыта.\n"
    "- Имя — убрать друга.\n"
    "📋 список — кто идёт.\n"
    "❓ ? — помощь"
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
