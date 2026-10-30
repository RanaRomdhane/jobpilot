"""Liveness and readiness."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from importlib.metadata import version

from fastapi import APIRouter, Response, status

from jobpilot_api.deps import State
from jobpilot_api.schemas import ComponentStatus, HealthResponse
from jobpilot_core.health import check_database, check_latex, check_redis, check_worker

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", summary="Process is up")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("", response_model=HealthResponse, summary="Readiness of every component")
async def health(state: State, response: Response) -> HealthResponse:
    db, redis, worker = await asyncio.gather(
        check_database(state.engine), check_redis(state.redis), check_worker(state.redis)
    )
    latex = check_latex(state.settings.latex_spool_dir, state.settings.latex_heartbeat_max_age_s)
    components = [db, redis, worker, latex]
    if any(c.critical and not c.ok for c in components):
        overall = "down"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif all(c.ok for c in components):
        overall = "ok"
    else:
        overall = "degraded"
    return HealthResponse(
        status=overall,
        version=version("jobpilot-api"),
        components=[ComponentStatus(**asdict(c)) for c in components],
    )
