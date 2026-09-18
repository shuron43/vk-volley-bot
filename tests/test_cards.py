"""Canonical event card publication tests."""

from __future__ import annotations

import datetime
import secrets
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.cards import (
    CardIdentityError,
    CardPublisher,
    MessageRef,
    _message_ref_from_response,
)
from src.config import Config
from src.storage import Storage
from vkbottle_types.objects import MessagesSendUserIdsResponseItem

if TYPE_CHECKING:
    from pathlib import Path


def _response(
    *, message_id: int | None = 12, cmid: int | None = 34
) -> list[MessagesSendUserIdsResponseItem]:
    return [
        MessagesSendUserIdsResponseItem(
            peer_id=2_000_000_001,
            message_id=message_id,
            conversation_message_id=cmid,
        )
    ]


def _api(response: object) -> MagicMock:
    api = MagicMock()
    api.messages.send = AsyncMock(return_value=response)
    api.messages.edit = AsyncMock()
    api.messages.delete = AsyncMock()
    return api


def _config() -> Config:
    return Config(
        vk_token=secrets.token_urlsafe(),
        chat_peer_id=2_000_000_001,
    )


def test_message_ref_normalizes_vk_response_shapes() -> None:
    assert _message_ref_from_response(7) == MessageRef(message_id=7)
    assert _message_ref_from_response(_response()) == MessageRef(
        message_id=12,
        conversation_message_id=34,
    )
    assert not _message_ref_from_response(0).usable
    assert not _message_ref_from_response([]).usable


@pytest.mark.anyio
async def test_replace_uses_peer_ids_persists_both_ids_and_deletes_old(
    tmp_path: Path,
) -> None:
    config = _config()
    storage = Storage(tmp_path / "participants.json")
    await storage.set_status_message_id(3, conversation_message_id=4)
    api = _api(_response())
    publisher = CardPublisher(api, config, storage)

    ref = await publisher.replace_current()

    assert ref == MessageRef(message_id=12, conversation_message_id=34)
    assert api.messages.send.await_args.kwargs["peer_ids"] == [config.chat_peer_id]
    assert "peer_id" not in api.messages.send.await_args.kwargs
    snapshot = await Storage(tmp_path / "participants.json").snapshot()
    assert snapshot.status_message_id == 12
    assert snapshot.status_conversation_message_id == 34
    api.messages.delete.assert_awaited_once_with(
        peer_id=config.chat_peer_id,
        cmids=[4],
        delete_for_all=True,
    )


@pytest.mark.anyio
async def test_invalid_send_response_keeps_previous_card(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.set_status_message_id(3, conversation_message_id=4)
    api = _api(0)
    publisher = CardPublisher(api, _config(), storage)

    with pytest.raises(CardIdentityError):
        await publisher.replace_current()

    snapshot = await storage.snapshot()
    assert snapshot.status_message_id == 3
    assert snapshot.status_conversation_message_id == 4
    api.messages.delete.assert_not_awaited()


@pytest.mark.anyio
async def test_edit_prefers_conversation_message_id(tmp_path: Path) -> None:
    config = _config()
    storage = Storage(tmp_path / "participants.json")
    await storage.set_status_message_id(3, conversation_message_id=4)
    api = _api(_response())
    publisher = CardPublisher(api, config, storage)

    ref = await publisher.edit_current(notice="Готово")

    assert ref == MessageRef(message_id=3, conversation_message_id=4)
    api.messages.edit.assert_awaited_once()
    edited = api.messages.edit.await_args.kwargs
    assert edited["conversation_message_id"] == 4
    assert edited["message_id"] is None
    assert "Готово" in edited["message"]
    api.messages.send.assert_not_awaited()


@pytest.mark.anyio
async def test_failed_edit_replaces_card(tmp_path: Path) -> None:
    config = _config()
    storage = Storage(tmp_path / "participants.json")
    await storage.set_status_message_id(3, conversation_message_id=4)
    api = _api(_response())
    api.messages.edit = AsyncMock(side_effect=OSError("old card unavailable"))
    publisher = CardPublisher(api, config, storage)

    ref = await publisher.edit_current()

    assert ref == MessageRef(message_id=12, conversation_message_id=34)
    api.messages.delete.assert_awaited_once_with(
        peer_id=config.chat_peer_id,
        cmids=[4],
        delete_for_all=True,
    )


@pytest.mark.anyio
async def test_publish_event_activates_only_after_card_has_ids(
    tmp_path: Path,
) -> None:
    config = _config()
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2026, 9, 22, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "weekly")
    api = _api(_response())
    publisher = CardPublisher(api, config, storage)

    async def activate(ref: MessageRef) -> None:
        await storage.activate_event(
            ref.message_id,
            starts_at,
            conversation_message_id=ref.conversation_message_id,
        )

    await publisher.publish_event(starts_at, 99, activate)

    snapshot = await storage.snapshot()
    assert snapshot.state == "open"
    assert snapshot.status_message_id == 12
    assert snapshot.status_conversation_message_id == 34
    assert api.messages.send.await_args.kwargs["random_id"] == 99


@pytest.mark.anyio
async def test_activation_failure_removes_uncommitted_card(tmp_path: Path) -> None:
    config = _config()
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2026, 9, 22, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "weekly")
    api = _api(_response())
    publisher = CardPublisher(api, config, storage)

    async def fail_activation(_ref: MessageRef) -> None:
        message = "cannot persist"
        raise OSError(message)

    with pytest.raises(OSError, match="cannot persist"):
        await publisher.publish_event(starts_at, 99, fail_activation)

    assert await storage.registration_state() == "opening"
    api.messages.delete.assert_awaited_once_with(
        peer_id=config.chat_peer_id,
        cmids=[34],
        delete_for_all=True,
    )
