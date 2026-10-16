"""Liveness probe. Does not talk to Qdrant or Langfuse.

Readiness checks against those services belong on a later /ready route once
retrieval is wired. /health staying process-local keeps CI and `uvicorn`
honest without requiring docker compose.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "capitalcite"
    version: str = Field(description="Installed package version.")


def _package_version() -> str:
    try:
        return version("capitalcite")
    except PackageNotFoundError:  # pragma: no cover - editable install always has metadata
        return "0.0.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return 200 when the API process is up."""
    return HealthResponse(version=_package_version())
