"""The connectors. One class per source; each states its terms in `info.attribution`.

Every connector returns `RawJob`s (untrusted text, not yet normalized) and goes through
`PoliteClient`, which enforces robots.txt (for scraped pages), daily caps, pacing and caching.
Parsing is defensive: a malformed item is skipped, never fatal for the whole source.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Protocol, cast
from urllib.parse import urljoin

from defusedxml import ElementTree

from jobpilot_core.config import Settings
from jobpilot_core.enums import Region
from jobpilot_jobs_sources.http import PoliteClient, SourceError
from jobpilot_jobs_sources.models import RawJob, SourceInfo, SourceQuery

log = logging.getLogger(__name__)


class JobSource(Protocol):
    info: SourceInfo

    def enabled(self, settings: Settings) -> bool: ...

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]: ...


# ------------------------------------------------------------------ helpers
def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value), tz=UTC)
        text = str(value).strip()
        if text[:4].isdigit():
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def _dec(value: Any) -> Decimal | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def _rss_items(xml: str) -> Iterable[Any]:
    root = ElementTree.fromstring(xml.encode("utf-8"))  # defused: no entities, no DTD tricks
    return cast("Iterable[Any]", root.iter("item"))


def _text(el: Any, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


# ------------------------------------------------------------------ Remotive (official API)
class Remotive:
    info = SourceInfo(
        name="remotive",
        display_name="Remotive",
        homepage="https://remotive.com",
        kind="api",
        regions=[Region.WORLDWIDE],
        attribution="Jobs via Remotive: link back to the original posting, at most 4 calls a day.",
    )

    def enabled(self, settings: Settings) -> bool:
        return True

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]:
        # one call for everything (their data is 24h delayed and changes slowly)
        data = await client.get_json(
            "https://remotive.com/api/remote-jobs",
            params={"limit": 500},
            source=self.info.name,
            daily_cap=settings.remotive_daily_cap,
            interval_s=1,
        )
        out: list[RawJob] = []
        for j in data.get("jobs", []) if isinstance(data, dict) else []:
            try:
                out.append(
                    RawJob(
                        source=self.info.name,
                        external_id=str(j["id"]),
                        url=j.get("url"),
                        title=j["title"],
                        company=j.get("company_name"),
                        location=j.get("candidate_required_location") or "Worldwide",
                        description_html=j.get("description") or "",
                        posted_at=_dt(j.get("publication_date")),
                        tags=[str(t) for t in j.get("tags") or []] + [j.get("category") or ""],
                        contract_hint=j.get("job_type"),
                        remote_hint=True,
                        salary_text=j.get("salary") or None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out


# ------------------------------------------------------------------ RemoteOK (public API)
class RemoteOK:
    info = SourceInfo(
        name="remoteok",
        display_name="Remote OK",
        homepage="https://remoteok.com",
        kind="api",
        regions=[Region.WORLDWIDE],
        attribution="Jobs via Remote OK: link back to the posting and name Remote OK as source.",
    )

    def enabled(self, settings: Settings) -> bool:
        return True

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]:
        data = await client.get_json(
            "https://remoteok.com/api",
            source=self.info.name,
            daily_cap=settings.remoteok_daily_cap,
            interval_s=1,
        )
        out: list[RawJob] = []
        for j in data if isinstance(data, list) else []:
            if not isinstance(j, dict) or "id" not in j or "position" not in j:
                continue  # the first element is the legal notice
            try:
                out.append(
                    RawJob(
                        source=self.info.name,
                        external_id=str(j["id"]),
                        url=j.get("url"),
                        title=j["position"],
                        company=j.get("company"),
                        location=j.get("location") or None,
                        description_html=j.get("description") or "",
                        posted_at=_dt(j.get("date") or j.get("epoch")),
                        tags=[str(t) for t in j.get("tags") or []],
                        remote_hint=True,
                        salary_min=_dec(j.get("salary_min")),
                        salary_max=_dec(j.get("salary_max")),
                        salary_currency="USD" if _dec(j.get("salary_max")) else None,
                        apply_url=j.get("url"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out


# ------------------------------------------------------------------ We Work Remotely (RSS)
class WeWorkRemotely:
    info = SourceInfo(
        name="weworkremotely",
        display_name="We Work Remotely",
        homepage="https://weworkremotely.com",
        kind="rss",
        regions=[Region.WORLDWIDE],
        attribution="Jobs via We Work Remotely (public RSS feed).",
    )
    FEEDS = (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    )

    def enabled(self, settings: Settings) -> bool:
        return True

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]:
        out: list[RawJob] = []
        for feed in self.FEEDS:
            xml = await client.get(
                feed, source=self.info.name, daily_cap=settings.weworkremotely_daily_cap
            )
            for item in _rss_items(xml):
                head = _text(item, "title")
                company, _, title = head.partition(":")
                link = _text(item, "link") or _text(item, "guid")
                if not link or not title:
                    continue
                out.append(
                    RawJob(
                        source=self.info.name,
                        external_id=link.rstrip("/").rsplit("/", 1)[-1][:255],
                        url=link,
                        title=title.strip(),
                        company=company.strip() or None,
                        location=_text(item, "region") or "Anywhere in the World",
                        description_html=_text(item, "description"),
                        posted_at=_dt(_text(item, "pubDate")),
                        tags=[_text(item, "category")],
                        remote_hint=True,
                    )
                )
        return out


# ------------------------------------------------------------------ Tanitjobs (public RSS)
class Tanitjobs:
    info = SourceInfo(
        name="tanitjobs",
        display_name="Tanitjobs",
        homepage="https://www.tanitjobs.com",
        kind="rss",
        regions=[Region.TUNISIA],
        attribution="Jobs via Tanitjobs (public RSS feed, personal non-commercial use).",
    )
    FEED = "https://www.tanitjobs.com/rss/"
    _ID = re.compile(r"/job/(\d+)/")

    def enabled(self, settings: Settings) -> bool:
        return True

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]:
        xml = await client.get(
            self.FEED, source=self.info.name, daily_cap=settings.tanitjobs_daily_cap, scraped=True
        )
        out: list[RawJob] = []
        for item in _rss_items(xml):
            link = _text(item, "link")
            m = self._ID.search(link)
            title = _text(item, "title")
            if not m or not title:
                continue
            # description starts with "City, Governorate, Tunisie" then the company, then HTML
            desc = _text(item, "description")
            first, _, rest = desc.partition("\n")
            company, _, body = rest.strip().partition("<br/>")
            title_clean = re.sub(r"\s+[–-]\s+[A-Z]{2}\s*\|.*$", "", title).strip()
            out.append(
                RawJob(
                    source=self.info.name,
                    external_id=m.group(1),
                    url=link,
                    title=title_clean or title,
                    company=re.sub(r"<[^>]+>", "", company).strip() or None,
                    location=first.strip() or "Tunisie",
                    description_html=body or desc,
                    posted_at=_dt(_text(item, "pubDate")),
                    country_hint="tn",
                )
            )
        return out


# ------------------------------------------------------------------ Keejob (listing pages)
class Keejob:
    info = SourceInfo(
        name="keejob",
        display_name="Keejob",
        homepage="https://www.keejob.com",
        kind="html",
        regions=[Region.TUNISIA],
        attribution=(
            "Jobs via Keejob: public listing pages only (allowed by robots.txt), a few pages "
            "twice a day, one request every few seconds."
        ),
    )
    LISTING = "https://www.keejob.com/offres-emploi/"
    _ID = re.compile(r"/offres-emploi/(\d+)/")
    _MONTHS: ClassVar[dict[str, int]] = {
        m: i + 1
        for i, m in enumerate(
            [
                "janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
                "septembre", "octobre", "novembre", "decembre",
            ]
        )
    }  # fmt: skip

    def enabled(self, settings: Settings) -> bool:
        return True

    def _date(self, text: str) -> datetime | None:
        from jobpilot_security.text import fold

        m = re.match(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", fold(text.strip()))
        if not m or m.group(2) not in self._MONTHS:
            return None
        try:
            return datetime(int(m.group(3)), self._MONTHS[m.group(2)], int(m.group(1)), tzinfo=UTC)
        except ValueError:
            return None

    def parse(self, html: str) -> list[RawJob]:
        from selectolax.lexbor import LexborHTMLParser as HTMLParser

        out: list[RawJob] = []
        for card in HTMLParser(html).css("article"):
            link = card.css_first("h2 a")
            if link is None:
                continue
            href = link.attributes.get("href") or ""
            m = self._ID.search(href)
            if not m:
                continue
            company = card.css_first('a[href*="/entreprises/"]:not([class*="block"])')
            # every fact on a card is an icon followed by its value, inside the same parent
            tags: list[str] = []
            contract: list[str] = []
            place = when = ""
            for icon in card.css("i"):
                cls = icon.attributes.get("class") or ""
                parent = icon.parent
                value = parent.text(strip=True) if parent is not None else ""
                if "fa-industry" in cls:
                    tags.append(value)
                elif "fa-briefcase" in cls:
                    contract.append(value)
                elif "fa-map-marker" in cls:
                    place = value
                elif "fa-clock" in cls or "fa-calendar" in cls:
                    when = value
            desc = card.css_first("p.leading-relaxed") or card.css_first("div.mb-3 p")
            out.append(
                RawJob(
                    source=self.info.name,
                    external_id=m.group(1),
                    url=urljoin(self.LISTING, href.split("?")[0]),
                    title=link.text(strip=True),
                    company=company.text(strip=True) if company else None,
                    location=place or "Tunisie",
                    description_html=desc.html or "" if desc else "",
                    posted_at=self._date(when),
                    tags=tags,
                    contract_hint=" ".join(contract) or None,
                    country_hint="tn",
                )
            )
        return out

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]:
        out: list[RawJob] = []
        for page in range(1, settings.keejob_pages + 1):
            html = await client.get(
                self.LISTING,
                params={"page": page} if page > 1 else None,
                source=self.info.name,
                daily_cap=settings.keejob_daily_cap,
                scraped=True,
                interval_s=settings.scrape_min_interval_s,
            )
            jobs = self.parse(html)
            if not jobs:
                if page == 1:
                    raise SourceError("parse_empty", "listing layout changed?")
                break
            out += jobs
        return out


# ------------------------------------------------------------------ Adzuna (official API, key)
class Adzuna:
    info = SourceInfo(
        name="adzuna",
        display_name="Adzuna",
        homepage="https://www.adzuna.com",
        kind="api",
        requires_key=True,
        regions=[Region.EUROPE, Region.USA, Region.CANADA],
        attribution="Jobs by Adzuna (official API).",
    )

    def enabled(self, settings: Settings) -> bool:
        return bool(settings.adzuna_app_id and settings.adzuna_app_key)

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]:
        assert settings.adzuna_app_id and settings.adzuna_app_key
        countries = [
            c for c in settings.adzuna_countries if not query.countries or c in query.countries
        ]
        what = " ".join(query.keywords[:1]) or "developer"
        out: list[RawJob] = []
        for country in countries:
            for term in query.keywords[:4] or [what]:
                data = await client.get_json(
                    f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                    params={
                        "app_id": settings.adzuna_app_id.get_secret_value(),
                        "app_key": settings.adzuna_app_key.get_secret_value(),
                        "what": term,
                        "max_days_old": query.max_age_days,
                        "results_per_page": 50,
                        "content-type": "application/json",
                    },
                    source=self.info.name,
                    daily_cap=settings.adzuna_daily_cap,
                    interval_s=1,
                )
                for j in data.get("results", []) if isinstance(data, dict) else []:
                    try:
                        out.append(
                            RawJob(
                                source=self.info.name,
                                external_id=str(j["id"]),
                                url=j.get("redirect_url"),
                                title=j["title"],
                                company=(j.get("company") or {}).get("display_name"),
                                location=(j.get("location") or {}).get("display_name"),
                                description_html=j.get("description") or "",
                                posted_at=_dt(j.get("created")),
                                tags=[(j.get("category") or {}).get("label") or ""],
                                contract_hint=" ".join(
                                    x for x in (j.get("contract_type"), j.get("contract_time")) if x
                                )
                                or None,
                                salary_min=_dec(j.get("salary_min")),
                                salary_max=_dec(j.get("salary_max")),
                                country_hint=country,
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
        return out


# ------------------------------------------------------------------ JSearch (RapidAPI, key)
def _jsearch_jobs(data: object) -> list[dict[str, Any]]:
    """Job dicts from a `/search-v2` body ({"data": {"jobs": [...]}}), or the legacy list shape."""
    body = data.get("data") if isinstance(data, dict) else None
    jobs = body.get("jobs") if isinstance(body, dict) else body
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


class JSearch:
    info = SourceInfo(
        name="jsearch",
        display_name="JSearch",
        homepage="https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch",
        kind="api",
        requires_key=True,
        regions=[Region.WORLDWIDE],
        attribution="Jobs via JSearch (RapidAPI), an official API over public job listings.",
    )

    def enabled(self, settings: Settings) -> bool:
        return bool(settings.rapidapi_key)

    async def search(
        self, client: PoliteClient, query: SourceQuery, settings: Settings
    ) -> list[RawJob]:
        assert settings.rapidapi_key
        terms = query.keywords[:2] or ["software developer"]
        days = (
            "today" if query.max_age_days <= 1 else "3days" if query.max_age_days <= 3 else "week"
        )
        out: list[RawJob] = []
        for term in terms:
            # v5 of the API retired `/search` (404 "Endpoint does not exist") for `/search-v2`,
            # which returns {"data": {"jobs": [...], "cursor": ...}}; one page is enough here
            data = await client.get_json(
                "https://jsearch.p.rapidapi.com/search-v2",
                params={"query": term, "date_posted": days},
                headers={
                    "X-RapidAPI-Key": settings.rapidapi_key.get_secret_value(),
                    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
                },
                source=self.info.name,
                daily_cap=settings.jsearch_daily_cap,
                interval_s=1,
            )
            for j in _jsearch_jobs(data):
                try:
                    place = ", ".join(
                        x
                        for x in (j.get("job_city"), j.get("job_state"), j.get("job_country"))
                        if x
                    )
                    out.append(
                        RawJob(
                            source=self.info.name,
                            external_id=str(j["job_id"])[:255],
                            url=j.get("job_apply_link"),
                            title=j["job_title"],
                            company=j.get("employer_name"),
                            location=place or None,
                            description_html=j.get("job_description") or "",
                            posted_at=_dt(j.get("job_posted_at_datetime_utc")),
                            contract_hint=j.get("job_employment_type"),
                            remote_hint=bool(j.get("job_is_remote")) or None,
                            salary_min=_dec(j.get("job_min_salary")),
                            salary_max=_dec(j.get("job_max_salary")),
                            salary_currency=j.get("job_salary_currency"),
                            country_hint=(j.get("job_country") or "")[:2].lower() or None,
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        return out


SOURCES: tuple[JobSource, ...] = (
    Remotive(),
    RemoteOK(),
    WeWorkRemotely(),
    Tanitjobs(),
    Keejob(),
    Adzuna(),
    JSearch(),
)
SOURCE_BY_NAME = {s.info.name: s for s in SOURCES}
