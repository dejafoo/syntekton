# Remote orchestration handover

**Status:** design and implementation handover

**Audience:** humans and AI agents implementing remote Product Factory use

**Scope:** one trusted operator using OpenCode and other existing CLIs from a
laptop while Product Factory, its workspaces, and locally hosted models run on
a private AMD AI server. This is not a design for a public multi-tenant agent
SaaS.

**Depends on:** the existing `product-factory.host/v1` protocol, host control
API, durable run store, capability/tool broker, approval gates, OpenCode
plugin, and observability service. See [host-integration.md](host-integration.md)
for the current contract.

---

## 1. Purpose and product boundary

Remote execution should let an operator keep their normal interactive surface
on a laptop while moving the expensive and long-running work near the models:

- OpenCode remains the primary interactive surface and retains its existing
  first-class plugin tools (`pf_run`, `pf_wait`, `pf_review`, `pf_merge`, and
  `pf_decline`). No slash command should be required for normal use.
- Product Factory runs on the server and remains responsible for planning,
  grants, tool execution, budgets, validation, repair, approval state,
  artifacts, and observability.
- Other CLIs, scripts, CI, and MCP-capable hosts can use the same remote host
  protocol. No orchestration behavior may live exclusively in the OpenCode
  plugin.
- The AMD server hosts the local model endpoint, worker runtime, server-side
  repository worktrees, and durable run data. Cloud fallback, if allowed,
  originates from the server under the run's routing and budget policy.

The project must **not** become an OpenCode replacement, an unauthenticated
remote shell, or a service that accepts arbitrary laptop filesystem paths.
Remote orchestration is an execution/control-plane deployment of the existing
product, not a new user-facing chat product.

### 1.1 The key distinction: remote orchestration is not remote MCP

MCP is one adapter over `HostService`; it is not the orchestration protocol.
The existing stdio MCP server is intentionally local to the process that starts
it. It can be tunneled over SSH, but that is not the durable network boundary
to build the product around.

For remote operation, the stable primary interface is:

1. `product-factory.host/v1` response/envelope semantics;
2. the existing HTTPS host control API for mutations and reads;
3. cursor-resumable REST/SSE for liveness and observability;
4. a transport-neutral client in host integrations.

Streamable HTTP MCP may be added later for hosts that require a remote MCP
endpoint. It must map to the same control service and never grow a second
orchestrator, state store, approval path, or tool policy.

---

## 2. Current starting point and gaps

The current implementation has strong foundations for this work:

- CLI, HTTP control routes, and stdio MCP call the same `HostService` and
  return `product-factory.host/v1` envelopes.
- The control API already supports asynchronous submit, status/inspect,
  approval/rejection/cancel/revision, materialization, durable projections,
  and SSE.
- Run data, events, artifacts, costs, and evidence are durable and scoped by
  run.
- The OpenCode plugin has a deliberately transport-neutral `PfClient`
  interface. Its current implementation is `CliPfClient`; plugin tool behavior
  is independent of that implementation.
- The worker already uses isolated run directories and repository worktrees,
  and the tool broker confines tool access to the execution workspace.

The following are local-machine assumptions and must be removed or made
explicit before declaring remote execution supported:

| Current assumption | Why it fails remotely | Required direction |
| --- | --- | --- |
| OpenCode shells out to a local `product-factory host …` binary. | It creates runs in the laptop's local data directory. | Add an HTTPS `PfClient`; retain CLI transport for local mode. |
| `repository_path` is a local absolute path. | A laptop path is meaningless and unsafe on the server. | Submit a typed workspace source; the server resolves its own workspace. |
| `approve --apply` and `materialize` write to that repository path. | They would write to a server clone, not the operator's laptop checkout. | Produce a verified delivery bundle and use a local landing adapter after confirmation. |
| A control request spawns a detached worker subprocess. | A service restart or host reboot has no durable worker lease/supervision model. | Add a server worker supervisor, leases, recovery, and service lifecycle. |
| The observer defaults to `127.0.0.1` and its subscription URLs do too. | Laptop clients cannot consume those URLs. | Configure a canonical external base URL behind a private authenticated ingress. |
| The only live gateway is OpenRouter (or mock). | An AMD local model service cannot be selected directly. | Add an OpenAI-compatible local gateway adapter, health probe, and explicit fallback policy. |

