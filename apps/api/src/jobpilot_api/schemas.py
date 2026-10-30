"""API response/request schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ComponentStatus(BaseModel):
    name: str
    ok: bool
    critical: bool
    detail: str | None = None
    latency_ms: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    components: list[ComponentStatus]


class TaskCreated(BaseModel):
    task_id: str
