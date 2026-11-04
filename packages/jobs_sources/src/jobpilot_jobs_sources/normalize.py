"""RawJob -> NormalizedJob: clean text, location, work mode, language, domain, dedup hash."""

from __future__ import annotations

import hashlib
import re

from jobpilot_core.enums import Region, WorkMode
from jobpilot_jobs_sources.geo import locate
from jobpilot_jobs_sources.models import NormalizedJob, RawJob
from jobpilot_jobs_sources.taxonomy import classify_domain, detect_contract, detect_seniority
from jobpilot_security import html_to_text, truncate
from jobpilot_security.text import fold

MAX_DESCRIPTION = 20_000

_REMOTE = re.compile(
    r"\b(remote|full[- ]remote|fully remote|100 ?% remote|teletravail|a distance|work from home"
    r"|anywhere)\b"
)
_HYBRID = re.compile(
    r"\b(hybrid|hybride|teletravail partiel|\d ?(?:jours?|days?) (?:de )?remote)\b"
)
_ONSITE = re.compile(r"\b(on[- ]?site|sur site|presentiel|in[- ]office)\b")

_FR = re.compile(r"\b(nous|vous|et|les|des|pour|avec|une|est|sur|dans|poste|recherchons)\b")
_EN = re.compile(r"\b(we|you|and|the|for|with|are|our|role|looking|will|team)\b")
_AR = re.compile(r"[؀-ۿ]")

# words that make two titles look different but mean the same job
_TITLE_NOISE = re.compile(r"\b(h/f|f/h|m/f|f/m|m/w/d|h/f/x|\(.*?\)|remote|teletravail|hybride)\b")


def detect_work_mode(raw: RawJob, text: str) -> WorkMode | None:
    if raw.remote_hint:
        return WorkMode.REMOTE
    head = fold(f"{raw.title} {raw.location or ''}")
    body = fold(text[:5000])
    if _HYBRID.search(head) or _HYBRID.search(body):
        return WorkMode.HYBRID
    if _REMOTE.search(head):
        return WorkMode.REMOTE
    if _ONSITE.search(head) or _ONSITE.search(body):
        return WorkMode.ONSITE
    if _REMOTE.search(body):
        return WorkMode.REMOTE
    return None if raw.remote_hint is None else WorkMode.ONSITE


def detect_language(text: str) -> str | None:
    sample = text[:3000]
    if len(_AR.findall(sample)) > 50:
        return "ar"
    folded = fold(sample)
    fr, en = len(_FR.findall(folded)), len(_EN.findall(folded))
    if fr + en < 5:
        return None
    return "fr" if fr > en else "en"


def _norm(value: str | None) -> str:
    v = _TITLE_NOISE.sub(" ", fold(value or ""))
    return re.sub(r"[^a-z0-9]+", " ", v).strip()


def dedup_hash(title: str, company: str | None, place: str | None) -> str:
    """Same job on several boards -> same hash: normalized title + company + location."""
    key = "|".join((_norm(title), _norm(company), _norm(place)))
    return hashlib.sha256(key.encode()).hexdigest()


_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


def safe_url(url: str | None) -> str | None:
    """Only absolute http(s) links reach the dashboard (no javascript:, data:, file:...)."""
    if not url:
        return None
    u = url.strip()
    return u[:2048] if re.match(r"^https?://[^\s/]+", u, re.I) else None


def normalize(raw: RawJob) -> NormalizedJob:
    clean = html_to_text(raw.description_html or "")
    text = truncate(clean.text, MAX_DESCRIPTION)
    country, region = locate(raw.location, country_hint=raw.country_hint)
    work_mode = detect_work_mode(raw, text)
    if region is None and work_mode is WorkMode.REMOTE and not raw.location:
        region = Region.WORLDWIDE
    place = country or (region.value if region else raw.location)
    return NormalizedJob(
        source=raw.source,
        external_id=raw.external_id[:255],
        url=safe_url(raw.url),
        title=truncate(raw.title.strip(), 500),
        company_name=(raw.company or "").strip()[:300] or None,
        location_raw=(raw.location or "").strip()[:500] or None,
        country=country,
        region=region,
        work_mode=work_mode,
        domain=classify_domain(raw.title, f"{' '.join(raw.tags)} {text}"),
        seniority=detect_seniority(raw.title, text),
        contract_type=detect_contract(raw.contract_hint, raw.title, text),
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        salary_currency=(raw.salary_currency or None) and raw.salary_currency[:3].upper(),
        language=detect_language(f"{raw.title}\n{text}"),
        description=text,
        hidden_text=clean.hidden,
        posted_at=raw.posted_at,
        dedup_hash=dedup_hash(raw.title, raw.company, place),
        apply_url=safe_url(raw.apply_url) or safe_url(raw.url),
        apply_email=raw.apply_email if raw.apply_email and _EMAIL.match(raw.apply_email) else None,
    )
