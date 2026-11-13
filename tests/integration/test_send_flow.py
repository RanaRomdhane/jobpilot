"""Review & send end to end on the test database (synthetic data, Gmail faked):
review -> draft -> edit -> Approve & Send -> sender service -> tracking -> reply linking."""

from __future__ import annotations

import base64
import email as email_lib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.policy import default
from pathlib import Path
from typing import Any

import httpx
import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.jobs import Job
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from jobpilot_core.config import Settings
from jobpilot_core.enums import (
    ApplicationStatus,
    CompileStatus,
    DocumentKind,
    EmailCategory,
    OAuthScopeSet,
    OutgoingStatus,
)
from jobpilot_core.models import (
    Application,
    ApplicationEvent,
    EmailMessage,
    GeneratedDocument,
    JobPosting,
    OAuthCredential,
    OutgoingEmail,
    Resume,
    TriageBatch,
)
from jobpilot_worker.pipeline import link_batch
from jobpilot_worker.sender import GmailSender, SendRefusedError, send_approved

from .conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration

MASTER: dict[str, Any] = {
    "language": "en",
    "contact": {"full_name": "Ada Example", "email": "ada@example.com", "phone": "+00 000 000"},
    "headline": "Machine Learning Engineer",
    "experiences": [
        {
            "id": "exp_1",
            "company": "Exemplia Labs",
            "title": "ML Engineer",
            "bullets": [{"id": "b_1", "text": "Built a BERT model reaching 73% accuracy"}],
        }
    ],
    "links": [
        {"id": "lnk_1", "kind": "linkedin", "url": "https://www.linkedin.com/in/ada-example"}
    ],
}
CV_CONTENT = {
    "language": "fr",
    "findings": [],
    "profile": {"headline": "Ingénieure IA"},
    "plan": {
        "experiences": [
            {
                "id": "exp_1",
                "bullets": [{"source": "b_1", "text": "Modèle BERT à 73 % d'exactitude"}],
            }
        ]
    },
}


class FakeSender(GmailSender):
    def __init__(self) -> None:
        super().__init__(OAuthTokenStub)  # type: ignore[arg-type]
        self.raw: list[str] = []

    async def send(self, raw: str) -> tuple[str, str]:
        self.raw.append(raw)
        return "msg-1", "thread-1"


OAuthTokenStub = object()


@pytest.fixture
async def ctx(
    api_settings: Settings, real_redis: Redis, engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[dict[str, Any]]:
    from jobpilot_api.main import create_app

    settings = api_settings.model_copy(update={"storage_dir": tmp_path})
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE job_posting, job_match, application, generated_document, outgoing_email, "
                "oauth_credentials, user_profile, resume, task_run, email_message, triage_batch "
                "RESTART IDENTITY CASCADE"
            )
        )
    (tmp_path / "documents" / "1").mkdir(parents=True)
    for name in ("cv_v1.pdf", "cover_letter_v1.pdf"):
        (tmp_path / "documents" / "1" / name).write_bytes(b"%PDF-1.4 " + name.encode())
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Resume(is_master=True, status="master", parsed=MASTER, parse_meta={}))
        job = JobPosting(
            source="remotive",
            external_id="j1",
            title="Ingénieur IA F/H",
            company_name="Exemplia Gulf",
            language="fr",
            description="Poste IA.",
            dedup_hash="h1",
            apply_email="jobs@example.com",
        )
        s.add(job)
        await s.flush()
        app = Application(
            job_id=job.id,
            role_title=job.title,
            status=ApplicationStatus.READY_FOR_REVIEW,
            contact_email="jobs@example.com",
        )
        s.add(app)
        await s.flush()
        for kind, path, content in (
            (DocumentKind.CV, "documents/1/cv_v1.pdf", CV_CONTENT),
            (DocumentKind.COVER_LETTER, "documents/1/cover_letter_v1.pdf", {"findings": []}),
        ):
            s.add(
                GeneratedDocument(
                    application_id=app.id,
                    kind=kind,
                    version=1,
                    content=content,
                    pdf_path=path,
                    compile_status=CompileStatus.SUCCESS,
                    page_count=1,
                )
            )
        await s.commit()
    api = create_app(settings)
    async with api.router.lifespan_context(api):
        transport = httpx.ASGITransport(app=api)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            yield {"client": c, "settings": settings, "sm": sm}


