# Baseline evidence (Phase 0)

**Starting commit:** see [`STARTING_COMMIT.txt`](STARTING_COMMIT.txt)  
**Branch for SD0 work:** `sd/sd0-trust-boundaries` (cut from that commit)

## Captured artifacts

| Artifact | Path |
| --- | --- |
| Starting SHA | [`STARTING_COMMIT.txt`](STARTING_COMMIT.txt) |
| HTTP/WS route inventory | [`route-inventory.json`](route-inventory.json) |
| Pack registry dump | [`pack-registry.json`](pack-registry.json) |
| Schema notes (pre-SD0) | [`schema-notes.json`](schema-notes.json) |
| Client / package versions | [`client-package-versions.json`](client-package-versions.json) |
| Verification transcripts | [`verification/`](verification/) |
| Fixture freeze notes | [`fixtures.md`](fixtures.md) |
| Parallel-stream ownership | [`ownership.md`](ownership.md) |

## Notable pre-SD0 findings (frozen)

- Unversioned `migrate()` / `_ensure_column` only; no `schema_migrations`.
- Unused `approvals` table in `SCHEMA_SQL` (not deployment authority).
- Unauthenticated `WEBSOCKET /api/v1/events/ws` present in route inventory.
- Handoffs are `HandoffRef` shape/pack checks only (no durable records).
- Deployment approval is pack-input field equality + `_approval_binding_verified`.
- Assembler uses `Path.rglob` without symlink confinement.

## Verification commands archived

See tracker “Required verification”. Archived where practical under `verification/`:

- `ruff format --check`, `ruff check`, `basedpyright` — captured at baseline.
- Broader `pytest` / npm / `uv build` — re-run during SD0/G0; note any baseline deferral in `verification/NOTES.md`.
