from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Groq: the largest model with strict structured outputs there. Anthropic: the current Opus.
DEFAULT_MODELS = {"groq": "openai/gpt-oss-120b", "anthropic": "claude-opus-5-5"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    session_secret: str
    cookie_secure: bool = False
    session_ttl_hours: int = 12

    # Column-mapping suggestions (TDD §9). Without a key, suggestions come from rules only.
    llm_provider: Literal["groq", "anthropic"] = "groq"
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    llm_model: str = ""  # empty: the provider's default below
    ai_enabled: bool = True  # tests switch it off so they never call a paid API

    max_upload_mb: int = 50
    max_upload_rows: int = 300_000
    run_worker: bool = True  # tests and one-off scripts process uploads themselves
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
    def llm_api_key(self) -> str:
        return self.groq_api_key if self.llm_provider == "groq" else self.anthropic_api_key

    @property
    def llm_model_name(self) -> str:
        return self.llm_model or DEFAULT_MODELS[self.llm_provider]

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
