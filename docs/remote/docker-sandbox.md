# Docker remote mock sandbox (PM3.0)

Local substitute for a private AMD / remote Product Factory server. Runs the
observe + host control API in **force-mock** and **remote mode** — no OpenRouter
key, no GPU, no local LLM.

Use this for PM3 HTTP integration (`RemotePfClient`) plus R2 `git_ref` provenance
and R3 delivery/landing tests. OpenCode remote smoke runs against the same stack
via [`scripts/opencode_remote_smoke.sh`](../../scripts/opencode_remote_smoke.sh)
(gated on `OPENCODE_INTEGRATION=1` and `opencode` on PATH).

## Quick start

```bash
export PRODUCT_FACTORY_OBSERVE_TOKEN=test-token   # required; do not commit real secrets
./scripts/docker_remote_up.sh
```

Harness env (host):

| Variable | Purpose |
| --- | --- |
| `PRODUCT_FACTORY_REMOTE_URL` | Client base URL (default `http://127.0.0.1:8765`) |
| `PRODUCT_FACTORY_OBSERVE_URL` | Canonical observe base advertised in meta |
| `PRODUCT_FACTORY_OBSERVE_TOKEN` | Bearer token (required by compose) |
| `PRODUCT_FACTORY_FORCE_MOCK` | Set to `1` inside the container |
| `DOCKER_INTEGRATION=1` | Run `tests/integration/test_remote_docker.py` for real; fail if Docker/compose unhealthy |

Laptop client example:

```bash
export PRODUCT_FACTORY_REMOTE_URL=http://127.0.0.1:8765
export PRODUCT_FACTORY_OBSERVE_TOKEN=test-token
uv run product-factory remote submit \
  --request ./tests/fixtures/intake/well_scoped_feature.md \
  --workflow change_intake \
  --mock
```

Tear down:

```bash
docker compose -f examples/remote/docker-compose.yml down
# optional: also drop the data volume
docker compose -f examples/remote/docker-compose.yml down -v
```

## What the stack provides

- Image built from the repo root [`Dockerfile`](../../Dockerfile) via
  [`examples/remote/docker-compose.yml`](../../examples/remote/docker-compose.yml).
- API bound to `0.0.0.0:8765` in-container; published as **`127.0.0.1:8765` only**.
- Persistent `/data` volume (sqlite + runs + seeded repos).
- Fixture registry id `sample_api` → `/data/repos/sample_api` (git repo seeded from
  `tests/fixtures/sample_api` on first start).
- Config overlay: `examples/remote/repositories.docker.yaml` mounted over
  `/app/config/repositories.yaml` (absolute container paths; no secrets).

## Integration tests

```bash
# Soft-skip (default): exercises the skip path without Docker
uv run pytest -q tests/integration/test_remote_docker.py

# Real HTTP against compose (fails clearly if Docker is missing/unhealthy)
DOCKER_INTEGRATION=1 PRODUCT_FACTORY_OBSERVE_TOKEN=test-token \
  uv run pytest -q tests/integration/test_remote_docker.py
```

`scripts/verify.sh` always invokes the docker integration module so soft-skip is
covered; set `DOCKER_INTEGRATION=1` to require a healthy stack. Keep the default
CI gate unit-only (`pytest -m "not integration"`).

Set `DOCKER_KEEP=1` to leave containers up after the pytest session.

## OpenCode remote smoke

```bash
# Soft-skip without opencode / Docker
./scripts/opencode_remote_smoke.sh

# Require the full remote path (brings compose up if it is not already healthy)
OPENCODE_INTEGRATION=1 PRODUCT_FACTORY_OBSERVE_TOKEN=test-token \
  ./scripts/opencode_remote_smoke.sh
```

Asserts the plugin registers `pf_run`/`pf_wait`/`pf_review`/`pf_merge`/`pf_decline`
in `opencode serve`, then drives the remote mock lifecycle (submit → wait →
review → approve `apply=false` → delivery download → `LandingAdapter` land →
landing receipt). Set `OPENCODE_SMOKE_KEEP=1` to keep the temp project and
compose stack for debugging.

## Stack contract for downstream agents

1. **Compose project / image:** `examples/remote/docker-compose.yml`, image tag
   `product-factory:local`, service name `product-factory`.
2. **URL / auth:** `http://127.0.0.1:8765` + bearer from
   `PRODUCT_FACTORY_OBSERVE_TOKEN` (harness default `test-token` via
   `docker_remote_up.sh` only — never baked into the image).
3. **Modes:** container always has `PRODUCT_FACTORY_FORCE_MOCK=1` and
   `PRODUCT_FACTORY_REMOTE_MODE=true`. Submit with `mock=True`; never send
   laptop `repository_path`.
4. **Workspace kinds today:** meta advertises `none`, `registered_path`, and
   `git_ref`, with `delivery_support: true`. `uploaded_git_bundle` is still
   deferred.
5. **Registered fixture:** repository id `sample_api` resolves to
   `/data/repos/sample_api` (normal git repo with an initial `fixture` commit).
   Reuse this path/id for `git_ref` provenance tests; extend
   `repositories.docker.yaml` rather than inventing a second sample repo unless
   needed.
6. **Project vs data:** `PRODUCT_FACTORY_ROOT=/app` (image config/skills),
   `PRODUCT_FACTORY_DATA_DIR=/data` (volume). Observe serve honors both.
7. **Bring-up helper:** `scripts/docker_remote_up.sh` builds, ups, and waits on
   `GET /api/v1/meta` with the bearer token.
8. **Still out of scope:** `uploaded_git_bundle` preflight/upload, local-model
   gateway and leased workers (R4), and live authenticated partner endpoints.

## Related

- Operator private-server notes: [`r0-private-server.md`](r0-private-server.md)
- Host/remote transport: PM2.B (`RemotePfClient`, remote CLI)