The existing simple bearer token is adequate for a private tunnel proof of
concept, but it is not a complete remote identity, ingress, or service trust
model.

---

## 3. Locked decisions and non-goals

These defaults prevent the remote feature from weakening the existing safety
and integration boundaries.

1. **Single trusted operator and one server first.** The first supported
   topology is one operator, one AMD server, one Product Factory data root, and
   one logical worker pool. Multi-user tenancy, per-organization isolation,
   billing, and internet-public registration are out of scope.
2. **Private network first.** The initial supported ingress is Tailscale,
   WireGuard, or an SSH tunnel. Direct public exposure is not an exit criterion.
   HTTPS and authenticated reverse-proxy deployment are required before any
   broader LAN/internet use.
3. **The server executes only server-owned workspaces.** A remote request may
   name a repository source and revision, not a laptop path, network share, or
   arbitrary server path. The server creates and owns the resolved worktree.
4. **The laptop owns final landing.** Remote approval approves the server's
   proposed result. Applying a patch or writing deliverables into a laptop
   checkout is a separate, local, explicitly confirmed action.
5. **No server push by default.** Remote runs do not push branches, open pull
   requests, deploy, or modify the source remote unless a later explicitly
   approved workflow introduces a separate integration with its own authority.
6. **The host protocol remains vendor-neutral.** OpenCode gets the best
   packaged experience, but every remote action must be expressible via the
   HTTPS host protocol. MCP is an optional adapter, not the only client path.
7. **Existing local behavior remains compatible.** `product-factory host …`,
   stdio MCP, local OpenCode CLI transport, and local `repository_path` retain
   their current semantics. Remote mode is opt-in and must never reinterpret a
   local run.
8. **Remote full-content capture is prohibited by default.** `redacted` is the
   maximum default capture level. Chain-of-thought is never stored or exposed.

---

## 4. Target architecture

```text
┌──────────────────────── Laptop / existing host ────────────────────────┐
│ OpenCode plugin        Other CLI / CI             Dashboard browser     │
│  RemotePfClient        HTTP client / optional MCP  HTTPS / tunnel        │
│       │                      │                       │                  │
│       └──────────────────────┴───────────────────────┘                  │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ private HTTPS, bearer/device identity
                                │ REST + SSE (cursor resumable)
┌───────────────────────────────▼── AMD server ──────────────────────────┐
│ authenticated ingress / reverse proxy                                    │
│   └─ Product Factory control + observability service                     │
│       ├─ HostService: host/v1 protocol, policy, approvals                │
│       ├─ workspace manager: clone/import → immutable source → worktree   │
│       ├─ worker supervisor: durable queue, leases, recovery              │
│       ├─ tool/connector broker + sandbox                                 │
│       ├─ local-first model router ─────► local OpenAI-compatible runtime │
│       │                                  └► cloud fallback by policy     │
│       └─ SQLite + run/artifact/content store + SSE/dashboard             │
│                                                                         │
│ server workspace root (not exposed as a client filesystem)              │
└─────────────────────────────────────────────────────────────────────────┘

Result delivery back to the laptop:
  server change/artifact bundle + manifest + hashes + base revision
       → local LandingAdapter verifies and applies only after user approval
```

### 4.1 Control plane

The FastAPI service becomes the remote control plane but must keep the current
routes and envelopes compatible. It is responsible for authentication,
authorization, request validation, durable state, and subscription URLs. It
does not become a backend-for-frontend and it does not receive the full host
chat transcript.

Every remote response continues to include the `product-factory.host/v1`
envelope. It should include enough location-neutral data for a host to follow a
run without shelling into the server:

- `run_id`, status, plan summary, and artifact/delivery metadata;
- a cursor-resumable subscription URL based on the configured public base URL;
- server-provided `execution_mode: "remote"` and non-sensitive workspace
  identity, never the raw server filesystem path;
- an explicit next-action hint (`wait`, `review`, `approve`, `decline`, or
  `download_and_land`) derived from durable status, not from client guesses.

The existing dashboard remains monitor-only. At first, access it through the
same private tunnel or proxy. Do not make the dashboard public just because the
host API becomes remote-capable.

### 4.2 Workspace and source model

Remote execution needs a typed workspace source in place of a client path.
The following conceptual request shape is the target; exact field names may
vary, but the semantic distinction is required.

