"""ASGI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from capitalcite.api.ask_limit import AskLimiter
from capitalcite.api.ask_models import hoist_openapi_defs
from capitalcite.api.routes.ask import router as ask_router
from capitalcite.api.routes.health import router as health_router
from capitalcite.api.routes.search import router as search_router
from capitalcite.config import get_settings
from capitalcite.logging import configure_logging
from capitalcite.retrieval import RetrievalSetup, create_retrieval_setup
from capitalcite.trace import configure_settings, shutdown


def retrieval_setup(application: FastAPI) -> RetrievalSetup | None:
    setup = getattr(application.state, "retrieval", None)
    return setup if isinstance(setup, RetrievalSetup) else None


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_settings(settings)
    application.state.retrieval = None
    try:
        application.state.retrieval = create_retrieval_setup(settings)
    except Exception:
        application.state.retrieval = None
    if settings.warehouse_path is not None:
        from capitalcite.config import metric_backends

        application.state.metric_backend, application.state.metric_citations = metric_backends(
            settings
        )
    try:
        yield
    finally:
        setup = retrieval_setup(application)
        if setup is not None:
            await setup.aclose()
        application.state.retrieval = None
        shutdown()


def _validation_detail(errors: Sequence[object]) -> list[object]:
    encoded = jsonable_encoder(list(errors))
    if not isinstance(encoded, list):
        return []
    cleaned: list[object] = []
    for item in encoded:
        if isinstance(item, dict):
            cleaned.append({key: value for key, value in item.items() if key != "input"})
        else:
            cleaned.append(item)
    return cleaned


def create_app() -> FastAPI:
    """Build the FastAPI app."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        summary="Cited, evaluated answers over U.S. bank filings.",
        lifespan=lifespan,
    )

    @application.exception_handler(RequestValidationError)
    async def hide_validation_input(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _validation_detail(exc.errors())})

    application.state.ask_limiter = AskLimiter(
        limit=settings.ask_rate_limit,
        window_s=settings.ask_rate_window_s,
        trusted_proxy_hops=settings.trusted_proxy_hops,
    )
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=False,
        )
    application.include_router(health_router)
    application.include_router(search_router)
    application.include_router(ask_router)
    generate = application.openapi

    def openapi() -> dict[str, object]:
        schema = generate()
        hoist_openapi_defs(schema)
        paths = schema.get("paths")
        if isinstance(paths, dict):
            stream = paths.get("/ask/stream")
            if isinstance(stream, dict):
                post = stream.get("post")
                if isinstance(post, dict):
                    responses = post.get("responses")
                    if isinstance(responses, dict):
                        ok = responses.get("200")
                        if isinstance(ok, dict):
                            content = ok.get("content")
                            if isinstance(content, dict):
                                content.pop("application/json", None)
        return schema

    application.openapi = openapi  # type: ignore[method-assign]
    return application


app = create_app()
