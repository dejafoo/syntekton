# SD3 → durability evidence (G3 contribution)

**Branch:** `sd/sd3-durability` (from `sd/sd1-executor-truth`)  
**Baseline:** [`../baseline/`](../baseline/)  
**Prior:** [`../sd0/`](../sd0/), [`../sd1/`](../sd1/)

## Implemented

| Slice | Evidence |
| --- | --- |
| SD3.A Repositories | `persistence/connection.py` (`SqliteActor`); `persistence/repositories/*`; migrations `005` eval schema, `006` retention/audit; `EvalStore` uses `db.evaluations` only |
| SD3.B Drain | `workers/supervisor.py` `drain()`; minimal `HostService.close` hook (`remove-host-drain-hook-2026-08`) |
| SD3.C Artifacts/backup | Atomic blob write (temp+fsync+rename+digest); backup v2 manifests with per-file checksums + high-water seq + restore validation |
| SD3.D Retention | `persistence/retention.py`; CLI `ops maintain|pin|unpin` dry-run-first with backup prerequisite + append-only audit |

## Hermetic verification

```text
uv run ruff check src/product_factory/persistence src/product_factory/workers/supervisor.py \
  src/product_factory/evaluation/store.py src/product_factory/host/service.py \
  tests/unit/test_sd3_*.py
uv run pytest -q tests/unit/test_sd3_*.py tests/unit/test_sd0_migrations.py \
  tests/unit/test_backup_restore.py tests/unit/test_worker_leases.py \
  tests/unit/test_persistence.py
uv run pytest -q -m "not integration"
```

Results: `pytest-not-integration.txt` — **974 passed**, 3 skipped, 14 deselected.

## Integration / operational

- Integration restart/restore drill: deferred to SD5 scheduled recovery job / G3 joint gate.
- Operational backup/restore drill: not claimed on this branch.

## Placement note

```text
Concern: persistence | executor(worker) | policy(retention)
Owning boundary: product_factory.persistence (+ workers.supervisor drain)
Authoritative source: SqliteActor + aggregate repositories; schema_migrations; retention_pins / maintenance_audit
Compatibility: Database façade API preserved for SD2; evaluation dual-write retained until export reader verified; HostService.close drain hook temporary (remove-host-drain-hook-2026-08)
Guardrail proof: tests/unit/test_sd3_*.py; pytest -m "not integration" (974 passed)
Temporary exception: HostService.close calls supervisor.drain before db.close — remove when SD2 lifecycle service owns shutdown
```

## Merge notes for SD2

- Do not rewrite `Database` method signatures; SD2 can keep using `coord.db.*`.
- Prefer new code use `db.runs` / `db.tasks` / `db.events` aggregates.
- Shutdown: if SD2 extracts lifecycle service, move drain there and delete the host hook.
- Schema: migrations 5–6 must apply before any SD2 feature that assumes eval/retention tables.
