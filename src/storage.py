"""JSON-backed storage for event participants."""

import asyncio
import logging
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

_MAX_NAME_LENGTH: Final = 100
_MAX_ENTRIES: Final = 100
_NAME_TOO_LONG_MSG = f"Name exceeds {_MAX_NAME_LENGTH} characters"
_LIMIT_REACHED_MSG = f"Participant limit ({_MAX_ENTRIES}) reached"
_LOGGER: Final = logging.getLogger(__name__)


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


class Storage:
    """Mutable accumulator of participant entries backed by a JSON file."""

    def __init__(self, path: Path) -> None:
        """Load existing data or start empty."""
        self._path: Path = path
        self._entries: list[Entry] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = _StorageData.model_validate_json(self._path.read_bytes())
        self._entries = list(data.participants)

    async def _save(self) -> None:
        data = _StorageData(participants=tuple(self._entries))
        temp_path = self._path.with_suffix(".tmp")

        def _write() -> None:
            _ = temp_path.write_text(
                data.model_dump_json(indent=2), encoding="utf-8"
            )
            _ = temp_path.replace(self._path)

        try:
            await asyncio.to_thread(_write)
        except OSError:
            _LOGGER.exception("Failed to persist storage to %s", self._path)
            raise

    async def add_user(self, vk_id: int, name: str) -> bool:
        """Add a VK user if not already present. Returns True if added."""
        if len(name) > _MAX_NAME_LENGTH:
            raise ValueError(_NAME_TOO_LONG_MSG)
        async with self._lock:
            if any(e.kind == "user" and e.vk_id == vk_id for e in self._entries):
                return False
            if len(self._entries) >= _MAX_ENTRIES:
                raise ValueError(_LIMIT_REACHED_MSG)
            self._entries.append(UserEntry(kind="user", vk_id=vk_id, name=name))
            await self._save()
            return True

    async def remove_user(self, vk_id: int) -> bool:
        """Remove a VK user. Returns True if removed."""
        async with self._lock:
            for i, e in enumerate(self._entries):
                if e.kind == "user" and e.vk_id == vk_id:
                    _ = self._entries.pop(i)
                    await self._save()
                    return True
            return False

    async def add_friend(self, name: str) -> None:
        """Add a friend by name."""
        if len(name) > _MAX_NAME_LENGTH:
            raise ValueError(_NAME_TOO_LONG_MSG)
        async with self._lock:
            if len(self._entries) >= _MAX_ENTRIES:
                raise ValueError(_LIMIT_REACHED_MSG)
            self._entries.append(FriendEntry(kind="friend", name=name))
            await self._save()

    async def remove_friend(self, name: str) -> bool:
        """Remove a friend by exact name. Returns True if removed."""
        async with self._lock:
            for i, e in enumerate(self._entries):
                if e.kind == "friend" and e.name == name:
                    _ = self._entries.pop(i)
                    await self._save()
                    return True
            return False

    async def list_entries(self) -> list[Entry]:
        """Return a shallow copy of current entries."""
        async with self._lock:
            return list(self._entries)

    async def clear(self) -> None:
        """Clear all entries and persist."""
        async with self._lock:
            self._entries.clear()
            await self._save()
