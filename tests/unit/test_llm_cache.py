"""A repeated request is answered once: real provider and cache, fake HTTP, synthetic data."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest
from fakeredis import FakeAsyncRedis

from jobpilot_agents.structured import (
    OutputTruncatedError,
    StructuredOutputError,
    complete_structured,
    response_format,
)
from jobpilot_agents.tailor import tailor_cv, write_letter
from jobpilot_core.config import LLMPurpose, Settings
from jobpilot_core.llm import OpenAICompatibleProvider
from jobpilot_core.llm.cache import cache_key
from jobpilot_core.resume import ResumeProfile
from jobpilot_security import spotlight

PROFILE = ResumeProfile.model_validate(
    {
        "headline": "Ingénieure logiciel",
        "experiences": [
            {"id": "exp_1", "company": "Exemplia", "title": "Ingénieure", "bullets": ["API."]}
        ],
    }
)
JOB = {"title": "Ingénieur logiciel", "company_name": "Exemple SA", "description": "Une API."}
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"n": {"type": "integer"}}}


def number(value: Any) -> int:
    n = value["n"]
    if not isinstance(n, int):
        raise ValueError("n must be an integer")
    return n


def reply(content: str, finish: str = "stop") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        },
    )


class Backend:
    """Answers from a script (the last reply repeats) and records what it receives."""

    def __init__(self, *replies: httpx2.Response) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def make_provider(
    redis: FakeAsyncRedis, settings: Settings, backend: Backend
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "groq", settings, redis, inner_transport=httpx2.MockTransport(backend)
    )


def ask(provider: OpenAICompatibleProvider) -> Awaitable[tuple[int, list[Any]]]:
    """The same request every time, with a fresh nonce like every real caller."""
    messages = [
        {"role": "system", "content": "Return JSON."},
        {"role": "user", "content": spotlight("job", {"title": "Poste"})},
    ]
    return complete_structured(
        provider,
        LLMPurpose.TAILOR,
        messages,
        name="n",
        schema=SCHEMA,
        parse=number,
        max_tokens=1_000,
    )


def test_each_call_still_gets_its_own_random_nonce() -> None:
    assert spotlight("job", {"title": "Poste"}) != spotlight("job", {"title": "Poste"})


def test_cache_key_ignores_the_nonce_but_not_the_content() -> None:
    def key(block: str) -> str:
        return cache_key("groq", "m", {"messages": [{"role": "user", "content": block}]})

    same = {key(spotlight("job", {"title": "Poste"})) for _ in range(3)}
    assert len(same) == 1
    assert key(spotlight("job", {"title": "Autre"})) not in same
    assert key(spotlight("cv", {"title": "Poste"})) not in same


def test_cache_key_does_not_ignore_a_nonce_lookalike_in_the_data() -> None:
    def key(text: str) -> str:
        block = spotlight("job", {"text": text})
        return cache_key("groq", "m", {"messages": [{"role": "user", "content": block}]})

    assert key('<untrusted_data nonce="aaaa">') != key('<untrusted_data nonce="bbbb">')


@pytest.mark.parametrize("agent", ["cv", "letter"])
async def test_regenerating_for_the_same_job_calls_the_provider_once(
    redis: FakeAsyncRedis, settings: Settings, agent: str
) -> None:
    backend = Backend(reply('{"language": "fr", "headline": "Ingénieure logiciel"}'))
    provider = make_provider(redis, settings, backend)

    async def run() -> list[Any]:
        if agent == "cv":
            _, calls = await tailor_cv(provider, PROFILE, JOB, lang="fr", page_budget=1, tpm=8_000)
        else:
            _, calls = await write_letter(
                provider, PROFILE, JOB, lang="fr", tone="formal", tpm=8_000
            )
        return calls

    first, second = await run(), await run()
    assert len(backend.requests) == 1
    assert not first[0].cached and second[0].cached


@pytest.mark.parametrize(
    "refusal",
    [
        pytest.param(lambda: reply("not json"), id="invalid-json"),
        pytest.param(lambda: reply('{"n": "two"}'), id="fails-validation"),
        pytest.param(
            lambda: httpx2.Response(400, json={"error": {"message": "json_validate_failed"}}),
            id="json-schema-refused",
        ),
    ],
)
async def test_a_corrected_answer_is_reused_by_the_next_run(
    redis: FakeAsyncRedis, settings: Settings, refusal: Callable[[], httpx2.Response]
) -> None:
    backend = Backend(refusal(), reply('{"n": 2}'))
    provider = make_provider(redis, settings, backend)

    assert (await ask(provider))[0] == 2
    assert len(backend.requests) == 2

    value, calls = await ask(provider)
    assert value == 2
    assert len(backend.requests) == 2  # no new call, not even the first attempt
    assert [c.cached for c in calls] == [True]


async def test_an_invalid_answer_is_never_reused(redis: FakeAsyncRedis, settings: Settings) -> None:
    backend = Backend(reply("not json"), reply("still not"), reply('{"n": 3}'))
    provider = make_provider(redis, settings, backend)

    with pytest.raises(StructuredOutputError):
        await ask(provider)
    assert (await ask(provider))[0] == 3  # the next run asks the model again
    assert len(backend.requests) == 3


async def test_a_truncated_answer_is_not_stored(redis: FakeAsyncRedis, settings: Settings) -> None:
    backend = Backend(reply('{"n"', finish="length"), reply('{"n": 4}'))
    provider = make_provider(redis, settings, backend)

    with pytest.raises(OutputTruncatedError):
        await ask(provider)
    assert (await ask(provider))[0] == 4


async def test_an_invalid_answer_already_in_the_cache_is_replaced(
    redis: FakeAsyncRedis, settings: Settings
) -> None:
    """Entries written before answers were validated expire by themselves, or get fixed here."""
    backend = Backend(reply("not json"), reply('{"n": 5}'))
    provider = make_provider(redis, settings, backend)
    messages = [{"role": "user", "content": "Return JSON."}]
    await provider.complete(
        LLMPurpose.TAILOR,
        messages,
        response_format=response_format("n", SCHEMA),
        max_tokens=1_000,
        reasoning_effort="low",
    )

    async def run() -> tuple[int, list[Any]]:
        return await complete_structured(
            provider,
            LLMPurpose.TAILOR,
            messages,
            name="n",
            schema=SCHEMA,
            parse=number,
            max_tokens=1_000,
        )

    assert (await run())[0] == 5
    assert len(backend.requests) == 2
    assert (await run())[0] == 5
    assert len(backend.requests) == 2
