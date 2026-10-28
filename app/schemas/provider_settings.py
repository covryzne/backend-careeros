from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.core.config import settings
from app.schemas.agent import StrictInput


class ProviderSettingsPatch(StrictInput):
    expected_version: int = Field(ge=0)
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip("/")
        try:
            url = urlsplit(value)
            _ = url.port
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and not any((url.username, url.password, url.query, url.fragment))
            )
        except ValueError:
            valid = False
        if not valid or any(char.isspace() for char in value) or "\\" in value:
            raise ValueError("Gunakan base URL HTTP/HTTPS tanpa kredensial, query, atau fragment")
        if url.scheme != "https" and settings.app_env not in {"development", "test"}:
            raise ValueError("Provider harus menggunakan HTTPS")
        return value

    @field_validator("model")
    @classmethod
    def valid_model(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Nama model tidak valid")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value is None or any(
            char.isspace() or not 33 <= ord(char) <= 126 for char in value.get_secret_value()
        ):
            raise ValueError("API key tidak boleh kosong atau mengandung whitespace")
        return value


class ProviderSettingsResponse(BaseModel):
    base_url: str
    model: str
    api_key_set: bool
    configured: bool
    source: Literal["saved", "environment", "none"]
    version: int
