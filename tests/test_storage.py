"""Storage persistence and concurrency tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from src.storage import Storage


@pytest.mark.anyio
async def test_storage_round_trip_when_entries_are_added(tmp_path: Path) -> None:
    """Given persisted entries, reloading restores both entry types."""
    # Given
    path = tmp_path / "participants.json"
    storage = Storage(path)

    # When
    assert await storage.add_user(1, "Alice") is True
    await storage.add_friend("Bob")

    # Then
    entries = await Storage(path).list_entries()
    assert [entry.kind for entry in entries] == ["user", "friend"]
    assert [entry.name for entry in entries] == ["Alice", "Bob"]


@pytest.mark.anyio
async def test_storage_preserves_original_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a replace failure, the original persisted JSON remains intact."""
    # Given
    path = tmp_path / "participants.json"
    storage = Storage(path)
    assert await storage.add_user(1, "Alice") is True
    original = path.read_text(encoding="utf-8")

    def fail_replace(self: Path, _target: Path) -> Path:
        message = "cannot replace"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail_replace)

    # When / Then
    with pytest.raises(OSError, match="cannot replace"):
        await storage.add_friend("Bob")

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.anyio
async def test_storage_serializes_concurrent_mutations(tmp_path: Path) -> None:
    """Given concurrent writes, every entry persists without corruption."""
    # Given
    path = tmp_path / "participants.json"
    storage = Storage(path)

    # When
    results = await asyncio.gather(
        *(storage.add_user(vk_id, f"User {vk_id}") for vk_id in range(20))
    )

    # Then
    assert all(results)
    entries = await Storage(path).list_entries()
    assert len(entries) == 20
    assert {entry.vk_id for entry in entries if entry.kind == "user"} == set(range(20))


@pytest.mark.anyio
async def test_storage_returns_a_list_copy(tmp_path: Path) -> None:
    """Given a returned list is mutated, storage keeps its own entries."""
    # Given
    storage = Storage(tmp_path / "participants.json")
    assert await storage.add_user(1, "Alice") is True
    entries = await storage.list_entries()

    # When
    _ = entries.pop()

    # Then
    assert len(await storage.list_entries()) == 1


@pytest.mark.anyio
async def test_storage_rejects_name_exceeding_max_length(tmp_path: Path) -> None:
    """Given a name longer than the limit, adding raises ValueError."""
    storage = Storage(tmp_path / "participants.json")
    long_name = "A" * 101

    with pytest.raises(ValueError, match="Name exceeds"):
        await storage.add_friend(long_name)


@pytest.mark.anyio
async def test_storage_rejects_user_when_entry_limit_reached(tmp_path: Path) -> None:
    """Given the participant list is full, adding a user raises ValueError."""
    storage = Storage(tmp_path / "participants.json")
    for i in range(100):
        await storage.add_friend(f"Friend{i}")

    with pytest.raises(ValueError, match="Participant limit"):
        await storage.add_user(999, "Overflow")


@pytest.mark.anyio
async def test_storage_rejects_friend_when_entry_limit_reached(tmp_path: Path) -> None:
    """Given the participant list is full, adding a friend raises ValueError."""
    storage = Storage(tmp_path / "participants.json")
    for i in range(100):
        await storage.add_friend(f"Friend{i}")

    with pytest.raises(ValueError, match="Participant limit"):
        await storage.add_friend("Overflow")
