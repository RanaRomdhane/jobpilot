"""The sender service: the only code that sends an email (ADR-0003).

It is a worker job, not an agent tool, and it holds the only `gmail.send` token. It sends an email
only with the one-time token from Approve & Send, checked in the same transaction that consumes it,
together with the daily cap and the duplicate guard. A crash after the token is consumed leaves
the email failed, never sent twice: you approve it again.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select

from jobpilot_core.enums import APPLICATION_LABEL, ApplicationStatus, OutgoingStatus
from jobpilot_core.models import Application, ApplicationEvent, OutgoingEmail
from jobpilot_core.outgoing import header_text, token_matches
from jobpilot_core.send_rules import check_send
from jobpilot_mcp_gmail.gmail_client import (
    API,
    GmailAuthExpiredError,
    GmailClient,
    GmailError,
    OAuthToken,
    TokenSaver,
    fresh_access_token,
)
from jobpilot_resume.storage import resume_path

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

LABEL_RETRY_WINDOW = timedelta(days=30)


class SendRefusedError(RuntimeError):
    """A check failed: nothing was sent. `code` is shown to the user."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class Attachment:
    name: str
    data: bytes


def build_message(
    *,
    from_name: str | None,
    from_address: str,
    to: str,
    cc: list[str],
    subject: str,
    body: str,
    attachments: list[Attachment],
) -> str:
    """The raw RFC 5322 message, base64url-encoded as the Gmail API expects."""
    msg = EmailMessage(policy=SMTP)
    msg["From"] = Address(display_name=header_text(from_name, 100), addr_spec=from_address)
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = header_text(subject)
    msg.set_content(body)
    for a in attachments:
        msg.add_attachment(a.data, maintype="application", subtype="pdf", filename=a.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class GmailSender:
    """Gmail with `gmail.send` only: one method, which sends."""

    def __init__(
        self,
        token: OAuthToken,
        *,
        on_token_refresh: TokenSaver | None = None,
        http: httpx.AsyncClient | None = None,
        api_url: str = API,
    ) -> None:
        self._token = token
        self._on_refresh = on_token_refresh
        self._http = http or httpx.AsyncClient(timeout=60)
        self._api = api_url.rstrip("/")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, raw: str) -> tuple[str, str]:
        """Send once (never retried: a timeout may still have sent it). Returns (id, threadId)."""
        token = await fresh_access_token(self._http, self._token, self._on_refresh)
        resp = await self._http.post(
            f"{self._api}/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw},
        )
        if resp.status_code >= 400:
            raise GmailError(f"gmail POST /messages/send -> {resp.status_code}")
        data = resp.json()
        return str(data["id"]), str(data.get("threadId") or data["id"])


SenderFactory = Callable[[], Awaitable[tuple[GmailSender, str]]]  # sender, account address
Labeler = Callable[[str], Awaitable[None]]  # adds JobPilot/Candidature to a sent message


async def _consume(
    session: AsyncSession,
    email_id: int,
    token: str,
    *,
    override_duplicate: bool,
    confirm_flagged: bool,
) -> tuple[OutgoingEmail, Application]:
    """Verify the approval and mark it consumed, in one transaction with a row lock."""
    email = await session.scalar(
        select(OutgoingEmail).where(OutgoingEmail.id == email_id).with_for_update()
    )
    if email is None:
        raise SendRefusedError("not_found")
    now = datetime.now(UTC)
    if email.status is not OutgoingStatus.APPROVED or email.approval_consumed_at is not None:
        raise SendRefusedError("not_approved")
    if not token_matches(token, email.approval_token_hash):
        raise SendRefusedError("bad_token")
    if email.approval_expires_at is None or email.approval_expires_at < now:
        email.status = OutgoingStatus.DRAFT
        raise SendRefusedError("approval_expired")
    app = await session.get(Application, email.application_id)
    if app is None:
        raise SendRefusedError("not_found")
    check = await check_send(
        session,
        email,
        app,
        override_duplicate=override_duplicate,
        confirm_flagged=confirm_flagged,
        now=now,
    )
    if not check.ok:
        email.status = OutgoingStatus.DRAFT
        raise SendRefusedError(check.blocking[0])
    email.approval_consumed_at = now
    return email, app


