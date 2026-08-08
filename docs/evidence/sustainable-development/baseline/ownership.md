# Declared file ownership (parallel streams after G1)

Owners below are **default PR boundaries**. Cross-cutting edits need an explicit note in the PR placement block.

## Persistence / durability (SD0.A, later SD3)

| Area | Paths |
| --- | --- |
| Schema / migrations | `src/product_factory/persistence/database.py`, `src/product_factory/persistence/migrations/` |
| Artifacts / backup | `src/product_factory/persistence/artifacts.py`, `artifact_policy.py`, `backup.py` |
| Migration fixtures / tests | `tests/unit/test_sd0_migrations.py`, `tests/fixtures/migrations/`, `tests/unit/test_rf6_migration_smoke.py` |
| Evidence | `docs/evidence/sustainable-development/sd0/` (migration slices), later `sd3/` |

## Host / protocol / API (SD0.D, later SD4)

| Area | Paths |
| --- | --- |
| FastAPI app / routes | `src/product_factory/api/` |
| Host service / CLI | `src/product_factory/host/` |
| Remote / MCP / OpenCode | `src/product_factory/remote/`, `src/product_factory/host_mcp/`, `integrations/opencode-plugin/` |
| Dashboard | `dashboard/` |
| Contract / SSE tests | `tests/contract/`, `tests/security/` (route-auth) |

## Orchestration / trust execution (SD0.B/C/E, later SD1–SD2)

| Area | Paths |
| --- | --- |
| Handoff / approval services | `src/product_factory/trust/` (new), `workflows/handoffs.py` (validation only) |
| Capability descriptors / executors | `src/product_factory/registry/`, `src/product_factory/executors/` |
| Connectors | `src/product_factory/connectors/` |
| Context inventory | `src/product_factory/context/`, `src/product_factory/repository/` |
| Coordinator (compat only) | `src/product_factory/orchestration/coordinator.py` — **prefer new modules**; temporary edits need dated removal issue |

## Evaluation / CI (later SD5–SD6)

| Area | Paths |
| --- | --- |
| Evaluation | `src/product_factory/evaluation/`, `tests/evaluation/` |
| Verify / CI | `scripts/verify.sh`, `scripts/package_smoke.sh`, `.github/workflows/` |
| Lockfiles | `uv.lock`, `dashboard/package-lock.json`, `integrations/opencode-plugin/package-lock.json` |
| Evidence | `docs/evidence/sustainable-development/sd5/` |

## Conflict rule

Do not land persistence schema changes in the same PR as host/v2 or coordinator decomposition. SD0 slices stay short and sequential where the playbook requires ordering (A before B/C tables).
