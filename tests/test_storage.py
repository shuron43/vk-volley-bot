"""Storage persistence and concurrency tests."""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from src.storage import Storage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

type _Mutation = Literal[
    "add_user",
    "remove_user",
    "add_friend",
    "remove_friend",
    "remove_by_name",
    "clear",
]


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
async def test_storage_loads_legacy_participants_as_closed(tmp_path: Path) -> None:
    """Given legacy JSON, loading supplies closed lifecycle defaults."""
    # Given
    path = tmp_path / "participants.json"
    path.write_text(
        '{"participants": [{"kind": "user", "vk_id": 1, "name": "Alice"}]}',
        encoding="utf-8",
    )

    # When
    storage = Storage(path)

    # Then
    assert await storage.registration_state() == "closed"
    assert await storage.status_message_id() is None
    assert [entry.name for entry in await storage.list_entries()] == ["Alice"]


@pytest.mark.anyio
async def test_storage_preserves_opening_for_recovery(tmp_path: Path) -> None:
    """An interrupted opening remains pending without clearing entries."""
    # Given
    path = tmp_path / "participants.json"
    path.write_text(
        """{
  "participants": [{"kind": "friend", "name": "Bob"}],
  "registration_state": "opening",
  "status_message_id": null
}""",
        encoding="utf-8",
    )

    # When
    storage = Storage(path)

    # Then
    assert await storage.registration_state() == "opening"
    assert await storage.is_registration_open() is False
    assert [entry.name for entry in await storage.list_entries()] == ["Bob"]


@pytest.mark.anyio
@pytest.mark.parametrize("finish_opening", [False, True])
async def test_guarded_registration_rejects_previous_collection(
    tmp_path: Path, finish_opening: bool
) -> None:
    storage = Storage(tmp_path / "participants.json")
    await storage.start_new_collection(1)
    collection = await storage.active_collection()
    assert collection is not None
    await storage.mark_opening()
    if finish_opening:
        await storage.start_new_collection(2)
    with pytest.raises(ValueError, match="сбор изменился"):
        await storage.add_user(1, "Alice", expected_collection=collection)
    with pytest.raises(ValueError, match="сбор изменился"):
        await storage.add_friend("Bob", expected_collection=collection)
    assert await storage.list_entries() == []


@pytest.mark.anyio
async def test_opening_identifier_survives_restart_and_mutations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "participants.json"
    storage = Storage(path)
    await storage.start_new_collection(1)
    collection = await storage.active_collection()
    await storage.mark_opening()
    random_id = await storage.announcement_random_id()
    assert random_id is not None
    await storage.clear()
    await storage.set_status_message_id(2)
    restored = Storage(path)
    await restored.mark_opening()
    assert await restored.announcement_random_id() == random_id
    await restored.start_new_collection(3)
    assert await restored.active_collection() != collection
    assert await restored.announcement_random_id() is None


@pytest.mark.anyio
async def test_storage_starts_new_collection_as_open(tmp_path: Path) -> None:
    """Given prior entries, starting a collection atomically clears and opens it."""
    # Given
    path = tmp_path / "participants.json"
    storage = Storage(path)
    await storage.add_friend("Bob")
    await storage.mark_opening()

    # When
    await storage.start_new_collection(123)

    # Then
    assert await storage.list_entries() == []
    assert await storage.registration_state() == "open"
    assert await storage.is_registration_open() is True
    assert await storage.status_message_id() == 123


@pytest.mark.anyio
async def test_storage_persists_status_message_id(tmp_path: Path) -> None:
    """Given a status message update, reloading returns the saved identifier."""
    # Given
    path = tmp_path / "participants.json"
    storage = Storage(path)

    # When
    await storage.set_status_message_id(456)

    # Then
    assert await Storage(path).status_message_id() == 456


@pytest.mark.anyio
async def test_storage_preserves_lifecycle_when_start_collection_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a failed collection start, live and saved state remain unchanged."""
    # Given
    path = tmp_path / "participants.json"
    storage = Storage(path)
    await storage.add_friend("Bob")
    original = path.read_text(encoding="utf-8")

    def fail_replace(self: Path, _target: Path) -> Path:
        message = "cannot replace"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail_replace)

    # When / Then
    with pytest.raises(OSError, match="cannot replace"):
        await storage.start_new_collection(123)

    assert [entry.name for entry in await storage.list_entries()] == ["Bob"]
    assert await storage.registration_state() == "closed"
    assert await storage.status_message_id() is None
    assert path.read_text(encoding="utf-8") == original
    persisted = Storage(path)
    assert [entry.name for entry in await persisted.list_entries()] == ["Bob"]
    assert await persisted.registration_state() == "closed"


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


@pytest.mark.parametrize(
    "mutation",
    [
        "add_user",
        "remove_user",
        "add_friend",
        "remove_friend",
        "remove_by_name",
        "clear",
    ],
)
@pytest.mark.anyio
async def test_storage_preserves_live_state_when_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: _Mutation,
) -> None:
    """A failed durable write leaves both live and persisted state unchanged."""
    # Given: two persisted entries and a failing atomic replacement
    path = tmp_path / "participants.json"
    storage = Storage(path)
    assert await storage.add_user(1, "Alice") is True
    await storage.add_friend("Bob")

    def fail_replace(self: Path, _target: Path) -> Path:
        message = "cannot replace"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail_replace)
    operation: Callable[[], Awaitable[object]]
    match mutation:
        case "add_user":
            operation = partial(storage.add_user, 2, "Carol")
        case "remove_user":
            operation = partial(storage.remove_user, 1)
        case "add_friend":
            operation = partial(storage.add_friend, "Carol")
        case "remove_friend":
            operation = partial(storage.remove_friend, "Bob")
        case "remove_by_name":
            operation = partial(storage.remove_by_name, "Alice")
        case "clear":
            operation = storage.clear
        case unreachable:
            assert_never(unreachable)

    # When: any mutating operation cannot persist its candidate state
    with pytest.raises(OSError, match="cannot replace"):
        await operation()

    # Then: neither the live object nor a fresh reader observes the mutation
    live_entries = await storage.list_entries()
    persisted_entries = await Storage(path).list_entries()
    assert [entry.name for entry in live_entries] == ["Alice", "Bob"]
    assert [entry.name for entry in persisted_entries] == ["Alice", "Bob"]


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


@pytest.mark.anyio
async def test_storage_remove_by_name_removes_user_or_friend(
    tmp_path: Path,
) -> None:
    """remove_by_name deletes the first entry matching the name."""
    storage = Storage(tmp_path / "participants.json")
    await storage.add_user(1, "Alice")
    await storage.add_friend("Bob")

    assert await storage.remove_by_name("Alice") is True
    entries = await storage.list_entries()
    assert [e.name for e in entries] == ["Bob"]

    assert await storage.remove_by_name("Bob") is True
    assert await storage.list_entries() == []

    assert await storage.remove_by_name("Charlie") is False
