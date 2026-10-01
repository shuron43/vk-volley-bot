"""Resolve VK configuration IDs without starting the application or changing .env."""

# CLI output is the purpose of this module.
# ruff: noqa: T201

import argparse
import re
import secrets
import sys
from typing import ClassVar
from urllib.parse import urlsplit

import anyio
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from vkbottle import VKAPIError
from vkbottle.api import API

_CHAT_OFFSET = 2_000_000_000
_POLL_WAIT = 25


class TokenSettings(BaseSettings):
    """Load only the token; unresolved application IDs are not required."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    vk_token: SecretStr


class ChatIds(BaseModel):
    """IDs observed in the community's own incoming message."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    peer_id: int
    from_id: int


class ProbeMessage(ChatIds):
    """Minimal message fields needed to identify the probe."""

    text: str = ""


class IncomingMessage(BaseModel):
    """Modern Bots Long Poll message_new payload."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    message: ProbeMessage


class PollUpdate(BaseModel):
    """A Long Poll update whose payload depends on its event type."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    type: str
    object: JsonValue


class PollBatch(BaseModel):
    """Successful Long Poll batch or a server recovery response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    ts: str | int | None = None
    failed: int | None = None
    updates: list[PollUpdate] = []


def probe_ids(update: PollUpdate, marker: str) -> ChatIds | None:
    """Accept only the exact marker sent directly by a user in a group chat."""
    if update.type != "message_new":
        return None
    message = IncomingMessage.model_validate(update.object).message
    if (
        message.text != marker
        or message.peer_id <= _CHAT_OFFSET
        or message.from_id <= 0
    ):
        return None
    return ChatIds(peer_id=message.peer_id, from_id=message.from_id)


def profile_identifier(value: str) -> str:
    """Normalize a VK profile URL, numeric ID, or screen name."""
    identifier = value.strip()
    if "://" in identifier or identifier.startswith(("vk.com/", "vk.ru/")):
        parsed = urlsplit(
            identifier if "://" in identifier else f"https://{identifier}",
        )
        if parsed.hostname not in {"vk.com", "www.vk.com", "m.vk.com", "vk.ru"}:
            message = "Нужна ссылка на профиль vk.com или vk.ru."
            raise ValueError(message)
        identifier = parsed.path.strip("/")
    if identifier.startswith("id") and identifier[2:].isdigit():
        identifier = identifier[2:]
    if not re.fullmatch(r"[A-Za-z0-9_\.]+", identifier):
        message = "Укажите числовой ID, короткое имя или прямую ссылку на профиль."
        raise ValueError(message)
    if identifier.isdigit() and int(identifier) <= 0:
        message = "ID пользователя должен быть положительным."
        raise ValueError(message)
    return identifier


async def find_chat(api: API, *, wait_seconds: int = 180) -> ChatIds:
    """Read a fresh probe from Bots Long Poll without sending chat messages."""
    groups = await api.groups.get_by_id()
    if not groups.groups:
        message = "Не найдено сообщество. Используйте токен сообщества."
        raise ValueError(message)
    group = groups.groups[0]
    server = await api.groups.get_long_poll_server(group_id=group.id)
    marker = f"vk-peer-{secrets.token_hex(6)}"
    print(f"Сообщество: {group.name} (ID {group.id}).", flush=True)
    print("Готово. Отправьте ТОЧНО это сообщение в НУЖНУЮ беседу:", flush=True)
    print(marker, flush=True)
    print(f"Ожидание {wait_seconds} с. Ctrl+C — отмена.", flush=True)
    ts = server.ts
    with anyio.fail_after(wait_seconds):
        while True:
            response = await api.http_client.request_json(
                url=server.server,
                method="POST",
                params={
                    "act": "a_check",
                    "key": server.key,
                    "ts": ts,
                    "wait": _POLL_WAIT,
                },
            )
            batch = PollBatch.model_validate(response)
            if batch.failed in {2, 3}:
                server = await api.groups.get_long_poll_server(group_id=group.id)
                ts = server.ts
                continue
            if batch.failed not in {None, 1}:
                message = "Long Poll отклонил запрос. Проверьте настройки сообщества."
                raise ValueError(message)
            if batch.ts is not None:
                ts = str(batch.ts)
            for update in batch.updates:
                result = probe_ids(update, marker)
                if result is not None:
                    return result


async def resolve_users(api: API, profiles: list[str]) -> list[int]:
    """Resolve each profile separately so a missing user cannot be overlooked."""
    identifiers = [profile_identifier(profile) for profile in profiles]
    ids: list[int] = []
    for identifier in identifiers:
        users = await api.users.get(user_ids=[identifier])
        if len(users) != 1 or users[0].id <= 0:
            message = f"Не найден пользователь: {identifier}. Проверьте ссылку профиля."
            raise ValueError(message)
        user = users[0]
        print(f"{identifier}: {user.first_name} {user.last_name} — {user.id}")
        if user.id not in ids:
            ids.append(user.id)
    return ids


async def run(command: str, profiles: list[str], wait_seconds: int) -> None:
    """Execute the requested lookup and release the HTTP session."""
    settings = TokenSettings()
    if not settings.vk_token.get_secret_value().strip():
        message = "Заполните VK_TOKEN в .env."
        raise ValueError(message)
    api = API(settings.vk_token.get_secret_value())
    try:
        if command == "chat":
            result = await find_chat(api, wait_seconds=wait_seconds)
            print(f"\nCHAT_PEER_ID={result.peer_id}")
            print(f"VK ID автора проверочного сообщения: {result.from_id}")
            print("Скопируйте CHAT_PEER_ID в .env и запустите обычного бота.")
        else:
            ids = await resolve_users(api, profiles)
            print("\nADMIN_VK_IDS_RAW=" + ",".join(str(vk_id) for vk_id in ids))
    finally:
        await api.http_client.close()


class CLIArgs(argparse.Namespace):
    """Typed arguments populated by argparse."""

    def __init__(self) -> None:
        """Initialize defaults before argparse populates the selected command."""
        super().__init__()
        self.command: str = ""
        self.timeout: int = 180
        self.profiles: list[str] = []


def main() -> None:
    """Parse CLI options and show actionable errors without printing the token."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    chat = commands.add_parser("chat", help="получить ID беседы по новому сообщению")
    _ = chat.add_argument(
        "--timeout", type=int, default=180, help="ожидание в секундах"
    )
    users = commands.add_parser("users", help="получить ID администраторов по профилям")
    _ = users.add_argument("profiles", nargs="+", help="ID, короткие имена или ссылки")
    args = CLIArgs()
    _ = parser.parse_args(namespace=args)
    if args.command == "chat" and args.timeout <= 0:
        parser.error("--timeout должен быть положительным")
    try:
        anyio.run(
            run,
            args.command,
            args.profiles,
            args.timeout,
        )
    except ValidationError:
        parser.exit(1, "Проверьте VK_TOKEN в .env и формат ответа API VK.\n")
    except TimeoutError:
        message = "Нет сообщения. Проверьте message_new и доступ ко всей переписке.\n"
        message += "Отправьте новую метку после «Готово».\n"
        parser.exit(1, message)
    except VKAPIError as exc:
        message = " ".join(
            (
                f"Ошибка VK API (код {exc.code}). Проверьте токен сообщества,",
                "права messages и настройки Long Poll.\n",
            )
        )
        parser.exit(1, message)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except OSError:
        parser.exit(1, "Не удалось подключиться к VK. Проверьте сеть.\n")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
