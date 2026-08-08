# SD4 operator notes

## What changed for operators

- Prefer `/api/v2` and `product-factory.host/v2` for new clients.
- Submit bodies no longer accept `mock` / `inline` / `sync` / `model_profile_set` /
  `project_profile` / `requested_artifacts`. Debug execution modes are server/test
  configuration (`PRODUCT_FACTORY_FORCE_MOCK` / `PRODUCT_FACTORY_HOST_DEBUG`).
- Handoffs on v2 are `{handoff_id, expected_digest}` only.
- Local CLI (`run` / `approve` / `reject` / `apply` / `resume` / `handoff *`) routes
  through the same `HostService` as host CLI, HTTP, and MCP.
- `product-factory ops backup|restore|backup-status` remain **administrative** —
  not run semantics.

## Dashboard

Still loopback/monitor-only. Meta advertises `dashboard.remote_browser=unsupported`.
Tunnel to loopback if you must view a remote host from a laptop; do not treat a
control token as a public browser deployment.

## Drift check

```bash
bash scripts/check_openapi_drift.sh
bash scripts/generate_host_openapi.sh   # when intentionally changing the contract
```

Wire the drift script into SD5 `scripts/verify.sh` on merge.
