"""Untrusted-input handling: sanitization, prompt-injection detection, spotlighting."""

from jobpilot_security.detector import InjectionDetector, InjectionReason, InjectionReport, Layer
from jobpilot_security.guard import LLMGuardClassifier, NullGuard, PromptGuardClassifier
from jobpilot_security.spotlight import SPOTLIGHT_POLICY, spotlight
from jobpilot_security.text import CleanText, clean_plain, html_to_text, normalize, truncate

__all__ = [
    "SPOTLIGHT_POLICY",
    "CleanText",
    "InjectionDetector",
    "InjectionReason",
    "InjectionReport",
    "LLMGuardClassifier",
    "Layer",
    "NullGuard",
    "PromptGuardClassifier",
    "clean_plain",
    "html_to_text",
    "normalize",
    "spotlight",
    "truncate",
]
