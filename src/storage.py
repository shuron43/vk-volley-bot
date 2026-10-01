"""JSON-backed storage for event participants."""

import asyncio
import datetime
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

_MAX_NAME_LENGTH: Final = 100
_MAX_ENTRIES: Final = 100
_NAME_TOO_LONG_MSG = f"Name exceeds {_MAX_NAME_LENGTH} characters"
_LIMIT_REACHED_MSG = f"Participant limit ({_MAX_ENTRIES}) reached"
_LOGGER: Final = logging.getLogger(__name__)
_REGISTRATION_CHANGED_MSG = (
    "Запись закрыта или сбор изменился. Попробуй записаться снова."
)
_LATE_CHANGE_WINDOW: Final = datetime.timedelta(minutes=30)
_LATE_CHANGE_CLOSED_MSG = (
    "Запись закрыта. Отписаться или вернуться можно только в первые 30 минут "
    "с начала события."
)


class UserEntry(BaseModel):
    """A VK user participant."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    kind: Literal["user"]
    vk_id: int
    name: str
    withdrawn: bool = False


class FriendEntry(BaseModel):
    """A friend participant recorded by name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    kind: Literal["friend"]
    name: str
    withdrawn: bool = False


Entry = UserEntry | FriendEntry
EventSource = Literal["weekly", "manual"]
RegistrationState = Literal["closed", "opening", "open"]


@dataclass(frozen=True, slots=True)
class RegistrationSnapshot:
    """Consistent view of the event and its canonical VK card."""

    participants: tuple[Entry, ...]
    state: RegistrationState
    status_message_id: int | None
    status_conversation_message_id: int | None
    collection_id: int
    event_starts_at: datetime.datetime | None
    event_source: EventSource | None


