from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from jobpilot_core.enums import ApplicationStatus, EmailCategory, SuggestionStatus
from jobpilot_core.models import EmailMessage, StatusSuggestion

from .conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration


@pytest.fixture
async def sm(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE email_message, triage_batch, application, application_event, "
                "status_suggestion, company, task_run, user_profile RESTART IDENTITY CASCADE"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)


async def seed_emails(sm: async_sessionmaker[AsyncSession]) -> list[int]:
    now = datetime.now(UTC)
    rows = [
        EmailMessage(
            gmail_id="g1",
            thread_id="t1",
            from_address="hr@acme.tn",
            subject="Entretien",
            snippet="Invitation",
            received_at=now,
            category=EmailCategory.ENTRETIEN,
            confidence=0.95,
            processed_at=now,
        ),
        EmailMessage(
            gmail_id="g2",
            thread_id="t2",
            from_address="news@jobs.tn",
            subject="Alertes",
            snippet="10 offres",
            received_at=now - timedelta(hours=1),
            category=EmailCategory.NEWSLETTER,
            confidence=0.6,
            processed_at=now,
            injection_flag=True,
            injection_score=0.9,
            injection_reasons=[
                {
                    "layer": "heuristic",
                    "rule": "fr.classify_as",
                    "category": "output_manipulation",
                    "weight": 0.85,
                }
            ],
        ),
        EmailMessage(
            gmail_id="g3",
            thread_id="t3",
            from_address="x@y.com",
            subject="Pending",
            snippet="?",
            received_at=now - timedelta(hours=2),
        ),
    ]
    async with sm() as s:
        s.add_all(rows)
        await s.commit()
        return [r.id for r in rows]


# ---------------------------------------------------------------- auth
async def test_protected_endpoints_require_login(anon_client: httpx.AsyncClient) -> None:
    for path in (
        "/api/v1/overview",
        "/api/v1/emails",
        "/api/v1/applications",
        "/api/v1/security",
        "/api/v1/settings",
        "/api/v1/gmail/status",
        "/api/v1/tasks",
    ):
        assert (await anon_client.get(path)).status_code == 401, path
    assert (await anon_client.get("/api/v1/health/live")).status_code == 200
    session = (await anon_client.get("/api/v1/auth/session")).json()
    assert session == {"authenticated": False, "password_set": True, "csrf_token": None}


async def test_login_sets_strict_httponly_cookie(anon_client: httpx.AsyncClient) -> None:
    bad = await anon_client.post("/api/v1/auth/login", json={"password": "nope"})
    assert bad.status_code == 401
    ok = await anon_client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "jp_session=" in cookie
    assert (await anon_client.get("/api/v1/auth/session")).json()["authenticated"] is True


async def test_mutations_require_csrf_token(client: httpx.AsyncClient) -> None:
    csrf = client.headers.pop("X-CSRF-Token")
    r = await client.post("/api/v1/applications", json={"company_name": "A", "role_title": "B"})
    assert r.status_code == 403 and r.json()["detail"] == "csrf_failed"
    client.headers["X-CSRF-Token"] = csrf
    r = await client.post("/api/v1/applications", json={"company_name": "A", "role_title": "B"})
    assert r.status_code == 201


