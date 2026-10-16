"""ASGI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from capitalcite.api.routes.ask import router as ask_router
from capitalcite.api.routes.health import router as health_router
from capitalcite.api.routes.search import router as search_router
from capitalcite.config import get_settings
from capitalcite.logging import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    yield


def create_app() -> FastAPI:
    """Build the FastAPI app."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        summary="Cited, evaluated answers over U.S. bank filings.",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(search_router)
    application.include_router(ask_router)
    return application


app = create_app()
