# Local model gateway cutover

Product Factory routes model profiles by configuration. `coding_worker` and
`local_target_reviewer` are `route_class: local`; OpenRouter currently stands
in for the local OpenAI-compatible endpoint. The host, API, packs, and workers
do not inspect the runtime type.

## Runtime contract

A replacement local runtime must:

- listen on a host-reachable loopback URL ending in `/v1`;
- implement `GET /v1/models` with `{"data": [{"id": "<configured model>"}]}`;
- implement `POST /v1/chat/completions` using OpenAI message, tool, and
  `response_format` fields;
- advertise the exact model ID configured in `config/models.yaml`;
- return OpenAI-style `choices`, `usage.prompt_tokens`, and
  `usage.completion_tokens`;
- accept an optional bearer token named by `api_key_env` (omit
  `api_key_env` when the loopback endpoint has no authentication).

The models response is the health/capability probe. An unreachable endpoint is
`local_unhealthy`; a missing model or missing advertised capabilities is
`capability_miss`.

## Hardware cutover

For each local profile in `config/models.yaml`:

1. Change only `base_url` from `https://openrouter.ai/api/v1` to the local
   runtime, for example `http://127.0.0.1:8000/v1`.
2. Change `model` if the runtime advertises a different ID.
3. Remove `api_key_env`, or set it to the environment variable holding the
   local runtime token.
4. Keep `provider_adapter: openai_compatible` and `route_class: local`.
5. Keep, narrow, or disable `cloud_fallback` according to deployment policy.

No host, API, CLI, workflow, pack, or worker change is part of the cutover.

## Fallback and budget policy

Cloud escalation is denied unless `cloud_fallback.enabled` is true and the
router's reason appears in `allowed_reasons`. A policy chooses exactly one
cloud `profile` or `adapter`. The request cost ceiling is checked before the
local call and again before escalation; a zero or exhausted ceiling never
falls through to cloud.

Set `PRODUCT_FACTORY_FORCE_MOCK=1` for deterministic CI and Docker execution.
The OpenRouter stand-in requires `OPENROUTER_API_KEY`.

Completed model telemetry includes `route`, `provider`, resolved `model`,
`fallback_reason`, end-to-end routing `latency_ms`, and whether `cost_usd` is
provider-reported or profile-estimated.
