"""LLM usage page API on the test database (synthetic log rows)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from jobpilot_api.routers.usage import cost_of
from jobpilot_core.config import Settings
from jobpilot_core.models import LLMCallLog

from .conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(
    api_settings: Settings, real_redis: Redis, engine: AsyncEngine
) -> AsyncIterator[httpx.AsyncClient]:
    from jobpilot_api.main import create_app

    settings = api_settings.model_copy(update={"llm_prices": {"model-a": (1.0, 2.0)}})
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE llm_call_log, user_profile RESTART IDENTITY CASCADE"))
    now = datetime.now(UTC)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        for purpose, model, t_in, t_out, cached, code, age, waited in (
            ("tailor", "model-a", 1_000_000, 500_000, False, 200, timedelta(hours=1), 3_000),
            ("tailor", "model-a", 400, 100, True, 200, timedelta(hours=1), 0),
            ("triage", "model-b", 2_000, 1_000, False, 200, timedelta(days=2), 500),
            ("triage", "model-b", 0, 0, False, 429, timedelta(days=2), 0),
            ("triage", "model-b", 9_000, 9_000, False, 200, timedelta(days=45), 0),
        ):
            s.add(
                LLMCallLog(
                    provider="groq",
                    model=model,
                    purpose=purpose,
                    input_tokens=t_in,
                    output_tokens=t_out,
                    cached=cached,
                    status_code=code,
                    latency_ms=800,
                    waited_ms=waited,
                    created_at=now - age,
                )
            )
        await s.commit()
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            yield c


def test_cost_needs_a_price() -> None:
    assert cost_of({"m": (1.0, 2.0)}, "m", 1_000_000, 500_000) == 2.0
    assert cost_of({}, "m", 10, 10) is None


async def test_usage_counts_live_calls_and_prices_the_known_models(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/v1/usage", params={"days": 30})
    assert r.status_code == 200, r.text
    data = r.json()
    totals = data["totals"]
    # the cached call and the 45-day-old call add no tokens; the 429 is a call and an error
    assert (totals["calls"], totals["cached_calls"], totals["errors"]) == (3, 1, 1)
    assert (totals["input_tokens"], totals["output_tokens"]) == (1_002_000, 501_000)
    assert totals["waited_s"] == 3.5 and totals["cost"] == 2.0
    tailor, triage = data["by_purpose"]
    assert (tailor["purpose"], tailor["calls"], tailor["cached_calls"], tailor["cost"]) == (
        "tailor",
        1,
        1,
        2.0,
    )
    assert (triage["purpose"], triage["calls"], triage["errors"], triage["cost"]) == (
        "triage",
        2,
        1,
        None,
    )
    assert data["unpriced_models"] == ["model-b"]
    assert len(data["by_day"]) == 30 and sum(d["tokens"] for d in data["by_day"]) == 1_503_000
    assert sum(d["calls"] for d in data["by_day"]) == 3
    assert data["today"]["tokens"] == 1_500_000 and data["today"]["provider"] == "groq"

    assert (await client.get("/api/v1/usage", params={"days": 0})).status_code == 422
    one = (await client.get("/api/v1/usage", params={"days": 90})).json()
    assert one["totals"]["calls"] == 4
