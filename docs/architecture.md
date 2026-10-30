# JobPilot — Architecture

Status: **proposed** (Phase 0). Updated at the end of every phase.

## 1. System overview

```
                         ┌──────────────────────────── docker compose ────────────────────────────┐
 Browser ── HTTPS ──►    │  web (Next.js 15)  ──REST/SSE──►  api (FastAPI)  ──enqueue──►  redis    │
 (single user)           │        ▲  typed client               │   ▲                        │  ▲   │
                         │        └── OpenAPI ──────────────────┘   │ SQL                    │  │   │
                         │                                          ▼                        ▼  │   │
                         │                                   postgres 16 + pgvector ◄─── worker (arq)
                         │                                                                │ │ │    │
                         │      ┌─────────── stdio (MCP) ─────────────────────────────────┘ │ │    │
                         │      ▼                                                           │ │    │
                         │  mcp_gmail  (read + label toolset ONLY)                          │ │    │
                         │      │                                     latex (sandbox, no net)◄─┘ │    │
                         └──────┼─────────────────────────────────────────────────────────────┼────┘
                                ▼                                                             ▼
                        Gmail API (gmail.modify token)              LLM provider (Groq / OpenAI)
                                                                    via shared Redis rate limiter
 sender service (in worker) ── separate gmail.send token ──► Gmail API   (only consumes approval tokens)
```

| Component | Responsibility | Talks to |
|---|---|---|
| `apps/web` | Dashboard, review & approve UI, i18n (FR default / EN) | `api` only |
| `apps/api` | REST `/api/v1`, auth, validation, SSE task progress, approval-token issuing | postgres, redis |
| `apps/worker` | All long work: Gmail sync, triage runs, scraping, matching, tailoring, compile, send | postgres, redis, mcp_gmail, latex, LLM |
| `apps/mcp_gmail` | MCP server exposing the **triage toolset** (read compact, add JobPilot labels) | Gmail API, postgres |
| `apps/agents` | Agent definitions, prompts, guardrails (OpenAI Agents SDK) | MCP servers, read-only DB tools |
| `packages/core` | Settings, DB models, `LLMProvider`, Redis token bucket, logging/redaction | — |
| `packages/security` | Injection detector, spotlighting, HTML stripping, LaTeX escaping | — |
| `packages/jobs_sources` | One `JobSource` connector per site/API | external job APIs |
| `packages/latex` | Jinja2 (LaTeX delimiters) rendering, compile client, log parser | latex container |

The Python side is one **uv workspace** (single lockfile, one package per folder above). The web app
is a **pnpm** project; its API types are generated from FastAPI's OpenAPI schema.

## 2. Trust boundaries

1. **Untrusted content** — email snippets, job descriptions, scraped HTML, CV text. Enters only via
   `packages/security` (strip → detect → wrap in spotlight blocks). Never reaches an agent as raw HTML.
2. **Agents** — can reason over untrusted content, so they get **no consequential tools**: the most
   powerful agent action in the system is *adding a `JobPilot/*` label*.
3. **Deterministic services** — compilation, sending, status changes from the UI. Not callable by agents.
4. **Human approval** — the only path to `gmail.send` is a one-time, hashed, short-TTL approval token
   created by a click in the review screen (see ADR-0003).

## 3. Inbox triage (core design)

Goal: classify the whole inbox backlog on the Groq free tier (8,000 TPM) in ~2 tool calls per batch.

```
worker: triage_run(task_id)
 1. incremental sync (history.list from last historyId) → upsert email_message rows (metadata only)
 2. unprocessed = SELECT ... WHERE category IS NULL           ← skip-processed, no LLM involved
 3. heuristic injection scan on each compact payload (pure Python)
 4. for each batch of N=15 (configurable):
      rate_limiter.acquire(estimated_tokens)                  ← waits if the minute budget is spent
      TriageAgent run:
        tool call 1: get_emails_compact(batch_id)       → ≤15 compact items, spotlighted
        tool call 2: bulk_classify_and_label(batch_id, results[15])
      bulk tool: validate (strict enum, ids ⊆ batch), persist, apply labels, flag suspicious
 5. pipeline linking: thread/domain match → application_event + suggested/auto status change
 6. progress published to Redis → SSE to the dashboard
```

