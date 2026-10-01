"""BDD scenarios for resolving configuration IDs without touching bot state."""

from __future__ import annotations

import secrets
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from src.vk_ids import (
    ChatIds,
    PollUpdate,
    TokenSettings,
    find_chat,
    main,
    probe_ids,
    profile_identifier,
    resolve_users,
    run,
)
from vkbottle.api import API


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("https://vk.com/id123?from=search", "123"),
        ("https://vk.ru/alex_volley/", "alex_volley"),
        ("vk.com/alex_volley", "alex_volley"),
        ("https://m.vk.com/id123", "123"),
        ("id123", "123"),
        ("123", "123"),
        (" alex_volley ", "alex_volley"),
    ],
)
def test_profile_input_resolves_to_api_identifier(profile: str, expected: str) -> None:
    # Given: a profile link or a user identifier copied from VK.
    # When: it is prepared for users.get.
    result = profile_identifier(profile)
    # Then: the API receives only the ID or screen name.
    assert result == expected


@pytest.mark.parametrize(
    "profile",
    ["", "0", "-123", "a,b", "https://other.ru/id1", "https://vk.com/im/convo/123"],
)
def test_invalid_profile_is_rejected(profile: str) -> None:
    with pytest.raises(ValueError, match=r"Укажите|Нужна|положительным"):
        profile_identifier(profile)


@pytest.mark.parametrize(
    ("text", "peer_id", "from_id", "expected"),
    [
        ("probe", 2_000_000_042, 123, ChatIds(peer_id=2_000_000_042, from_id=123)),
        ("other", 2_000_000_042, 123, None),
        ("probe", 123, 123, None),
        ("probe", 2_000_000_000, 123, None),
        ("probe", 2_000_000_042, -123, None),
    ],
)
def test_probe_identifies_only_direct_message_in_target_chat(
    text: str,
    peer_id: int,
    from_id: int,
    expected: ChatIds | None,
) -> None:
    # Given: a message delivered to the community, including private/wrong messages.
    update = PollUpdate(
        type="message_new",
        object={
            "message": {"text": text, "peer_id": peer_id, "from_id": from_id},
        },
    )
    # When: the exact probe is checked.
    result = probe_ids(update, "probe")
    # Then: only the group chat's own peer_id and personal author ID are returned.
    assert result == expected


def test_non_message_event_is_ignored() -> None:
    assert probe_ids(PollUpdate(type="message_event", object={}), "probe") is None


def test_token_settings_do_not_require_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: setup is incomplete and no application config can be loaded.
    token = secrets.token_urlsafe()
    monkeypatch.setenv("VK_TOKEN", token)
    monkeypatch.delenv("CHAT_PEER_ID", raising=False)
    # When: the lookup settings are loaded without reading a user's .env.
    settings = TokenSettings(_env_file=None)
    # Then: only the secret token is required and its repr does not expose it.
    assert settings.vk_token.get_secret_value() == token
    assert token not in repr(settings)


@pytest.mark.anyio
async def test_users_are_named_and_resolved_before_admin_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = MagicMock(spec=API)
    api.users.get = AsyncMock(
        return_value=[
            MagicMock(id=123, first_name="Имя", last_name="Фамилия"),
        ]
    )
    result = await resolve_users(api, ["https://vk.com/person", "id123"])
    assert result == [123]
    assert api.users.get.await_args_list[0].kwargs == {"user_ids": ["person"]}
    assert "Имя Фамилия — 123" in capsys.readouterr().out


@pytest.mark.anyio
async def test_missing_user_does_not_silently_become_admin() -> None:
    api = MagicMock(spec=API)
    api.users.get = AsyncMock(return_value=[])
    with pytest.raises(ValueError, match="Не найден пользователь"):
        await resolve_users(api, ["missing"])


