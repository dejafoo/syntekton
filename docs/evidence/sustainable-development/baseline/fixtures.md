# Frozen fixtures for SD0

## Empty database

- **Meaning:** no SQLite file yet; first `Database(path)` / migration runner creates schema from versioned migrations.
- **Test ownership:** `tests/unit/test_sd0_migrations.py` (empty fixture) and migration suite.

## Pre-SD0 database upgrade path

- **Meaning:** a SQLite file that has the pre-SD0 table set (including unused `approvals`) and **no** `schema_migrations` ledger.
- **Characterization:** existing `tests/unit/test_rf6_migration_smoke.py` legacy layout remains valid as an older partial schema; SD0 adds an explicit **current pre-SD0** fixture that matches post-RF6 / pre-migration-runner shape (full `SCHEMA_SQL` tables, unused `approvals`).
- **Upgrade:** opening `Database` must baseline-record expected tables, then apply additive SD0 migrations (rename `approvals` → `legacy_approvals`, handoff/approval tables) without rebuilding rows.

## Host/v1 compatibility

- Preserve `host/v1` request/response shapes through SD0.
- `HandoffRef` remains the v1 assertion shape on submit; durable `HandoffRecord` is resolved server-side.
- Action approvals introduce new `/api/v1/action-approvals*` routes (additive); deployment pack_input `approval_binding` ceases to be authority.
- SSE `GET /api/v1/runs/{run_id}/events/stream` remains the live protocol; WebSocket removed (breaking for unused surface only).

## Dashboard / OpenCode package pins

Pinned at baseline (see `client-package-versions.json`):

- `product-factory` / dashboard / OpenCode plugin: **0.1.0**
- `requires-python`: `>=3.13,<3.14`

Do not bump these packages as part of SD0 trust-boundary work.
