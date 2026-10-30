"""Eval suites call the real LLM provider: `make eval` (needs GROQ_API_KEY and `make infra`)."""

from tests.integration.conftest import (  # noqa: F401 (re-exported fixtures)
    engine,
    integration_settings,
    real_redis,
)
