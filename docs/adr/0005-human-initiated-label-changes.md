# ADR-0005 — Human-initiated label changes

- Status: Accepted (Phase 3)
- Date: 2026-10-30
- Amends: ADR-0002

## Context

ADR-0002 forbids any code path that removes Gmail labels, so a wrong classification could only be
corrected in Gmail by hand. The dashboard lets the user re-classify an email and clear a
`JobPilot/⚠ Suspicious` flag; the Gmail labels should follow those corrections.

## Decision

1. A separate **user-action service** (`apps/worker/.../user_labels.py`) can add *and remove*
   labels, restricted to the `JobPilot/` namespace (checked against ids it resolved itself).
2. It lives in the worker, **outside** the MCP package and the agents package: no agent tool, MCP
   server or prompt can reach it. Security tests keep asserting that the MCP triage code has no
   label-removal path.
3. It only runs as a background job enqueued by an **authenticated, CSRF-checked** dashboard
   request (re-classify, clear suspicious). Each change is recorded in the database (the user's
   category is kept in `email_message.manual_category`, which also feeds the evaluation set).

## Consequences

- The agent's capabilities are unchanged: the worst case of an injection is still a wrong,
  visible label that the user can now fix from the dashboard in one click.
- The Gmail token used by the worker can technically remove labels anywhere; the restriction is
  enforced in code, like the rest of ADR-0002.
