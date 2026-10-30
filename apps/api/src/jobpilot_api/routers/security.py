"""Sécurité: flagged items with their evidence, and the injection test-suite results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.enums import EmailCategory
from jobpilot_core.models import EmailMessage
from jobpilot_security.suite import load_samples, run_heuristic_suite

router = APIRouter(prefix="/security", tags=["security"])


class FlaggedItem(BaseModel):
    kind: str  # "email" (job postings and CVs join in later phases)
    id: int
    title: str | None
    source: str | None
    category: EmailCategory | None
    score: float | None
    received_at: datetime | None
    reasons: list[dict[str, Any]]


class SuiteRun(BaseModel):
    precision: float
    recall: float
    tp: int
    fp: int
    fn: int
    tn: int
    injections: int
    benign: int
    by_kind: dict[str, dict[str, int]]
    misses: list[str]
    false_positives: list[str]


class SecurityReport(BaseModel):
    flagged: list[FlaggedItem]
    suite: SuiteRun
    eval_reports: dict[str, Any]


@router.get("", response_model=SecurityReport)
async def security_report(_: Authenticated, session: Session, state: State) -> SecurityReport:
    emails = (
        await session.scalars(
            select(EmailMessage)
            .where(EmailMessage.injection_flag)
            .order_by(EmailMessage.injection_score.desc().nulls_last())
            .limit(200)
        )
    ).all()
    r = run_heuristic_suite()
    samples = load_samples()
    return SecurityReport(
        flagged=[
            FlaggedItem(
                kind="email",
                id=e.id,
                title=e.subject,
                source=e.from_address,
                category=e.category,
                score=e.injection_score,
                received_at=e.received_at,
                reasons=[x for x in e.injection_reasons if float(x.get("weight", 0)) > 0],
            )
            for e in emails
        ],
        suite=SuiteRun(
            precision=round(r.precision, 3),
            recall=round(r.recall, 3),
            tp=r.tp,
            fp=r.fp,
            fn=r.fn,
            tn=r.tn,
            injections=len(samples["injections"]),
            benign=len(samples["benign"]),
            by_kind=r.by_kind,
            misses=r.misses,
            false_positives=r.false_positives,
        ),
        eval_reports=_eval_reports(state.settings.storage_dir / "eval"),
    )


def _eval_reports(directory: Path) -> dict[str, Any]:
    """Latest `make eval` outputs, without per-sample rows (they may contain email excerpts)."""
    out: dict[str, Any] = {}
    for name in ("triage_eval", "injection_eval"):
        path = directory / f"{name}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data.pop("rows", None)
        data.pop("errors", None)
        data["generated_at"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        out[name] = data
    return out
