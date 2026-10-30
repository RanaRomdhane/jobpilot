from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from jobpilot_core.config import Settings


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis()
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        groq_api_key="gsk_test_key_not_real_000000000000",
        openai_api_key="sk-test-key-not-real-0000000000000",
        fernet_key="0" * 43 + "=",
    )
