"""Cheap, conservative token estimation used to reserve rate-limit budget before a call.

Estimates are deliberately pessimistic (≈3 chars/token, which over-counts for English and is close
for French); the reservation is reconciled with the provider-reported usage after the call.
"""

from __future__ import annotations

import json
import math
from typing import Any

CHARS_PER_TOKEN = 3.0
PER_MESSAGE_OVERHEAD = 4
PER_REQUEST_OVERHEAD = 8


def estimate_text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _content_chars(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    # multi-part content / tool calls / anything structured
    return len(json.dumps(content, ensure_ascii=False))


def estimate_prompt_tokens(body: dict[str, Any]) -> int:
    """Input tokens for an OpenAI-compatible chat.completions (or responses) payload."""
    chars = 0
    messages = body.get("messages") or body.get("input") or []
    if isinstance(messages, str):
        chars += len(messages)
        n_messages = 1
    else:
        n_messages = len(messages)
        for m in messages:
            if isinstance(m, dict):
                chars += _content_chars(m.get("content"))
                if m.get("tool_calls"):
                    chars += _content_chars(m["tool_calls"])
            else:
                chars += _content_chars(m)
    for key in ("tools", "response_format", "text", "instructions"):
        if body.get(key):
            chars += _content_chars(body[key])
    return (
        math.ceil(chars / CHARS_PER_TOKEN)
        + n_messages * PER_MESSAGE_OVERHEAD
        + PER_REQUEST_OVERHEAD
    )


def requested_output_tokens(body: dict[str, Any], default: int) -> int:
    for key in ("max_completion_tokens", "max_tokens", "max_output_tokens"):
        value = body.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return default


def estimate_request_tokens(
    body: dict[str, Any], output_reserve: int, request_overhead: int = 0
) -> int:
    """Prompt + output reservation: providers count both against tokens-per-minute.

    `request_overhead` covers tokens the provider adds server-side to every request (e.g. the
    chat template / system preamble of gpt-oss, measured at ~70 tokens on Groq).
    """
    return (
        estimate_prompt_tokens(body)
        + request_overhead
        + requested_output_tokens(body, output_reserve)
    )
