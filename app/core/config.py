from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: Literal["development", "test", "staging", "production"] = "development"
    allow_local_identity: bool = True
    supabase_url: str | None = None
    supabase_jwks_url: str | None = None
    supabase_jwt_audience: str = "authenticated"
    auth_jwks_timeout_seconds: float = Field(default=5, gt=0, le=30)
    auth_jwks_cache_seconds: int = Field(default=300, ge=1, le=3600)
    require_profile_version: bool = False
    job_matching_enabled: bool = False
    agent_runtime_allowed: bool = True
    agent_worker_embedded: bool = True
    agent_enabled: bool = False
    agent_internal_writes_enabled: bool = False
    agent_approvals_enabled: bool = False
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    provider_encryption_key: SecretStr | None = None
    agent_max_tool_calls: int = Field(default=12, ge=1, le=12)
    agent_max_turns: int = Field(default=8, ge=1, le=8)
    agent_timeout_seconds: int = Field(default=90, ge=1, le=90)
    document_storage_dir: Path = BACKEND_ROOT / ".private-documents"
    document_max_bytes: int = 10 * 1024 * 1024
    database_url: str
    crawler_root: Path = BACKEND_ROOT.parent / "careeros-crawler"
    crawler_python: Path | None = None
    crawler_browser_mode: str = Field(default="auto", pattern="^(auto|headed|headless)$")
    crawler_run_timeout_seconds: int = Field(default=3600, ge=60, le=14400)
    crawler_lease_seconds: int = Field(default=30, ge=15, le=120)
    api_v1_prefix: str = "/api/v1"
    allowed_origins: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def auth_endpoints(self) -> tuple[str, str]:
        """Only deployment configuration chooses the trusted issuer and keys URL."""
        base = (self.supabase_url or "").rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("SUPABASE_URL must be an HTTPS project origin")
        issuer = f"{base}/auth/v1"
        jwks = f"{issuer}/.well-known/jwks.json"
        if self.supabase_jwks_url and self.supabase_jwks_url != jwks:
            raise ValueError("SUPABASE_JWKS_URL must match SUPABASE_URL's Auth JWKS endpoint")
        if not self.supabase_jwt_audience.strip():
            raise ValueError("SUPABASE_JWT_AUDIENCE is required")
        return issuer, jwks

    def validate_deployment(self) -> None:
        if self.app_env in {"development", "test"}:
            return
        if self.allow_local_identity:
            raise ValueError("ALLOW_LOCAL_IDENTITY must be false outside development/test")
        self.auth_endpoints()
        if not self.allowed_origins or "*" in self.allowed_origins:
            raise ValueError("Explicit ALLOWED_ORIGINS are required for deployment")
        from cryptography.fernet import Fernet

        try:
            if not self.provider_encryption_key:
                raise ValueError()
            Fernet(self.provider_encryption_key.get_secret_value().encode())
        except (ValueError, TypeError):
            raise ValueError("A valid stable PROVIDER_ENCRYPTION_KEY is required") from None

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
