"""Logging with secret and personal-data redaction applied to every record."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"), "[REDACTED_GROQ_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bya29\.[A-Za-z0-9_\-]+"), "[REDACTED_GOOGLE_TOKEN]"),
    (re.compile(r"\b1//[A-Za-z0-9_\-]{20,}"), "[REDACTED_GOOGLE_REFRESH]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+"), r"\1 [REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
]

# Fields that may carry message content; dropped entirely when body redaction is on.
BODY_FIELDS = frozenset({"body", "snippet", "html", "text", "content", "description", "raw_text"})


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self, redact_bodies: bool = True) -> None:
        super().__init__()
        self.redact_bodies = redact_bodies

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            record.data = self._clean(extra)
        return True

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in data.items():
            if self.redact_bodies and k in BODY_FIELDS:
                out[k] = f"[REDACTED {len(str(v))} chars]"
            elif isinstance(v, str):
                out[k] = redact(v)
            elif isinstance(v, dict):
                out[k] = self._clean(v)
            else:
                out[k] = v
        return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            payload["data"] = data
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", redact_bodies: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(redact_bodies))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
