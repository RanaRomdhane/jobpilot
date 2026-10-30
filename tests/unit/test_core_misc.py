from __future__ import annotations

import logging

import pytest

from jobpilot_core.config import LLMPurpose, Settings
from jobpilot_core.crypto import SecretBox, SecretBoxError, generate_key
from jobpilot_core.enums import EmailCategory
from jobpilot_core.log import RedactingFilter, redact


def test_redacts_keys_tokens_and_emails() -> None:
    text = (
        "key gsk_abcdefghijklmnopqrstuvwxyz012345 and sk-proj-abcdefghijklmnopqrstuv "
        "Bearer abc.def.ghi from rana@example.com"
    )
    out = redact(text)
    assert "gsk_" not in out and "sk-proj" not in out and "abc.def" not in out
    assert "rana@example.com" not in out and "[EMAIL]" in out


def test_filter_drops_bodies_from_structured_data() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    record.data = {
        "snippet": "Bonjour, votre entretien...",
        "subject_len": 12,
        "nested": {"body": "secret"},
    }
    RedactingFilter(redact_bodies=True).filter(record)
    data = record.__dict__["data"]
    assert data["snippet"].startswith("[REDACTED")
    assert data["nested"]["body"].startswith("[REDACTED")
    assert data["subject_len"] == 12


def test_secret_box_roundtrip_and_wrong_key() -> None:
    box = SecretBox(generate_key())
    token = box.encrypt('{"refresh_token": "x"}')
    assert box.decrypt(token) == '{"refresh_token": "x"}'
    with pytest.raises(SecretBoxError):
        SecretBox(generate_key()).decrypt(token)
    with pytest.raises(SecretBoxError):
        SecretBox("not-a-key")


def test_models_are_chosen_per_purpose_and_provider(settings: Settings) -> None:
    assert settings.model_for(LLMPurpose.TRIAGE) == "openai/gpt-oss-120b"
    assert settings.model_for(LLMPurpose.TRIAGE, "openai") == settings.openai_model_triage
    assert settings.limits_for("groq") == (8_000, 30, 200_000)


def test_cors_origins_accept_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, http://127.0.0.1:3000")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_category_labels() -> None:
    assert EmailCategory.ENTRETIEN.gmail_label == "JobPilot/Entretien"
    assert {c.value for c in EmailCategory} == {
        "Recruteur",
        "Entretien",
        "Refus",
        "Offre",
        "Newsletter",
        "Autre",
    }
