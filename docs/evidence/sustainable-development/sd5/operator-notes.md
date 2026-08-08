# SD5 operator notes

## Supported install

```bash
uv sync --frozen --extra dev
# or
./scripts/bootstrap.sh
```

Do not release from an unlocked resolution. If `uv lock` / lock drift appears in CI, refresh `uv.lock` in a dedicated PR and re-run the gate.

## TypeScript packages

```bash
npm --prefix dashboard ci
npm --prefix integrations/opencode-plugin ci
```

Prefer `npm ci` over `npm install` in CI and verify paths.

## Package smoke

```bash
npm --prefix dashboard ci
npm --prefix dashboard run build
bash scripts/package_smoke.sh
```

## Scheduled recovery

`.github/workflows/scheduled-recovery.yml` runs weekly (and on `workflow_dispatch`).

- Always runs hermetic `tests/unit/test_backup_restore.py`.
- Docker remote and backup integration soft-skip unless repository variable `FORCE_SCHEDULED=1`.
- Worker shutdown is a hermetic stub until SD3 drain proof exists.

Live secrets must never be attached to pull_request workflows.
