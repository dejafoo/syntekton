# ADR-007 — Effective task policy and run-scoped artifact instances

## Status

Accepted (RF2 schema registered; RF3 capture path enforced)

## Context

The pre-PM5 hardening gate
([handover_post_mvp_refactoring.md](../handover_post_mvp_refactoring.md))
requires:

1. **One effective policy** — the grant enforced by the broker is the same
   object shown to the model and persisted in the task context manifest.
2. **Artifact ownership + capture authority** — content-addressed bytes may be
   deduplicated, but readability is run-scoped and capture-policy controlled.

Today (post-PM4):

- Prompt tool lists are built from `required_tool_classes` before grant
  narrowing in [`coordinator.py`](../src/product_factory/orchestration/coordinator.py).
- Stack profiles contribute digests, not a pinned rendered resource.
- [`ObservabilityQueryService.artifact_content`](../src/product_factory/observability/query.py)
  treats artifacts as full capture (`capture_level=None` → FULL), while model
  content refs honor capture level — so validation raw stdout can bypass policy
  via the artifact content route.

This ADR freezes the durable contracts for R2 and R3. Field names may be
renamed only if ownership and invariants stay equivalent.

## Decision

### 1. `EffectiveTaskPolicy` (R2)

Immutable snapshot resolved **before** `assemble_context`, persisted on the
task (JSON column or artifact + task pointer). Schema version:
`effective_task_policy.v1`.

| Field group | Contents |
| --- | --- |
| Identity | `schema_version`, `task_id`, `run_id`, `pack_id`, `pack_version`, `capability`, `executor_mode` |
| Grant | `allowed_tool_names: list[str]` (exact), `allowed_tool_classes`, `connector_decisions: {tool_or_connector_id: allow\|deny\|reason}`, `path_scopes`, `call_limits`, `result_limits`, `data_classification` |
| Prompt reduction | `prompt_tool_names: list[str]` ⊆ `allowed_tool_names`; `prompt_reduction_reason: str \| null` |
| Skills / profile | `skill_ids`, `profile_ids`, `reference_pack_ids`, `stack_profile_artifact_sha256` (nullable), `stack_profile_digest`, `stack_profile_schema_version` |
| Routing | `route_class`, `primary_model_profile`, `fallback_model_profile` (named cloud profile or null), `fallback_eligible: bool`, `budget_ceiling` snapshot |
| Validation / repair | `validator_ids`, `repair_eligible: bool`, `approval_required: bool` |

**Invariants**

- Broker authorization uses `allowed_tool_names` only.
- Canonical tool schemas and prompt tool descriptions use `prompt_tool_names`
  only; never advertise a tool outside `allowed_tool_names`.
- Dashboard and task manifest project this same object (or a documented subset).
- Missing snapshot on historical tasks → `legacy_unresolved`; do not reconstruct
  as if the exact grant were known.

**Sequencing change (R2 code)**

1. Resolve `EffectiveTaskPolicy`.
2. Persist it.
3. `assemble_context` / prompt build consume it.
4. Create `ToolBroker` grant from it.
5. On resume, reuse the pinned policy + stack-profile artifact identity; do not
   re-detect the repository for policy fields.

### 2. Stack profile resource (R2)

Persist a bounded rendered profile as a run artifact (role e.g.
`stack_profile`), including detector version, source input digests, confidence,
limitations, and profile schema version. Digest alone is insufficient for
resume reproducibility.

### 3. `ArtifactInstance` (R3)

Keep global content-addressed storage. Add a run-scoped relation (table
`artifact_instances` or equivalent):

| Column / field | Purpose |
| --- | --- |
| `instance_id` | Stable primary key |
| `run_id` | Owner run |
| `sha256` | Bytes key (may be shared across runs) |
| `role` | Logical role (plan, change_set, validation_evidence, …) |
| `content_class` | See matrix below |
| `producer_task_id` / `producer_tool` / `producer_validator` | Provenance |
| `event_seq` | Creation sequence |
| `media_type`, `schema_id`, `schema_version`, `size_bytes`, `display_name` | Display |
| `classification`, `capture_level`, `visibility` | Policy |
| `retention`, `truncated` metadata | Ops |
| `parent_instance_ids` / input refs | Lineage |

**Visibility enum:** `available` | `redacted` | `metadata_only` | `unavailable`

**API:** every hash content/download route requires `(run_id, sha256)` ownership
via an instance. Unknown and cross-run hashes both return indistinguishable
`404`. Response body shape:

- `available: true` + payload
- `available: false`, `reason: capture_off|metadata_only|expired|not_retained`
- `available: true`, `redacted: true` + stored redacted payload only

### 4. Content class × capture matrix (R3)

Canonical stored representation by capture level. “—” means do not retain a
recoverable body for that class at that level.

| content_class | `off` | `metadata` | `redacted` | `full` |
| --- | --- | --- | --- | --- |
| `durable_output` | metadata only | metadata + role | redacted durable | full durable |
| `normalized_evidence` | metadata only | metadata + summary fields | redacted report | full report |
| `raw_tool_capture` | — | metadata only | redacted raw | full raw |
| `raw_source_capture` | — | metadata only | redacted raw | full raw |
| `raw_validation_capture` | — | metadata only | redacted stdout/stderr | full raw |
| `model_capture` | — | metadata only | redacted prompt/response | full |

Rules:

- Apply at **write** and **read** time.
- Never keep a raw copy solely because another endpoint labels it an artifact.
- Validation **report** (`normalized_evidence` / `durable_output`) may remain
  available while `raw_validation_capture` is unavailable.
- Historical rows without classification → `legacy_unknown`; not auto-exposed
  as full.

### 5. Routing identity (R2, feeds R5)

On fallback, persist the **named cloud profile** actually used (provider, model,
pricing basis, data policy, reason). Do not relabel a cloud completion as the
original local profile. Unknown usage remains explicitly estimated/unknown.

## Consequences

- R2 implementation must not invent alternate grant sources after this freeze.
- R3 must migrate artifact APIs before claiming capture-policy closure.
- Additive DB/OpenAPI migrations only; old runs remain readable as legacy.
- R6 dashboard work consumes these projections; do not invent a second policy
  object for UI.

## Non-goals

- PackExecutionPolicy / executor catalogue (R4 ADR or plan).
- Real local AMD probes/circuit breakers — completed in RF5; see
  `docs/remote/local-model-gateway.md`. Dashboard projections for route
  identity remain RF6.
- Multi-tenant authn/z.

## Acceptance

Accepted when:

1. `effective_task_policy.v1` is registered and round-tripped in tests.
2. At least one task fixture shows grant = prompt tools ⊆ allowed = broker.
3. Artifact content routes refuse raw validation capture under `metadata`/`off`.
4. Cross-run hash access returns 404.