async def enable_sending(c: httpx.AsyncClient, sm: async_sessionmaker[Any], cap: int = 20) -> None:
    prefs = {"locale": "fr", "timezone": "Africa/Tunis", "daily_send_cap": cap}
    r = await c.put("/api/v1/settings/preferences", json={**prefs, "sending_enabled": True})
    assert r.status_code == 200, r.text
    async with sm() as s:
        s.add(
            OAuthCredential(
                provider="google",
                scope_set=OAuthScopeSet.SEND,
                account_email="ada@example.com",
                encrypted_token=b"not-used-here",
            )
        )
        await s.commit()


async def approval_token(settings: Settings, task_id: str) -> tuple[str, list[Any]]:
    """What the API handed to the worker (the token exists nowhere else)."""
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        info = await Job(task_id, pool).info()
    finally:
        await pool.aclose()
    assert info is not None and info.function == "send_email"
    args = list(info.args)
    return str(args[2]), args


async def test_review_approve_send_and_track(ctx: dict[str, Any]) -> None:
    c, settings, sm = ctx["client"], ctx["settings"], ctx["sm"]

    review = (await c.get("/api/v1/outgoing/review/1")).json()
    mail = review["email"]
    assert mail["to_address"] == "jobs@example.com" and mail["status"] == "draft"
    assert mail["subject"] == "Candidature : Ingénieur IA F/H"
    assert mail["body"].endswith(
        "Cordialement,\nAda Example\n+00 000 000\nhttps://www.linkedin.com/in/ada-example"
    )
    assert [a["name"] for a in mail["attachments"]] == [
        "CV_Ada_Example_Exemplia_Gulf.pdf",
        "CoverLetter_Ada_Example_Exemplia_Gulf.pdf",
    ]
    assert {"sending_disabled", "gmail_send_not_connected"} <= set(review["checks"]["blocking"])
    assert {
        "where": "b_1",
        "master": MASTER["experiences"][0]["bullets"][0]["text"],
        "tailored": "Modèle BERT à 73 % d'exactitude",
    } in review["changes"]
    refused = await c.post(f"/api/v1/outgoing/{mail['id']}/approve", json={})
    assert refused.status_code == 409 and "sending_disabled" in refused.json()["detail"]["blocking"]

    await enable_sending(c, sm)
    bad = await c.put(
        f"/api/v1/outgoing/{mail['id']}",
        json={"to_address": "x@example.com\nBcc: y@example.com", "subject": "s", "body": "b"},
    )
    assert bad.status_code == 422
    edited = await c.put(
        f"/api/v1/outgoing/{mail['id']}",
        json={
            "to_address": "Me.Other@Example.com",
            "subject": mail["subject"],
            "body": "Bonjour.\r\nAda",
        },
    )
    assert edited.status_code == 200 and edited.json()["to_address"] == "me.other@example.com"
    review = (await c.get("/api/v1/outgoing/review/1")).json()
    assert (
        review["checks"]["blocking"] == [] and "recipient_changed" in review["checks"]["warnings"]
    )

    started = await c.post(f"/api/v1/outgoing/{mail['id']}/approve", json={})
    assert started.status_code == 202, started.text
    token, _ = await approval_token(settings, started.json()["task_id"])
    async with sm() as s:
        row = await s.get(OutgoingEmail, mail["id"])
        assert row is not None and row.status is OutgoingStatus.APPROVED
        assert row.approval_token_hash and token not in row.approval_token_hash
    assert (await c.post(f"/api/v1/outgoing/{mail['id']}/approve", json={})).status_code == 409

    sender = FakeSender()
    labelled: list[str] = []

    async def factory() -> tuple[GmailSender, str]:
        return sender, "ada@example.com"

    async def labeler(message_id: str) -> None:
        labelled.append(message_id)

    result = await send_approved(
        sm,
        settings.storage_dir,
        mail["id"],
        token,
        sender_factory=factory,
        labeler=labeler,
        from_name="Ada Example",
    )
    assert result == {
        "sent": True,
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "labelled": True,
    }
    assert labelled == ["msg-1"]
    msg = email_lib.message_from_bytes(base64.urlsafe_b64decode(sender.raw[0]), policy=default)
    assert msg["To"] == "me.other@example.com" and msg["From"] == "Ada Example <ada@example.com>"
    assert [p.get_filename() for p in msg.iter_attachments()] == [
        "CV_Ada_Example_Exemplia_Gulf.pdf",
        "CoverLetter_Ada_Example_Exemplia_Gulf.pdf",
    ]
    async with sm() as s:
        app = await s.get(Application, 1)
        row = await s.get(OutgoingEmail, mail["id"])
        kinds = (await s.scalars(select(ApplicationEvent.type).order_by(ApplicationEvent.id))).all()
    assert app is not None and app.status is ApplicationStatus.SENT
    assert app.gmail_thread_id == "thread-1" and app.applied_at is not None
    assert row is not None and row.status is OutgoingStatus.SENT and row.sent_message_id == "msg-1"
    assert kinds[-2:] == ["send_approved", "email_sent"]

    # the token is spent: replaying it sends nothing
    with pytest.raises(SendRefusedError) as again:
        await send_approved(sm, settings.storage_dir, mail["id"], token, sender_factory=factory)
    assert again.value.code == "not_approved" and len(sender.raw) == 1

    # a reply in that thread is linked, whatever its category
    async with sm() as s:
        batch = TriageBatch(id=uuid.uuid4())
        s.add(batch)
        await s.flush()
        s.add(
            EmailMessage(
                gmail_id="r1",
                thread_id="thread-1",
                from_address="me.other@example.com",
                subject="Re: Candidature",
                category=EmailCategory.AUTRE,
                confidence=0.9,
                batch_id=batch.id,
            )
        )
        await s.commit()
        linked = await link_batch(s, batch.id, auto_apply_confidence=0.8)
        received = await s.scalar(
            select(ApplicationEvent).where(ApplicationEvent.type == "email_received")
        )
    assert linked.linked == 1 and received is not None and received.application_id == 1


