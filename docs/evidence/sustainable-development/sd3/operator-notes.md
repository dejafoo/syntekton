# SD3 operator notes

## Backup

```bash
product-factory ops backup --dest /path/to/backup.tar.gz
product-factory ops backup-status
product-factory ops restore --archive /path/to/backup.tar.gz --data-dir .product-factory
```

Manifests are version 2: per-file checksums, `high_water_event_seq`, and an
explicit note that configuration/skills/profiles outside the data root must be
backed up separately.

## Maintenance (dry-run first)

```bash
# Inventory / planned prune (default dry-run)
product-factory ops maintain --prune-run run-abc123
product-factory ops maintain --max-age-days 30

# Pin then execute with backup prerequisite
product-factory ops pin run-abc123 --reason "incident"
product-factory ops backup --dest /tmp/pf-before-prune.tar.gz
product-factory ops maintain --execute --prune-run run-old --backup-ref /tmp/pf-before-prune.tar.gz
```

Deletion targets are explicit run IDs only. Path-shaped inputs are refused.
Audit rows append to `maintenance_audit` and are not rewritten.

## Shutdown

Set `PRODUCT_FACTORY_SHUTDOWN_GRACE_SECONDS` (default 15). Host close stops
admissions, waits cooperatively, records `forced_shutdown_recovery_required`,
then closes SQLite last.
