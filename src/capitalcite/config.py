"""Process configuration loaded from the environment.

Settings fail at process start, not at first use. Compose-only keys in `.env`
are ignored so one file can feed both Docker and the app.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from capitalcite.chunking.models import ChunkerName


class Settings(BaseSettings):
    """Runtime settings for the API process.

    Vector-store and Langfuse fields are wired now so later ingest, retrieval,
    and tracing issues do not have to invent a second config style.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "capitalcite"
    app_env: str = "local"
    log_level: str = "INFO"

    sec_user_agent: str = Field(
        description=(
            "Declared User-Agent for SEC EDGAR. Must include a contact email. "
            "See https://www.sec.gov/os/accessing-edgar-data"
        ),
    )

    data_dir: Path = Field(
        default=Path("data"),
        description="Root for raw EDGAR filings (gitignored contents).",
    )

    chunker: ChunkerName = Field(
        default=ChunkerName.SECTION,
        description="Chunking strategy: fixed, recursive, or section.",
    )
    chunk_size: int = Field(default=512, ge=1, description="Target chunk size in tokens.")
    chunk_overlap: int = Field(default=64, ge=0, description="Overlap in tokens.")

    openai_api_key: str | None = None

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "filings"

    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    @field_validator("sec_user_agent")
    @classmethod
    def sec_user_agent_must_include_email(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or "@" not in stripped:
            raise ValueError(
                "SEC_USER_AGENT must identify you with a contact email, "
                "e.g. 'Your Name you@example.com'."
            )
        return stripped

    @model_validator(mode="after")
    def overlap_smaller_than_size(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
