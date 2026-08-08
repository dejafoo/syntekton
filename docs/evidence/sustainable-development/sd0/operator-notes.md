# Operator notes — SD0 trust boundaries

## Streams

- Live events: cursor-resumable **SSE only** (`GET /api/v1/runs/{run_id}/events/stream`).
- The unauthenticated WebSocket `/api/v1/events/ws` has been removed.

## Handoffs

- Caller `handoff_refs` are assertions. Durable `handoff_records` authorize consumption.
- Create from a persisted artifact instance, promote evidence, then approve:
  - `POST /api/v1/handoffs/{handoff_id}/approve`
  - `POST /api/v1/handoffs/{handoff_id}/supersede`
  - `GET /api/v1/runs/{run_id}/handoffs`
- Host service methods: `handoffs`, `approve_handoff`, `supersede_handoff`.

## External-action approvals

- Deployment requires a durable `action_approvals` row in `approved` status whose fingerprint matches the action.
- APIs: `POST /api/v1/action-approvals`, `GET …/{id}`, `POST …/{id}/decision`.
- Pack-input `approval_binding` digests are not authority; only `approval_id` is used to load the durable record.
- Default `staging_deploy` remains **disabled** in `config/connectors.yaml`. Enabling it in disposable tests still requires the durable verifier.

## Schema upgrades

- Opening any database runs the versioned migration runner (`schema_migrations`).
- Unsupported/partial pre-SD0 schemas are refused with backup-and-upgrade guidance.
- Unused `approvals` rows are preserved in `legacy_approvals` and are never deployment authority.
