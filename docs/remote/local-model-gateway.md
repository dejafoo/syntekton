# Local model gateway cutover

Product Factory routes model profiles by configuration. `coding_worker` and
`local_target_reviewer` are `route_class: local` with measured probes, a circuit
breaker, and a **named** cloud fallback profile. OpenRouter currently stands in
for the local OpenAI-compatible endpoint until hardware cutover. The host, API,
packs, and workers do not inspect the runtime type.

## Runtime contract

A replacement local runtime must:

- listen on a host-reachable loopback URL ending in `/v1`;
- implement `GET /v1/models` with `{"data": [{"id": "<configured model>"}]}`;
- implement `POST /v1/chat/completions` using OpenAI message, tool, and
  `response_format` fields;
- advertise the exact model ID configured in `config/models.yaml`;
- return OpenAI-style `choices`, `usage.prompt_tokens`, and
  `usage.completion_tokens` (usage may be absent; cost is then estimated);
- accept an optional bearer token named by `api_key_env` (omit
  `api_key_env` when the loopback endpoint has no authentication).

### Probes and admission (RF5)

The router does **not** treat a missing catalogue capability field as proof of
support. Local profiles run:

1. **Light probes** — reachability (`GET /models`), model identity, optional
   context advertisement, latency budget.
2. **Deep probes** (periodic) — structured-output and tool-call protocol checks
   against the underlying adapter (never through the router, to avoid recursion).

Role admission maps task capabilities (for example `implementation` → requires
proven `tool_calling`) via `gateway.admission`. Failures surface as
`capability_miss` or `local_unhealthy` and escalate only when
`cloud_fallback.allowed_reasons` permits them.

Circuit-breaker state opens after consecutive local provider failures and skips
the local endpoint until recovery timeout, so fallback budget is not spent on a
flapping runtime. Open circuits report `local_unhealthy`.

Measured snapshots are written under
`.product-factory/ops/local_route_admission/<profile>.json`
(`local_route_admission.v1`).

## Hardware cutover

For each local profile in `config/models.yaml`:

1. Change only `base_url` from `https://openrouter.ai/api/v1` to the local
   runtime, for example `http://127.0.0.1:8000/v1`, **or** set
   `PRODUCT_FACTORY_LOCAL_BASE_URL` (applies to every local
   `openai_compatible` profile).
2. Change `model` / `PRODUCT_FACTORY_LOCAL_MODEL` if the runtime advertises a
   different ID.
3. Remove `api_key_env`, or set `PRODUCT_FACTORY_LOCAL_API_KEY_ENV` to the
   environment variable holding the local runtime token.
4. Keep `provider_adapter: openai_compatible` and `route_class: local`.
5. Keep the named `cloud_fallback.profile` (for example `coding_worker_cloud`
   or `strong_reviewer`); do not silently reintroduce adapter-only fallback if
   you need independent cloud spend identity.

No host, API, CLI, workflow, pack, or worker change is part of the cutover.

## Fallback and budget policy

Cloud escalation is denied unless `cloud_fallback.enabled` is true and the
router's reason appears in `allowed_reasons`. A policy chooses exactly one
cloud `profile` or `adapter`. Prefer a named cloud **profile** so provider,
model, pricing, and telemetry remain independent of the local route. The
request cost ceiling is checked before the local call and again before
escalation; a zero or exhausted ceiling never falls through to cloud.

Set `PRODUCT_FACTORY_FORCE_MOCK=1` for deterministic CI and Docker execution.
The OpenRouter stand-in requires `OPENROUTER_API_KEY`.

### Opt-in live evaluation

```bash
PRODUCT_FACTORY_LOCAL_LIVE=1 \
PRODUCT_FACTORY_LOCAL_BASE_URL=http://127.0.0.1:8000/v1 \
PRODUCT_FACTORY_LOCAL_MODEL=<id> \
uv run pytest -m integration tests/integration/test_local_model_live.py
```

This profile is never required for hermetic unit CI.

## Telemetry

Completed model telemetry includes `route`, `provider`, resolved `model`,
`primary_profile`, `fallback_profile`, `fallback_reason`, end-to-end routing
`latency_ms`, circuit snapshot, proven admission capabilities, and whether
`cost_usd` is provider-reported or profile-estimated. Local cost and cloud
spend remain separable because fallback uses a distinct cloud profile.