async def send_approved(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage_dir: Path,
    email_id: int,
    token: str,
    *,
    sender_factory: SenderFactory,
    labeler: Labeler | None = None,
    from_name: str | None = None,
    override_duplicate: bool = False,
    confirm_flagged: bool = False,
) -> dict[str, Any]:
    async with sessionmaker() as session:
        try:
            email, app = await _consume(
                session,
                email_id,
                token,
                override_duplicate=override_duplicate,
                confirm_flagged=confirm_flagged,
            )
        except SendRefusedError as exc:
            if exc.code != "not_found":
                await _event(session, email_id, "send_refused", {"reason": exc.code})
            await session.commit()
            raise
        await session.commit()  # the token is spent before anything leaves
        attachments = [
            Attachment(a["name"], resume_path(storage_dir, a["path"]).read_bytes())
            for a in email.attachments
        ]
        to, cc, subject, body = (
            email.to_address or "",
            list(email.cc or []),
            email.subject,
            email.body,
        )
        app_id = app.id

    sender, account = await sender_factory()
    try:
        raw = build_message(
            from_name=from_name,
            from_address=account,
            to=to,
            cc=cc,
            subject=subject,
            body=body,
            attachments=attachments,
        )
        message_id, thread_id = await sender.send(raw)
    except Exception as exc:
        async with sessionmaker() as session:
            row = await session.get(OutgoingEmail, email_id)
            assert row is not None
            row.status = OutgoingStatus.FAILED
            row.error = f"{type(exc).__name__}: {exc}"[:500]
            session.add(
                ApplicationEvent(
                    application_id=app_id, type="send_failed", payload={"error": row.error}
                )
            )
            await session.commit()
        raise
    finally:
        await sender.aclose()

    now = datetime.now(UTC)
    async with sessionmaker() as session:
        row = await session.get(OutgoingEmail, email_id)
        application = await session.get(Application, app_id)
        assert row is not None and application is not None
        row.status = OutgoingStatus.SENT
        row.sent_at = now
        row.sent_message_id = message_id
        row.gmail_thread_id = thread_id
        row.error = None
        previous = application.status
        application.status = ApplicationStatus.SENT
        application.applied_at = now
        application.gmail_thread_id = thread_id  # replies in this thread link to it (triage)
        application.contact_email = to
        session.add(
            ApplicationEvent(
                application_id=app_id,
                type="email_sent",
                payload={
                    "to": to,
                    "subject": subject,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "from_status": previous.value,
                },
            )
        )
        await session.commit()

    labelled = False
    if labeler is not None:
        try:
            await labeler(message_id)
            labelled = True
        except Exception as exc:  # the email is sent: a missing label must not hide that
            # the next triage run tries again (`label_sent_applications`)
            log.warning("sent, but %s could not be applied: %s", APPLICATION_LABEL, exc)
    if labelled:
        async with sessionmaker() as session:
            row = await session.get(OutgoingEmail, email_id)
            assert row is not None
            row.labelled_at = datetime.now(UTC)
            await session.commit()
    return {"sent": True, "message_id": message_id, "thread_id": thread_id, "labelled": labelled}


async def label_sent_applications(
    sessionmaker: async_sessionmaker[AsyncSession], gmail: GmailClient
) -> int:
    """Apply JobPilot/Candidature to sent applications that do not have it yet.

    The label is added with the triage token, which may have been expired when the email
    was sent. Runs at each triage; a message that cannot be labelled (deleted since) is left
    for the next run, and no longer tried once it is older than `LABEL_RETRY_WINDOW`.
    """
    since = datetime.now(UTC) - LABEL_RETRY_WINDOW
    async with sessionmaker() as session:
        pending = (
            await session.scalars(
                select(OutgoingEmail).where(
                    OutgoingEmail.status == OutgoingStatus.SENT,
                    OutgoingEmail.sent_message_id.is_not(None),
                    OutgoingEmail.labelled_at.is_(None),
                    OutgoingEmail.sent_at >= since,
                )
            )
        ).all()
        if not pending:
            return 0
        ids = await gmail.ensure_labels([APPLICATION_LABEL])
        done = 0
        for email in pending:
            assert email.sent_message_id is not None
            try:
                await gmail.add_labels([email.sent_message_id], [ids[APPLICATION_LABEL]], ids)
            except GmailAuthExpiredError:
                raise
            except GmailError as exc:
                log.warning("%s not applied to a sent application: %s", APPLICATION_LABEL, exc)
                continue
            email.labelled_at = datetime.now(UTC)
            done += 1
        await session.commit()
        return done


async def _event(session: AsyncSession, email_id: int, kind: str, payload: dict[str, Any]) -> None:
    email = await session.get(OutgoingEmail, email_id)
    if email is not None:
        session.add(
            ApplicationEvent(application_id=email.application_id, type=kind, payload=payload)
        )
