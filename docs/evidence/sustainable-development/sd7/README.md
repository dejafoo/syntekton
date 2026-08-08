# SD7 — Simplification / governance (evidence)

**Branch:** `sd/sd7-sd8-simplify`  
**Base:** `sd/sd6-evaluation` @ `fc9338faedef5f9ab9b3fbfe503b4ea2df19c13d`  
**Gate:** post-G4 simplification (G4 operational still deferred upstream)

## Removals / verifications

| Surface | Action | Replacement | Compatibility evidence |
| --- | --- | --- | --- |
| LangGraph demo (`graph.py`/`state.py`/`--graph-demo`) + deps | **Removed** | `RunLifecycleEngine` / HostService resume | ADR-001 superseded; `tests/unit/test_sd7_simplification.py` |
| WebSocket `/api/v1/events/ws` | **Verified absent** | SSE | SD0 tests + SD7 source-absence check |
| `completed (stub)` | **Verified absent** | Honest executor terminals | SD1 + SD7 source-absence check |
| `project_profile` | **Removed** from `RunRequest` | none (never authoritative) | SD7 unit test; still listed in host/v2 rejected fields |
| `model_profile_set` / `requested_artifacts` | **Strict deprecation** | capability routing / `artifact_overrides` | host/v2 reject; lifecycle deprecation event; land-map alias test |
| `config/workflows.yaml` | **Retained non-authoritative** | `workflows.registry` | loader docstring + SD7 pack-authority test |
| JSONL as protocol event authority | **Demoted** | SQLite EventStore via HostService | `_events_from_jsonl` removed; contract source ∈ {sqlite,observe} |
| Eval dual-write (`evaluation_runs`) | **Retained** | pending export/reader | See [eval-dual-write-retention.md](eval-dual-write-retention.md) |
| “Live staging” naming | **Renamed** | `simulated_staging` / `simulated-local` | legacy config/target aliases; connector tests |

## Governance docs

- `SECURITY.md`
- `CONTRIBUTING.md`
- `docs/governance/licensing-decision.md` (decision only — no LICENSE file)
- `docs/governance/release-changelog-policy.md`
- Registry catalogs: `docs/catalogs/` via `scripts/generate_registry_catalogs.py`

## Placement note

```text
Concern: policy | protocol | persistence
Owning boundary: host/service, connectors/deploy, registry/catalogs, docs/governance
Authoritative source: SQLite EventStore; workflow/capability/connector registries
Compatibility: host/v1 deprecated fields retained; staging_deploy config alias; eval dual-write retained
Guardrail proof: tests/unit/test_sd7_simplification.py + focused SD0/SD1 absence checks
Temporary exception: none; G4 operational AMD proof remains deferred (SD6)
```

## Hermetic proof

See archived pytest outputs in this directory after verification runs.
