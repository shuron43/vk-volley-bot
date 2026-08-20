"""Shared inline keyboard builder for bot responses."""

from vkbottle import Callback, Keyboard, KeyboardButtonColor


def build_inline_keyboard() -> str:
    """Build the shared inline command keyboard."""
    return (
        Keyboard(one_time=False, inline=True)
        .add(
            Callback("✅ Записаться", payload={"cmd": "join"}),
            color=KeyboardButtonColor.SECONDARY,
        )
        .add(
            Callback("↩️ Отписаться", payload={"cmd": "leave"}),
            color=KeyboardButtonColor.SECONDARY,
        )
        .row()
        .add(Callback("📋 Список", payload={"cmd": "list"}))
        .add(Callback("❓ Помощь", payload={"cmd": "help"}))
        .get_json()
    )
