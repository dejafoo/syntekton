# R0 — Private single-server operator proof

Parallel ops track for PM0.D. This is enough to run Product Factory on a private
AMD (or any) server and observe it from a laptop — **not** remote workspaces,
`RemotePfClient`, or delivery landing (those are PM2/PM3).

## Topology

```text
Laptop (OpenCode / host CLI)  --HTTPS/SSE-->  Tunnel/VPN  -->  Server (pf API + data dir)
```

- Server owns the persistent data directory and process lifecycle.
- Laptop owns final land decisions (`materialize` / OpenCode merge). The server
  must not push, open PRs, or deploy by default.

## Required configuration

| Variable / setting | Purpose |
| --- | --- |
| `PRODUCT_FACTORY_OBSERVE_URL` | Base URL the laptop uses for observe/SSE (public tunnel or VPN hostname). Also used as `canonical_observe_base` in `GET /api/v1/meta`. |
| `PRODUCT_FACTORY_REMOTE_URL` | Laptop client base URL for `product-factory remote …` / OpenCode `RemotePfClient` (PM2.B). |
| `PRODUCT_FACTORY_REMOTE_MODE` | When `true` on the server, HTTP submit rejects client `repository_path` and accepts `repository_id` only. |
| Bearer token | `PRODUCT_FACTORY_OBSERVE_TOKEN` (canonical) or alias `PRODUCT_FACTORY_HOST_TOKEN`. When set, unauthenticated clients are refused for observe and control. |
| Persistent data dir | Absolute path for `.product-factory` (or configured `data_dir`); survives restarts. |
| `config/repositories.yaml` | Thin registry mapping `repository_id` → absolute server path (R1 workspace kind `registered_path`). |

Example (server shell):

```bash
export PRODUCT_FACTORY_DATA_DIR=/var/lib/product-factory
export PRODUCT_FACTORY_OBSERVE_TOKEN=replace-me
# Optional alias used by some R0 sketches:
# export PRODUCT_FACTORY_HOST_TOKEN=replace-me
export PRODUCT_FACTORY_REMOTE_MODE=true
export PRODUCT_FACTORY_OBSERVE_URL=https://pf.example.internal
# Observe/host API (adjust to your process manager / entrypoint)
product-factory observe serve --host 127.0.0.1 --port 8765
```

Laptop:

```bash
export PRODUCT_FACTORY_REMOTE_URL=https://pf.example.internal
export PRODUCT_FACTORY_OBSERVE_URL=https://pf.example.internal
export PRODUCT_FACTORY_OBSERVE_TOKEN=replace-me
product-factory remote submit --request ./request.md --workflow technical_plan --mock
```

## Operator checklist

1. Place config under a persistent directory; do not store secrets in the repo.
2. Start the observe/host API behind a private tunnel or VPN only.
3. Confirm `GET /api/v1/meta` and SSE work through the tunnel with the bearer token.
4. Confirm requests without the token are refused when a token is configured.
5. Confirm host envelopes never include raw server filesystem paths outside the
   declared workspace / run relative paths.
6. Restart the service and confirm queued/completed runs remain in the data dir.

## Explicitly out of R0

- Full OpenCode remote merge / delivery landing (R3)
- Workspace `git_ref` remote checkout (R2)
- Local-model gateway (R4)

See also remote orchestration handover §R0 when present on the docs branch.
