"""Re-score stored injection evidence with the current heuristic weights.

Run after recalibrating `jobpilot_security.heuristics`:
`uv run python scripts/rescore_injection.py`.
With `--rescan-flagged`, flagged emails are re-read from Gmail (read-only) and scanned again with
every layer, for rules whose *matching* changed (not only their weight). Prints which emails
changed. Gmail labels are never removed by JobPilot (least privilege): an
email that is no longer flagged keeps its `JobPilot/⚠ Suspicious` label until you remove it.
"""

from __future__ import annotations

import argparse
import asyncio

from redis.asyncio import Redis
from sqlalchemy import select

from jobpilot_core.config import get_settings
from jobpilot_core.crypto import SecretBox
from jobpilot_core.db import create_engine, create_sessionmaker
from jobpilot_core.enums import OAuthScopeSet
from jobpilot_core.llm import build_provider
from jobpilot_core.models import EmailMessage
from jobpilot_mcp_gmail.credentials import CredentialStore
from jobpilot_mcp_gmail.gmail_client import GmailClient
from jobpilot_security import InjectionDetector, InjectionReport
from jobpilot_security.detector import reweigh
from jobpilot_security.factory import build_detector


async def main(rescan_flagged: bool) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    redis = Redis.from_url(settings.redis_url)
    gmail: GmailClient | None = None
    detector: InjectionDetector | None = None
    if rescan_flagged:
        token, _ = await CredentialStore(sessionmaker, SecretBox.from_settings(settings)).load(
            OAuthScopeSet.TRIAGE
        )
        gmail = GmailClient(token)
        detector = build_detector(settings, build_provider(settings, redis))
    changed = 0
    async with sessionmaker() as session:
        for email in (await session.scalars(select(EmailMessage))).all():
            if gmail is not None and detector is not None and email.injection_flag:
                msg = await gmail.get_message(email.gmail_id)
                scanned = await detector.scan(msg.content, extra_text=msg.subject)
                # keep the classifier's own report, which a rescan cannot reproduce
                model = [r for r in email.injection_reasons if r.get("layer") == "model"]
                email.injection_reasons = [
                    *(r.model_dump(mode="json") for r in scanned.reasons),
                    *model,
                ]
            old = InjectionReport.model_validate(
                {
                    "score": email.injection_score or 0.0,
                    "flagged": email.injection_flag,
                    "reasons": email.injection_reasons,
                }
            )
            new = reweigh(old, settings.injection_threshold)
            if new.flagged != old.flagged or abs(new.score - old.score) > 1e-6:
                changed += 1
                if new.flagged != old.flagged:
                    print(
                        f"{email.id}: flagged {old.flagged} -> {new.flagged} "
                        f"({old.score:.2f} -> {new.score:.2f})"
                    )
                email.injection_score = new.score
                email.injection_flag = new.flagged
                email.injection_reasons = [r.model_dump(mode="json") for r in new.reasons]
        await session.commit()
    if gmail is not None:
        await gmail.aclose()
    await redis.aclose()
    await engine.dispose()
    print(f"{changed} emails re-scored")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rescan-flagged", action="store_true")
    asyncio.run(main(parser.parse_args().rescan_flagged))
