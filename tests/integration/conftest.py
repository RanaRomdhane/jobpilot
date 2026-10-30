"""Integration fixtures: a real PostgreSQL (migrated) and Redis.

Locally they target the docker-compose services (`make up` or `make infra`); in CI, the job's
service containers. Tests are skipped locally when the services are unreachable, but fail in CI.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jobpilot_core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _default_test_db_url() -> str:
    """Same server and credentials as DATABASE_URL (from .env), but the `<name>_test` database."""
    url = make_url(Settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or _default_test_db_url()
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")

pytestmark = pytest.mark.integration


async def _ensure_database() -> None:
    admin_url, _, name = TEST_DB_URL.rpartition("/")
    engine = create_async_engine(f"{admin_url}/postgres", isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def integration_settings() -> Iterator[Settings]:
    try:
        asyncio.run(asyncio.wait_for(_ensure_database(), timeout=10))
    except Exception as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(
            f"postgres not reachable for integration tests ({type(exc).__name__}); run `make infra`"
        )
    settings = Settings(
        _env_file=None,
        env="test",
        database_url=TEST_DB_URL,
        redis_url=TEST_REDIS_URL,
        groq_api_key="gsk_test_key_not_real_000000000000",
        latex_spool_dir=ROOT / "storage" / "test-spool",
    )
    cfg = Config(str(ROOT / "packages/core/alembic.ini"))
    # explicit URL: never let the test run migrate (or downgrade!) the dev database
    cfg.attributes["database_url"] = TEST_DB_URL
    assert cfg.attributes["database_url"].endswith("_test")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield settings


@pytest.fixture
async def engine(integration_settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(integration_settings.database_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def real_redis(integration_settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(integration_settings.redis_url)
    try:
        await client.ping()
    except Exception:
        if os.environ.get("CI"):
            raise
        pytest.skip("redis not reachable; run `make infra`")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
