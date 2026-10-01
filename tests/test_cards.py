"""Canonical event card publication tests."""

from __future__ import annotations

import datetime
import secrets
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.cards import (
    CardIdentityError,
    CardPublisher,
    EventChangedError,
    MessageRef,
    _message_ref_from_response,
)
from src.config import Config
from src.storage import Storage
from vkbottle_types.objects import (
    MessagesDeleteFullResponseItem,
    MessagesSendUserIdsResponseItem,
)

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

    random_id = await storage.announcement_random_id()
    assert random_id is not None
    await publisher.publish_event(starts_at, random_id, activate)

    snapshot = await storage.snapshot()
    assert snapshot.state == "open"
    assert snapshot.status_message_id == 12
    assert snapshot.status_conversation_message_id == 34
    assert api.messages.send.await_args.kwargs["random_id"] == random_id


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

    random_id = await storage.announcement_random_id()
    assert random_id is not None
    with pytest.raises(OSError, match="cannot persist"):
        await publisher.publish_event(starts_at, random_id, fail_activation)

    assert await storage.registration_state() == "opening"
    api.messages.delete.assert_awaited_once_with(
        peer_id=config.chat_peer_id,
        cmids=[34],
        delete_for_all=True,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["opening", "open", "closed"])
async def test_delete_event_cancels_durably_and_removes_card(
    tmp_path: Path,
    state: str,
) -> None:
    # Given: an event with a card, at any stage of its lifecycle.
    config = _config()
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2099, 9, 22, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "manual")
    await storage.set_status_message_id(12, conversation_message_id=34)
    if state != "opening":
        await storage.activate_event(12, starts_at, conversation_message_id=34)
        await storage.add_friend("Друг")
    if state == "closed":
        assert await storage.close_registration()
    changed = await storage.schedule_change_event()
    api = _api(_response())
    publisher = CardPublisher(api, config, storage)

    # When: the administrator deletes the event.
    assert await publisher.delete_event()

    # Then: a restart cannot resurrect its participants, deadline or card.
    snapshot = await Storage(tmp_path / "participants.json").snapshot()
    assert snapshot.state == "closed"
    assert not snapshot.participants
    assert snapshot.event_starts_at is None
    assert snapshot.event_source is None
    assert snapshot.status_message_id is None
    assert snapshot.status_conversation_message_id is None
    assert await storage.announcement_random_id() is None
    assert changed.is_set()
    api.messages.delete.assert_awaited_once_with(
        peer_id=config.chat_peer_id,
        cmids=[34],
        delete_for_all=True,
    )
    api.messages.send.assert_not_awaited()
    api.messages.edit.assert_not_awaited()
    assert not await publisher.delete_event()
    assert api.messages.delete.await_count == 1


@pytest.mark.anyio
async def test_cancelled_announcement_retry_cannot_publish_a_card(
    tmp_path: Path,
) -> None:
    # Given: an announcement that failed earlier and was subsequently cancelled.
    storage = Storage(tmp_path / "participants.json")
    starts_at = datetime.datetime(2099, 9, 22, 19, 30)  # noqa: DTZ001
    assert await storage.begin_event(starts_at, "manual")
    random_id = await storage.announcement_random_id()
    assert random_id is not None
    api = _api(_response())
    publisher = CardPublisher(api, _config(), storage)
    assert await publisher.delete_event()
    activate = AsyncMock()

    # When: a queued retry attempts delivery of the deleted event.
    with pytest.raises(EventChangedError):
        await publisher.publish_event(starts_at, random_id, activate)

    # Then: no new message is sent and registration stays closed.
    api.messages.send.assert_not_awaited()
    activate.assert_not_awaited()
    assert await storage.registration_state() == "closed"


@pytest.mark.anyio
async def test_failed_cancellation_save_preserves_event_and_card(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(12)
    await storage.add_friend("Друг")
    before = await storage.snapshot()
    api = _api(_response())
    publisher = CardPublisher(api, _config(), storage)
    with (
        patch.object(Storage, "_save", new=AsyncMock(side_effect=OSError("disk full"))),
        pytest.raises(OSError, match="disk full"),
    ):
        await publisher.delete_event()
    assert await storage.snapshot() == before
    assert await Storage(tmp_path / "participants.json").snapshot() == before
    api.messages.delete.assert_not_awaited()


@pytest.mark.anyio
async def test_vk_delete_failure_reports_cancelled_event(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(12)
    api = _api(_response())
    api.messages.delete.side_effect = OSError("VK unavailable")
    publisher = CardPublisher(api, _config(), storage)
    with pytest.raises(OSError, match="Событие отменено"):
        await publisher.delete_event()
    assert await storage.registration_state() == "closed"
    assert not await storage.list_entries()
    assert (await storage.snapshot()).status_message_id == 12
    api.messages.delete.side_effect = None
    assert await publisher.delete_event()
    assert (await storage.snapshot()).status_message_id is None


@pytest.mark.anyio
async def test_vk_individual_delete_refusal_preserves_card_for_retry(
    tmp_path: Path,
) -> None:
    # Given: VK returns success at transport level but refuses this message.
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(12)
    api = _api(_response())
    api.messages.delete.return_value = [
        MessagesDeleteFullResponseItem(message_id=12, response=False)
    ]
    publisher = CardPublisher(api, _config(), storage)
    # When: cancellation tries to remove the card.
    with pytest.raises(OSError, match="Событие отменено"):
        await publisher.delete_event()
    # Then: the event is cancelled, but deletion is never reported as successful.
    assert await storage.registration_state() == "closed"
    assert (await storage.snapshot()).status_message_id == 12
