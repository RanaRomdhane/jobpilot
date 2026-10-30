"""Classification eval with the real model: accuracy per category, tool calls and tokens per batch.

`make eval`. Runs the real TriageAgent + the real MCP server subprocess against the test database;
Gmail is not involved (labelling is skipped because no Gmail credential exists in the test DB).
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from jobpilot_agents.tracing import configure_local_tracing
from jobpilot_agents.triage import build_triage_agent, run_triage_batch, triage_mcp_server
from jobpilot_core.config import LLMPurpose, Settings, get_settings
from jobpilot_core.enums import EmailCategory
from jobpilot_core.llm import MemoryUsageSink, build_provider
from jobpilot_core.models import EmailMessage, TriageBatch
from jobpilot_mcp_gmail.tools import batch_handle, create_batches
from jobpilot_security import InjectionDetector, clean_plain

pytestmark = pytest.mark.eval

DATA = json.loads((Path(__file__).parent / "data" / "triage_eval.json").read_text("utf-8"))
REPORT = Path(__file__).resolve().parents[2] / "storage" / "eval" / "triage_eval.json"
MIN_ACCURACY = 0.85


async def test_triage_accuracy(
    integration_settings: Settings, engine: AsyncEngine, real_redis: Redis
) -> None:
    real = get_settings()
    if not (real.groq_api_key and real.groq_api_key.get_secret_value()):
        pytest.skip("GROQ_API_KEY not set")
    configure_local_tracing()
    # the MCP subprocess reads its DB from the environment: point it at the test database
    os.environ["DATABASE_URL"] = integration_settings.database_url
    settings = real.model_copy(
        update={
            "database_url": integration_settings.database_url,
            "redis_url": integration_settings.redis_url,
        }
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE email_message, triage_batch RESTART IDENTITY CASCADE"))

    detector = InjectionDetector()
    now = datetime.now(UTC)
    expected: dict[int, str] = {}
    async with sm() as s:
        for i, sample in enumerate(DATA):
            name, addr = parseaddr(sample["from"])
            report = detector.scan_heuristics(
                clean_plain(f"{sample['subject']}\n{sample['snippet']}")
            )
            row = EmailMessage(
                gmail_id=f"eval-{sample['id']}",
                thread_id=f"t-{sample['id']}",
                from_address=addr,
                from_name=name,
                subject=sample["subject"],
                snippet=sample["snippet"],
                received_at=now - timedelta(minutes=i),
                injection_flag=report.flagged,
                injection_score=report.score,
                injection_reasons=[r.model_dump(mode="json") for r in report.reasons],
            )
            s.add(row)
            await s.flush()
            expected[row.id] = sample["expected"]
        await s.commit()
        batch_ids = await create_batches(s, settings.triage_batch_size)

    sink = MemoryUsageSink()
    provider = build_provider(settings, real_redis, usage_sink=sink)
    per_batch: list[dict[str, Any]] = []
    started = time.perf_counter()
    async with triage_mcp_server(timeout_s=60) as server:
        agent = build_triage_agent(
            provider.client(LLMPurpose.TRIAGE), provider.model(LLMPurpose.TRIAGE), server
        )
        for bid in batch_ids:
            t0, n0 = time.perf_counter(), len(sink.records)
            stats = await run_triage_batch(agent, batch_handle(bid), 15)
            calls = sink.records[n0:]
            per_batch.append(
                {
                    "tool_calls": stats.tool_calls,
                    "attempts": stats.attempts,
                    "llm_calls": len(calls),
                    "input_tokens": sum(r.input_tokens for r in calls),
                    "output_tokens": sum(r.output_tokens for r in calls),
                    "estimated_tokens": sum(r.estimated_tokens for r in calls),
                    "waited_s": round(sum(r.waited_ms for r in calls) / 1000, 1),
                    "seconds": round(time.perf_counter() - t0, 1),
                }
            )
    elapsed = time.perf_counter() - started

    async with sm() as s:
        rows = {e.id: e for e in (await s.scalars(select(EmailMessage))).all()}
        statuses = Counter(b.status.value for b in (await s.scalars(select(TriageBatch))).all())

    per_cat: dict[str, Counter[str]] = defaultdict(Counter)
    confusion: list[dict[str, str]] = []
    for eid, want in expected.items():
        category = rows[eid].category
        got = category.value if category is not None else "unclassified"
        per_cat[want]["total"] += 1
        per_cat[want]["correct"] += got == want
        if got != want:
            confusion.append(
                {
                    "id": rows[eid].gmail_id,
                    "expected": want,
                    "got": got,
                    "reason": rows[eid].classification_reason or "",
                }
            )
    correct = sum(c["correct"] for c in per_cat.values())
    accuracy = correct / len(expected)
    injection_ids = {"eval-n09", "eval-n11", "eval-a08"}
    flagged = {e.gmail_id for e in rows.values() if e.injection_flag}

    summary = {
        "accuracy": round(accuracy, 3),
        "per_category": {
            k: round(v["correct"] / v["total"], 3) for k, v in sorted(per_cat.items())
        },
        "batches": dict(statuses),
        "per_batch": per_batch,
        "avg_tool_calls_per_batch": round(
            sum(b["tool_calls"] for b in per_batch) / max(1, len(per_batch)), 2
        ),
        "avg_tokens_per_batch": round(
            sum(b["input_tokens"] + b["output_tokens"] for b in per_batch) / max(1, len(per_batch))
        ),
        "elapsed_s": round(elapsed, 1),
        "injections_flagged": sorted(injection_ids & flagged),
        "false_flags": sorted(flagged - injection_ids),
        "errors": confusion,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))

    assert len(DATA) >= 60
    assert statuses.get("done", 0) == len(batch_ids)
    assert accuracy >= MIN_ACCURACY
    assert injection_ids <= flagged, "baseline injection emails must be flagged"
    brief_baseline = rows_by_gmail(rows)["eval-n09"]
    assert brief_baseline.category == EmailCategory.NEWSLETTER


def rows_by_gmail(rows: dict[int, EmailMessage]) -> dict[str, EmailMessage]:
    return {e.gmail_id: e for e in rows.values()}
