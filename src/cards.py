"""Canonical event card publishing and replacement."""

import asyncio
import datetime
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final, final

from vkbottle import VKAPIError
from vkbottle.api import API
from vkbottle_types.objects import MessagesSendUserIdsResponseItem

from src.config import Config
from src.formatting import format_registration_card
from src.keyboard import build_inline_keyboard
from src.storage import Entry, RegistrationSnapshot, RegistrationState, Storage

_LOGGER: Final = logging.getLogger(__name__)
_MAX_RANDOM_ID: Final = 2_147_483_646


class CardIdentityError(OSError):
    """VK accepted a card but did not return an editable message identifier."""


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Identifiers that can address a message in a VK conversation."""

    message_id: int | None = None
    conversation_message_id: int | None = None

    @property
    def usable(self) -> bool:
        """Return whether VK can edit or delete the referenced message."""
        return bool(self.message_id or self.conversation_message_id)


type ActivateCard = Callable[[MessageRef], Awaitable[None]]


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _message_ref_from_response(
    response: list[MessagesSendUserIdsResponseItem] | int,
) -> MessageRef:
    """Normalize both VK ``messages.send`` response shapes."""
    if isinstance(response, int):
        return MessageRef(message_id=_positive_int(response))
    if not response:
        return MessageRef()
    item = response[0]
    return MessageRef(
        message_id=_positive_int(item.message_id),
        conversation_message_id=_positive_int(item.conversation_message_id),
    )


def _snapshot_ref(snapshot: RegistrationSnapshot) -> MessageRef:
    return MessageRef(
        message_id=_positive_int(snapshot.status_message_id),
        conversation_message_id=_positive_int(snapshot.status_conversation_message_id),
    )


@final
class CardPublisher:
    """Keep one durable event card and serialize every visible update."""

    def __init__(self, api: API, config: Config, storage: Storage) -> None:
        """Bind the VK API, target chat, and durable event storage."""
        self._api = api
        self._config = config
        self._storage = storage
        self._keyboard = build_inline_keyboard()
        self._lock = asyncio.Lock()

    async def _send(
        self,
        entries: Sequence[Entry],
        state: RegistrationState,
        event_starts_at: datetime.datetime | None,
        *,
        notice: str | None = None,
        random_id: int | None = None,
    ) -> MessageRef:
        response = await self._api.messages.send(
            peer_ids=[self._config.chat_peer_id],
            message=format_registration_card(
                entries,
                state,
                event_starts_at,
                notice=notice,
            ),
            keyboard=self._keyboard,
            random_id=(
                random_id
                if random_id is not None
                else secrets.randbelow(_MAX_RANDOM_ID) + 1
            ),
        )
        ref = _message_ref_from_response(response)
        if not ref.usable:
            message = "VK did not return an editable ID for the event card"
            raise CardIdentityError(message)
        _LOGGER.info(
            "Event card sent: message_id=%s conversation_message_id=%s",
            ref.message_id,
            ref.conversation_message_id,
        )
        return ref

    async def _delete(self, ref: MessageRef) -> None:
        if not ref.usable:
            return
        try:
            if ref.conversation_message_id is not None:
                _ = await self._api.messages.delete(
                    peer_id=self._config.chat_peer_id,
                    cmids=[ref.conversation_message_id],
                    delete_for_all=True,
                )
            elif ref.message_id is not None:
                _ = await self._api.messages.delete(
                    message_ids=[ref.message_id],
                    delete_for_all=True,
                )
        except (OSError, TimeoutError, VKAPIError):
            _LOGGER.warning(
                "Could not delete superseded event card: message_id=%s cmid=%s",
                ref.message_id,
                ref.conversation_message_id,
            )

    async def _replace_unlocked(self, *, notice: str | None = None) -> MessageRef:
        snapshot = await self._storage.snapshot()
        old_ref = _snapshot_ref(snapshot)
        new_ref = await self._send(
            snapshot.participants,
            snapshot.state,
            snapshot.event_starts_at,
            notice=notice,
        )
        try:
            await self._storage.set_status_message_id(
                new_ref.message_id,
                conversation_message_id=new_ref.conversation_message_id,
            )
        except BaseException:
            await self._delete(new_ref)
            raise
        if new_ref != old_ref:
            await self._delete(old_ref)
        return new_ref

    async def replace_current(self, *, notice: str | None = None) -> MessageRef:
        """Publish the current state last in chat and remove the prior card."""
        async with self._lock:
            return await self._replace_unlocked(notice=notice)

    async def edit_current(self, *, notice: str | None = None) -> MessageRef:
        """Edit the canonical card, replacing it only when editing is impossible."""
        async with self._lock:
            snapshot = await self._storage.snapshot()
            ref = _snapshot_ref(snapshot)
            if not ref.usable:
                return await self._replace_unlocked(notice=notice)
            try:
                _ = await self._api.messages.edit(
                    peer_id=self._config.chat_peer_id,
                    message_id=(
                        ref.message_id if ref.conversation_message_id is None else None
                    ),
                    conversation_message_id=ref.conversation_message_id,
                    message=format_registration_card(
                        snapshot.participants,
                        snapshot.state,
                        snapshot.event_starts_at,
                        notice=notice,
                    ),
                    keyboard=self._keyboard,
                )
            except (OSError, TimeoutError, VKAPIError):
                _LOGGER.exception("Could not edit event card; publishing replacement")
                return await self._replace_unlocked(notice=notice)
            return ref

    async def publish_event(
        self,
        event_starts_at: datetime.datetime,
        random_id: int,
        activate: ActivateCard,
    ) -> MessageRef:
        """Publish and atomically activate a new empty event card."""
        async with self._lock:
            old_snapshot = await self._storage.snapshot()
            old_ref = _snapshot_ref(old_snapshot)
            new_ref = await self._send(
                (),
                "open",
                event_starts_at,
                random_id=random_id,
            )
            try:
                await activate(new_ref)
            except BaseException:
                await self._delete(new_ref)
                raise
            if new_ref != old_ref:
                await self._delete(old_ref)
            return new_ref

    async def diagnostic_notice(self) -> str:
        """Return admin-readable state without exposing secrets."""
        snapshot = await self._storage.snapshot()
        start = (
            snapshot.event_starts_at.strftime("%d.%m.%Y %H:%M")
            if snapshot.event_starts_at is not None
            else "не задано"
        )
        return (
            f"Состояние: {snapshot.state}; начало: {start}; "
            f"источник: {snapshot.event_source or 'не задан'}; "
            f"message_id: {snapshot.status_message_id}; "
            f"cmid: {snapshot.status_conversation_message_id}"
        )
