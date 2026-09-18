from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.mysql"), env_file_encoding="utf-8", extra="ignore"
    )

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_BASE_URL", "OPENAI_API_BASE")
    )
    openai_model: str = "gpt-4o-mini"
    openai_use_responses_api: bool = False
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = (
        None
    )
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    chat_timeout_seconds: float = Field(default=90, gt=0, le=600)
    database_url: SecretStr | None = None
    auth_enabled: bool = False
    default_user_id: str = Field(default="local-user", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    image_directory: Path = Path("runtime/uploads")
    exercise_media_directory: Path = (
        Path(__file__).resolve().parents[3] / "flutter-init/assets/exercises/images"
    )

    @property
    def model_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())
