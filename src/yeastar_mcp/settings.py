"""Runtime configuration loaded from environment variables."""

from functools import cached_property

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Yeastar connection settings.

    Empty values are treated as missing so that `.env.example` can be sourced
    and `doctor` can still run before credentials are issued.
    """

    model_config = SettingsConfigDict(
        env_prefix="YEASTAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    verify_ssl: bool = True
    timeout_seconds: float = 30.0
    allow_raw_numbers: bool = False

    @field_validator("base_url", "client_id", "client_secret", mode="before")
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else None

    @cached_property
    def credentials_configured(self) -> bool:
        return bool(self.base_url and self.client_id and self.client_secret)
