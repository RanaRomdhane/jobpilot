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
    # dashboard login: Argon2 hash set by `make password` (the password itself is never stored)
    dashboard_password_hash: SecretStr | None = None
    session_ttl_hours: int = Field(default=168, ge=1, le=24 * 90)
    session_cookie_secure: bool = False  # True when served over HTTPS
    login_max_attempts_per_minute: int = 5
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
    # a dedicated prompt-injection classifier (returns a probability); see jobpilot_security.guard
    groq_model_guard: str = "meta-llama/llama-prompt-guard-2-86m"
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
    # tokens the provider adds to every request (chat template / hidden preamble); measured on
    # Groq gpt-oss-120b: a 36-char prompt is billed 78 input tokens
    groq_request_overhead_tokens: int = 80
    openai_request_overhead_tokens: int = 16
    llm_max_wait_s: float = 300.0  # longest an LLM call may queue for budget
    # the guard model has its own provider budget (Groq limits are per model)
    groq_guard_tpm: int = 15_000
    groq_guard_rpm: int = 30
    openai_guard_tpm: int = 200_000
    openai_guard_rpm: int = 500

    # --- prompt-injection detection
    injection_threshold: float = Field(default=0.5, ge=0, le=1)
    guard_enabled: bool = True
    guard_threshold: float = Field(default=0.8, ge=0, le=1)  # guard probability that flags alone

    # --- gmail
    google_client_file: Path = Path("secrets/google_credentials.json")
    gmail_query: str = "in:inbox -category:promotions -category:social"
    gmail_backfill_days: int = Field(default=90, ge=1, le=3650)
    gmail_max_backfill: int = 3_000

    # --- resume (CV uploads live in <storage_dir>/resumes, outside any web root)
    resume_max_upload_mb: int = Field(default=5, ge=1, le=20)
    resume_extract_timeout_s: float = 60.0

    # --- triage
    triage_batch_size: int = Field(default=15, ge=1, le=50)
    triage_snippet_max_chars: int = 300
    triage_subject_max_chars: int = 150
    triage_max_turns: int = 4
    # status changes from emails apply automatically at/above this confidence (never if flagged)
    pipeline_auto_apply_confidence: float = Field(default=0.85, ge=0, le=1)

    # --- job discovery (Phase 5)
    # identifiable, with a contact page: what polite crawling etiquette asks for
    jobs_user_agent: str = (
        "JobPilot/0.1 (personal job-search assistant; +https://github.com/RanaRomdhane/jobpilot)"
    )
    jobs_request_timeout_s: float = 30.0
    jobs_cache_ttl_s: int = 3 * 3600
    # official APIs that need keys (connectors stay disabled until set)
    adzuna_app_id: SecretStr | None = None
    adzuna_app_key: SecretStr | None = None
    adzuna_countries: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["fr", "de", "gb", "nl", "be"]
    )
    rapidapi_key: SecretStr | None = None
    # per-source daily request caps (the sources' own terms / free tiers)
    remotive_daily_cap: int = 4  # Remotive asks for at most 4 calls a day
    remoteok_daily_cap: int = 4
    weworkremotely_daily_cap: int = 8
    tanitjobs_daily_cap: int = 6
    keejob_daily_cap: int = 12  # listing pages
    keejob_pages: int = Field(default=3, ge=1, le=5)
    adzuna_daily_cap: int = 100
    jsearch_daily_cap: int = 6  # free RapidAPI plan: ~200 requests a month

    # ---- application factory (Phase 6)
    cover_letter_tone: Literal["formal", "modern"] = "formal"
    cv_page_budget: int = 1
    latex_compile_timeout_s: float = 90.0
    scrape_min_interval_s: float = 6.0  # between two pages of the same HTML site
    # fetch + match runs (UTC hours): 07:00 and 17:00 UTC = 08:00 and 18:00 in Tunis
    jobs_fetch_hours: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [7, 17])
    jobs_max_age_days: int = Field(default=30, ge=1, le=180)

    # --- matching
    embedding_dim: int = 384  # intfloat/multilingual-e5-small
    match_recall_k: int = 80
    match_rerank_top_n: int = Field(default=10, ge=0, le=40)
    match_rerank_batch: int = Field(default=5, ge=1, le=10)

    @field_validator("adzuna_countries", "jobs_fetch_hours", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

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

    def request_overhead_for(self, provider: ProviderName) -> int:
        if provider == "groq":
            return self.groq_request_overhead_tokens
        return self.openai_request_overhead_tokens

    def limits_for(self, provider: ProviderName) -> tuple[int, int, int]:
        """Return (tokens_per_minute, requests_per_minute, tokens_per_day)."""
        if provider == "groq":
            return self.groq_tpm, self.groq_rpm, self.groq_tpd
        return self.openai_tpm, self.openai_rpm, self.openai_tpd

    def guard_limits_for(self, provider: ProviderName) -> tuple[int, int]:
        if provider == "groq":
            return self.groq_guard_tpm, self.groq_guard_rpm
        return self.openai_guard_tpm, self.openai_guard_rpm


@lru_cache
def get_settings() -> Settings:
    return Settings()