```json
{
  "request_text": "curated task request",
  "workflow_type": "repository_change",
  "workspace": {
    "kind": "git_ref",
    "repository_id": "github:my-org/my-repo",
    "revision": "40-character-commit",
    "ref": "optional-readable-branch-name"
  },
  "model_profile_set": "local-first",
  "budget_usd": 3.0
}
```

The initial supported kinds should be deliberately small:

| Kind | Meaning | Security/property |
| --- | --- | --- |
| `git_ref` | Clone/fetch a registered repository and resolve a pinned commit. | Preferred default; server credential is looked up by `repository_id`, never sent in the request. |
| `uploaded_git_bundle` | A content-addressed Git bundle uploaded through a bounded authenticated upload flow, then pinned by SHA-256. | Supports unpushed laptop work without granting remote laptop filesystem access. |
| `server_workspace` | A pre-registered immutable server checkout/version. | Administrative/CI use only; clients name an ID, never a raw path. |

Do not initially support SSHFS, NFS, arbitrary `file://` URLs, arbitrary Git
URLs with embedded credentials, or a generic local-agent filesystem proxy.
They blur the authority boundary and make results non-reproducible.

The workspace manager must record the canonical repository identity, source
revision, source bundle hash where relevant, resolved commit, and worktree
content identity in the run manifest. Worker tools receive only the resulting
server worktree. A later remote run must reproduce the same source even if the
laptop changed meanwhile.

### 4.3 Delivery and local landing

The current `materialize` action remains useful for a local deployment and for
server-owned destinations, but it cannot mean “write to the user's laptop” in
remote mode. Do not disguise a server-side copy as a successful laptop merge.

For every remote run that can produce a user-facing result, produce a delivery
manifest containing:

- run ID, workflow/pack version, source repository identity, and base commit;
- validation/review status and approval decision;
- a content-addressed patch (for code changes) and/or named artifact blobs;
- each blob's SHA-256, media type, byte size, logical name, and suggested
  repository-relative destination;
- artifact-land-map roles and an explicitly bounded set of safe local paths;
- cost/usage summary and evidence-bundle reference.

The local host integration owns a `LandingAdapter`:

1. retrieve the delivery manifest and blobs over authenticated HTTPS;
2. verify hashes, media types, path confinement, and the target Git base;
3. show the operator the exact patch/files and request the existing explicit
   confirmation;
4. apply a patch using a local Git-aware operation, or atomically write only
   manifest-approved artifacts below the local workspace root;
5. report a local landing receipt (target revision/file hashes) to the server
   as an audit event, without exposing arbitrary local files.

If the local base commit differs from the delivery manifest, landing fails
closed with a typed conflict result. Automatic three-way merge, force apply,
or arbitrary overwrite is out of scope for the first version. The user can
rebase, ask for a new remote run, or explicitly choose a later conflict-aware
workflow.

Remote `approve` must not include the legacy `apply=true` behavior. It approves
the result for delivery. The plugin must use `LandingAdapter` for the local
apply step. This distinction is central to retaining the current confirmation
and path-security guarantees.

### 4.4 Worker lifecycle and persistence

On a laptop, detached subprocesses are sufficient for an MVP. On a server,
they need a durable supervisor boundary:

- submission writes an idempotent queued job before scheduling work;
- a worker acquires a database-backed lease with owner ID, attempt, expiry,
  heartbeat, and cancellation flag;
- a worker reconnects/resumes an eligible run using the existing durable resume
  contracts; a lost lease is detected and surfaced rather than silently
  duplicate-executed;
- server restart scans pending/expired leases and resumes or marks them with a
  typed recovery state according to policy;
- concurrency is configured centrally and model capacity is a scheduling
  input; only one task holds a given worktree write lease;
- worker stdout/stderr remains per-run diagnostic data, while events and
  projections remain the authoritative operator surface.

For the first server, SQLite in WAL mode is still an appropriate state store
when the API and worker supervisor share one durable local volume. Do not split
writers across hosts. A later multi-node worker topology requires a deliberate
database/queue design and is not an incidental scale-out of this phase.

### 4.5 Model and connector locality

The orchestration process should call the local AMD model runtime over a
loopback/private network endpoint. Add an `OpenAICompatibleGateway` (or
equivalent provider-neutral adapter) rather than teaching planners or workers
about a particular model runtime. It must support the canonical message,
structured-output, tool-calling, retry, cancellation, and instrumentation
contracts already used by `ModelGateway`.

