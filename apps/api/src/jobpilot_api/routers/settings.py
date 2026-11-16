"""Paramètres: read-only runtime configuration plus the user's editable preferences."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.config import LLMPurpose
from jobpilot_core.models import OAuthCredential, UserProfile

router = APIRouter(prefix="/settings", tags=["settings"])


class LLMSettings(BaseModel):
    provider: str
    models: dict[str, str]
    groq_key_set: bool
    openai_key_set: bool
    fallback_to_openai: bool
    limits: dict[str, int]
    guard_enabled: bool


class GmailScope(BaseModel):
    scope_set: str
    account_email: str | None


class Preferences(BaseModel):
    locale: Literal["fr", "en"] = "fr"
    timezone: str = Field(default="Africa/Tunis", max_length=64)
    daily_send_cap: int = Field(default=20, ge=1, le=200)
    # Approve & Send works only when this is on and the gmail.send token is connected
    sending_enabled: bool = False
    # empty: name, phone and LinkedIn from the master profile
    email_signature: str = Field(default="", max_length=1_000)


class SettingsOut(BaseModel):
    llm: LLMSettings
    gmail: list[GmailScope]
    schedules: dict[str, str]
    triage: dict[str, object]
    job_sources: list[dict[str, object]]
    preferences: Preferences


async def _profile(session: Session) -> UserProfile:
    profile = (await session.scalars(select(UserProfile).limit(1))).first()
    if profile is None:
        profile = UserProfile()
        session.add(profile)
        await session.commit()
    return profile


@router.get("", response_model=SettingsOut)
async def get_settings(_: Authenticated, session: Session, state: State) -> SettingsOut:
    s = state.settings
    tpm, rpm, tpd = s.limits_for(s.llm_provider)
    creds = (await session.scalars(select(OAuthCredential))).all()
    profile = await _profile(session)
    return SettingsOut(
        llm=LLMSettings(
            provider=s.llm_provider,
            models={p.value: s.model_for(p) for p in LLMPurpose},
            groq_key_set=bool(s.groq_api_key and s.groq_api_key.get_secret_value()),
            openai_key_set=bool(s.openai_api_key and s.openai_api_key.get_secret_value()),
            fallback_to_openai=s.llm_fallback_to_openai,
            limits={"tokens_per_minute": tpm, "requests_per_minute": rpm, "tokens_per_day": tpd},
            guard_enabled=s.guard_enabled,
        ),
        gmail=[
            GmailScope(scope_set=c.scope_set.value, account_email=c.account_email) for c in creds
        ],
        schedules={"triage": "every 30 minutes (:05 and :35)"},
        triage={
            "batch_size": s.triage_batch_size,
            "gmail_query": s.gmail_query,
            "backfill_days": s.gmail_backfill_days,
            "auto_apply_confidence": s.pipeline_auto_apply_confidence,
            "injection_threshold": s.injection_threshold,
        },
        job_sources=[],  # Phase 5
        preferences=Preferences(
            locale="en" if profile.locale == "en" else "fr",
            timezone=profile.timezone,
            daily_send_cap=profile.daily_send_cap,
            sending_enabled=profile.sending_enabled,
            email_signature=str((profile.preferences or {}).get("email_signature") or ""),
        ),
    )


@router.put("/preferences", response_model=Preferences)
async def update_preferences(body: Preferences, _: Authenticated, session: Session) -> Preferences:
    profile = await _profile(session)
    profile.locale = body.locale
    profile.timezone = body.timezone
    profile.daily_send_cap = body.daily_send_cap
    profile.sending_enabled = body.sending_enabled
    # a new dict, so SQLAlchemy sees the JSONB column change
    profile.preferences = {
        **(profile.preferences or {}),
        "email_signature": body.email_signature.replace("\r\n", "\n").strip(),
    }
    await session.commit()
    return body
