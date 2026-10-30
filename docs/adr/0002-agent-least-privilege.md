# ADR-0002 — Agents run with least privilege

- Status: Accepted (Phase 0)
- Date: 2026-10-30

## Context

Agents read attacker-controllable text (any sender can email me; anyone can publish a job post).
Prompt injection cannot be fully prevented by detection alone, so the blast radius must be bounded
by what agents are *able* to do.

## Decision

1. Each agent gets its own minimal tool set (see architecture §6). Tool sets are declared in code
   and asserted by tests.
2. The Gmail MCP server implements **only** read-compact and add-`JobPilot/*`-label operations. There
   is no code for send, draft, delete, trash, forward, filters or label removal.
3. The triage OAuth token uses `gmail.modify` (applying labels to messages requires it; `gmail.labels`
   alone cannot). The MCP server restricts usage further: `messages.modify` with `addLabelIds` only,
   label ids resolved from a fixed `JobPilot/` whitelist, `removeLabelIds` always empty.
4. Bulk tools only act on ids from the batch the orchestrator issued (`batch_id`).
5. Compilation, sending and status changes are deterministic services, never agent tools.
6. Untrusted content is spotlighted (delimited data blocks) and every agent output is schema-validated.

## Consequences

- Worst case for a successful injection: a wrong `JobPilot/*` label — visible and reversible.
- `gmail.modify` is broader than needed at the Google level; mitigated in code and documented in
  `docs/security.md` (Phase 2).
