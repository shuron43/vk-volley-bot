"""Formatting helpers for bot responses."""

import datetime
from collections.abc import Sequence
from typing import Final, assert_never

from src.storage import Entry, FriendEntry, RegistrationState, UserEntry

_HELP_TEXT: Final = (
    "Запись открывается после анонса и закрывается в момент начала события.\n"
    "Для себя — «записаться» или кнопка «✅ Записаться».\n"
    "Чтобы отписаться — «отписаться» или кнопка «↩️ Отписаться».\n"
    "После начала отписка и возврат доступны только первые 30 минут.\n"
    "При отписке имя остаётся внизу с (-); повторная запись снимает отметку.\n"
    "Это правило действует и для друзей: - Имя / + Имя.\n"
    "`+` без имени подскажет, как записаться, но не добавит вас.\n"
    "+ Имя — записать друга, пока запись открыта.\n"
    "- Имя — убрать друга.\n"
    "список — показать актуальную карточку в конце чата\n"
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
_MONTHS_GENITIVE: Final = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def format_entries(entries: Sequence[Entry]) -> str:
    """Format the participant list for a bot response."""
    if not entries:
        return "Пока никто не записался."

    lines: list[str] = []
    for index, entry in enumerate(entries, 1):
        name = f"{entry.name} (-)" if entry.withdrawn else entry.name
        match entry:
            case UserEntry():
                lines.append(f"{index}. {name}")
            case FriendEntry():
                lines.append(f"{index}. {name} (друг)")
            case unreachable:  # type: ignore[reportUnnecessaryComparison]
                assert_never(unreachable)
    return "Список участников:\n" + "\n".join(lines)


def help_text() -> str:
    """Return lifecycle-aware help for text messages."""
    return _HELP_TEXT


def format_registration_card(
    entries: Sequence[Entry],
    state: RegistrationState,
    event_starts_at: datetime.datetime | None,
    *,
    notice: str | None = None,
) -> str:
    """Build the canonical registration card."""
    labels = {
        "open": "запись открыта",
        "opening": "открываем запись",
        "closed": "запись закрыта",
    }
    lines = [f"🏐 Волейбол — {labels[state]}"]
    if event_starts_at is not None:
        weekday = _WEEKDAYS[event_starts_at.weekday()].capitalize()
        month = _MONTHS_GENITIVE[event_starts_at.month - 1]
        lines.extend(
            (
                f"📅 {weekday}, {event_starts_at.day} {month} {event_starts_at.year}",
                f"🕢 Начало: {event_starts_at:%H:%M}",
            )
        )
        if state == "open":
            lines.append(f"⏳ Запись закроется автоматически в {event_starts_at:%H:%M}")
    participant_count = sum(not entry.withdrawn for entry in entries)
    lines.extend(("", f"Участники: {participant_count}"))
    if entries:
        body = format_entries(entries).removeprefix("Список участников:\n")
        lines.append(body)
    else:
        lines.append("Пока никто не записался.")
    if notice:
        lines.extend(("", f"ℹ️ {notice}"))
    return "\n".join(lines)