Routing must record whether a call was local or cloud, the resolved model,
model-runtime health/capability decision, tokens, latency, and cost basis. A
cloud fallback is permitted only when the selected policy says so and must
surface its reason (for example: capability gap, local unavailability,
context limit, or bounded retries exhausted). Server-side credentials and
connector credentials stay on the server.

Local filesystem MCP connectors now refer to server-local configured roots, not
to the laptop workspace. The server must continue to require explicit roots,
tool allowlists, and broker grants. A remote client may submit curated text or
an uploaded bundle; it does not turn its filesystem into a worker connector.

### 4.6 Identity, ingress, and content security

The minimum production-like topology is a private network plus TLS. Recommended
layers are:

1. VPN/tunnel identity (Tailscale, WireGuard, or SSH) for the first deployment;
2. TLS termination at a reverse proxy when using an HTTPS endpoint;
3. a per-user/device credential, short-lived where practical, passed as a
   bearer token or validated by the proxy;
4. server-side authorization that distinguishes read/control/delivery-upload
   operations and never trusts a forwarded client IP unless the proxy is
   explicitly trusted;
5. server-side secret references for Git and cloud credentials, not credential
   strings in requests, events, run directories, or model prompts.

The current `PRODUCT_FACTORY_OBSERVE_TOKEN` provides a useful initial control
token, especially over a tunnel. Before supporting a non-loopback bind behind
a proxy, make trusted-proxy behavior explicit and test it; otherwise
`request.client` may be the proxy rather than the real client. CORS is needed
only for browser clients; the OpenCode plugin's Node HTTP client does not need
CORS permission.

Content controls remain authoritative at the API boundary:

- default to `redacted`; do not enable `full` capture merely for remote
  debugging;
- preserve run-scoped hash ownership checks for artifacts/content;
- never expose server worktree paths in normal API responses or client logs;
- enforce bounded upload size/type/hash before a Git bundle enters the server;
- rate-limit/control concurrent submissions and failed authentication attempts;
- keep model/control endpoints private even if the dashboard is reachable.

### 4.7 Implementation ownership and durable records

Keep the new concerns separate from the coordinator's workflow logic. The
following module boundaries are intended to guide implementation; exact names
may change, but their ownership must not blur.

| Concern | Existing starting point | Remote responsibility |
| --- | --- | --- |
| Host protocol | `host/protocol.py`, `host/service.py`, `api/control.py` | Preserve host/v1 envelopes; validate remote mode and expose delivery information. |
| API/auth/streaming | `api/app.py`, `api/auth.py`, `api/routes.py`, `api/streaming.py` | Canonical external URL, scopes, trusted ingress, uploads, delivery reads, and cursor-resumable SSE. |
| Workspace lifecycle | coordinator/worktree helpers | New workspace manager owns source resolution, clone/import validation, worktree leases, cleanup, and provenance. |
| Worker lifecycle | `HostService._spawn_worker`, coordinator resume contracts | New supervisor owns queue/lease/heartbeat/recovery; it invokes the ordinary coordinator rather than reimplementing it. |
| Delivery/local landing | `host/service.py` materialization and OpenCode plugin merge handler | New delivery builder is server-side; shared local `LandingAdapter` is client-side. Do not add laptop writes to `HostService`. |
| Client transport | OpenCode `PfClient` / `CliPfClient` | `RemotePfClient` and a reusable CLI/SDK implementation share HTTP serialization and protocol checks. |
| Model routing | `gateway/`, scheduler/profile policy | New local OpenAI-compatible adapter and health/capability policy; no model-runtime branches in host clients. |
| Observability | recorder/query/dashboard | Emit source-import, lease, routing, delivery, and local-receipt events; projections remain authoritative. |

Add durable records rather than storing these facts only as ad hoc event
payloads:

- `workspace_sources` / run source fields: kind, repository ID, resolved
  revision, import hash, source size, and non-secret credential-reference ID;
- `worker_leases`: run/task, owner ID, attempt, acquired/heartbeat/expiry,
  recovery outcome, and cancellation observation;
- `delivery_manifests`: immutable manifest digest, run/base revision, blob
  references, validation/approval snapshot, and creation time;
- `landing_receipts`: delivery digest, client identity, reported local base and
  resulting hashes, timestamp, and typed result. This is evidence, not a
  server-side command to write a client path.

