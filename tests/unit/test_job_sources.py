"""Connectors and the polite HTTP client, replayed from recorded (synthetic) fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import httpx
import pytest

from jobpilot_core.config import Settings
from jobpilot_core.enums import Region, WorkMode
from jobpilot_jobs_sources.http import PoliteClient, SourceError
from jobpilot_jobs_sources.models import SourceQuery
from jobpilot_jobs_sources.normalize import normalize
from jobpilot_jobs_sources.sources import (
    Adzuna,
    JSearch,
    Keejob,
    RemoteOK,
    Remotive,
    Tanitjobs,
    WeWorkRemotely,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "jobs"
ROUTES: dict[str, str] = {
    "remotive.com/api/remote-jobs": "remotive.json",
    "remoteok.com/api": "remoteok.json",
    "weworkremotely.com/categories/remote-programming-jobs.rss": "weworkremotely.rss",
    "weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss": "weworkremotely.rss",
    "www.tanitjobs.com/rss/": "tanitjobs.rss",
    "www.keejob.com/offres-emploi/": "keejob.html",
    "api.adzuna.com/v1/api/jobs/fr/search/1": "adzuna.json",
    "jsearch.p.rapidapi.com/search-v2": "jsearch.json",
}
ROBOTS = "User-agent: *\nAllow: /\nDisallow: /*keyword\n"


def recorded(calls: list[httpx.Request], robots: str = ROBOTS) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        key = f"{request.url.host}{request.url.path}"
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        name = ROUTES.get(key)
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, content=(FIX / name).read_bytes())

    return httpx.MockTransport(handler)


async def _no_sleep(_: float) -> None:
    return None


def make_client(transport: httpx.MockTransport, **kw: Any) -> PoliteClient:
    return PoliteClient(
        fakeredis.aioredis.FakeRedis(),
        user_agent="JobPilot-test/0",
        transport=transport,
        min_interval_s=0,
        sleep=_no_sleep,
        **kw,
    )


SETTINGS = Settings(
    _env_file=None,
    adzuna_app_id="test-id",
    adzuna_app_key="test-key",
    adzuna_countries=["fr"],
    rapidapi_key="test-rapid",
    keejob_pages=1,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Remotive(), 2),  # the malformed third item is skipped
        (RemoteOK(), 2),  # the legal notice is skipped
        (WeWorkRemotely(), 4),  # two feeds, same fixture
        (Tanitjobs(), 2),
        (Keejob(), 2),
        (Adzuna(), 2),
        (JSearch(), 1),
    ],
    ids=lambda v: getattr(getattr(v, "info", None), "name", str(v)),
)
async def test_every_connector_parses_its_recorded_response(source: Any, expected: int) -> None:
    calls: list[httpx.Request] = []
    client = make_client(recorded(calls))
    jobs = await source.search(client, SourceQuery(keywords=["machine learning"]), SETTINGS)
    await client.aclose()
    assert len(jobs) == expected
    assert all(j.source == source.info.name and j.title and j.external_id for j in jobs)
    ua = {c.headers["user-agent"] for c in calls}
    assert ua == {"JobPilot-test/0"}  # identifiable on every request


def test_connectors_extract_the_fields_that_matter() -> None:
    html = (FIX / "keejob.html").read_text(encoding="utf-8")
    kj = Keejob().parse(html)[0]
    assert kj.external_id == "880001" and kj.company == "Exemplia"
    assert kj.location == "Ariana, Ariana" and kj.posted_at == datetime(2026, 11, 3, tzinfo=UTC)
    assert kj.contract_hint == "CDI SIVP" and kj.tags == ["informatique / télécoms"]
    assert kj.url == "https://www.keejob.com/offres-emploi/880001/developpeur-python-junior/"


async def test_normalization_end_to_end() -> None:
    client = make_client(recorded([]))
    tanit = await Tanitjobs().search(client, SourceQuery(), SETTINGS)
    keejob = await Keejob().search(client, SourceQuery(), SETTINGS)
    remoteok = await RemoteOK().search(client, SourceQuery(), SETTINGS)
    jsearch = await JSearch().search(client, SourceQuery(), SETTINGS)
    await client.aclose()

    t = normalize(tanit[0])
    assert (t.title, t.company_name) == ("Ingénieur Data Science Junior (H/F)", "Exemplia Tunisie")
    assert (t.country, t.region, t.work_mode) == ("tn", Region.TUNISIA, WorkMode.ONSITE)
    assert (t.domain, t.seniority, t.contract_type, t.language) == (
        "ai_ml",
        "junior",
        "permanent",
        "fr",
    )

    k = normalize(keejob[0])
    assert (k.country, k.work_mode, k.contract_type, k.seniority) == (
        "tn",
        WorkMode.HYBRID,
        "fixed_term",  # SIVP is a fixed-term first-job contract
        "junior",
    )
    assert k.domain == "software_backend"

    r = normalize(remoteok[0])
    # a remote-only board: its own remote flag wins over wording in the text
    assert (r.region, r.work_mode, r.salary_currency) == (Region.EUROPE, WorkMode.REMOTE, "USD")
    lead = normalize(remoteok[1])
    assert (lead.region, lead.seniority) == (Region.WORLDWIDE, "lead")  # no location, remote

    j = normalize(jsearch[0])
    assert (j.country, j.region, j.domain) == ("ae", Region.MIDDLE_EAST, "ai_ml")


async def test_robots_txt_is_enforced_for_scraped_pages() -> None:
    calls: list[httpx.Request] = []
    client = make_client(recorded(calls, robots="User-agent: *\nDisallow: /offres-emploi/\n"))
    with pytest.raises(SourceError) as exc:
        await Keejob().search(client, SourceQuery(), SETTINGS)
    await client.aclose()
    assert exc.value.code == "robots_disallow"
    assert [c.url.path for c in calls] == ["/robots.txt"]  # the page itself was never requested


async def test_wildcard_robots_rules_are_understood() -> None:
    client = make_client(recorded([]))
    assert await client.robots_allowed("https://www.tanitjobs.com/rss/")
    assert not await client.robots_allowed("https://www.tanitjobs.com/jobs/?keyword=python")
    await client.aclose()


async def test_daily_cap_cache_and_retry() -> None:
    attempts: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, json={"jobs": []})

    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    client = PoliteClient(
        fakeredis.aioredis.FakeRedis(),
        user_agent="t",
        transport=httpx.MockTransport(flaky),
        min_interval_s=0,
        sleep=sleep,
    )
    url = "https://remotive.com/api/remote-jobs"
    assert await client.get_json(url, source="remotive", daily_cap=2) == {"jobs": []}
    assert slept == [3.0] and len(attempts) == 2  # Retry-After honoured
    # second call: served from cache, no request, no budget used
    await client.get_json(url, source="remotive", daily_cap=2)
    assert len(attempts) == 2 and await client.calls_today("remotive") == 1
    await client.get_json(url, params={"limit": 1}, source="remotive", daily_cap=2)
    with pytest.raises(SourceError) as exc:
        await client.get_json(url, params={"limit": 2}, source="remotive", daily_cap=2)
    assert exc.value.code == "daily_cap"
    await client.aclose()


async def test_oversized_responses_are_refused() -> None:
    def huge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (9 * 1024 * 1024))

    client = make_client(httpx.MockTransport(huge))
    with pytest.raises(SourceError) as exc:
        await client.get("https://remoteok.com/api", source="remoteok", daily_cap=5)
    assert exc.value.code == "too_large"
    await client.aclose()


def test_xml_bombs_are_rejected() -> None:
    from defusedxml import EntitiesForbidden

    from jobpilot_jobs_sources.sources import _rss_items

    bomb = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        "<rss><channel><item><title>&b;</title></item></channel></rss>"
    )
    with pytest.raises(EntitiesForbidden):
        list(_rss_items(bomb))


def test_keyed_sources_stay_off_without_keys() -> None:
    bare = Settings(_env_file=None)
    assert not Adzuna().enabled(bare) and not JSearch().enabled(bare)
    assert Remotive().enabled(bare)


def test_only_safe_links_and_emails_are_kept() -> None:
    from jobpilot_jobs_sources.models import RawJob

    n = normalize(
        RawJob(
            source="remoteok",
            external_id="x",
            title="Dev",
            url="javascript:alert(document.cookie)",
            apply_url="data:text/html,<script>x</script>",
            apply_email="not an email <script>",
        )
    )
    assert n.url is None and n.apply_url is None and n.apply_email is None
    ok = normalize(
        RawJob(
            source="remoteok",
            external_id="y",
            title="Dev",
            url="https://remoteok.com/remote-jobs/y",
            apply_email="jobs@example.com",
        )
    )
    assert ok.apply_url == "https://remoteok.com/remote-jobs/y"
    assert ok.apply_email == "jobs@example.com"
