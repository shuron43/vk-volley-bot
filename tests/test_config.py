"""Tests for application configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from src.config import Config


def _set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VK_TOKEN", "token")
    monkeypatch.setenv("CHAT_PEER_ID", "2000000001")


@pytest.mark.parametrize("collect_time", ["10:00", "00:00", "23:59"])
def test_config_accepts_valid_collect_time(
    monkeypatch: pytest.MonkeyPatch,
    collect_time: str,
) -> None:
    """Config accepts valid HH:MM values."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("COLLECT_TIME", collect_time)

    config = Config()

    assert config.collect_time == collect_time


@pytest.mark.parametrize(
    "collect_time",
    ["24:00", "99:99", "7:30", "10:0", "10", "abc", "10:70"],
)
def test_config_rejects_invalid_collect_time(
    monkeypatch: pytest.MonkeyPatch,
    collect_time: str,
) -> None:
    """Config rejects malformed or out-of-range collect times."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("COLLECT_TIME", collect_time)

    with pytest.raises(ValidationError, match="collect_time"):
        Config()


def test_config_path_returns_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a data_path string, the path property returns a Path."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("DATA_PATH", "app/data/participants.json")
    config = Config()
    assert config.path == Path("app/data/participants.json")


def test_config_collect_time_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a valid collect_time, hour and minute properties parse correctly."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("COLLECT_TIME", "14:30")
    config = Config()
    assert config.collect_hour == 14
    assert config.collect_minute == 30


@pytest.mark.parametrize("bad_peer_id", ["0", "-1", "-2000000001"])
def test_config_rejects_non_positive_chat_peer_id(
    monkeypatch: pytest.MonkeyPatch,
    bad_peer_id: str,
) -> None:
    """Config rejects non-positive chat_peer_id values."""
    monkeypatch.setenv("VK_TOKEN", "token")
    monkeypatch.setenv("CHAT_PEER_ID", bad_peer_id)

    with pytest.raises(ValidationError, match="chat_peer_id"):
        Config()


@pytest.mark.parametrize("bad_path", ["../data.json", "foo/../bar.json"])
def test_config_rejects_data_path_with_traversal(
    monkeypatch: pytest.MonkeyPatch,
    bad_path: str,
) -> None:
    """Config rejects data_path containing path traversal."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("DATA_PATH", bad_path)

    with pytest.raises(ValidationError, match="data_path"):
        Config()
