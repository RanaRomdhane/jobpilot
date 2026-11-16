"""Application emails: the draft, its attachments' names, and the one-time approval token.

Everything here is deterministic. The draft is a fixed template in the posting's language: no
model writes it, so it cannot claim anything about the candidate (ADR-0003, Phase 7). Values that
come from a job posting (title, company, apply address) are untrusted and are checked before they
reach an email header.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from jobpilot_core.enums import DocumentKind

Lang = Literal["fr", "en"]

APPROVAL_TTL = timedelta(minutes=10)
DUPLICATE_WINDOW = timedelta(days=30)
MAX_SUBJECT = 200

# one plain address: no display name, no list, no header tricks
_ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$")
_CONTROL = re.compile("[\x00-\x1f\x7f\u2028\u2029]+")


def valid_address(value: str | None) -> bool:
    return bool(value) and len(value or "") <= 254 and _ADDRESS.fullmatch(value or "") is not None


def header_text(value: str | None, limit: int = MAX_SUBJECT) -> str:
    """A single line safe for an email header: no line breaks or control characters."""
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", value or "")).strip()[:limit]


def _slug(text: str | None) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")[:40]


def attachment_name(kind: DocumentKind, full_name: str | None, company: str | None) -> str:
    """CV_Firstname_Lastname_Company.pdf / CoverLetter_Firstname_Lastname_Company.pdf"""
    prefix = "CV" if kind is DocumentKind.CV else "CoverLetter"
    parts = [prefix, _slug(full_name), _slug(company)]
    return "_".join(p for p in parts if p) + ".pdf"


def default_signature(full_name: str | None, phone: str | None, linkedin: str | None) -> str:
    """Name, phone and LinkedIn from the master profile, one per line."""
    return "\n".join(p.strip() for p in (full_name, phone, linkedin) if p and p.strip())


@dataclass(slots=True)
class Draft:
    subject: str
    body: str


_TEMPLATES: dict[Lang, tuple[str, str]] = {
    "fr": (
        "Candidature : {role}",
        "Madame, Monsieur,\n\n"
        "Je vous adresse ma candidature au poste de {role}{at_company}. Vous trouverez ci-joints "
        "mon CV et ma lettre de motivation.\n\n"
        "Je reste à votre disposition pour un entretien.\n\n"
        "Cordialement,\n{signature}",
    ),
    "en": (
        "Application: {role}",
        "Dear Hiring Manager,\n\n"
        "Please find attached my application for the {role} position{at_company}: my CV and my "
        "cover letter.\n\n"
        "I would welcome the opportunity to discuss it in an interview.\n\n"
        "Kind regards,\n{signature}",
    ),
}


def compose(lang: str, role: str | None, company: str | None, signature: str) -> Draft:
    """The application email for one job, in its language (French unless it is English)."""
    key: Lang = "en" if lang == "en" else "fr"
    subject_tpl, body_tpl = _TEMPLATES[key]
    role_text = header_text(role, 150) or ("le poste" if key == "fr" else "the")
    company_text = header_text(company, 150)
    if company_text:
        at_company = f" chez {company_text}" if key == "fr" else f" at {company_text}"
    else:
        at_company = ""
    body = body_tpl.format(role=role_text, at_company=at_company, signature=signature.strip())
    return Draft(subject=header_text(subject_tpl.format(role=role_text)), body=body.rstrip())


# --------------------------------------------------------------------------- approval token


def new_approval_token() -> tuple[str, str]:
    """A random one-time token and the hash that is stored; the token itself never is."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, stored_hash: str | None) -> bool:
    return bool(stored_hash) and hmac.compare_digest(hash_token(token), stored_hash or "")


def approval_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + APPROVAL_TTL


def same_role(a: str | None, b: str | None) -> bool:
    """Two role titles name the same job: case, accents, punctuation and "F/H" are ignored."""

    def norm(text: str | None) -> str:
        folded = unicodedata.normalize("NFKD", (text or "").casefold())
        plain = "".join(c for c in folded if not unicodedata.combining(c))
        plain = re.sub(
            r"\(?\b(?:f\s*/\s*h|h\s*/\s*f|m\s*/\s*f|f\s*/\s*m|m\s*/\s*w|w\s*/\s*m)\b\)?", " ", plain
        )
        return " ".join(re.findall(r"[a-z0-9]+", plain))

    return bool(norm(a)) and norm(a) == norm(b)
