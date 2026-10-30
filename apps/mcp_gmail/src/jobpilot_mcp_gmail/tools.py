"""Triage tool logic (plain Python, so it is testable without MCP or an LLM).

The MCP server exposes exactly three of these operations to the TriageAgent:
`list_unprocessed_emails`, `get_emails_compact` and `bulk_classify_and_label`.
Batches are created by the orchestrator, never by the agent: the agent can only read and label
the emails of the batch it was given.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import String, cast, func, select, update

from jobpilot_core.enums import SUSPICIOUS_LABEL, EmailCategory, TriageBatchStatus
from jobpilot_core.models import EmailMessage, TriageBatch
from jobpilot_mcp_gmail.gmail_client import GmailClient, GmailError
from jobpilot_mcp_gmail.schemas import BulkResult, ClassificationItem, CompactEmail
from jobpilot_security import InjectionDetector, InjectionReport, spotlight, truncate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

ALL_LABELS = [c.gmail_label for c in EmailCategory] + [SUSPICIOUS_LABEL]
MAX_VALIDATION_FAILURES = 2  # first failure → one retry; second → batch rejected

GmailFactory = Callable[[], Awaitable[GmailClient]]
_items = TypeAdapter(list[ClassificationItem])


async def create_batches(
    session: AsyncSession, size: int, *, task_run_id: uuid.UUID | None = None, limit: int = 10_000
) -> list[uuid.UUID]:
    """Batch the unclassified emails that are in no pending batch (newest first)."""
    pending = select(TriageBatch.id).where(TriageBatch.status == TriageBatchStatus.PENDING)
    rows = (
        await session.scalars(
            select(EmailMessage.id)
            .where(
                EmailMessage.category.is_(None),
                (EmailMessage.batch_id.is_(None)) | (EmailMessage.batch_id.not_in(pending)),
            )
            .order_by(EmailMessage.received_at.desc().nulls_last(), EmailMessage.id)
            .limit(limit)
        )
    ).all()
    batch_ids: list[uuid.UUID] = []
    for i in range(0, len(rows), size):
        chunk = rows[i : i + size]
        batch = TriageBatch(id=uuid.uuid4(), task_run_id=task_run_id, email_count=len(chunk))
        session.add(batch)
        await session.flush()
        await session.execute(
            update(EmailMessage).where(EmailMessage.id.in_(chunk)).values(batch_id=batch.id)
        )
        batch_ids.append(batch.id)
    await session.commit()
    return batch_ids


class TriageTools:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        gmail: GmailFactory,
        detector: InjectionDetector,
        *,
        snippet_max_chars: int = 300,
        subject_max_chars: int = 150,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._gmail = gmail
        self._detector = detector
        self._snippet_max = snippet_max_chars
        self._subject_max = subject_max_chars
        self._label_ids: dict[str, str] | None = None

    # ------------------------------------------------------------------ tool 1
    async def list_unprocessed_emails(self) -> dict[str, Any]:
        async with self._sessionmaker() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(EmailMessage)
                .where(EmailMessage.category.is_(None))
            )
        return {"unprocessed": int(count or 0)}

    # ------------------------------------------------------------------ tool 2
    async def _batch_emails(self, session: AsyncSession, batch_id: uuid.UUID) -> list[EmailMessage]:
        return list(
            (
                await session.scalars(
                    select(EmailMessage)
                    .where(EmailMessage.batch_id == batch_id)
                    .order_by(EmailMessage.received_at.desc().nulls_last(), EmailMessage.id)
                )
            ).all()
        )

    async def get_emails_compact(self, batch_id: str) -> str:
        async with self._sessionmaker() as session:
            bid = await resolve_batch(session, batch_id)
            batch = await session.get(TriageBatch, bid) if bid else None
            if bid is None or batch is None or batch.status != TriageBatchStatus.PENDING:
                return f"error: batch {batch_id!r} is unknown or already finished"
            emails = await self._batch_emails(session, bid)
        compact = [
            self._compact(i, e).model_dump(exclude_defaults=True)
            for i, e in enumerate(emails, start=1)
        ]
        n = len(compact)
        # The trailer is ours (outside the untrusted block): it pins the next step, because some
        # models otherwise answer with a text table instead of calling the tool.
        return (
            f"Batch {batch_id}: {n} emails (n = 1..{n}).\n"
            + spotlight("emails", compact)
            + f"\nNext step: call bulk_classify_and_label with batch_id={batch_id!r} and exactly "
            f"{n} results (n = 1..{n}). Do not write the classification as a message."
        )

    def _compact(self, n: int, e: EmailMessage) -> CompactEmail:
        sender = f"{e.from_name} <{e.from_address}>" if e.from_name else (e.from_address or "?")
        signals = sorted(
            {str(r.get("category")) for r in e.injection_reasons if r.get("layer") == "heuristic"}
        )
        return CompactEmail(
            n=n,
            sender=truncate(sender, 120),
            subject=truncate(e.subject or "", self._subject_max),
            date=e.received_at.date().isoformat() if e.received_at else None,
            snippet=truncate(e.snippet or "", self._snippet_max),
            signals=signals,
        )

    # ------------------------------------------------------------------ tool 3
    async def bulk_classify_and_label(self, batch_id: str, results: Any) -> BulkResult:
        async with self._sessionmaker() as session:
            bid = await resolve_batch(session, batch_id)
            if bid is None:
                # likely a mistyped id: not final, so the agent can correct it in the same run
                return BulkResult(
                    ok=False,
                    batch_id=batch_id,
                    errors=[f"unknown batch_id {batch_id!r}: use the exact id from the task"],
                )
            batch = await session.get(TriageBatch, bid, with_for_update=True)
            if batch is None or batch.status != TriageBatchStatus.PENDING:
                return BulkResult(
                    ok=False,
                    batch_id=batch_id,
                    final=True,
                    errors=[f"batch {batch_id} is not pending"],
                )
            batch.tool_calls += 1
            emails = await self._batch_emails(session, bid)
            errors = self._validate(results, len(emails))
            if errors:
                batch.validation_failures += 1
                final = batch.validation_failures >= MAX_VALIDATION_FAILURES
                if final:
                    batch.status = TriageBatchStatus.FAILED
                    batch.error = "; ".join(errors)[:2000]
                    batch.finished_at = datetime.now(UTC)
                    # release the emails so a later run can retry them in a new batch
                    for e in emails:
                        e.batch_id = None
                await session.commit()
                return BulkResult(ok=False, batch_id=batch_id, errors=errors, final=final)

            items = sorted(_items.validate_python(results), key=lambda it: it.n)
            now = datetime.now(UTC)
            flagged = 0
            for item, email in zip(items, emails, strict=True):
                report = InjectionReport.model_validate(
                    {
                        "score": email.injection_score or 0.0,
                        "flagged": email.injection_flag,
                        "reasons": email.injection_reasons,
                    }
                )
                if item.suspicious:
                    report = report.merged_with(
                        self._detector.model_report(item.reason), self._detector.threshold
                    )
                email.category = EmailCategory(item.category)
                email.confidence = item.confidence
                email.classification_reason = item.reason
                email.processed_at = now
                email.extracted = (
                    {"interview_at": item.interview_at.isoformat()} if item.interview_at else {}
                )
                email.injection_flag = report.flagged
                email.injection_score = report.score
                email.injection_reasons = [r.model_dump(mode="json") for r in report.reasons]
                flagged += report.flagged
            batch.status = TriageBatchStatus.DONE
            batch.finished_at = now
            await session.commit()
            labelled = await self.apply_labels(emails)
        return BulkResult(
            ok=True,
            batch_id=batch_id,
            classified=len(items),
            labelled=labelled,
            flagged=flagged,
            final=True,
        )

    @staticmethod
    def _validate(results: Any, expected: int) -> list[str]:
        try:
            items = _items.validate_python(results)
        except ValidationError as exc:
            return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]]
        ns = [it.n for it in items]
        errors: list[str] = []
        if sorted(ns) != list(range(1, expected + 1)):
            missing = sorted(set(range(1, expected + 1)) - set(ns))
            extra = sorted({n for n in ns if n < 1 or n > expected or ns.count(n) > 1})
            errors.append(
                f"expected exactly one result for each n in 1..{expected}; "
                f"missing={missing} invalid_or_duplicate={extra}"
            )
        return errors

    async def apply_labels(self, emails: list[EmailMessage]) -> int:
        """Add JobPilot labels in Gmail. Failures leave `label_applied=False` for a later retry."""
        try:
            gmail = await self._gmail()
            if self._label_ids is None:
                self._label_ids = await gmail.ensure_labels(ALL_LABELS)
            groups: dict[tuple[str, ...], list[EmailMessage]] = defaultdict(list)
            for e in emails:
                if e.category is None:
                    continue
                names = [e.category.gmail_label] + ([SUSPICIOUS_LABEL] if e.injection_flag else [])
                groups[tuple(self._label_ids[n] for n in names)].append(e)
            for label_ids, group in groups.items():
                await gmail.add_labels([e.gmail_id for e in group], label_ids, self._label_ids)
        except (GmailError, RuntimeError) as exc:
            log.warning(
                "labelling failed; will retry later", extra={"data": {"error": str(exc)[:200]}}
            )
            return 0
        async with self._sessionmaker() as session:
            await session.execute(
                update(EmailMessage)
                .where(EmailMessage.id.in_([e.id for e in emails]))
                .values(label_applied=True)
            )
            await session.commit()
        return len(emails)


HANDLE_LEN = 8


def batch_handle(batch_id: uuid.UUID) -> str:
    """Short id shown to the agent: long UUIDs get mistyped by models now and then."""
    return batch_id.hex[:HANDLE_LEN]


async def resolve_batch(session: AsyncSession, batch_id: str) -> uuid.UUID | None:
    """Accept a full UUID or a short handle; None if unknown or ambiguous."""
    value = str(batch_id).strip().lower()
    try:
        return uuid.UUID(value)
    except ValueError:
        pass
    if len(value) != HANDLE_LEN or any(c not in "0123456789abcdef" for c in value):
        return None
    matches = (
        await session.scalars(
            select(TriageBatch.id).where(cast(TriageBatch.id, String).like(f"{value}%")).limit(2)
        )
    ).all()
    return matches[0] if len(matches) == 1 else None


async def apply_pending_labels(
    tools: TriageTools, sessionmaker: async_sessionmaker[AsyncSession], *, limit: int = 1_000
) -> int:
    """Retry Gmail labelling for classified emails whose label could not be applied earlier."""
    async with sessionmaker() as session:
        emails = list(
            (
                await session.scalars(
                    select(EmailMessage)
                    .where(
                        EmailMessage.category.is_not(None), EmailMessage.label_applied.is_(False)
                    )
                    .limit(limit)
                )
            ).all()
        )
    return await tools.apply_labels(emails) if emails else 0
