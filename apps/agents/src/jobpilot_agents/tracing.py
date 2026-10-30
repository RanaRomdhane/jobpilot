"""Local tracing: agent spans go to our logs, never to a third-party tracing backend."""

from __future__ import annotations

import logging
from typing import Any

from agents import set_trace_processors
from agents.tracing import Span, Trace, TracingProcessor

log = logging.getLogger("jobpilot.agents.trace")


class LogTracingProcessor(TracingProcessor):
    def on_trace_start(self, trace: Trace) -> None:
        log.debug("trace start", extra={"data": {"trace": trace.name}})

    def on_trace_end(self, trace: Trace) -> None:
        log.debug("trace end", extra={"data": {"trace": trace.name}})

    def on_span_start(self, span: Span[Any]) -> None:
        return None

    def on_span_end(self, span: Span[Any]) -> None:
        data = span.span_data.export() if span.span_data else {}
        # keep span metadata only: tool inputs/outputs may contain email content
        summary = {k: v for k, v in data.items() if k in ("type", "name", "server", "model")}
        if span.error:
            summary["error"] = str(span.error.get("message", ""))[:200]
        log.info("agent span", extra={"data": summary})

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None


def configure_local_tracing() -> None:
    set_trace_processors([LogTracingProcessor()])
