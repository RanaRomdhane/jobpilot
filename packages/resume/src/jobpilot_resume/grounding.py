"""Grounding: every parsed fact must come from the document itself.

The parser is told to copy text verbatim. This check verifies it: each extracted string must be
made of words that occur in the source text. Anything that does not is reported as `unverified` on
the profile form, so you confirm or fix it before it can reach the master profile.
"""

from __future__ import annotations

import re
from typing import Any

from jobpilot_core.resume import ResumeProfile
from jobpilot_security.text import fold

_WORD = re.compile(r"[a-z0-9][a-z0-9+#]*")
MIN_SHARE = 0.75


def words(text: str) -> list[str]:
    return [w for w in _WORD.findall(fold(text)) if len(w) >= 3 or w.isdigit()]


def grounded(value: str, vocabulary: set[str], share: float = MIN_SHARE) -> bool:
    """Are (almost) all of `value`'s words in `vocabulary`? Short values need every word."""
    ws = words(value)
    if not ws:
        return True
    present = sum(w in vocabulary for w in ws)
    return present == len(ws) if len(ws) <= 2 else present / len(ws) >= share


def grounding_warnings(profile: ResumeProfile, source_text: str) -> list[dict[str, Any]]:
    vocabulary = set(words(source_text))
    out: list[dict[str, Any]] = []

    def check(path: str, item_id: str | None, value: str | None) -> None:
        if value and not grounded(value, vocabulary):
            out.append({"path": path, "id": item_id, "text": value[:200]})

    check("contact.full_name", None, profile.contact.full_name)
    check("headline", None, profile.headline)
    check("summary", None, profile.summary)
    for i, e in enumerate(profile.experiences):
        check(f"experiences.{i}.company", e.id, e.company)
        check(f"experiences.{i}.title", e.id, e.title)
        for j, b in enumerate(e.bullets):
            check(f"experiences.{i}.bullets.{j}", b.id, b.text)
    for i, ed in enumerate(profile.education):
        check(f"education.{i}.institution", ed.id, ed.institution)
        check(f"education.{i}.degree", ed.id, ed.degree)
        for j, b in enumerate(ed.details):
            check(f"education.{i}.details.{j}", b.id, b.text)
    for i, p in enumerate(profile.projects):
        check(f"projects.{i}.name", p.id, p.name)
        check(f"projects.{i}.description", p.id, p.description)
        for j, b in enumerate(p.bullets):
            check(f"projects.{i}.bullets.{j}", b.id, b.text)
    for i, g in enumerate(profile.skills):
        for j, skill in enumerate(g.skills):
            check(f"skills.{i}.skills.{j}", g.id, skill)
    for i, c in enumerate(profile.certifications):
        check(f"certifications.{i}.name", c.id, c.name)
    for i, x in enumerate(profile.extra_sections):
        for j, b in enumerate(x.bullets):
            check(f"extra_sections.{i}.bullets.{j}", b.id, b.text)
    return out
