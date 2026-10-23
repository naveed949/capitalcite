"""Liveness probe. HTTP 200 when the process is up.

point_count is best-effort. A missing index, a down store, or any error
becomes 0. This route does not talk to Langfuse and does not return 503.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import RetrievalSetup, create_retrieval_setup, point_count
from capitalcite.trace import git_sha as trace_git_sha

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "capitalcite"
    version: str = Field(description="Installed package version.")
    git_sha: str = Field(description="Build revision, or unknown when git is unavailable.")
    point_count: int = Field(
        ge=0,
        description="Points in the configured index. 0 when the index cannot be read.",
    )


def _package_version() -> str:
    try:
        return version("capitalcite")
    except PackageNotFoundError:  # pragma: no cover - editable install always has metadata
        return "0.0.0"


def _reported_sha(settings: Settings) -> str:
    if settings.git_sha is not None:
        return settings.git_sha
    return trace_git_sha()


async def _point_count(settings: Settings) -> int:
    setup: RetrievalSetup | None = None
    try:
        setup = create_retrieval_setup(settings)
        return await point_count(setup)
    except Exception:
        return 0
    finally:
        if setup is not None:
            await setup.aclose()


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Return 200 when the API process is up."""
    return HealthResponse(
        version=_package_version(),
        git_sha=_reported_sha(settings),
        point_count=await _point_count(settings),
    )
