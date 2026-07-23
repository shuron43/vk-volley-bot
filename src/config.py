"""Application configuration loaded from environment variables."""

from pathlib import Path
from typing import ClassVar, Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MAX_WEEKDAY: Final = 6
_WEEKDAY_ERROR_MSG = "Weekday must be between 0 (Monday) and 6 (Sunday)"
_COLLECT_TIME_ERROR_MSG = (
    "collect_time must be HH:MM with hour 00-23 and minute 00-59"
)
_MAX_HOUR: Final = 23
_MAX_MINUTE: Final = 59
_DATA_PATH_ERROR_MSG = "data_path must not contain path traversal"


class Config(BaseSettings):
    """Bot configuration."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=False,
    )

    vk_token: str = Field(description="VK community API token")
    chat_peer_id: int = Field(
        ge=1,
        description="VK chat peer_id where the bot operates",
    )
    collect_weekday: int = Field(
        default=2,
        description="Weekday to start collection (0=Monday, 6=Sunday)",
    )
    collect_time: str = Field(
        default="10:00",
        description="Local time to announce collection HH:MM",
    )
    remind_enabled: bool = Field(
        default=True,
        description="Send a reminder message on a fixed weekday before collection",
    )
    remind_weekday: int = Field(
        default=0,
        description="Weekday for reminder (0=Monday, 6=Sunday)",
    )
    remind_time: str = Field(
        default="08:00",
        description="Local time for reminder HH:MM",
    )
    data_path: str = Field(
        default="data.json",
        description="Path to JSON storage file",
    )

    @property
    def path(self) -> Path:
        """Return ``data_path`` as a ``pathlib.Path``."""
        return Path(self.data_path)

    @property
    def collect_hour(self) -> int:
        """Hour component of ``collect_time`` (0-23)."""
        return int(self.collect_time.split(":")[0])

    @property
    def collect_minute(self) -> int:
        """Minute component of ``collect_time`` (0-59)."""
        return int(self.collect_time.split(":")[1])

    @property
    def remind_hour(self) -> int:
        """Hour component of ``remind_time`` (0-23)."""
        return int(self.remind_time.split(":")[0])

    @property
    def remind_minute(self) -> int:
        """Minute component of ``remind_time`` (0-59)."""
        return int(self.remind_time.split(":")[1])

    @field_validator("data_path")
    @classmethod
    def _validate_data_path(cls, v: str) -> str:
        if any(part == ".." for part in Path(v).parts):
            raise ValueError(_DATA_PATH_ERROR_MSG)
        return v

    @field_validator("collect_weekday", "remind_weekday")
    @classmethod
    def _validate_weekday(cls, v: int) -> int:
        if not 0 <= v <= _MAX_WEEKDAY:
            raise ValueError(_WEEKDAY_ERROR_MSG)
        return v

    @field_validator("collect_time", "remind_time")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        if v.count(":") != 1:
            raise ValueError(_COLLECT_TIME_ERROR_MSG)

        parts = v.split(":")
        hour_text, minute_text = parts
        if len(hour_text) != 2 or len(minute_text) != 2:  # noqa: PLR2004
            raise ValueError(_COLLECT_TIME_ERROR_MSG)
        if not hour_text.isdigit() or not minute_text.isdigit():
            raise ValueError(_COLLECT_TIME_ERROR_MSG)

        hour = int(hour_text)
        minute = int(minute_text)
        if not 0 <= hour <= _MAX_HOUR or not 0 <= minute <= _MAX_MINUTE:
            raise ValueError(_COLLECT_TIME_ERROR_MSG)

        return v
