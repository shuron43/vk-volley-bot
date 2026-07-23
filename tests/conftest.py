"""Shared pytest fixtures."""

from __future__ import annotations

import secrets
from pathlib import Path  # noqa: TC003

import pytest
from src.config import Config
from src.storage import Storage


@pytest.fixture
def fake_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    """Return a ``Config`` with minimal required env vars set."""
    monkeypatch.setenv("VK_TOKEN", secrets.token_urlsafe())
    monkeypatch.setenv("CHAT_PEER_ID", "2000000001")
    return Config()


@pytest.fixture
async def tmp_storage(tmp_path: Path) -> Storage:
    """Return a ``Storage`` backed by a temporary file."""
    return Storage(tmp_path / "participants.json")