Do not overload run business status with laptop delivery state. A run can be
`completed` or `awaiting_approval` while it has no delivery, a delivery ready
for download, a delivery rejected locally, or a successfully landed receipt.
Those are related durable records/events shown by projections, not ambiguous
alternate meanings of `completed`.

---

## 5. Host integration design

### 5.1 OpenCode: primary packaged experience

The plugin remains a thin host adapter. It should gain a `RemotePfClient`
implementation of the existing `PfClient` interface and select it when, for
example, `PRODUCT_FACTORY_REMOTE_URL` is configured. The CLI client remains
the default when that setting is absent.

`RemotePfClient` maps the familiar tools onto HTTP without changing the
model-facing tool names or requiring slash commands:

| OpenCode tool | Remote behavior |
| --- | --- |
| `pf_run` | Build a typed workspace-source submission; return run ID and durable subscription information. |
| `pf_wait` | Poll status or consume cursor-resumable SSE within bounded host-tool execution time. |
| `pf_review` | Read run/task/validation/artifact/delivery projections; summarize evidence without downloading unavailable captured content. |
| `pf_merge` | Request explicit confirmation, approve remote delivery if required, then use `LandingAdapter` locally. |
| `pf_decline` | Reject an awaiting-approval delivery or cooperatively cancel a running server job. |

The plugin must never silently fall back from remote to local mode, and must
display the selected endpoint/workspace mode in its result. It must reject a
remote `repository_path` supplied by a model. For a normal OpenCode workspace,
the plugin determines a pinned local Git commit and submits a `git_ref` when
the server has that repository registered; it offers a bounded bundle-upload
path when the requested work includes unpushed local changes.

### 5.2 Other CLIs, automation, and MCP

The reusable client contract is HTTP host/v1 plus SSE, not the OpenCode plugin.
Other integrations can:

- call the HTTPS control API directly from scripts/CI;
- use a small vendor-neutral `pf remote` CLI client that emits the same JSON
  envelopes as local `product-factory host` commands;
- use a future remote Streamable HTTP MCP adapter when their host only supports
  remote MCP; or
- initially start stdio MCP through SSH for a trusted operator, recognizing
  that its workspace must still be server-side.

The remote MCP adapter, if built, exposes the same narrow tools as the stdio
adapter and uses the same `RemotePfClient`/control service. It does not
duplicate the OpenCode plugin's local landing behavior. A host without a local
plugin can download a delivery bundle and invoke the generic local landing CLI
after explicit confirmation.

### 5.3 API compatibility and additions

Keep current local control endpoints compatible. Add remote semantics through
new optional fields and endpoints rather than changing the meaning of an
existing local `repository_path` request.

Required additions include:

| Capability | Contract direction |
| --- | --- |
| Server capability discovery | `GET /api/v1/meta` advertises remote mode, supported workspace source kinds, delivery support, protocol/API version, and canonical subscription base. |
| Typed workspace submission | `POST /api/v1/runs` accepts `workspace` in remote mode; raw client `repository_path` is rejected for remote requests. |
| Bounded bundle import | Preflight/upload/finalize endpoints with declared size, digest, and media type. Uploads are untrusted until server validation succeeds. |
| Delivery manifest | Read-only run-scoped endpoint returning only approved/authorized output hashes, base revision, and safe local path suggestions. |
| Artifact download | Run-scoped, hash-verified blob endpoint with correct content disposition; no browser/client filesystem paths. |
| Landing receipt | Authenticated, append-only event/record that proves only the claimed local landing result; it cannot broaden a grant or cause a server write. |
| Worker health | Read-only service/queue/model-capacity projections distinct from individual run status. |

All mutations remain idempotent through caller-provided request IDs. Preserve
the current at-least-once SSE model; clients deduplicate by event ID/sequence
and reconnect using `after_seq`.

---

## 6. Delivery work packages

Each work package should deliver independently testable value. Do not start a
later package by weakening the approval, artifact ownership, or local-mode
contracts from an earlier one.

### R0 — Private remote proof and deployment boundary

Deliver a documented single-server deployment reachable only through an SSH
tunnel or private VPN. Run the control/observer service under a service manager
with a persistent data volume and configured canonical external URL. Confirm
that dashboard/API/SSE work through the tunnel with a control token.