**MCP tools (triage toolset, the only toolset agents ever see):**

| Tool | Behaviour |
|---|---|
| `list_unprocessed_emails()` | Count of emails still lacking a category |
| `get_emails_compact(batch_id)` | `{n, sender, subject≤150, date, snippet≤300, signals}` per email inside an `<untrusted_data>` block, followed by a trusted trailer naming the next call. No body, no HTML, no attachments, no Gmail/DB ids |
| `bulk_classify_and_label(batch_id, results)` | One call for the whole batch. Each result: `{n, category, confidence, suspicious, reason, interview_at?}`. Requires exactly one result per `n`; persists, merges injection evidence, adds `JobPilot/<Category>` (+ `JobPilot/⚠ Suspicious`) labels. Gmail failures leave `label_applied=false` for a later retry |

The server **has no** send, draft, trash, delete, forward, filter or label-removal code path at all —
not merely unregistered tools. Security tests assert the exposed tool list and scan the code.

**Batch handles:** the agent sees an 8-hex-char handle of the batch UUID (models mistype long
ids). Tools act only on pending batches the orchestrator created; an unknown handle returns a
non-final error so the agent can correct it.

**Run control:** `tool_choice="required"` and a custom `tool_use_behavior` that ends the run as
soon as the bulk tool reports the batch final — so a batch costs exactly 2 LLM calls. If the
provider rejects a turn, the batch is retried once from scratch.

**Injection handling:** at sync time every full body (visible + hidden text) is scanned by the
heuristics and the guard model (`llama-prompt-guard-2-86m`, own budget). At classification time
the model's `suspicious` report is merged in. Flagged emails get their true category label **plus**
`JobPilot/⚠ Suspicious` and are excluded from automatic pipeline changes. Details and measured
precision/recall: `docs/security.md`.

**Validation:** classification output is parsed into a Pydantic model with a `Literal` category enum;
on failure the batch is retried once with the validation error, then left unprocessed and reported.

## 4. LLM provider abstraction & rate limiting

- `LLMProvider` → one `OpenAICompatibleProvider` configured for Groq or OpenAI (both speak the
  chat-completions protocol), plus an optional `FallbackProvider`. The Agents SDK runs on
  `OpenAIChatCompletionsModel` with the provider's `AsyncOpenAI` client.
- **The limiter lives in the HTTP transport** (`RateLimitedTransport`, an `httpx2` transport — the
  client library of openai ≥ 3). Every request, whether from our code or from the Agents SDK, reserves
  its estimated tokens (prompt + `max_completion_tokens`, or a 1,024-token reserve), is reconciled
  with the real `usage` afterwards, and is logged to `llm_call_log`. The SDK's own retries are off.
- Model per **purpose** (`triage`, `guard`, `parse_resume`, `rerank`, `tailor`, `cover_letter`) from
  `.env`; provider switch requires no code change.
- **Redis token bucket** (one atomic Lua script using the Redis server clock) per provider, with
  TPM, RPM and TPD buckets; a request is admitted only if *every* bucket allows it. Real usage above
  the estimate becomes debt that later requests wait for.
- 429 → honour `retry-after` / `x-ratelimit-reset-*`, set a provider-wide block key so *all*
  processes pause, then retry with jittered exponential backoff; optional OpenAI fallback (off).
- Response cache keyed by `sha256(provider, model, prompt, inputs)`; every call logged to `llm_call_log`.

## 5. Data model

As listed in the brief (§5), with these proposed refinements:

