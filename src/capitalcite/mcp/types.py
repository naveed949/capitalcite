"""MCP tool arguments. Parse at the MCP boundary, then trust execute()."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from capitalcite.ask.types import AskModelName


class AskToolArgs(BaseModel):
    """RAG ask over MCP. path=agent stays on HTTP."""

    model_config = ConfigDict(frozen=True)
    question: str = Field(min_length=1)
    ticker: str | None = None
    form: str | None = None
    period: str | None = None
    k: int | None = Field(default=None, ge=1, le=50)
    model: AskModelName | None = None
