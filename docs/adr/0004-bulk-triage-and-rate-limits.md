# ADR-0004 — Bulk triage under a global token budget

- Status: Accepted (Phase 0)
- Date: 2026-10-30

## Context

The Groq free tier allows 8,000 tokens/minute. One tool call per email would multiply prompt
overhead and round-trips; per-process throttling would break as soon as the API and worker both call
the LLM.

## Decision

- Skip-processed first: only emails with `category IS NULL` are ever sent to a model.
- Batches of 15 (configurable) compact payloads (from, subject, snippet ≤ 300 chars, date).
- Per batch the agent makes ~2 tool calls: `get_emails_compact(batch_id)` and
  `bulk_classify_and_label(batch_id, results)`. Tool-call counts are recorded per batch.
- A **Redis token bucket** (atomic Lua) per `provider:model` for TPM and RPM, shared by every process.
  Calls reserve estimated tokens and reconcile with real usage; waiting replaces fixed sleeps.
- 429 handling honours `retry-after` with jittered exponential backoff.

## Measured (Phase 2, `make eval`, 66 emails, gpt-oss-120b on Groq free tier)

| | per batch of 15 |
|---|---|
| tool calls | 2.0 (`get_emails_compact`, `bulk_classify_and_label`) |
| LLM calls | 2 (the run ends on the bulk tool's result: no closing turn) |
| tokens | ≈ 4.35k (≈ 3.4k in, ≈ 0.95k out, reasoning effort "low") |
| wall time | 4–35 s, paced by the limiter (no 429 from the provider) |

Throughput ≈ 30 emails/min. The daily budget (200k tokens/day on the free tier) caps a single day
at ≈ 45 batches ≈ 690 emails; a larger 90-day backfill simply continues on the next scheduled run.

Lessons that shaped the implementation:
- gpt-oss sometimes answers with a text table instead of calling the tool: `get_emails_compact`
  ends with a trusted trailer naming the next call, `tool_choice="required"` is kept, and a batch
  is retried once from scratch if the provider rejects a turn.
- Long UUIDs get mistyped: the agent sees an 8-hex-char batch handle; unknown handles are a
  non-final error so the agent can correct itself.
- Groq adds ≈ 70 hidden prompt tokens per request: the estimator adds a per-provider overhead.

## Consequences

- A 1,000-email backlog takes ≈ 35 min of runtime spread over two days of free-tier budget, in the
  background with SSE progress.
- A load test (Phase 8) verifies the limiter never lets the minute budget be exceeded.