Value: a real laptop can observe and control a server-hosted mock run without
opening a public port or changing workspace semantics.

Exit criteria:

- service restart preserves run/event/artifact data;
- subscription URLs work from the laptop and reconnect by cursor;
- unauthenticated and non-tunnel access are refused;
- no server filesystem path appears in a normal host response.

### R1 — Remote host transport and OpenCode read/control loop

Implement `RemotePfClient` and endpoint/token configuration in the OpenCode
plugin. It must use the existing host/v1 envelope and remote HTTP routes, while
leaving `CliPfClient` unchanged. Implement a small transport-neutral client
library or CLI wrapper so non-OpenCode hosts are not forced to copy plugin
logic.

Initially support plan/investigation runs with a server-registered repository
or no repository, plus status, review, cancel/reject, and SSE/poll fallback.
Do not claim local merge support yet.

Value: OpenCode uses its normal plugin tools against the remote service with no
slash commands; other clients can perform the same run lifecycle over HTTP.

Exit criteria:

- OpenCode `pf_run` → `pf_wait` → `pf_review` works across the network;
- CLI and HTTP clients receive equivalent host/v1 envelopes;
- unknown/mismatched protocol versions fail clearly;
- disconnect during SSE resumes without missed or duplicated terminal state;
- a configured remote endpoint never falls back to a local CLI/data root.

### R2 — Reproducible remote workspaces

Implement the workspace manager, typed `git_ref` source, server repository
registry/credential references, pinned revision resolution, server worktree
creation, and manifest provenance. Add bounded `uploaded_git_bundle` support
only after the pinned-clone path is proven.

Value: repository-change workflows execute safely next to the models on an
auditable source snapshot instead of depending on a laptop path.

Exit criteria:

- a remote code-change run records repository identity and exact base commit;
- the server rejects raw local paths, unsafe URLs, unpinned revisions, and
  unregistered credential references;
- a duplicate request ID does not create a second run/worktree;
- unpushed changes delivered through a bundle reproduce the declared commit and
  bundle hash;
- tool sandbox tests show a worker cannot escape its server worktree.

### R3 — Delivery bundle and local landing

Implement delivery manifests, run-scoped blob download, and a generic local
`LandingAdapter`; integrate it with OpenCode's existing `pf_merge` confirmation
path. Preserve server-local `materialize` for local deployments, but make
remote `pf_merge` use delivery + local landing only.

Value: a remote code change or document can be safely landed in the laptop
workspace without giving the server laptop filesystem authority.

Exit criteria:

- patch and multi-document deliveries verify all hashes before write;
- destination escape, changed base revision, missing blob, and digest mismatch
  fail closed without modifying the laptop worktree;
- no apply/write happens when the operator declines or confirmation is absent;
- a successful landing records local receipt and retains the server evidence;
- the same delivery can be consumed by OpenCode and a generic CLI.

### R4 — Server worker supervision and local-model execution

Replace detached-process-only scheduling with a supervised leased worker model.
Add the real OpenAI-compatible local gateway adapter, model health/capability
probes, local/cloud routing policy, and server-owned secret handling.

Value: the server can recover from restart, use the AMD-hosted models by
default, and escalate to cloud only under visible policy.

Exit criteria:

- an interrupted leased run resumes or reaches a typed recoverable state;
- duplicate workers cannot run the same task/worktree concurrently;
- local-model and cloud-fallback calls have correct provider/model/latency/
  cost-basis observability;
- cloud escalation records an allowed reason and respects the global budget;
- server restart and model-runtime outage are covered by integration tests.

### R5 — Hardening, remote MCP, and operator readiness

Harden ingress, uploads, rate limits, audit records, backup/restore, and
operator documentation. Add Streamable HTTP MCP only when it enables a real
non-OpenCode host; it is not required for the OpenCode plugin path.

Value: the remote installation is operable, diagnosable, and usable from other
CLI ecosystems without a bespoke integration per client.

Exit criteria:

- authenticated remote MCP (if implemented) has contract parity with the HTTP
  host operations and no second state/approval path;
- security tests cover proxy/header spoofing, token failures, cross-run blob
  access, hostile upload archives, and delivery path escapes;
- backup/restore recovers a completed run and evidence bundle;
- an operator guide covers tunnel/VPN, service health, model health, recovery,
  safe upgrade, and incident revoke/rotate procedures.

---

## 7. Verification strategy

