from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    session_secret: str
    cookie_secure: bool = False
    session_ttl_hours: int = 12

    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"

    max_upload_mb: int = 50
    upload_dir: Path = REPO_ROOT / "var" / "uploads"

    @field_validator("session_secret")
    @classmethod
    def secret_is_long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError(
                "SESSION_SECRET must be at least 32 characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return value

    @property
    def sqlalchemy_url(self) -> str:
        """DATABASE_URL as given by Neon or docker, pointed at the psycopg 3 driver."""
        for prefix in ("postgresql://", "postgres://"):
            if self.database_url.startswith(prefix):
                return "postgresql+psycopg://" + self.database_url.removeprefix(prefix)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