- `email_message.history_id`, `label_ids` (array) and `batch_id`; unique on `gmail_id`.
- `gmail_sync_state` (single row per account): last `historyId`, last full sync time.
- `triage_batch` — id, email ids, status, tokens used, tool-call count (proves the ~2 calls/batch target).
- `status_suggestion` — proposed application status changes awaiting user confirmation.
- `saved_search` — filters JSON, schedule, last run, new-match count (needed by §6.4).
- `task_run` — background task id, kind, status, progress, error (backs `/tasks` + SSE).
- `approval_token` kept inline on `outgoing_email` (hash + expiry + `consumed_at`) as in the brief.
- `resume.parsed` holds the profile **with stable ids** on every experience, bullet, project and skill,
  so tailoring output can reference source ids (no-fabrication check).
- `job_posting.embedding vector(768)` (dimension fixed by the chosen embedding model, see plan).

## 6. Agents

| Agent | Tools | Output schema |
|---|---|---|
| TriageAgent | MCP triage toolset | via `bulk_classify_and_label` args |
| ResumeParserAgent | none | `ResumeProfile` |
| JobMatcherAgent | `get_job`, `get_master_profile` (read-only) | `MatchRerank` |
| TailorAgent | `get_job`, `get_master_profile`, `get_template_schema` | `TailoredCV` (every bullet has `source_id`) |
| CoverLetterAgent | same as TailorAgent | `CoverLetter` (claims reference `source_id`s) |

Guardrails: input guardrail = injection detector; output guardrail = schema + no-fabrication validator
(ids exist, employers/dates/degrees unchanged, skills ⊆ master skills). Tracing on; usage stored.

## 7. LaTeX pipeline

`Jinja2(block_start='\BLOCK{', variable_start='\VAR{', comment_start='\#{', autoescape=latex_escape)`
renders into a per-job temp dir. The `latex` container has `network_mode: none`, so it is driven
through a **spool volume**: the worker writes `in/<job>/` (sources, then `job.json` atomically), the
sandbox writes `out/<job>/result.json` + PDF, and refreshes a `heartbeat` file that the health check
reads. The container compiles with **network disabled**, no
shell-escape, `openout_any=p`, read-only template mount, CPU/memory/time limits. The compile client
returns PDF + parsed log. Engine choice (TeX Live `latexmk` vs Tectonic) is decided in Phase 6 —
Tectonic downloads packages on demand, which conflicts with a no-network sandbox.

## 8. Sending

Review screen → `POST /outgoing/{id}/approve` returns nothing usable by agents; it creates a random
token, stores `sha256(token)` + 10-min expiry, and enqueues `send_email(outgoing_id, token)`. The
sender service checks hash, expiry, `consumed_at IS NULL`, daily cap and duplicate guard inside one
DB transaction, then sends with the separate `gmail.send` credential, labels the thread
`JobPilot/Candidature`, and records `sent_message_id` / `gmail_thread_id`.

## 9. Web ↔ API

The browser only talks to the Next.js origin. `apps/web/src/app/api/v1/[...path]/route.ts` is a
server-side proxy to the API on the internal Docker network (`API_INTERNAL_URL`), streaming responses
(SSE included). It forwards only the `jp_session` cookie (and the API's `Set-Cookie` for it);
authentication itself is enforced by the API (Argon2 password, Redis sessions, CSRF header, see
`docs/security.md` T7). A Next middleware redirects to `/login` when no session cookie exists. The API's CORS allow-list contains only the dashboard origin(s).

Background work follows one pattern: the API creates a `task_run` row, enqueues an arq job with the
same id and returns `202 {task_id}`; the worker reports progress through `TaskReporter` (DB row +
Redis pub/sub); `GET /api/v1/tasks/{id}/events` streams it as SSE.

## 10. Local ports

All published on `127.0.0.1` only: web 3000, api 8000, postgres **5433** (so a locally installed
PostgreSQL keeps 5432; `POSTGRES_HOST_PORT`), redis 6379 (`REDIS_HOST_PORT`).

## 11. Runtime constraints of the dev machine

Measured in Phase 0: 4-thread AMD Athlon Gold 3150U, ~10 GB RAM, ~44 GB free disk. Consequences:
smaller embedding model by default, the web app can run on the host instead of in a container during
development, and the LaTeX image is kept lean. See the plan for the concrete choices.
