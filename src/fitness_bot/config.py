from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_BASE_URL", "OPENAI_API_BASE")
    )
    openai_model: str = "gpt-4o-mini"
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = (
        None
    )
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    chat_timeout_seconds: float = Field(default=90, gt=0, le=600)

    @property
    def model_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())
