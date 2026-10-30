"""Injection detection with every layer (heuristics + real guard model): precision / recall.

`make eval`. Writes storage/eval/injection_eval.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from redis.asyncio import Redis

from jobpilot_core.config import Settings, get_settings
from jobpilot_core.llm import MemoryUsageSink, build_provider
from jobpilot_security.factory import build_detector
from tests.security.test_injection_suite import BENIGN, INJECTIONS, to_clean

pytestmark = pytest.mark.eval

REPORT = Path(__file__).resolve().parents[2] / "storage" / "eval" / "injection_eval.json"


async def test_layered_detection(integration_settings: Settings, real_redis: Redis) -> None:
    settings = get_settings()
    if not (settings.groq_api_key and settings.groq_api_key.get_secret_value()):
        pytest.skip("GROQ_API_KEY not set")
    settings = settings.model_copy(update={"redis_url": integration_settings.redis_url})
    sink = MemoryUsageSink()
    detector = build_detector(settings, build_provider(settings, real_redis, usage_sink=sink))

    rows = []
    for sample, is_injection in [*((s, True) for s in INJECTIONS), *((s, False) for s in BENIGN)]:
        report = await detector.scan(to_clean(sample))
        guard = next((r for r in report.reasons if r.layer == "guard"), None)
        heur = sum(r.weight for r in report.reasons if r.layer == "heuristic") > 0
        rows.append(
            {
                "id": sample["id"],
                "injection": is_injection,
                "flagged": report.flagged,
                "score": report.score,
                "heuristic_hit": heur,
                "guard": guard.excerpt if guard else None,
            }
        )

    def metrics(flag_key: str) -> dict[str, float]:
        tp = sum(r["injection"] and r[flag_key] for r in rows)
        fp = sum((not r["injection"]) and r[flag_key] for r in rows)
        fn = sum(r["injection"] and not r[flag_key] for r in rows)
        return {
            "precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
            "recall": round(tp / (tp + fn), 3) if tp + fn else 1.0,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    guard_alone = [
        dict(
            r,
            guard_flag=bool(r["guard"])
            and float(str(r["guard"]).split()[-1]) >= settings.guard_threshold,
        )
        for r in rows
    ]
    combined = metrics("flagged")
    summary = {
        "combined": combined,
        "guard_only": {
            "precision": _ratio(guard_alone, True),
            "recall": _recall(guard_alone),
        },
        "guard_calls": sum(r.purpose == "guard" for r in sink.records),
        "misses": [r["id"] for r in rows if r["injection"] and not r["flagged"]],
        "false_positives": [r["id"] for r in rows if not r["injection"] and r["flagged"]],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({**summary, "rows": rows}, indent=2, ensure_ascii=False), "utf-8")
    print("\n" + json.dumps(summary, indent=2))
    assert combined["precision"] >= 0.95
    assert combined["recall"] >= 0.95


def _ratio(rows: list[dict[str, object]], _: bool) -> float:
    tp = sum(bool(r["injection"]) and bool(r["guard_flag"]) for r in rows)
    fp = sum((not r["injection"]) and bool(r["guard_flag"]) for r in rows)
    return round(tp / (tp + fp), 3) if tp + fp else 1.0


def _recall(rows: list[dict[str, object]]) -> float:
    tp = sum(bool(r["injection"]) and bool(r["guard_flag"]) for r in rows)
    fn = sum(bool(r["injection"]) and not r["guard_flag"] for r in rows)
    return round(tp / (tp + fn), 3) if tp + fn else 1.0
