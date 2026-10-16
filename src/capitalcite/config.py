"""Process configuration loaded from the environment.

Settings fail at process start, not at first use. Compose-only keys in `.env`
are ignored so one file can feed both Docker and the app.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    openai_api_key: str | None = Field(
        default=None,
        description=(
            "Secret for EMBEDDER=openai-small and ASK_MODEL=openai. "
            "bge-small, minilm, and ASK_MODEL=fake do not read it."
        ),
    )
    embedder: Literal["bge-small", "minilm", "openai-small"] = Field(
        default="bge-small",
        description=(
            "Dense model. bge-small and minilm run locally. openai-small needs OPENAI_API_KEY."
        ),
    )
    ask_model: Literal["openai", "fake"] = Field(
        default="openai",
        description=(
            "Chat model for POST /ask. fake quotes a retrieved chunk and does not read "
            "OPENAI_API_KEY."
        ),
    )
    ask_openai_model: str = Field(
        default="gpt-4o-mini",
        min_length=1,
        description="OpenAI chat model used when ASK_MODEL=openai.",
    )
    ask_min_hit_score: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Refuse POST /ask before the model when the top hit score is below this floor. "
            "Hybrid RRF scores are typically near 0.03. 0 disables the floor."
        ),
    )
    ask_max_steps: int = Field(
        default=8,
        ge=1,
        description="Agent graph stops after this many plan and tool steps.",
    )
    ask_max_tokens: int = Field(
        default=8000,
        ge=1,
        description="Agent graph stops when the estimated token use reaches this.",
    )
    gate_policy: Path | None = Field(
        default=None,
        description=(
            "JSON allow-list for agent tool calls. Empty uses the shipped policy. "
            "A missing or invalid file denies every tool."
        ),
    )
    search_k: int = Field(default=8, ge=1, le=50, description="Default number of hits for search.")
    rrf_k: int = Field(
        default=60,
        ge=1,
        description="Reciprocal rank fusion constant passed to Qdrant.",
    )

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_path: Path | None = Field(
        default=None,
        description=(
            "Embedded Qdrant directory. When set, the process does not dial QDRANT_URL. "
            "QDRANT_URL=:memory: is the process-local store."
        ),
    )
    qdrant_collection: str = "filings"

    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    @field_validator("qdrant_url")
    @classmethod
    def qdrant_url_stripped(cls, value: str) -> str:
        return value.strip()

    @field_validator("qdrant_path", "gate_policy", mode="before")
    @classmethod
    def blank_path_is_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