Use real network boundaries in integration tests. In-process FastAPI tests are
necessary but do not prove proxy URLs, authentication, SSE buffering, or client
landing behavior.

### Contract and unit tests

- HTTP and CLI responses remain host/v1-compatible for local mode.
- `RemotePfClient` maps every existing action and preserves failure envelopes.
- workspace-source validation rejects all unsupported/mixed source forms.
- delivery-manifest transformation, hash verification, status mapping, and
  local path validation have deterministic unit tests.
- model routing tests cover local success, local capability mismatch, allowed
  cloud fallback, denied fallback, and budget rejection.

### Integration tests

- start the real server against a temporary data volume and call it through a
  TLS-capable test proxy or tunnel-equivalent;
- submit a Git fixture at a pinned revision, execute a mock code change, and
  retrieve the delivery bundle from a separate simulated laptop process;
- demonstrate SSE disconnect/reconnect using `after_seq`, including an event
  emitted during reconnect;
- restart the API/worker supervisor during queued and active work, then verify
  lease recovery semantics;
- run an OpenCode plugin smoke with `RemotePfClient` and a mock `ask`, proving
  that no slash command or local PF server is required;
- run a generic CLI client smoke against the same remote run and delivery.

### Security and failure tests

- bearer/device credential absent, invalid, expired, and wrong-scope;
- untrusted `X-Forwarded-*` headers and a trusted-proxy configuration;
- client-supplied `/Users/...`, `C:\\...`, `..`, symlink, raw server path, and
  URL-with-credentials workspace attempts;
- oversized/truncated/corrupt bundle uploads and archive path traversal;
- cross-run artifact/content/delivery hash access;
- changed local base commit, malicious artifact path, local write failure, and
  confirmation denial during landing;
- server model outage, connector outage, worker crash, server restart, and
  duplicate submit delivery.

### Operational acceptance targets

For a healthy private LAN/VPN deployment, target:

- submit acknowledgement within two seconds excluding source import;
- a newly persisted event visible to a subscribed client within two seconds at
  p95;
- bounded exponential reconnect with a visible stale state and no silent event
  loss after cursor recovery;
- every remote run traceable to a source identity/revision, worker attempt,
  model route, validation evidence, and delivery manifest;
- zero laptop filesystem writes without an explicit local confirmation.

---

## 8. Migration and compatibility rules

1. Do not remove the local CLI, stdio MCP, local dashboard behavior, or existing
   `repository_path` contract while adding remote mode.
2. Gate remote mode by explicit endpoint/configuration and advertise it via the
   metadata/capabilities endpoint. Never infer remote mode from a hostname or
   a path string.
3. Maintain `product-factory.host/v1` compatibility. Add optional fields; use
   a new protocol/API version only for a genuinely incompatible semantic change.
4. Preserve current run/artifact/content ownership and capture-policy rules.
   A delivery endpoint is not permission to fetch every stored prompt or tool
   body.
5. Keep server-local materialization and laptop-local landing distinct in code,
   UI text, audit events, and test names. Ambiguous “merge succeeded” messages
   are a correctness bug.
6. Keep local and remote benchmark results distinguishable. Record workspace
   import time, network transfer time, local-model throughput, and cloud spend
   so deployment overhead is not mistaken for orchestration quality.

---

## 9. Explicit non-goals for this handover

- Public hosted service or multi-tenant control plane.
- Arbitrary execution on a laptop or exposing a laptop filesystem MCP server to
  the remote worker.
- Automatic remote Git push, pull request creation, deployment, or secrets
  rotation.
- A browser dashboard that can approve, apply, or otherwise mutate runs.
- Replacing native OpenCode tools, skills, sessions, or user experience.
- Making Streamable HTTP MCP a prerequisite for remote OpenCode support.
- Multi-server distributed scheduling or replacing SQLite before the one-server
  topology has been proven.

---

## 10. Completion definition

Remote orchestration is ready for normal single-operator use when an operator
on a laptop can use OpenCode's existing plugin tools—without slash commands—to
submit a repository task to the AMD server, observe a durable remote run,
inspect its plan/evidence/cost, explicitly approve it, and safely land the
verified result into the local workspace. The same remote run must be
controllable by a vendor-neutral HTTP/CLI client, and the server must never
receive implicit laptop filesystem authority or bypass the existing budget,
tool, validation, capture, and approval policies.
