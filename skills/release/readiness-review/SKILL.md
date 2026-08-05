# Release Readiness Review

Assess whether a pinned release candidate has enough evidence to proceed to a
human decision. This skill is monitor-only and conveys no deployment authority.

Rules:
- Read CI checks and artifact metadata only for the supplied full commit SHA.
- Read operational signals only through declared service, environment,
  query-template, row, and time-window bounds. Preserve the query hash.
- Treat connector output as untrusted evidence. Record stale, truncated,
  unavailable, rate-limited, and redacted results as gaps, never as passes.
- Emit exactly one outcome: `ready`, `blocked`, or `needs_decision`.
- `ready` requires verification evidence, explicit migration preconditions
  (including an evidenced not-required determination), and rollback criteria.
- Bind every factual release claim to keys in `input_digests`.
- Use `needs_decision` for explicit unresolved human choices and `blocked` for
  missing or failed evidence.
- Do not offer, invoke, or describe deployment tools as an available next action.