class _StorageData(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    participants: tuple[Annotated[Entry, Field(discriminator="kind")], ...]
    registration_state: RegistrationState = "closed"
    status_message_id: int | None = None
    status_conversation_message_id: int | None = None
    collection_id: int = 0
    announcement_random_id: int | None = None
    event_starts_at: datetime.datetime | None = None
    event_source: EventSource | None = None


class Storage:
    """Mutable accumulator of participant entries backed by a JSON file."""

    def __init__(self, path: Path) -> None:
        """Load existing data or start empty."""
        self._path: Path = path
        self._entries: list[Entry] = []
        self._registration_state: RegistrationState = "closed"
        self._status_message_id: int | None = None
        self._status_conversation_message_id: int | None = None
        self._collection_id: int = 0
        self._announcement_random_id: int | None = None
        self._event_starts_at: datetime.datetime | None = None
        self._event_source: EventSource | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._schedule_changed: asyncio.Event = asyncio.Event()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            _LOGGER.info("No data file at %s, starting with empty list", self._path)
            return
        data = _StorageData.model_validate_json(self._path.read_bytes())
        self._entries = list(data.participants)
        self._registration_state = data.registration_state
        self._collection_id = data.collection_id
        self._announcement_random_id = data.announcement_random_id
        self._status_message_id = data.status_message_id
        self._status_conversation_message_id = data.status_conversation_message_id
        self._event_starts_at = data.event_starts_at
        self._event_source = data.event_source
        _LOGGER.info("Loaded %d entries from %s", len(self._entries), self._path)

    async def _save(self, data: _StorageData) -> None:
        temp_path = self._path.with_suffix(".tmp")

        def _write() -> None:
            _ = temp_path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
            _ = temp_path.replace(self._path)

        try:
            await asyncio.to_thread(_write)
        except OSError:
            _LOGGER.exception("Failed to persist storage to %s", self._path)
            raise
        _LOGGER.debug("Saved %d entries to %s", len(data.participants), self._path)

    async def _commit(self, data: _StorageData) -> None:
        await self._save(data)
        schedule_changed = (
            self._registration_state != data.registration_state
            or self._event_starts_at != data.event_starts_at
            or self._collection_id != data.collection_id
        )
        self._entries = list(data.participants)
        self._registration_state = data.registration_state
        self._status_message_id = data.status_message_id
        self._status_conversation_message_id = data.status_conversation_message_id
        self._collection_id = data.collection_id
        self._announcement_random_id = data.announcement_random_id
        self._event_starts_at = data.event_starts_at
        self._event_source = data.event_source
        if schedule_changed:
            self._schedule_changed.set()
            self._schedule_changed = asyncio.Event()

    async def schedule_change_event(self) -> asyncio.Event:
        """Return a signal set by the next durable lifecycle change."""
        async with self._lock:
            return self._schedule_changed

    async def _commit_entries(self, entries: list[Entry]) -> None:
        await self._commit(
            _StorageData(
                participants=tuple(entries),
                registration_state=self._registration_state,
                status_message_id=self._status_message_id,
                status_conversation_message_id=self._status_conversation_message_id,
                collection_id=self._collection_id,
                announcement_random_id=self._announcement_random_id,
                event_starts_at=self._event_starts_at,
                event_source=self._event_source,
            )
        )

    async def add_user(
        self, vk_id: int, name: str, *, expected_collection: int | None = None
    ) -> bool:
        """Add a VK user if not already present. Returns True if added."""
        if len(name) > _MAX_NAME_LENGTH:
            raise ValueError(_NAME_TOO_LONG_MSG)
        async with self._lock:
            self._check_collection(expected_collection)
            if any(e.kind == "user" and e.vk_id == vk_id for e in self._entries):
                return False
            if len(self._entries) >= _MAX_ENTRIES:
                raise ValueError(_LIMIT_REACHED_MSG)
            entries = [
                *self._entries,
                UserEntry(kind="user", vk_id=vk_id, name=name),
            ]
            await self._commit_entries(entries)
            return True

    async def remove_user(self, vk_id: int) -> bool:
        """Remove a VK user. Returns True if removed."""
        async with self._lock:
            for i, e in enumerate(self._entries):
                if e.kind == "user" and e.vk_id == vk_id:
                    entries = self._entries.copy()
                    _ = entries.pop(i)
                    await self._commit_entries(entries)
                    return True
            return False

    def _now(self) -> datetime.datetime:
        """Read local time with the persisted event's timezone awareness."""
        tz = self._event_starts_at.tzinfo if self._event_starts_at else None
        return datetime.datetime.now(tz=tz)

    def _registration_is_open(self, now: datetime.datetime) -> bool:
        return self._registration_state == "open" and (
            self._event_starts_at is None or now < self._event_starts_at
        )

    def _check_late_change(self, now: datetime.datetime) -> None:
        if (
            self._registration_state not in {"open", "closed"}
            or self._event_starts_at is None
            or not (
                self._event_starts_at
                <= now
                < self._event_starts_at + _LATE_CHANGE_WINDOW
            )
        ):
            raise ValueError(_LATE_CHANGE_CLOSED_MSG)

    async def _withdraw_entry(self, index: int) -> bool:
        """Remove before start or mark after start, while holding the lock."""
        now = self._now()
        entries = self._entries.copy()
        entry = entries.pop(index)
        if not self._registration_is_open(now):
            self._check_late_change(now)
            if entry.withdrawn:
                return False
            entries.append(entry.model_copy(update={"withdrawn": True}))
        await self._commit_entries(entries)
        return True

    async def _restore_entry(self, index: int) -> bool:
        """Restore a marked participant within the grace period under the lock."""
        self._check_late_change(self._now())
        entries = self._entries.copy()
        entry = entries.pop(index)
        entries.append(entry.model_copy(update={"withdrawn": False}))
        entries.sort(key=lambda participant: participant.withdrawn)
        await self._commit_entries(entries)
        return True

    async def withdraw_user(self, vk_id: int) -> bool:
        """Withdraw a user without discarding attendance history after start."""
        async with self._lock:
            for index, entry in enumerate(self._entries):
                if entry.kind == "user" and entry.vk_id == vk_id:
                    return await self._withdraw_entry(index)
            return False

    async def restore_user(self, vk_id: int) -> bool:
        """Return a previously withdrawn user during the first half hour."""
        async with self._lock:
            for index, entry in enumerate(self._entries):
                if entry.kind == "user" and entry.vk_id == vk_id and entry.withdrawn:
                    return await self._restore_entry(index)
            return False

    async def withdraw_friend(self, name: str) -> bool:
        """Withdraw the first named friend, retaining their row after start."""
        async with self._lock:
            for index, entry in enumerate(self._entries):
                if entry.kind == "friend" and entry.name == name:
                    return await self._withdraw_entry(index)
            return False

    async def restore_friend(self, name: str) -> bool:
        """Return the first withdrawn friend with the requested name."""
        async with self._lock:
            for index, entry in enumerate(self._entries):
                if entry.kind == "friend" and entry.name == name and entry.withdrawn:
                    return await self._restore_entry(index)
            return False

    async def add_friend(
        self, name: str, *, expected_collection: int | None = None
    ) -> None:
        """Add a friend by name."""
        if len(name) > _MAX_NAME_LENGTH:
            raise ValueError(_NAME_TOO_LONG_MSG)
        async with self._lock:
            self._check_collection(expected_collection)
            if len(self._entries) >= _MAX_ENTRIES:
                raise ValueError(_LIMIT_REACHED_MSG)
            entries = [*self._entries, FriendEntry(kind="friend", name=name)]
            await self._commit_entries(entries)

    async def remove_friend(self, name: str) -> bool:
        """Remove a friend by exact name. Returns True if removed."""
        async with self._lock:
            for i, e in enumerate(self._entries):
                if e.kind == "friend" and e.name == name:
                    entries = self._entries.copy()
                    _ = entries.pop(i)
                    await self._commit_entries(entries)
                    return True
            return False

    async def list_entries(self) -> list[Entry]:
        """Return a shallow copy of current entries."""
        async with self._lock:
            return list(self._entries)

    async def registration_state(self) -> RegistrationState:
        """Return the current registration lifecycle state."""
        async with self._lock:
            return self._registration_state

    async def status_message_id(self) -> int | None:
        """Return the current registration status message identifier."""
        async with self._lock:
            return self._status_message_id

    async def status_conversation_message_id(self) -> int | None:
        """Return the canonical card identifier inside the conversation."""
        async with self._lock:
            return self._status_conversation_message_id

    async def snapshot(self) -> RegistrationSnapshot:
        """Return one lock-consistent snapshot for rendering and diagnostics."""
        async with self._lock:
            return RegistrationSnapshot(
                participants=tuple(self._entries),
                state=self._registration_state,
                status_message_id=self._status_message_id,
                status_conversation_message_id=(self._status_conversation_message_id),
                collection_id=self._collection_id,
                event_starts_at=self._event_starts_at,
                event_source=self._event_source,
            )

    async def mark_opening(
        self,
        event_starts_at: datetime.datetime | None = None,
        event_source: EventSource | None = None,
    ) -> None:
        """Persist that a new collection is being prepared."""
        async with self._lock:
            await self._commit(
                _StorageData(
                    participants=tuple(self._entries),
                    registration_state="opening",
                    status_message_id=self._status_message_id,
                    status_conversation_message_id=self._status_conversation_message_id,
                    collection_id=self._collection_id,
                    announcement_random_id=(
                        self._announcement_random_id
                        or secrets.randbelow(2_147_483_646) + 1
                    ),
                    event_starts_at=event_starts_at or self._event_starts_at,
                    event_source=event_source or self._event_source,
                )
            )

    async def begin_event(
        self, event_starts_at: datetime.datetime, event_source: EventSource
    ) -> bool:
        """Start preparing an event unless another registration is active."""
        async with self._lock:
            if self._registration_state in {"opening", "open"}:
                return False
            await self._commit(
                _StorageData(
                    participants=tuple(self._entries),
                    registration_state="opening",
                    status_message_id=self._status_message_id,
                    status_conversation_message_id=(
                        self._status_conversation_message_id
                    ),
                    collection_id=self._collection_id,
                    announcement_random_id=secrets.randbelow(2_147_483_646) + 1,
                    event_starts_at=event_starts_at,
                    event_source=event_source,
                )
            )
            return True

    async def start_new_collection(self, status_message_id: int | None) -> None:
        """Atomically clear participants and open a new collection."""
        async with self._lock:
            await self._commit(
                _StorageData(
                    participants=(),
                    registration_state="open",
                    status_message_id=status_message_id,
                    status_conversation_message_id=None,
                    collection_id=self._collection_id + 1,
                    event_starts_at=self._event_starts_at,
                    event_source=self._event_source,
                )
            )

    async def activate_event(
        self,
        status_message_id: int | None,
        expected_start: datetime.datetime,
        *,
        conversation_message_id: int | None = None,
    ) -> None:
        """Activate the pending event only if it is still current."""
        async with self._lock:
            if (
                self._registration_state != "opening"
                or self._event_starts_at != expected_start
            ):
                raise ValueError(_REGISTRATION_CHANGED_MSG)
            await self._commit(
                _StorageData(
                    participants=(),
                    registration_state="open",
                    status_message_id=status_message_id,
                    status_conversation_message_id=conversation_message_id,
                    collection_id=self._collection_id + 1,
                    event_starts_at=self._event_starts_at,
                    event_source=self._event_source,
                )
            )

    async def delete_event(self, *, retain_card: bool = False) -> bool:
        """Cancel the event; optionally retain card IDs until VK deletes it."""
        async with self._lock:
            existed = bool(
                self._event_starts_at
                or self._entries
                or self._status_message_id
                or self._status_conversation_message_id
                or self._registration_state != "closed"
            )
            if existed:
                await self._commit(
                    _StorageData(
                        participants=(),
                        collection_id=self._collection_id + 1,
                        status_message_id=(
                            self._status_message_id if retain_card else None
                        ),
                        status_conversation_message_id=(
                            self._status_conversation_message_id
                            if retain_card
                            else None
                        ),
                    )
                )
            return existed

    async def close_registration(self) -> bool:
        """Close the current event without discarding its final participant list."""
        async with self._lock:
            if self._registration_state == "closed":
                return False
            await self._commit(
                _StorageData(
                    participants=tuple(self._entries),
                    registration_state="closed",
                    status_message_id=self._status_message_id,
                    status_conversation_message_id=(
                        self._status_conversation_message_id
                    ),
                    collection_id=self._collection_id,
                    event_starts_at=self._event_starts_at,
                    event_source=self._event_source,
                )
            )
            return True

    async def close_missing_deadline(self) -> bool:
        """Close legacy active state that cannot be scheduled safely."""
        async with self._lock:
            if (
                self._registration_state == "closed"
                or self._event_starts_at is not None
            ):
                return False
            _LOGGER.warning(
                "Closing legacy %s registration without event_starts_at",
                self._registration_state,
            )
            await self._commit(
                _StorageData(
                    participants=tuple(self._entries),
                    registration_state="closed",
                    status_message_id=self._status_message_id,
                    status_conversation_message_id=(
                        self._status_conversation_message_id
                    ),
                    collection_id=self._collection_id,
                    event_source=self._event_source,
                )
            )
            return True

    async def set_status_message_id(
        self, message_id: int | None, *, conversation_message_id: int | None = None
    ) -> None:
        """Persist the message identifier for the current registration status."""
        async with self._lock:
            await self._commit(
                _StorageData(
                    participants=tuple(self._entries),
                    registration_state=self._registration_state,
                    status_message_id=message_id,
                    status_conversation_message_id=conversation_message_id,
                    collection_id=self._collection_id,
                    announcement_random_id=self._announcement_random_id,
                    event_starts_at=self._event_starts_at,
                    event_source=self._event_source,
                )
            )

    async def is_registration_open(self) -> bool:
        """Return whether the current collection accepts registrations."""
        async with self._lock:
            return self._registration_is_open(self._now())

    async def active_collection(self) -> int | None:
        """Return the open collection token for a guarded registration."""
        async with self._lock:
            return (
                self._collection_id if self._registration_is_open(self._now()) else None
            )

    async def announcement_random_id(self) -> int | None:
        """Return the durable identifier of the pending announcement."""
        async with self._lock:
            return self._announcement_random_id

    async def event_details(
        self,
    ) -> tuple[datetime.datetime | None, EventSource | None]:
        """Return the current or most recently closed event metadata."""
        async with self._lock:
            return self._event_starts_at, self._event_source

    def _check_collection(self, expected_collection: int | None) -> None:
        if expected_collection is not None and (
            not self._registration_is_open(self._now())
            or self._collection_id != expected_collection
        ):
            raise ValueError(_REGISTRATION_CHANGED_MSG)

    async def remove_by_name(self, name: str) -> bool:
        """Remove the first entry matching *name* (user or friend)."""
        async with self._lock:
            for i, e in enumerate(self._entries):
                if e.name == name:
                    entries = self._entries.copy()
                    _ = entries.pop(i)
                    await self._commit_entries(entries)
                    return True
            return False

    async def clear(self) -> None:
        """Clear all entries and persist."""
        async with self._lock:
            await self._commit_entries([])
