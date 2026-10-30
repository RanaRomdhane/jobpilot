from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis

from jobpilot_core.config import Settings
from jobpilot_core.db import create_engine, create_sessionmaker
from jobpilot_core.health import WORKER_HEARTBEAT_KEY
from jobpilot_worker.main import heartbeat, ping

pytestmark = pytest.mark.integration


@pytest.fixture
async def worker_ctx(
    integration_settings: Settings, real_redis: Redis
) -> AsyncIterator[dict[str, Any]]:
    engine = create_engine(integration_settings)
    ctx = {
        "settings": integration_settings,
        "engine": engine,
        "sessionmaker": create_sessionmaker(engine),
        "app_redis": real_redis,
    }
    yield ctx
    await engine.dispose()


async def test_live(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/health/live")
    assert r.status_code == 200


async def test_health_degraded_without_worker_then_ok_parts(
    client: httpx.AsyncClient, worker_ctx: dict[str, Any], real_redis: Redis
) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    comps = {c["name"]: c for c in body["components"]}
    assert comps["database"]["ok"] and "pgvector" in comps["database"]["detail"]
    assert comps["redis"]["ok"]
    assert not comps["worker"]["ok"]
    assert body["status"] == "degraded"

    await heartbeat(worker_ctx)
    assert await real_redis.get(WORKER_HEARTBEAT_KEY) is not None
    comps = {c["name"]: c for c in (await client.get("/api/v1/health")).json()["components"]}
    assert comps["worker"]["ok"]


async def test_health_reports_latex_heartbeat(
    client: httpx.AsyncClient, integration_settings: Settings
) -> None:
    spool = integration_settings.latex_spool_dir
    spool.mkdir(parents=True, exist_ok=True)
    (spool / "heartbeat").write_text(json.dumps({"ts": time.time(), "engine": "none"}))
    comps = {c["name"]: c for c in (await client.get("/api/v1/health")).json()["components"]}
    assert comps["latex"]["ok"]


async def test_ping_task_streams_progress_over_sse(
    client: httpx.AsyncClient, worker_ctx: dict[str, Any]
) -> None:
    r = await client.post("/api/v1/tasks/ping")
    assert r.status_code == 202
    task_id = r.json()["task_id"]

    received: list[dict[str, Any]] = []

    async def listen() -> None:
        async with client.stream("GET", f"/api/v1/tasks/{task_id}/events") as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[5:].strip())
                    received.append(event)
                    if event["status"] in ("succeeded", "failed"):
                        return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.3)  # let the stream subscribe
    result = await ping(worker_ctx, task_id, steps=3, delay_s=0.01)
    await asyncio.wait_for(listener, timeout=10)

    assert result == {"pong": True, "steps": 3}
    statuses = [e["status"] for e in received]
    assert statuses[0] == "queued"
    assert statuses[-1] == "succeeded"
    assert any(0 < e["progress"] < 1 for e in received)

    final = (await client.get(f"/api/v1/tasks/{task_id}")).json()
    assert final["status"] == "succeeded" and final["result"]["pong"] is True


async def test_unknown_task_is_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_cors_rejects_unknown_origin(client: httpx.AsyncClient) -> None:
    r = await client.options(
        "/api/v1/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers
