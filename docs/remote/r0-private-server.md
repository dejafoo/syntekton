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
| `PRODUCT_FACTORY_OBSERVE_URL` | Base URL the laptop uses for observe/SSE (public tunnel or VPN hostname). |
| Host public base URL | Same origin hosts use for `product-factory.host/v1` when not local. |
| Bearer token | When set, unauthenticated clients must be refused. |
| Persistent data dir | Absolute path for `.product-factory` (or configured `data_dir`); survives restarts. |

Example (server shell):

```bash
export PRODUCT_FACTORY_DATA_DIR=/var/lib/product-factory
export PRODUCT_FACTORY_HOST_TOKEN=replace-me
# Observe API (adjust to your process manager / entrypoint)
export PRODUCT_FACTORY_OBSERVE_URL=https://pf.example.internal
```

Laptop:

```bash
export PRODUCT_FACTORY_OBSERVE_URL=https://pf.example.internal
export PRODUCT_FACTORY_HOST_TOKEN=replace-me
```

## Operator checklist

1. Place config under a persistent directory; do not store secrets in the repo.
2. Start the observe/host API behind a private tunnel or VPN only.
3. Confirm `GET` health/SSE works through the tunnel with the bearer token.
4. Confirm requests without the token are refused when a token is configured.
5. Confirm host envelopes never include raw server filesystem paths outside the
   declared workspace / run relative paths.
6. Restart the service and confirm queued/completed runs remain in the data dir.

## Explicitly out of R0

- `RemotePfClient` and laptop↔server agent control protocol
- Workspace `git_ref` remote checkout
- Delivery landing / push / PR automation
- Local-model gateway (R4)

See also [`handover_remote_orchestration.md`](../handover_remote_orchestration.md) §R0.
