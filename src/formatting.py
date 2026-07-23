"""Formatting helpers for bot responses."""

from typing import Final, assert_never

from src.storage import Entry, FriendEntry, UserEntry

_MAX_NAME_LENGTH: Final = 100


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
