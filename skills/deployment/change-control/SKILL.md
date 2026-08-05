# Deployment Change Control

This is a checklist, not deployment authority. The workflow, operator approval,
connector policy, and target registry remain authoritative.

Rules:
- Accept only a `ready` ReleasePlan whose digest is present in the approval binding.
- Require the approval binding to match the release-plan digest, artifact digest,
  target id, and declared change window before any effect.
- Resolve the target through the operator registry. Reject unknown, disabled, and
  production targets.
- Use the declared idempotency key. Reconcile an existing receipt before retrying;
  never create a second deployment for the same key.
- Hold one concurrency lock per target until success, halt plus rollback, or an
  explicit reconciliation decision.
- Record confirmation before `start_deployment` and `rollback_deployment`.
- Verify declared health checks after rollout. Failed health means halt; run the
  declared rollback and retain both failure and rollback receipts.
- On timeout or partial response, record `unknown` with reconciliation required.
  Do not retry blindly.
- Emit `deployment_record.v1` with immutable bindings, action log, health evidence,
  policy decisions, and rollback result.
