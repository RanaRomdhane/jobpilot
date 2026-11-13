# ADR-0003 — Every outgoing email is human-approved

- Status: Accepted (Phase 0)
- Date: 2026-10-30

## Context

Sending an application is irreversible and speaks in my name. No automated component — least of all
an LLM — may decide to send.

## Decision

1. Sending uses a **separate OAuth credential** with only `gmail.send`, requested when sending is
   enabled in Settings, stored Fernet-encrypted.
2. The only producer of a send is the **Approve & Send** action on the review screen, which creates a
   random one-time token; only `sha256(token)` and an expiry (default 10 min) are stored.
3. The sender service (worker job, not an agent tool) verifies, in one transaction: token hash match,
   not expired, not consumed, daily cap not reached, no duplicate application (same company + role
   within 30 days, unless explicitly overridden at review). It then marks the token consumed and sends.
4. Every approval and send is written to `application_event` (audit trail).

## Implementation notes (Phase 7)

- The token reaches the sender as an argument of its worker job (Redis), never through an
  agent. The API checks first, the sender checks again: cap and duplicates can change between
  the click and the send.
- The send credential also has `userinfo.email`, because `gmail.send` cannot read the profile
  to tell which account it belongs to. A token for a different account than triage is refused.
- A send that fails is never retried automatically: Gmail may have sent it before the error.
- After a send, the application gets the Gmail thread id, so triage links replies in that
  thread whatever their category, and the sent message gets `JobPilot/Candidature`.

## Consequences

- No "auto-apply" mode exists. Bulk applying means approving each email.
- Losing the token (e.g. worker crash after consume, before send) requires a re-approval — the safe
  failure direction.
