"""Focused setup scenarios and separate CLI/Long Poll infrastructure contracts."""

from __future__ import annotations

import secrets
from types import SimpleNamespace
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


@pytest.fixture
def vk_api(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setenv("VK_TOKEN", secrets.token_urlsafe())
    api = MagicMock(spec=API)
    api.http_client = MagicMock()
    api.http_client.close = AsyncMock()
    monkeypatch.setattr("src.vk_ids.API", MagicMock(return_value=api))
    return api


def _poll_server(api: MagicMock) -> None:
    api.groups.get_by_id = AsyncMock(
        return_value=SimpleNamespace(groups=[SimpleNamespace(id=7, name="Волейбол")])
    )
    api.groups.get_long_poll_server = AsyncMock(
        return_value=SimpleNamespace(
            server="https://lp.vk.com/test", key="initial-key", ts="1"
        )
    )


def _probe_batch(peer_id: int, ts: str) -> dict[str, object]:
    return {
        "ts": ts,
        "updates": [
            {
                "type": "message_new",
                "object": {
                    "message": {
                        "peer_id": peer_id,
                        "from_id": 123,
                        "text": "vk-peer-abcdef",
                    }
                },
            }
        ],
    }


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
    assert profile_identifier(profile) == expected


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
def test_probe_accepts_only_matching_group_message(
    text: str, peer_id: int, from_id: int, expected: ChatIds | None
) -> None:
    update = PollUpdate(
        type="message_new",
        object={"message": {"text": text, "peer_id": peer_id, "from_id": from_id}},
    )
    assert probe_ids(update, "probe") == expected


def test_non_message_event_is_ignored() -> None:
    assert probe_ids(PollUpdate(type="message_event", object={}), "probe") is None


def test_token_settings_do_not_require_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    token = secrets.token_urlsafe()
    monkeypatch.setenv("VK_TOKEN", token)
    monkeypatch.delenv("CHAT_PEER_ID", raising=False)
    settings = TokenSettings(_env_file=None)
    assert settings.vk_token.get_secret_value() == token
    assert token not in repr(settings)


@pytest.mark.anyio
async def test_profile_resolution_preserves_identity_and_deduplicates_ids(
    vk_api: MagicMock,
) -> None:
    vk_api.users.get = AsyncMock(
        return_value=[SimpleNamespace(id=123, first_name="Имя", last_name="Фамилия")]
    )
    result = await resolve_users(vk_api, ["https://vk.com/person", "id123"])
    assert result == [123]
    assert [call.kwargs["user_ids"] for call in vk_api.users.get.await_args_list] == [
        ["person"],
        ["123"],
    ]


@pytest.mark.anyio
async def test_setup_operator_resolves_admin_profiles(
    vk_api: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: two requested VK profiles and their real API response shapes.
    token = secrets.token_urlsafe()
    monkeypatch.setenv("VK_TOKEN", token)
    vk_api.users.get = AsyncMock(
        side_effect=[
            [SimpleNamespace(id=123, first_name="Алиса", last_name="Первая")],
            [SimpleNamespace(id=456, first_name="Борис", last_name="Второй")],
        ]
    )

    # When: the operator requests administrator IDs.
    await run("users", ["https://vk.com/alice", "id456"], 1)

    # Then: both named profiles precede a ready config line, without the token.
    output = capsys.readouterr().out
    config_position = output.index("ADMIN_VK_IDS_RAW=123,456")
    assert output.index("Алиса Первая") < config_position
    assert output.index("Борис Второй") < config_position
    assert token not in output


@pytest.mark.anyio
async def test_setup_operator_requests_missing_admin_profile(
    vk_api: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: VK cannot resolve the requested profile.
    vk_api.users.get = AsyncMock(return_value=[])

    # When: the operator requests its administrator ID.
    with pytest.raises(ValueError, match="Не найден пользователь: missing"):
        await run("users", ["missing"], 1)

    # Then: the missing identity is rejected without a usable admin config line.
    assert "ADMIN_VK_IDS_RAW=" not in capsys.readouterr().out


@pytest.mark.anyio
async def test_setup_operator_identifies_chat_after_private_probe(
    vk_api: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: initialized community Long Poll with a private probe before a chat probe.
    token = secrets.token_urlsafe()
    monkeypatch.setenv("VK_TOKEN", token)
    monkeypatch.setattr("src.vk_ids.secrets.token_hex", lambda _: "abcdef")
    _poll_server(vk_api)
    vk_api.http_client.request_json = AsyncMock(
        side_effect=[_probe_batch(123, "2"), _probe_batch(2_000_000_042, "3")]
    )

    # When: the operator requests the conversation ID.
    await run("chat", [], 1)

    # Then: readiness precedes the group config line; no chat message or token leaks.
    output = capsys.readouterr().out
    assert output.index("Готово") < output.index("CHAT_PEER_ID=2000000042")
    assert "VK ID автора проверочного сообщения: 123" in output
    assert token not in output
    assert vk_api.messages.mock_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("failed", [1, 2, 3])
async def test_long_poll_resumes_with_updated_cursor_or_refreshed_server(
    vk_api: MagicMock, monkeypatch: pytest.MonkeyPatch, failed: int
) -> None:
    # Given: a recovery response and a matching message after it.
    monkeypatch.setattr("src.vk_ids.secrets.token_hex", lambda _: "abcdef")
    _poll_server(vk_api)
    initial = vk_api.groups.get_long_poll_server.return_value
    refreshed = SimpleNamespace(
        server="https://lp.vk.com/refreshed", key="refreshed-key", ts="20"
    )
    vk_api.groups.get_long_poll_server.side_effect = [initial, refreshed]
    vk_api.http_client.request_json = AsyncMock(
        side_effect=[{"failed": failed, "ts": "2"}, _probe_batch(2_000_000_042, "21")]
    )

    # When: polling recovers and reads the next batch.
    result = await find_chat(vk_api, wait_seconds=1)

    # Then: failure 1 advances ts; failures 2/3 refresh the server before continuing.
    assert result == ChatIds(peer_id=2_000_000_042, from_id=123)
    resumed = vk_api.http_client.request_json.await_args.kwargs
    server = initial if failed == 1 else refreshed
    assert resumed["url"] == server.server
    assert resumed["params"]["key"] == server.key
    assert resumed["params"]["ts"] == ("2" if failed == 1 else "20")
    assert vk_api.groups.get_long_poll_server.await_count == (1 if failed == 1 else 2)


@pytest.mark.anyio
async def test_chat_wait_is_bounded(vk_api: MagicMock) -> None:
    _poll_server(vk_api)

    async def wait_for_messages(**_kwargs: object) -> None:
        await anyio.sleep_forever()

    vk_api.http_client.request_json = AsyncMock(side_effect=wait_for_messages)
    with pytest.raises(TimeoutError):
        await find_chat(vk_api, wait_seconds=0)


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["chat", "users"])
@pytest.mark.parametrize("failed", [False, True])
async def test_cli_releases_http_session_after_lookup(
    vk_api: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    failed: bool,
) -> None:
    # Given: lookup operations that either return or raise while owning an API session.
    lookup = AsyncMock(
        return_value=ChatIds(peer_id=2_000_000_042, from_id=123)
        if command == "chat"
        else [123]
    )
    if failed:
        lookup.side_effect = ValueError("lookup failed")
    operation = "find_chat" if command == "chat" else "resolve_users"
    monkeypatch.setattr(f"src.vk_ids.{operation}", lookup)

    # When: the lookup finishes or unwinds after an error.
    if failed:
        with pytest.raises(ValueError, match="lookup failed"):
            await run(command, ["person"], 1)
    else:
        await run(command, ["person"], 1)

    # Then: the HTTP session is always closed once.
    vk_api.http_client.close.assert_awaited_once_with()


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
