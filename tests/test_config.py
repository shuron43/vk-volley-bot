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


def test_config_event_defaults_and_time_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.delenv("EVENT_WEEKDAY", raising=False)
    monkeypatch.delenv("EVENT_TIME", raising=False)
    config = Config()
    assert config.event_weekday == 1
    assert config.event_time == "19:30"
    assert config.event_hour == 19
    assert config.event_minute == 30


def test_config_rejects_event_at_announcement_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("COLLECT_WEEKDAY", "1")
    monkeypatch.setenv("COLLECT_TIME", "19:30")
    monkeypatch.setenv("EVENT_WEEKDAY", "1")
    monkeypatch.setenv("EVENT_TIME", "19:30")
    with pytest.raises(ValidationError, match="different moments"):
        Config()


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


@pytest.mark.parametrize("remind_time", ["08:00", "00:00", "23:59"])
def test_config_accepts_valid_remind_time(
    monkeypatch: pytest.MonkeyPatch,
    remind_time: str,
) -> None:
    """Config accepts valid HH:MM values for remind_time."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("REMIND_TIME", remind_time)

    config = Config()
    assert config.remind_time == remind_time


@pytest.mark.parametrize(
    "remind_time",
    ["24:00", "99:99", "7:30", "10:0", "10", "abc", "10:70"],
)
def test_config_rejects_invalid_remind_time(
    monkeypatch: pytest.MonkeyPatch,
    remind_time: str,
) -> None:
    """Config rejects malformed or out-of-range remind times."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("REMIND_TIME", remind_time)

    with pytest.raises(ValidationError, match="remind_time"):
        Config()


@pytest.mark.parametrize("remind_weekday", [0, 3, 6])
def test_config_accepts_valid_remind_weekday(
    monkeypatch: pytest.MonkeyPatch,
    remind_weekday: int,
) -> None:
    """Config accepts valid weekday values for remind_weekday."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("REMIND_WEEKDAY", str(remind_weekday))
    monkeypatch.setenv("REMIND_TIME", "09:00")

    config = Config()
    assert config.remind_weekday == remind_weekday


@pytest.mark.parametrize("remind_weekday", [-1, 7, 10])
def test_config_rejects_invalid_remind_weekday(
    monkeypatch: pytest.MonkeyPatch,
    remind_weekday: int,
) -> None:
    """Config rejects out-of-range remind_weekday values."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("REMIND_WEEKDAY", str(remind_weekday))

    with pytest.raises(ValidationError, match="remind_weekday"):
        Config()


def test_config_remind_time_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a valid remind_time, hour and minute properties parse correctly."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("REMIND_TIME", "07:45")
    config = Config()
    assert config.remind_hour == 7
    assert config.remind_minute == 45


def test_config_remind_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reminder settings have sensible defaults when not overridden."""
    _set_base_env(monkeypatch)
    config = Config()
    assert config.remind_enabled is True
    assert config.remind_weekday == 1
    assert config.remind_time == "08:00"
    assert config.remind_hour == 8
    assert config.remind_minute == 0


def test_config_rejects_enabled_reminder_at_collection_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled collection and reminder events cannot occupy one instant."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("COLLECT_WEEKDAY", "2")
    monkeypatch.setenv("COLLECT_TIME", "10:00")
    monkeypatch.setenv("REMIND_ENABLED", "true")
    monkeypatch.setenv("REMIND_WEEKDAY", "2")
    monkeypatch.setenv("REMIND_TIME", "10:00")

    with pytest.raises(ValidationError, match="remind_time"):
        Config()


@pytest.mark.parametrize(
    "admin_vk_ids_raw",
    ["", "123456789", "123456789,987654321"],
)
def test_config_accepts_valid_admin_vk_ids(
    monkeypatch: pytest.MonkeyPatch,
    admin_vk_ids_raw: str,
) -> None:
    """Config parses comma-separated admin VK IDs."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_VK_IDS_RAW", admin_vk_ids_raw)

    config = Config()
    expected = (
        tuple(int(x) for x in admin_vk_ids_raw.split(",")) if admin_vk_ids_raw else ()
    )
    assert config.admin_vk_ids == expected


@pytest.mark.parametrize(
    "admin_vk_ids_raw",
    ["123,", ",123", "123,,456", "abc", "0", "-1"],
)
def test_config_rejects_invalid_admin_vk_ids_during_construction(
    monkeypatch: pytest.MonkeyPatch,
    admin_vk_ids_raw: str,
) -> None:
    """Malformed or non-positive admin IDs fail at the settings boundary."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_VK_IDS_RAW", admin_vk_ids_raw)

    with pytest.raises(ValidationError, match="ADMIN_VK_IDS_RAW"):
        Config()


def test_config_is_admin_checks_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_admin returns True only for configured VK IDs."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_VK_IDS_RAW", "123,456")

    config = Config()
    assert config.is_admin(123) is True
    assert config.is_admin(456) is True
    assert config.is_admin(789) is False
