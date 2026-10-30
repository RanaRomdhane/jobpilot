from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from agents import Runner
from agents.exceptions import MaxTurnsExceeded

from jobpilot_agents.triage import output_budget, run_triage_batch, tool_result_json
from jobpilot_core.ratelimit import RateLimitExceededError


@dataclass
class FakeRun:
    final_output: Any
    new_items: list[Any] = field(default_factory=list)
    raw_responses: list[Any] = field(default_factory=list)


def scripted(*outcomes: Any) -> Any:
    calls = list(outcomes)

    async def run(*_: Any, **__: Any) -> FakeRun:
        outcome = calls.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeRun(final_output=outcome)

    return run


DONE = json.dumps({"ok": True, "final": True, "errors": []})


async def test_finishes_on_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Runner, "run", scripted(DONE))
    stats = await run_triage_batch(object(), "abcd1234", 15)  # type: ignore[arg-type]
    assert stats.finished and stats.attempts == 1 and stats.error is None


async def test_retries_once_after_a_failed_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Runner, "run", scripted(MaxTurnsExceeded("x"), DONE))
    stats = await run_triage_batch(object(), "abcd1234", 15)  # type: ignore[arg-type]
    assert stats.finished and stats.attempts == 2


async def test_non_ok_final_result_is_retried_then_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rejected = json.dumps({"ok": False, "final": True, "errors": ["batch rejected"]})
    monkeypatch.setattr(Runner, "run", scripted(rejected, rejected))
    stats = await run_triage_batch(object(), "abcd1234", 15)  # type: ignore[arg-type]
    assert not stats.finished and stats.attempts == 2 and stats.error == "batch rejected"


async def test_budget_exhaustion_stops_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Runner, "run", scripted(RateLimitExceededError("daily budget")))
    stats = await run_triage_batch(object(), "abcd1234", 15)  # type: ignore[arg-type]
    assert stats.budget_exhausted and stats.attempts == 1


def test_tool_result_json_unwraps_mcp_content() -> None:
    assert tool_result_json({"type": "text", "text": '{"final": true}'}) == {"final": True}
    assert tool_result_json([{"type": "text", "text": '{"a": 1}'}]) == {"a": 1}
    assert tool_result_json("not json") is None


def test_output_budget_scales_with_batch() -> None:
    assert output_budget(15) == 1950 and output_budget(1) < output_budget(15)
