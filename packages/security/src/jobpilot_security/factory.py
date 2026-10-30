"""Build the injection detector from settings and the configured LLM provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jobpilot_core.config import LLMPurpose, Settings
from jobpilot_security.detector import InjectionDetector
from jobpilot_security.guard import Guard, LLMGuardClassifier, NullGuard, PromptGuardClassifier

if TYPE_CHECKING:
    from jobpilot_core.llm import LLMProvider


def build_guard(settings: Settings, provider: LLMProvider | None) -> Guard:
    if not settings.guard_enabled or provider is None:
        return NullGuard()
    model = provider.model(LLMPurpose.GUARD)
    client = provider.guard_client()
    if "prompt-guard" in model:
        return PromptGuardClassifier(client, model)
    return LLMGuardClassifier(client, model)


def build_detector(settings: Settings, provider: LLMProvider | None) -> InjectionDetector:
    return InjectionDetector(
        build_guard(settings, provider),
        threshold=settings.injection_threshold,
        guard_threshold=settings.guard_threshold,
    )
