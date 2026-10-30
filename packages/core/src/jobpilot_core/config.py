"""Application settings, loaded from environment / `.env` only."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LLMPurpose(StrEnum):
    """Every LLM call declares why it is made; the model is chosen per purpose."""

    TRIAGE = "triage"
    GUARD = "guard"
    PARSE_RESUME = "parse_resume"
    RERANK = "rerank"
    TAILOR = "tailor"
    COVER_LETTER = "cover_letter"


ProviderName = Literal["groq", "openai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_redact_bodies: bool = True

    # --- infrastructure
    # host defaults target the docker-compose services from the host (postgres on 5433)
    database_url: str = "postgresql+asyncpg://jobpilot:jobpilot@127.0.0.1:5433/jobpilot"
    redis_url: str = "redis://127.0.0.1:6379/0"
    storage_dir: Path = Path("storage")
    latex_spool_dir: Path = Path("storage/latex-spool")
    latex_heartbeat_max_age_s: int = 30

    # --- security
    fernet_key: SecretStr | None = None
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- LLM providers
    llm_provider: ProviderName = "groq"
    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    llm_fallback_to_openai: bool = False
    llm_request_timeout_s: float = 60.0
    llm_max_retries: int = 4
    llm_cache_ttl_s: int = 7 * 24 * 3600

    # models per purpose and provider (override any of them from .env)
    groq_model_triage: str = "openai/gpt-oss-120b"
    groq_model_guard: str = "openai/gpt-oss-120b"
    groq_model_parse_resume: str = "openai/gpt-oss-120b"
    groq_model_rerank: str = "openai/gpt-oss-120b"
    groq_model_tailor: str = "openai/gpt-oss-120b"
    groq_model_cover_letter: str = "openai/gpt-oss-120b"
    openai_model_triage: str = "gpt-4.1-mini"
    openai_model_guard: str = "gpt-4.1-mini"
    openai_model_parse_resume: str = "gpt-4.1-mini"
    openai_model_rerank: str = "gpt-4.1-mini"
    openai_model_tailor: str = "gpt-4.1"
    openai_model_cover_letter: str = "gpt-4.1"

    # rate limits (per provider, shared by every process through Redis).
    # Check your real limits at https://console.groq.com/settings/limits ; tpd = 0 disables it.
    groq_tpm: int = 8_000
    groq_rpm: int = 30
    groq_tpd: int = 200_000
    openai_tpm: int = 200_000
    openai_rpm: int = 500
    openai_tpd: int = 0
    llm_output_reserve_tokens: int = 1_024  # reserved when a request sets no max tokens
    llm_max_wait_s: float = 300.0  # longest an LLM call may queue for budget

    # --- triage
    triage_batch_size: int = Field(default=15, ge=1, le=50)
    triage_snippet_max_chars: int = 300

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def model_for(self, purpose: LLMPurpose, provider: ProviderName | None = None) -> str:
        p = provider or self.llm_provider
        value = getattr(self, f"{p}_model_{purpose.value}")
        assert isinstance(value, str)
        return value

    def api_key_for(self, provider: ProviderName) -> SecretStr | None:
        return self.groq_api_key if provider == "groq" else self.openai_api_key

    def base_url_for(self, provider: ProviderName) -> str:
        return self.groq_base_url if provider == "groq" else self.openai_base_url

    def limits_for(self, provider: ProviderName) -> tuple[int, int, int]:
        """Return (tokens_per_minute, requests_per_minute, tokens_per_day)."""
        if provider == "groq":
            return self.groq_tpm, self.groq_rpm, self.groq_tpd
        return self.openai_tpm, self.openai_rpm, self.openai_tpd


@lru_cache
def get_settings() -> Settings:
    return Settings()
