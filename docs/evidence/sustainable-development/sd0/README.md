# SD0 → G0 evidence

**Branch:** `sd/sd0-trust-boundaries`  
**Baseline:** [`../baseline/`](../baseline/) (commit `3255853cf17d8ef03bc0f0a6b773f92a65b3c076`)

## Implemented

| Slice | Evidence |
| --- | --- |
| SD0.A Migrations | `src/product_factory/persistence/migrations/`; `tests/unit/test_sd0_migrations.py` |
| SD0.B Handoffs | `src/product_factory/trust/handoffs.py`; API routes in `api/control.py`; `tests/security/test_handoff_authority.py` |
| SD0.C Approvals | `src/product_factory/trust/approvals.py`; coordinator consumption; `tests/security/test_action_approval_authority.py` |
| SD0.D WS removal | `api/app.py` (SSE only); `tests/security/test_route_auth_inventory.py` |
| SD0.E Inventory | `context/safe_inventory.py`; assembler + stack_profile routed; `tests/security/test_safe_repository_inventory.py` |

## Hermetic verification

```text
uv run ruff check src tests
uv run pytest -q tests/unit/test_sd0_migrations.py tests/security/test_*.py \
  tests/graph/test_deployment_approval_binding.py tests/graph/test_deployment_execution_pack.py \
  tests/contract/test_observability_api.py
uv run pytest -q -m "not integration"
```

Results archived when re-run: see `pytest-not-integration.txt` (916 passed / 1 fixed deployment pack failure then re-verified).

## Integration / operational

- Integration: deferred beyond hermetic API/SSE/migration suites (no live connector or live-model claim).
- Operational: `staging_deploy` remains disabled in default `config/connectors.yaml` without durable ApprovalService verification.

## G0 checklist mapping

- Forged handoffs/approvals fail before spend/connector: security tests + host `invalid_handoff`.
- Symlinked/prohibited paths excluded from prompts: `test_safe_repository_inventory.py`.
- No unauthenticated live stream: WebSocket removed; SSE behind `require_auth`.
- Deployment disabled without verifier: default connector config + durable approval ID required.