async def test_logout_invalidates_session(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/overview")).status_code == 401


async def test_login_is_rate_limited(anon_client: httpx.AsyncClient) -> None:
    codes = [
        (await anon_client.post("/api/v1/auth/login", json={"password": "x"})).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [401] * 5 and codes[-1] == 429


# ---------------------------------------------------------------- emails
async def test_email_list_filters_and_counts(
    client: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> None:
    await seed_emails(sm)
    page = (await client.get("/api/v1/emails")).json()
    assert page["total"] == 3 and page["counts"]["flagged"] == 1
    assert page["counts"]["unclassified"] == 1
    flagged = (await client.get("/api/v1/emails", params={"flagged": True})).json()
    assert [e["subject"] for e in flagged["items"]] == ["Alertes"]
    entretien = (await client.get("/api/v1/emails", params={"category": "Entretien"})).json()
    assert entretien["total"] == 1
    low = (await client.get("/api/v1/emails", params={"max_confidence": 0.7})).json()
    assert [e["subject"] for e in low["items"]] == ["Alertes"]
    assert (await client.get("/api/v1/emails", params={"q": "acme"})).json()["total"] == 1


async def test_reclassify_and_clear_enqueue_label_jobs(
    client: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seed_emails(sm)
    r = (await client.patch(f"/api/v1/emails/{ids[1]}/category", json={"category": "Autre"})).json()
    assert r["email"]["category"] == "Autre" and r["email"]["manual_category"] == "Autre"
    task = (await client.get(f"/api/v1/tasks/{r['task_id']}")).json()
    assert task["kind"] == "user_label_change"
    cleared = (await client.post(f"/api/v1/emails/{ids[1]}/clear-suspicious")).json()
    assert cleared["email"]["injection_flag"] is False
    assert cleared["email"]["injection_reasons"][-1]["rule"] == "user.cleared"
    missing = await client.patch("/api/v1/emails/999999/category", json={"category": "Autre"})
    assert missing.status_code == 404
    bad = await client.patch(f"/api/v1/emails/{ids[0]}/category", json={"category": "Offer"})
    assert bad.status_code == 422


# ---------------------------------------------------------------- applications
async def test_application_lifecycle(
    client: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seed_emails(sm)
    created = (
        await client.post(
            "/api/v1/applications",
            json={
                "company_name": "Acme",
                "company_domain": "https://www.acme.tn/careers",
                "role_title": "Backend Engineer",
                "contact_email": "hr@acme.tn",
            },
        )
    ).json()
    assert created["status"] == "sent" and created["company"]["domain"] == "acme.tn"
    assert created["applied_at"] is not None
    app_id = created["id"]

    moved = await client.patch(
        f"/api/v1/applications/{app_id}/status", json={"status": "interview"}
    )
    assert moved.json()["status"] == "interview"
    await client.post(f"/api/v1/applications/{app_id}/emails", json={"email_id": ids[0]})
    detail = (await client.get(f"/api/v1/applications/{app_id}")).json()
    assert [e["type"] for e in detail["events"]][:3] == [
        "email_linked",
        "status_changed",
        "created",
    ]
    assert detail["gmail_thread_id"] == "t1" and detail["emails"][0]["subject"] == "Entretien"

    listing = (await client.get("/api/v1/applications", params={"status": "interview"})).json()
    assert [a["id"] for a in listing] == [app_id]
    assert (await client.delete(f"/api/v1/applications/{app_id}")).status_code == 204
    assert (await client.get(f"/api/v1/applications/{app_id}")).status_code == 404


async def test_suggestions_accept_and_dismiss(
    client: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> None:
    app_id = (
        await client.post(
            "/api/v1/applications", json={"company_name": "Acme", "role_title": "Dev"}
        )
    ).json()["id"]
    async with sm() as s:
        s.add_all(
            [
                StatusSuggestion(
                    application_id=app_id,
                    from_status=ApplicationStatus.SENT,
                    to_status=ApplicationStatus.INTERVIEW,
                    confidence=0.7,
                    interview_at=datetime.now(UTC) + timedelta(days=3),
                ),
                StatusSuggestion(
                    application_id=app_id,
                    from_status=ApplicationStatus.SENT,
                    to_status=ApplicationStatus.REJECTED,
                    confidence=0.5,
                ),
            ]
        )
        await s.commit()
    pending = (await client.get("/api/v1/suggestions")).json()
    assert len(pending) == 2 and pending[0]["company_name"] == "Acme"
    by_target = {p["to_status"]: p["id"] for p in pending}
    accepted = (await client.post(f"/api/v1/suggestions/{by_target['interview']}/accept")).json()
    assert accepted["status"] == SuggestionStatus.ACCEPTED
    await client.post(f"/api/v1/suggestions/{by_target['rejected']}/dismiss")
    app = (await client.get(f"/api/v1/applications/{app_id}")).json()
    assert app["status"] == "interview" and app["interview_at"] is not None
    again = await client.post(f"/api/v1/suggestions/{by_target['rejected']}/accept")
    assert again.status_code == 409


# ---------------------------------------------------------------- overview / security / settings
async def test_overview_kpis_and_funnel(
    client: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> None:
    await seed_emails(sm)
    for st in ("sent", "sent", "interview", "offer", "rejected"):
        await client.post(
            "/api/v1/applications",
            json={"company_name": f"C-{st}", "role_title": "Dev", "status": st},
        )
    ov = (await client.get("/api/v1/overview")).json()
    k = ov["kpis"]
    assert k["applications_sent"] == 5 and k["offers"] == 1 and k["interviews"] == 2
    assert k["response_rate"] == pytest.approx(3 / 5)
    assert [f["count"] for f in ov["funnel"]] == [5, 3, 2, 1]
    assert ov["security_alerts"] == 1 and ov["inbox"]["Newsletter"] == 1


async def test_security_report_runs_the_suite(
    client: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> None:
    await seed_emails(sm)
    rep = (await client.get("/api/v1/security")).json()
    assert [f["title"] for f in rep["flagged"]] == ["Alertes"]
    assert rep["suite"]["injections"] >= 30 and rep["suite"]["recall"] >= 0.9


async def test_settings_hide_secrets_and_save_preferences(
    client: httpx.AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> None:
    s = (await client.get("/api/v1/settings")).json()
    assert s["llm"]["provider"] == "groq" and s["llm"]["groq_key_set"] is True
    assert "gsk_" not in str(s)
    saved = await client.put(
        "/api/v1/settings/preferences",
        json={"locale": "en", "timezone": "Europe/Paris", "daily_send_cap": 10},
    )
    assert saved.status_code == 200
    assert (await client.get("/api/v1/settings")).json()["preferences"]["locale"] == "en"
