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

log = logging.getLogger(__name__)


class StructuredOutputError(RuntimeError):
    pass


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
    response_format: dict[str, Any] = {
        "type": "json_schema",
        "json_schema": {"name": name, "schema": schema, "strict": False},
    }
    msgs = list(messages)
    results: list[ChatResult] = []
    error = "no attempt"
    attempt = 0
    while attempt <= retries:
        try:
            res = await provider.complete(
                purpose,
                msgs,
                response_format=response_format,
                max_tokens=max_tokens,
                reasoning_effort="low",
                use_cache=attempt == 0,
            )
        except APIStatusError as exc:
            if exc.status_code == 400 and response_format["type"] == "json_schema":
                # Groq answers 400 when generated JSON fails its schema check, or when the model
                # has no structured-output support: fall back to JSON mode + schema in the prompt.
                log.warning("json_schema mode refused (%s); retrying in JSON mode", exc.message)
                response_format = {"type": "json_object"}
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
            raise StructuredOutputError(f"{name}: output truncated at {max_tokens} tokens")
        try:
            return parse(json.loads(res.content)), results
        except (ValueError, ValidationError) as exc:
            error = str(exc)[:600]
            msgs = [
                *msgs,
                {"role": "assistant", "content": res.content[:3000]},
                {
                    "role": "user",
                    "content": f"That JSON is invalid: {error}\nReturn the corrected JSON only.",
                },
            ]
        attempt += 1
    raise StructuredOutputError(f"{name}: invalid output after {retries + 1} attempts: {error}")