@pytest.mark.anyio
@pytest.mark.parametrize("failed", [1, 2, 3])
async def test_ready_probe_returns_community_peer_and_does_not_send_messages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: int,
) -> None:
    # Given: community Long Poll is initialized before a new probe is sent.
    monkeypatch.setattr("src.vk_ids.secrets.token_hex", lambda _: "abcdef")
    api = MagicMock(spec=API)
    api.http_client = MagicMock()
    api.groups.get_by_id = AsyncMock(
        return_value=MagicMock(
            groups=[
                MagicMock(id=7, name="Волейбол"),
            ]
        )
    )
    api.groups.get_long_poll_server = AsyncMock(
        return_value=MagicMock(
            server="https://lp.vk.com/test",
            key="secret",
            ts="1",
        )
    )
    api.http_client.request_json = AsyncMock(
        side_effect=[
            {"failed": failed, "ts": "2"},
            {
                "ts": "3",
                "updates": [
                    {
                        "type": "message_new",
                        "object": {
                            "message": {
                                "peer_id": 123,
                                "from_id": 123,
                                "text": "vk-peer-abcdef",
                            },
                        },
                    }
                ],
            },
            {
                "ts": "4",
                "updates": [
                    {
                        "type": "message_new",
                        "object": {
                            "message": {
                                "peer_id": 2_000_000_042,
                                "from_id": 123,
                                "text": "vk-peer-abcdef",
                            },
                        },
                    }
                ],
            },
        ]
    )
    # When: the probe is sent first in private, then in the intended chat.
    result = await find_chat(api, wait_seconds=1)
    # Then: the personal dialog is ignored and the community's group peer is used.
    assert result == ChatIds(peer_id=2_000_000_042, from_id=123)
    assert "Готово" in capsys.readouterr().out
    assert api.http_client.request_json.await_args.kwargs["params"]["ts"] == "3"
    assert api.groups.get_long_poll_server.await_count == (1 if failed == 1 else 2)
    assert api.messages.mock_calls == []


@pytest.mark.anyio
async def test_chat_wait_is_bounded() -> None:
    api = MagicMock(spec=API)
    api.http_client = MagicMock()

    async def wait_for_messages(**_kwargs: object) -> None:
        await anyio.sleep_forever()

    api.http_client.request_json = AsyncMock(side_effect=wait_for_messages)
    api.groups.get_by_id = AsyncMock(
        return_value=MagicMock(
            groups=[
                MagicMock(id=7, name="Волейбол"),
            ]
        )
    )
    api.groups.get_long_poll_server = AsyncMock(return_value=MagicMock(ts="1"))
    with pytest.raises(TimeoutError):
        await find_chat(api, wait_seconds=0)


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["chat", "users"])
async def test_lookup_prints_config_line_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    # Given: successful API lookups with a secret community token.
    token = secrets.token_urlsafe()
    monkeypatch.setenv("VK_TOKEN", token)
    api = MagicMock(spec=API)
    api.http_client = MagicMock()
    api.http_client.close = AsyncMock()
    monkeypatch.setattr("src.vk_ids.API", MagicMock(return_value=api))
    monkeypatch.setattr(
        "src.vk_ids.find_chat",
        AsyncMock(
            return_value=ChatIds(peer_id=2_000_000_042, from_id=123),
        ),
    )
    monkeypatch.setattr("src.vk_ids.resolve_users", AsyncMock(return_value=[123, 456]))
    # When: the setup command runs.
    await run(command, ["person"], 180)
    # Then: a ready-to-copy config line is printed, without exposing the token.
    output = capsys.readouterr().out
    expected = (
        "CHAT_PEER_ID=2000000042" if command == "chat" else "ADMIN_VK_IDS_RAW=123,456"
    )
    assert expected in output
    assert token not in output
    api.http_client.close.assert_awaited_once_with()


@pytest.mark.anyio
async def test_failed_lookup_closes_connection_without_admin_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("VK_TOKEN", secrets.token_urlsafe())
    api = MagicMock(spec=API)
    api.http_client = MagicMock()
    api.http_client.close = AsyncMock()
    monkeypatch.setattr("src.vk_ids.API", MagicMock(return_value=api))
    monkeypatch.setattr(
        "src.vk_ids.resolve_users",
        AsyncMock(
            side_effect=ValueError("Не найден пользователь"),
        ),
    )
    with pytest.raises(ValueError, match="Не найден пользователь"):
        await run("users", ["missing"], 180)
    assert "ADMIN_VK_IDS_RAW=" not in capsys.readouterr().out
    api.http_client.close.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["chat"], ("chat", [], 180)),
        (["chat", "--timeout", "600"], ("chat", [], 600)),
        (["users", "id123", "person"], ("users", ["id123", "person"], 180)),
    ],
)
def test_documented_cli_commands_select_expected_lookup(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected: tuple[str, list[str], int],
) -> None:
    monkeypatch.setattr("sys.argv", ["vk_ids", *arguments])
    runner = MagicMock()
    monkeypatch.setattr("src.vk_ids.anyio.run", runner)
    main()
    runner.assert_called_once_with(run, *expected)
