# Operator guide — post-MVP refactoring gate (RF6) + PM5.E hardening

Single-user remote / local-server operations for Product Factory after the
RF1–RF6 runtime hardening and PM5 remote ingress controls. Mutations remain on
the host CLI/MCP/control API; the dashboard is monitor-only.

## Data layout and backup

Default data root: `.product-factory/` under the project (or the remote
server’s configured root).

| Path | Role |
| --- | --- |
| `data/product_factory.sqlite` | Authoritative runs, tasks, events, invocations, artifact instances |
| `runs/<run_id>/` | Per-run artifacts, prompts, content captures, output projections |
| `ops/local_route_admission/` | Measured local-model admission evidence (RF5) |
| `ops/ingress-audit.jsonl` | Auth / rate-limit / upload audit trail (PM5.E) |
| `uploads/` | Staged and finalized git-bundle uploads (PM5.E) |

**Backup (recommended):** stop writers when practical, then:

```bash
product-factory ops backup --dest /safe/path/pf-backup.tar.gz
# optional explicit root:
product-factory ops backup --data-dir /var/lib/product-factory --dest ./pf-backup.tar.gz
```

The archive includes a consistent SQLite snapshot (via the SQLite backup API),
`runs/`, `ops/`, and `uploads/`, plus `backup-manifest.json` with digests and
run ids. Prefer this over a live filesystem copy when the API is busy.

**Manual copy:** stop writers (or accept a brief inconsistency window), copy the
entire `.product-factory` directory (SQLite + `runs/` + `ops/`). Prefer
`sqlite3 .backup` on the DB when available.

**Restore:**

```bash
product-factory ops restore --archive /safe/path/pf-backup.tar.gz --replace
```

`--replace` moves aside a non-empty target data root before extracting. On
open, `Database.migrate()` applies additive column/table upgrades
restart-safely. Old runs remain readable; missing RF2+ fields surface as
`legacy_policy` / `legacy_unknown` visibility rather than fabricated grants.

**Opt-in drill:** `BACKUP_INTEGRATION=1 uv run pytest tests/integration/test_backup_restore.py`

## Remote ingress (PM5.E)

Configured under `config/policies.yaml` → `ingress` (env overrides available):

| Control | Behavior |
| --- | --- |
| Trusted proxies | `X-Forwarded-*` / `Forwarded` ignored unless `trust_forwarded_headers` is true **and** the peer matches `trusted_proxies` |
| Auth failure limits | Failed bearer attempts are rate-limited and audited |
| Submit / upload limits | Concurrent submission and upload floods return HTTP 429 |
| Upload bounds | Git-bundle preflight/upload/finalize enforces size, media type, SHA-256, and path-escape rejection |

Env overrides: `PRODUCT_FACTORY_TRUSTED_PROXIES`, `PRODUCT_FACTORY_TRUST_FORWARDED`,
`PRODUCT_FACTORY_MAX_UPLOAD_BYTES`.

Streamable HTTP MCP remains deferred until a concrete non-OpenCode host needs it.

## Migrations

Migrations are additive only (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE …
ADD COLUMN`). They never rewrite historical event payloads. After upgrade:

- Tasks without `effective_policy_json` → `legacy_policy: true` on task
  projections.
- Artifacts without `artifact_instances` rows → content API returns
  `available: false`, `visibility: legacy_unknown`.
- Invocations without `routing_json` → route/fallback fields are null; costs
  may group under `route: unknown`.

## Capture and evidence availability

`PRODUCT_FACTORY_CAPTURE_LEVEL`: `off` | `metadata` | `redacted` | `full`
(default `redacted`).

| Visibility | Operator meaning |
| --- | --- |
| `available` | Body may be shown |
| `redacted` | Body shown as stored redacted form |
| `metadata_only` / `unavailable` | No recoverable body; reason on ContentView |
| `legacy_unknown` | Pre-instance artifact; not auto-exposed as full |

Unknown or cross-run hashes return **404**. The dashboard never de-redacts.

## Local vs cloud model labels

- `route_class` / invocation `route`: `local` or `cloud`.
- Named fallback shows `fallback_profile` + `fallback_reason` (for example
  `local_unhealthy`, `capability_miss`, `provider_error`).
- Costs expose `by_route` so local estimated spend and cloud spend stay
  separable.
- OpenRouter may still stand in for local until
  `PRODUCT_FACTORY_LOCAL_BASE_URL` / `base_url` cutover — see
  [remote/local-model-gateway.md](remote/local-model-gateway.md).

## Restart and recovery

Workers recover leased runs from SQLite + run directories. Observer processes are
read-only against the same DB. After restart:

1. Confirm `GET /api/v1/health` (`wal_mode`, `latest_seq`).
2. Open the run detail; SSE may show stale/reconnecting while projections
   remain authoritative.
3. Do not treat worker stdout as state — use events and projections.

## Usability walkthrough — blocked or awaiting task

1. Open `/dashboard/` → select the run.
2. **Plan** tab: select the blocked/failed/awaiting task.
3. Read **Granted tools**, **Route**, **Fallback**, and **Stack profile**
   (or the legacy-policy warning).
4. **Execution**: check model invocations for actual provider/model and any
   fallback reason; inspect tool calls and validator results.
5. **Evidence**: open artifacts/captures; note visibility / unavailable
   reasons; review repair lineage when present.
6. **Costs**: confirm local vs cloud via `by_route`.
7. Follow the banner **next action** (also on the task): typically
   `product-factory approve <run_id> [--apply]` / `reject`, or revise and
   re-submit via host CLI/MCP.

PM5 release / ops / deployment packs are available for monitor-only release,
read-only ops, and one non-production deployment path. Production rollout and
Streamable HTTP MCP remain deferred.
