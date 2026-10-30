"""Triage run: sync Gmail, batch unclassified emails, run the TriageAgent per batch, link pipeline.

Pacing comes from the shared Redis rate limiter inside the LLM transport: when the Groq budget
for the minute is spent, the next request simply waits — no fixed sleeps between batches.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from jobpilot_agents.triage import build_triage_agent, run_triage_batch, triage_mcp_server
from jobpilot_core.config import LLMPurpose, Settings
from jobpilot_core.crypto import SecretBox
from jobpilot_core.enums import OAuthScopeSet, TriageBatchStatus
from jobpilot_core.llm import DbUsageSink, build_provider
from jobpilot_core.models import EmailMessage, TriageBatch
from jobpilot_mcp_gmail.credentials import CredentialStore
from jobpilot_mcp_gmail.gmail_client import GmailClient, OAuthToken
from jobpilot_mcp_gmail.tools import (
    TriageTools,
    apply_pending_labels,
    batch_handle,
    create_batches,
)
from jobpilot_security.factory import build_detector
from jobpilot_worker.gmail_sync import GmailSync
from jobpilot_worker.pipeline import link_batch

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from jobpilot_core.tasks import TaskReporter

log = logging.getLogger(__name__)


@dataclass
class TriageSummary:
    sync_mode: str = ""
    synced: int = 0
    batches: int = 0
    batches_failed: int = 0
    classified: int = 0
    flagged: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    linked: int = 0
    auto_applied: int = 0
    suggested: int = 0
    relabelled: int = 0
    budget_exhausted: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def tool_calls_per_batch(self) -> float:
        ok = self.batches - self.batches_failed
        return round(self.tool_calls / ok, 2) if ok else 0.0


async def run_triage(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    reporter: TaskReporter,
    *,
    sync: bool = True,
) -> dict[str, Any]:
    summary = TriageSummary()
    store = CredentialStore(sessionmaker, SecretBox.from_settings(settings))
    token, account = await store.load(OAuthScopeSet.TRIAGE)

    async def save(t: OAuthToken) -> None:
        await store.save(OAuthScopeSet.TRIAGE, t)

    gmail = GmailClient(token, on_token_refresh=save)
    provider = build_provider(settings, redis, usage_sink=DbUsageSink(sessionmaker))
    try:
        if sync:
            await reporter.progress(0.01, "syncing Gmail")

            async def sync_progress(p: float, msg: str) -> None:
                await reporter.progress(0.01 + 0.29 * p, msg)

            result = await GmailSync(
                settings, sessionmaker, gmail, build_detector(settings, provider), account or ""
            ).run(sync_progress)
            summary.sync_mode, summary.synced = result.mode, result.added

        async def gmail_factory() -> GmailClient:
            return gmail

        tools = TriageTools(
            sessionmaker,
            gmail_factory,
            build_detector(settings, None),
            snippet_max_chars=settings.triage_snippet_max_chars,
            subject_max_chars=settings.triage_subject_max_chars,
        )
        summary.relabelled = await apply_pending_labels(tools, sessionmaker)

        async with sessionmaker() as session:
            batch_ids = await create_batches(
                session, settings.triage_batch_size, task_run_id=reporter.task_id
            )
        summary.batches = len(batch_ids)
        if not batch_ids:
            await reporter.progress(1.0, "nothing to classify")
            return _result(summary)

        client = provider.client(LLMPurpose.TRIAGE)
        model = provider.model(LLMPurpose.TRIAGE)
        async with triage_mcp_server() as server:
            agent = build_triage_agent(client, model, server, batch_size=settings.triage_batch_size)
            for i, batch_id in enumerate(batch_ids, start=1):
                await reporter.progress(
                    0.3 + 0.7 * (i - 1) / len(batch_ids), f"batch {i}/{len(batch_ids)}"
                )
                await _run_batch(agent, batch_id, settings, sessionmaker, summary)
                if summary.budget_exhausted:
                    # the remaining batches stay unclassified for the next (scheduled) run
                    await _release(sessionmaker, batch_ids[i:])
                    await reporter.progress(1.0, "LLM budget exhausted; resuming next run")
                    break
        await reporter.progress(1.0, "done")
        return _result(summary)
    finally:
        await gmail.aclose()


async def _run_batch(
    agent: Any,
    batch_id: uuid.UUID,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    summary: TriageSummary,
) -> None:
    async with sessionmaker() as session:
        batch = await session.get(TriageBatch, batch_id)
        size = batch.email_count if batch else 0
    stats = await run_triage_batch(
        agent, batch_handle(batch_id), size, max_turns=settings.triage_max_turns
    )
    tool_calls, llm_calls = stats.tool_calls, stats.llm_calls
    tokens_in, tokens_out, error = stats.input_tokens, stats.output_tokens, stats.error
    summary.budget_exhausted = stats.budget_exhausted

    async with sessionmaker() as session:
        batch = await session.get(TriageBatch, batch_id)
        assert batch is not None
        batch.llm_calls, batch.input_tokens, batch.output_tokens = llm_calls, tokens_in, tokens_out
        if batch.status == TriageBatchStatus.PENDING:  # the agent never completed the bulk call
            batch.status = TriageBatchStatus.FAILED
            batch.error = error or "agent finished without classifying the batch"
            batch.finished_at = datetime.now(UTC)
            await session.execute(
                update(EmailMessage).where(EmailMessage.batch_id == batch_id).values(batch_id=None)
            )
        await session.commit()
        ok = batch.status == TriageBatchStatus.DONE
        summary.tool_calls += tool_calls if ok else 0
        summary.llm_calls += llm_calls
        summary.input_tokens += tokens_in
        summary.output_tokens += tokens_out
        if not ok:
            summary.batches_failed += 1
            summary.errors.append(batch.error or "failed")
            return
        summary.classified += batch.email_count
        link = await link_batch(
            session, batch_id, auto_apply_confidence=settings.pipeline_auto_apply_confidence
        )
    summary.linked += link.linked
    summary.auto_applied += link.auto_applied
    summary.suggested += link.suggested
    async with sessionmaker() as session:
        summary.flagged += int(
            await session.scalar(
                select(func.count())
                .select_from(EmailMessage)
                .where(EmailMessage.batch_id == batch_id, EmailMessage.injection_flag.is_(True))
            )
            or 0
        )


async def _release(
    sessionmaker: async_sessionmaker[AsyncSession], batch_ids: list[uuid.UUID]
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            update(TriageBatch)
            .where(TriageBatch.id.in_(batch_ids), TriageBatch.status == TriageBatchStatus.PENDING)
            .values(
                status=TriageBatchStatus.FAILED,
                error="budget exhausted",
                finished_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(EmailMessage)
            .where(EmailMessage.batch_id.in_(batch_ids), EmailMessage.category.is_(None))
            .values(batch_id=None)
        )
        await session.commit()


def _result(summary: TriageSummary) -> dict[str, Any]:
    data = asdict(summary)
    data["tool_calls_per_batch"] = summary.tool_calls_per_batch
    data["errors"] = summary.errors[:10]
    return data