async def test_expired_or_wrong_tokens_and_the_daily_cap(ctx: dict[str, Any]) -> None:
    c, settings, sm = ctx["client"], ctx["settings"], ctx["sm"]
    await enable_sending(c, sm, cap=1)
    mail = (await c.get("/api/v1/outgoing/review/1")).json()["email"]
    started = await c.post(f"/api/v1/outgoing/{mail['id']}/approve", json={})
    token, _ = await approval_token(settings, started.json()["task_id"])
    sender = FakeSender()

    async def factory() -> tuple[GmailSender, str]:
        return sender, "ada@example.com"

    with pytest.raises(SendRefusedError) as wrong:
        await send_approved(sm, settings.storage_dir, mail["id"], "guess", sender_factory=factory)
    assert wrong.value.code == "bad_token"

    async with sm() as s:
        row = await s.get(OutgoingEmail, mail["id"])
        assert row is not None
        row.approval_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    with pytest.raises(SendRefusedError) as late:
        await send_approved(sm, settings.storage_dir, mail["id"], token, sender_factory=factory)
    assert late.value.code == "approval_expired" and sender.raw == []
    async with sm() as s:
        row = await s.get(OutgoingEmail, mail["id"])
        assert row is not None and row.status is OutgoingStatus.DRAFT  # approve again

    started = await c.post(f"/api/v1/outgoing/{mail['id']}/approve", json={})
    token, _ = await approval_token(settings, started.json()["task_id"])
    await send_approved(sm, settings.storage_dir, mail["id"], token, sender_factory=factory)

    # a second application to the same company and role: duplicate, and the cap of 1 is reached
    async with sm() as s:
        s.add(
            Application(
                job_id=1, role_title="ingénieur ia", status=ApplicationStatus.READY_FOR_REVIEW
            )
        )
        await s.flush()
        for kind in (DocumentKind.CV, DocumentKind.COVER_LETTER):
            s.add(
                GeneratedDocument(
                    application_id=2,
                    kind=kind,
                    version=1,
                    content={"findings": []},
                    pdf_path="documents/1/cv_v1.pdf",
                    compile_status=CompileStatus.SUCCESS,
                )
            )
        await s.commit()
    second = (await c.get("/api/v1/outgoing/review/2")).json()
    assert {"daily_cap", "duplicate"} <= set(second["checks"]["blocking"])
    assert second["checks"]["duplicate_ids"] == [1] and second["checks"]["sent_today"] == 1


async def test_applied_via_link_is_tracked(ctx: dict[str, Any]) -> None:
    c, sm = ctx["client"], ctx["sm"]
    r = await c.post("/api/v1/outgoing/applications/1/applied-via-link")
    assert r.status_code == 200 and r.json()["status"] == "sent"
    async with sm() as s:
        app = await s.get(Application, 1)
        kind = await s.scalar(select(ApplicationEvent.type).order_by(ApplicationEvent.id.desc()))
    assert app is not None and app.applied_at is not None and kind == "applied_via_link"
