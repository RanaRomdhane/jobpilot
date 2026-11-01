"""Structured JSON completions with schema validation and one corrective retry."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from openai import APIStatusError
from pydantic import ValidationError

from jobpilot_core.config import LLMPurpose
from jobpilot_core.llm import ChatResult, LLMProvider
from jobpilot_core.llm.tokens import estimate_prompt_tokens

log = logging.getLogger(__name__)
MIN_RETRY_OUTPUT = 512


class StructuredOutputError(RuntimeError):
    pass


class OutputTruncatedError(StructuredOutputError):
    """The model hit `max_tokens`: retrying the same request cannot help, splitting it can."""


def response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": False}}


async def complete_structured[T](
    provider: LLMProvider,
    purpose: LLMPurpose,
    messages: list[dict[str, Any]],
    *,
    name: str,
    schema: dict[str, Any],
    parse: Callable[[Any], T],
    max_tokens: int,
    retries: int = 1,
) -> tuple[T, list[ChatResult]]:
    """Ask for JSON matching `schema`, validate it with `parse`, retry once with the error.

    Uses the provider's structured-output mode (`json_schema`); a model without it gets JSON mode
    and the schema in the prompt instead.
    """
    fmt = response_format(name, schema)
    msgs = list(messages)
    results: list[ChatResult] = []
    error = "no attempt"
    attempt = 0
    # A retry carries more prompt (previous answer + error, or the schema in JSON mode): take it
    # out of the output budget so the request never grows past what the caller planned for.
    budget = estimate_prompt_tokens({"messages": msgs, "response_format": fmt}) + max_tokens
    while attempt <= retries:
        prompt = estimate_prompt_tokens({"messages": msgs, "response_format": fmt})
        out_tokens = max(MIN_RETRY_OUTPUT, min(max_tokens, budget - prompt))
        try:
            res = await provider.complete(
                purpose,
                msgs,
                response_format=fmt,
                max_tokens=out_tokens,
                reasoning_effort="low",
                use_cache=attempt == 0,
            )
        except APIStatusError as exc:
            if exc.status_code == 400 and fmt["type"] == "json_schema":
                # Groq answers 400 when generated JSON fails its schema check, or when the model
                # has no structured-output support: fall back to JSON mode + schema in the prompt.
                log.warning("json_schema mode refused (%s); retrying in JSON mode", exc.message)
                fmt = {"type": "json_object"}
                msgs = [
                    {
                        "role": "system",
                        "content": f"{msgs[0]['content']}\n\nReturn JSON matching this schema:\n"
                        + json.dumps(schema, separators=(",", ":")),
                    },
                    *msgs[1:],
                ]
                attempt += 1
                continue
            raise
        results.append(res)
        if res.finish_reason == "length":
            raise OutputTruncatedError(f"{name}: output truncated at {out_tokens} tokens")
        try:
            return parse(json.loads(res.content)), results
        except (ValueError, ValidationError) as exc:
            error = str(exc)[:600]
            log.warning("%s: invalid output, retrying: %s", name, error)
            # only the error, not the previous answer: the model regenerates the JSON anyway, and
            # resending it would eat the output budget of the retry
            msgs = [
                *msgs[: len(messages)],  # the original turns (JSON mode: system has the schema)
                {
                    "role": "user",
                    "content": f"Your previous JSON was invalid: {error}\n"
                    "Return the complete, corrected JSON only.",
                },
            ]
        attempt += 1
    raise StructuredOutputError(f"{name}: invalid output after {retries + 1} attempts: {error}")
