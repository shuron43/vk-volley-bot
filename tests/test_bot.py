"""Unit tests for bot presentation helpers."""

import json

import pytest
from src.bot import (
    _admin_help_text,
    build_inline_keyboard,
    extract_friend_name,
    format_entries,
    help_text,
)
from src.storage import FriendEntry, UserEntry


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], "Пока никто не записался."),
        (
            [UserEntry(kind="user", vk_id=1, name="Алексей")],
            "Список участников:\n1. Алексей",
        ),
        (
            [FriendEntry(kind="friend", name="Андрей")],
            "Список участников:\n1. Андрей (друг)",
        ),
        (
            [
                UserEntry(kind="user", vk_id=1, name="Алексей"),
                FriendEntry(kind="friend", name="Андрей"),
            ],
            "Список участников:\n1. Алексей\n2. Андрей (друг)",
        ),
    ],
)
def test_format_entries_when_entries_vary(
    entries: list[UserEntry | FriendEntry], expected: str
) -> None:
    # Given: stored participant entries of each supported kind.
    # When: the list presentation is built.
    actual = format_entries(entries)

    # Then: the user-visible list text matches the existing contract.
    assert actual == expected


def test_help_text_when_requested() -> None:
    # Given: no external dependencies.
    # When: the help presentation is built.
    text = help_text()

    # Then: it contains every documented command family.
    assert text
    assert "записаться" in text
    assert "отписаться" in text
    assert "+ Имя" in text
    assert "- Имя" in text
    assert "список" in text
    assert "помощь" in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+ Name", "Name"),
        ("+   Name  ", "Name"),
        ("- Name", "Name"),
        ("-   Name  ", "Name"),
        ("+", ""),
    ],
)
def test_extract_friend_name_when_command_contains_sign(
    text: str, expected: str
) -> None:
    # Given: a friend command with a plus or minus sign.
    # When: the name is extracted.
    actual = extract_friend_name(text)

    # Then: whitespace and the command sign are excluded.
    assert actual == expected


def test_build_inline_keyboard_when_serialized() -> None:
    # Given: no external dependencies.
    # When: the inline keyboard is built.
    keyboard = build_inline_keyboard()

    # Then: VK can consume it as JSON.
    assert isinstance(json.loads(keyboard), dict)


def test_admin_help_text_contains_commands() -> None:
    """Admin help lists every documented admin command."""
    text = _admin_help_text()
    assert "очистить" in text
    assert "сбросить" in text
    assert "убрать" in text
    assert "удалить" in text
