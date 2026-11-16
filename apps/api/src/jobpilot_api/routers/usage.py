"""Utilisation: what the language models were asked, per purpose and per day, from `llm_call_log`.

Costs are estimates: tokens of the calls that reached a provider, at the prices set in `LLM_PRICES`.
A call answered from the cache costs nothing and is counted apart.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Date, case, cast, func, or_, select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.models import LLMCallLog
from jobpilot_core.send_rules import profile, start_of_day

router = APIRouter(prefix="/usage", tags=["usage"])

Prices = dict[str, tuple[float, float]]  # model -> USD per million input / output tokens


def cost_of(prices: Prices, model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = prices.get(model)
    if price is None:
        return None
    return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 6)


def _sum(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known), 6) if known else None


class UsageTotals(BaseModel):
    calls: int  # reached a provider
    cached_calls: int  # answered from the cache
    errors: int
    input_tokens: int
    output_tokens: int
    waited_s: float  # time spent queuing for the rate limit
    cost: float | None


class PurposeUsage(BaseModel):
    purpose: str
    provider: str
    model: str
    calls: int
    cached_calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    avg_latency_ms: int
    cost: float | None


class DayUsage(BaseModel):
    day: date
    calls: int
    cached_calls: int
    tokens: int
    cost: float | None


class TodayUsage(BaseModel):
    tokens: int
    daily_limit: int  # 0: the provider has none configured
    provider: str


class UsageOut(BaseModel):
    days: int
    totals: UsageTotals
    by_purpose: list[PurposeUsage]
    by_day: list[DayUsage]
    today: TodayUsage
    unpriced_models: list[str]  # used in the period, with no price configured


@router.get("", response_model=UsageOut)
async def usage(
    _: Authenticated,
    session: Session,
    state: State,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> UsageOut:
    settings = state.settings
    prices: Prices = settings.llm_prices
    user = await profile(session)
    today_start = start_of_day(user.timezone)
    since = today_start - timedelta(days=days - 1)
    live = LLMCallLog.cached.is_(False)
    failed = or_(LLMCallLog.status_code >= 400, LLMCallLog.error.is_not(None))
    n_live = func.count().filter(live)
    n_cached = func.count().filter(LLMCallLog.cached.is_(True))
    n_errors = func.count().filter(failed)
    tok_in = func.coalesce(func.sum(case((live, LLMCallLog.input_tokens), else_=0)), 0)
    tok_out = func.coalesce(func.sum(case((live, LLMCallLog.output_tokens), else_=0)), 0)

    rows = (
        await session.execute(
            select(
                LLMCallLog.purpose,
                LLMCallLog.provider,
                LLMCallLog.model,
                n_live,
                n_cached,
                n_errors,
                tok_in,
                tok_out,
                func.coalesce(func.avg(LLMCallLog.latency_ms).filter(live), 0),
                func.coalesce(func.sum(LLMCallLog.waited_ms), 0),
            )
            .where(LLMCallLog.created_at >= since)
            .group_by(LLMCallLog.purpose, LLMCallLog.provider, LLMCallLog.model)
            .order_by((tok_in + tok_out).desc())
        )
    ).all()
    by_purpose = [
        PurposeUsage(
            purpose=purpose,
            provider=provider,
            model=model,
            calls=calls,
            cached_calls=cached,
            errors=errors,
            input_tokens=int(t_in),
            output_tokens=int(t_out),
            avg_latency_ms=round(float(latency)),
            cost=cost_of(prices, model, int(t_in), int(t_out)),
        )
        for purpose, provider, model, calls, cached, errors, t_in, t_out, latency, _w in rows
    ]
    waited_ms = sum(int(r[9]) for r in rows)

    local_day = cast(func.timezone(user.timezone, LLMCallLog.created_at), Date)
    day_rows = (
        await session.execute(
            select(local_day, LLMCallLog.model, n_live, n_cached, tok_in, tok_out)
            .where(LLMCallLog.created_at >= since)
            .group_by(local_day, LLMCallLog.model)
        )
    ).all()
    per_day: dict[date, DayUsage] = {}
    first = since.date()
    for i in range(days):
        d = first + timedelta(days=i)
        per_day[d] = DayUsage(day=d, calls=0, cached_calls=0, tokens=0, cost=None)
    for d, model, calls, cached, t_in, t_out in day_rows:
        entry = per_day.setdefault(d, DayUsage(day=d, calls=0, cached_calls=0, tokens=0, cost=None))
        entry.calls += calls
        entry.cached_calls += cached
        entry.tokens += int(t_in) + int(t_out)
        entry.cost = _sum([entry.cost, cost_of(prices, model, int(t_in), int(t_out))])

    today_tokens = int(
        await session.scalar(
            select(
                func.coalesce(func.sum(LLMCallLog.input_tokens + LLMCallLog.output_tokens), 0)
            ).where(
                live,
                LLMCallLog.provider == settings.llm_provider,
                LLMCallLog.created_at >= datetime.now(UTC) - timedelta(days=1),
            )
        )
        or 0
    )
    _tpm, _rpm, tpd = settings.limits_for(settings.llm_provider)
    return UsageOut(
        days=days,
        totals=UsageTotals(
            calls=sum(p.calls for p in by_purpose),
            cached_calls=sum(p.cached_calls for p in by_purpose),
            errors=sum(p.errors for p in by_purpose),
            input_tokens=sum(p.input_tokens for p in by_purpose),
            output_tokens=sum(p.output_tokens for p in by_purpose),
            waited_s=round(waited_ms / 1000, 1),
            cost=_sum([p.cost for p in by_purpose]),
        ),
        by_purpose=by_purpose,
        by_day=[per_day[d] for d in sorted(per_day)],
        today=TodayUsage(tokens=today_tokens, daily_limit=tpd, provider=settings.llm_provider),
        unpriced_models=sorted({p.model for p in by_purpose if p.calls and p.model not in prices}),
    )
