## Summary

<!-- What this PR does and which phase / issue it belongs to. -->

## Acceptance criteria

<!-- Copy the phase's acceptance criteria from docs/plan.md and tick them. -->
- [ ]

## Security checklist

- [ ] All external text (emails, job posts, scraped pages, CVs) goes through `packages/security`
- [ ] No agent gained a tool that can send, delete, forward or remove labels
- [ ] Agent outputs are validated against Pydantic schemas
- [ ] Generated content only uses facts from the master profile (when applicable)
- [ ] No secrets, tokens or personal data committed or logged

## Tests

- [ ] Unit / integration tests added or updated
- [ ] `make lint typecheck test` passes locally
- [ ] CI green

## Notes for review

<!-- Trade-offs, follow-ups, screenshots. -->
