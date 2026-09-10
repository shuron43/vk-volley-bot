"""JSON-backed storage for event participants."""

import asyncio
import logging
import secrets
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


class UserEntry(BaseModel):
    """A VK user participant."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    kind: Literal["user"]
    vk_id: int
    name: str


class FriendEntry(BaseModel):
    """A friend participant recorded by name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    kind: Literal["friend"]
    name: str


Entry = UserEntry | FriendEntry


class _StorageData(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    participants: tuple[Annotated[Entry, Field(discriminator="kind")], ...]
    registration_state: Literal["closed", "opening", "open"] = "closed"
    status_message_id: int | None = None
    collection_id: int = 0
    announcement_random_id: int | None = None


class Storage:
    """Mutable accumulator of participant entries backed by a JSON file."""

    def __init__(self, path: Path) -> None:
        """Load existing data or start empty."""
        self._path: Path = path
        self._entries: list[Entry] = []
        self._registration_state: Literal["closed", "opening", "open"] = "closed"
        self._status_message_id: int | None = None
        self._collection_id: int = 0
        self._announcement_random_id: int | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
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
        self._entries = list(data.participants)
        self._registration_state = data.registration_state
        self._status_message_id = data.status_message_id
        self._collection_id = data.collection_id
        self._announcement_random_id = data.announcement_random_id

    async def _commit_entries(self, entries: list[Entry]) -> None:
        await self._commit(
            _StorageData(
                participants=tuple(entries),
                registration_state=self._registration_state,
                status_message_id=self._status_message_id,
                collection_id=self._collection_id,
                announcement_random_id=self._announcement_random_id,
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

    async def registration_state(self) -> Literal["closed", "opening", "open"]:
        """Return the current registration lifecycle state."""
        async with self._lock:
            return self._registration_state

    async def status_message_id(self) -> int | None:
        """Return the current registration status message identifier."""
        async with self._lock:
            return self._status_message_id

    async def mark_opening(self) -> None:
        """Persist that a new collection is being prepared."""
        async with self._lock:
            await self._commit(
                _StorageData(
                    participants=tuple(self._entries),
                    registration_state="opening",
                    status_message_id=self._status_message_id,
                    collection_id=self._collection_id,
                    announcement_random_id=(
                        self._announcement_random_id
                        or secrets.randbelow(2_147_483_646) + 1
                    ),
                )
            )

    async def start_new_collection(self, status_message_id: int | None) -> None:
        """Atomically clear participants and open a new collection."""
        async with self._lock:
            await self._commit(
                _StorageData(
                    participants=(),
                    registration_state="open",
                    status_message_id=status_message_id,
                    collection_id=self._collection_id + 1,
                )
            )

    async def set_status_message_id(self, message_id: int | None) -> None:
        """Persist the message identifier for the current registration status."""
        async with self._lock:
            await self._commit(
                _StorageData(
                    participants=tuple(self._entries),
                    registration_state=self._registration_state,
                    status_message_id=message_id,
                    collection_id=self._collection_id,
                    announcement_random_id=self._announcement_random_id,
                )
            )

    async def is_registration_open(self) -> bool:
        """Return whether the current collection accepts registrations."""
        async with self._lock:
            return self._registration_state == "open"

    async def active_collection(self) -> int | None:
        """Return the open collection token for a guarded registration."""
        async with self._lock:
            return self._collection_id if self._registration_state == "open" else None

    async def announcement_random_id(self) -> int | None:
        """Return the durable identifier of the pending announcement."""
        async with self._lock:
            return self._announcement_random_id

    def _check_collection(self, expected_collection: int | None) -> None:
        if expected_collection is not None and (
            self._registration_state != "open"
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
