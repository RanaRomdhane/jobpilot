"""Gmail → `email_message` sync: first a backfill, then incremental via `historyId`.

Only metadata and the Gmail snippet are stored; the full body is read once, locally, to scan it
for prompt injection (visible and hidden text) and is then discarded. No LLM is involved here
except the guard classifier, which has its own rate-limit budget.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from jobpilot_core.config import Settings
from jobpilot_core.models import EmailMessage, GmailSyncState
from jobpilot_mcp_gmail.gmail_client import (
    GmailClient,
    HistoryExpiredError,
    MessageNotFoundError,
    ParsedMessage,
)
from jobpilot_security import InjectionDetector

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

# mirror the default query for incremental history, which cannot be filtered by search query
SKIPPED_LABELS = frozenset(
    {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "SPAM", "TRASH", "DRAFT", "SENT", "CHAT"}
)
FETCH_CONCURRENCY = 6

Progress = Callable[[float, str], Awaitable[None]]


@dataclass(slots=True)
class SyncResult:
    mode: str  # "backfill" | "incremental"
    seen: int
    added: int
    flagged: int
    skipped: int


async def _noop(_: float, __: str) -> None:
    return None


class GmailSync:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        gmail: GmailClient,
        detector: InjectionDetector,
        account_email: str,
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._gmail = gmail
        self._detector = detector
        self._account = account_email

    async def run(self, progress: Progress = _noop) -> SyncResult:
        async with self._sessionmaker() as session:
            state = (
                await session.scalars(
                    select(GmailSyncState).where(GmailSyncState.account_email == self._account)
                )
            ).first()
        mode = "incremental"
        if state is None or state.last_history_id is None:
            mode = "backfill"
        else:
            try:
                ids, latest = await self._gmail.list_history(state.last_history_id)
            except HistoryExpiredError:
                log.info("gmail history expired; running a backfill")
                mode = "backfill"
        if mode == "backfill":
            # take the history id *before* listing so nothing between the two is missed
            latest = int((await self._gmail.get_profile())["historyId"])
            query = f"{self._settings.gmail_query} newer_than:{self._settings.gmail_backfill_days}d"
            ids = await self._gmail.list_message_ids(query, limit=self._settings.gmail_max_backfill)

        new_ids = await self._unknown(ids)
        await progress(0.0, f"{mode}: {len(new_ids)} new of {len(ids)} emails")
        added = flagged = skipped = 0
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        done = 0

        async def ingest(gmail_id: str) -> None:
            nonlocal added, flagged, skipped, done
            async with sem:
                try:
                    msg = await self._gmail.get_message(gmail_id)
                except MessageNotFoundError:
                    # history still lists messages deleted since; one gone email must not
                    # fail the whole sync (and block every later one behind the same history id)
                    log.info("gmail message %s no longer exists; skipped", gmail_id)
                    msg = None
                if msg is None or set(msg.label_ids) & SKIPPED_LABELS:
                    skipped += 1
                else:
                    row = await self._to_row(msg)
                    async with self._sessionmaker() as session:
                        session.add(row)
                        await session.commit()
                    added += 1
                    flagged += row.injection_flag
                done += 1
                if done % 10 == 0 or done == len(new_ids):
                    await progress(done / max(1, len(new_ids)), f"synced {done}/{len(new_ids)}")

        await asyncio.gather(*(ingest(i) for i in new_ids))
        await self._save_state(latest, backfill=mode == "backfill")
        return SyncResult(mode, len(ids), added, flagged, skipped)

    async def _unknown(self, ids: list[str]) -> list[str]:
        if not ids:
            return []
        async with self._sessionmaker() as session:
            known = set(
                (
                    await session.scalars(
                        select(EmailMessage.gmail_id).where(EmailMessage.gmail_id.in_(ids))
                    )
                ).all()
            )
        return [i for i in ids if i not in known]

    async def _to_row(self, msg: ParsedMessage) -> EmailMessage:
        report = await self._detector.scan(msg.content, extra_text=msg.subject)
        return EmailMessage(
            gmail_id=msg.gmail_id,
            thread_id=msg.thread_id,
            history_id=msg.history_id,
            from_address=msg.from_address,
            from_name=msg.from_name,
            to_address=msg.to_address,
            subject=msg.subject,
            snippet=msg.snippet,
            received_at=msg.received_at,
            gmail_label_ids=msg.label_ids,
            injection_flag=report.flagged,
            injection_score=report.score,
            injection_reasons=[r.model_dump(mode="json") for r in report.reasons],
        )

    async def _save_state(self, history_id: int, *, backfill: bool) -> None:
        now = datetime.now(UTC)
        async with self._sessionmaker() as session:
            state = (
                await session.scalars(
                    select(GmailSyncState).where(GmailSyncState.account_email == self._account)
                )
            ).first()
            if state is None:
                state = GmailSyncState(account_email=self._account)
                session.add(state)
            state.last_history_id = history_id
            if backfill:
                state.last_full_sync_at = now
            else:
                state.last_incremental_sync_at = now
            await session.commit()
