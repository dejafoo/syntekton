# SR7 — Compatibility inventory

**Status:** inventory only (first SR7.A slice); no host/v1 removal  
**Depends on:** post-G5 for actual removals; this document may be maintained earlier  
**Related:** [next-work-packages-sustainable-remediation.md](../next-work-packages-sustainable-remediation.md) §11  
**Performance:** SD8 baselines are **baseline-only** — see
[`docs/evidence/sustainable-development/sd8/`](../evidence/sustainable-development/sd8/).
No SR7 optimization claims until SR6/G5 measurements and before/after evidence
exist.

## Purpose

List remaining compatibility debt with an owner, removal condition, and evidence
needed before deletion. Every deletion still requires a compatibility test plus
telemetry or repository-usage evidence (§11.1).

## Inventory

| Surface | Owner | Current state | Removal condition | Evidence needed |
| --- | --- | --- | --- | --- |
| **host/v1** (`product-factory.host/v1`, `/api/v1` mutation paths, MCP `HostResponse` envelopes) | host protocol / SR4 | Retained. Remote Python client defaults to v2 with explicit `protocol="v1"`; OpenCode and MCP remain principally v1 consumers; SSE/delivery still under `/api/v1`. | Primary clients on host/v2; durable v1 deprecation telemetry; written support decision if extended. **Do not remove in this slice.** | Client inventory; deprecation telemetry; OpenCode↔Python golden parity; compatibility + absence tests after cut. |
| **Workflow aliases** (`code_change` → `repository_change`, `architecture` → `technical_plan`) | `workflows.registry` | Ingress normalization only; accepted by host/v1 surfaces via `list_accepted_workflow_ids()`. | Host/v1 aliases retired or accepted only through an explicit v1 adapter; durable runs store canonical ids; no live alias callers. | Alias deprecation telemetry or repo-usage scan; host contract tests; migration of stored alias metadata if any. |
| **Deprecated request fields** (`model_profile_set`, `requested_artifacts`, mock/sync/inline/`handoff_refs` on v1) | domain `RunRequest` + host adapters | host/v2 rejects; host/v1 retains for compatibility; land-map still reads `requested_artifacts`. | v1 removal or written field-level support decision; typed overrides sole ingress. | Contract tests; client migration proof; deprecation events already emitted where lifecycle supports them. |
| **RunCoordinator persistence access** (`RunCoordinator.db` and related attribute passthrough) | orchestration façade | Thin façade over `RunLifecycleEngine`; exposes `self.db` for HostService/tests; `__getattr__` delegates to engine. | Callers use application services / UoW; HostService no longer reads `coord.db` for authority; monkeypatch surface retired. | Call-site inventory; HostService construction via composition root; guardrail that coordinator does not grow new persistence writes. |
| **Temporary lifecycle delegates** (`_execute_task` / `_build_execution_context` / research-prompt compat on engine; coordinator class-method delegates; compose callbacks `remove-coordinator-compose-callbacks-2026-08`; handoff resolve hooks “SD0.B temporary”) | lifecycle / SR2 | Sequencing still holds temporary ownership and monkeypatch delegates after SR2 extractions. | Named owners fully own behavior; tests stop monkeypatching private coordinator/engine methods; removal issues closed. | Import-boundary / AST guards; characterization suite green without private delegates; issue closure evidence. |
| **HostService.close drain hook** (`remove-host-drain-hook-2026-08`) | host / workers | Temporary graceful drain before `coord.db.close()`. | Dedicated lifecycle/shutdown owner; HostService is protocol-only for close. | Drain ownership tests; removal issue closed. |
| **Legacy evaluation dual writes** (`evaluation_scores` → `evaluation_runs`) | persistence evaluations | Retained per SD7; scores preferred, runs are mirror. | Verified export/reader for historical `evaluation_runs`; operator DB migration. | See [`docs/evidence/sustainable-development/sd7/eval-dual-write-retention.md`](../evidence/sustainable-development/sd7/eval-dual-write-retention.md); migration + dual-write absence test. |
| **Stale JSONL authority paths** | host events | Protocol authority is SQLite EventStore; `_events_from_jsonl` removed; per-run `events.jsonl` is optional diagnostic mirror only. | Confirm no remaining reader treats JSONL as authority; optional mirror may remain as non-authoritative. | Source-absence / contract `source ∈ {sqlite,observe}`; operator doc sync. |
| **Unused LangGraph / demo dependencies** | orchestration | **Already removed** in SD7 (`graph.py`/`state.py`/`--graph-demo`, `langgraph` deps). | N/A — keep absence tests. | `tests/unit/test_sd7_simplification.py`; ADR-001 superseded. |
| **Dead remote-client helpers** | `product_factory.remote` | No helper flagged safe-to-delete in this inventory pass; v1 path is explicit compatibility adapter. | Usage evidence that a helper has zero callers and is not part of the v1 adapter surface. | Import/call graph + compatibility test before delete. |
| **Connector / target aliases** (`staging_deploy` ↔ `simulated_staging`, target id aliases) | connectors/deploy | Retained config/target aliases after rename. | Config migrations complete; no legacy keys in operator configs. | Connector catalog + config alias tests; usage scan. |
| **Obsolete trackers and architectural claims** | docs / verification | Historical handovers and pre-SR trackers may overstate current authority (coordinator ownership, host/v1 as sole client contract, etc.). | Claims reconciled to current architecture docs + evidence levels; historical docs marked historical. | Doc audit against behavior; SR5 documentation truth package. |
| **Non-authoritative `config/workflows.yaml`** | config loader | Retained non-authoritative; registry is pack authority. | Delete only after confirming no operator tooling depends on the file as documentation. | Pack-authority test (existing SD7); usage decision. |
| **Pack identity optional deprecated fields** (`validation_policy` / `routing_defaults` on packs) | workflow packs | Optional for hash/shape stability; unused for decisions. | Hash migration plan if fields dropped. | Pack hash stability tests; removal decision. |

## SR7.B measurement note (non-claim)

Hotspots listed in §11.2 (safe inventory, prompt assembly, task preparation,
SQLite transactions, projections, model admission, worker concurrency, SSE,
validation) must be measured before and after any change.

Existing SD8 artifacts are **baselines only**:

- [`docs/evidence/sustainable-development/sd8/README.md`](../evidence/sustainable-development/sd8/README.md)
- [`docs/evidence/sustainable-development/sd8/baselines/synthetic-small-medium.json`](../evidence/sustainable-development/sd8/baselines/synthetic-small-medium.json)

They do **not** authorize AMD concurrency tuning, model/prompt/context changes,
or production performance wins. Model/prompt/context optimizations additionally
require re-running SR6 quality gates.

## Non-goals for this slice

- Removing host/v1 or workflow aliases
- Model or prompt tuning
- Speculative caching beyond already-accepted SD8 inventory-cache scaffolding
- Editing `.cursor/plans`
