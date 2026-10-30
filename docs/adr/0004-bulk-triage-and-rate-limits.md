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

## Consequences

- Throughput ≈ 2 batches/min (~30 emails/min) on the free tier; a 1,000-email backlog takes ~35 min,
  run in the background with SSE progress.
- A load test (Phase 8) verifies the limiter never lets the minute budget be exceeded.
