"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from jobpilot_api.routers import (
    applications,
    ats,
    auth,
    documents,
    emails,
    gmail,
    health,
    jobs,
    outgoing,
    overview,
    resume,
    security,
    tasks,
    templates,
)
from jobpilot_api.routers import (
    settings as settings_router,
)
from jobpilot_api.state import AppState
from jobpilot_core.config import Settings, get_settings
from jobpilot_core.db import create_engine, create_sessionmaker
from jobpilot_core.log import configure_logging

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_redact_bodies)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis.from_url(settings.redis_url)
        arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        app.state.jobpilot = AppState(
            settings=settings,
            engine=engine,
            sessionmaker=create_sessionmaker(engine),
            redis=redis,
            arq=arq,
        )
        try:
            yield
        finally:
            await arq.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="JobPilot API",
        version=version("jobpilot-api"),
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    # The dashboard talks to the API through its own server-side proxy; CORS only allows the
    # configured web origin(s), never "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    v1 = APIRouter(prefix=API_PREFIX)
    v1.include_router(health.router)
    v1.include_router(auth.router)
    v1.include_router(overview.router)
    v1.include_router(emails.router)
    v1.include_router(applications.router)
    v1.include_router(resume.router)
    v1.include_router(ats.router)
    v1.include_router(jobs.router)
    v1.include_router(jobs.saved_router)
    v1.include_router(templates.router)
    v1.include_router(documents.router)
    v1.include_router(outgoing.router)
    v1.include_router(security.router)
    v1.include_router(settings_router.router)
    v1.include_router(tasks.router)
    v1.include_router(gmail.router)
    app.include_router(v1)
    return app


app = create_app()
