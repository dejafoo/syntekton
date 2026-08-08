# SD7 — Simplification/governance and SD8 — measured performance

**Status:** `[~]` scaffolding complete on `sd/sd7-sd8-simplify` (SD8 = baselines only; G4 operational still deferred).  
**Dependencies:** G4 hermetic foundation (upstream); operational G4 deferred.  
**Findings:** SD7 owns F-03, F-05, F-13, F-21, F-22, F-25, F-26; SD8 addresses performance aspects of F-04, F-12, F-19, and F-20.  
**Order:** SD7 removes only proven-obsolete surfaces. SD8 optimizes only from SD6 measurements. Neither expands product scope.

## SD7 — Simplification and governance

### Removal rule

For each removal, record its replacement, active-client inventory, compatibility test, deprecation telemetry (where supported), retention/backup impact, and removal commit. Do not remove merely because a surface appears unused in source search.

- [x] LangGraph graph demo/state and unused dependencies.
- [x] WebSocket stream (normally completed in SD0; retain this item as removal verification).
- [x] Successful task stub (normally completed in SD1; retain verification).
- [x] Dead workflow configuration (`workflows.yaml` retained non-authoritative; registry owns packs).
- [x] Ignored routing/project fields (`project_profile` removed; `model_profile_set` deprecated).
- [x] Deprecated artifact request alias (`requested_artifacts` retained + deprecated; v2 rejects).
- [x] JSONL as a protocol-authoritative fallback.
- [~] Legacy evaluation dual writes and aliases — **retained** until export/reader exists ([evidence](evidence/sustainable-development/sd7/eval-dual-write-retention.md)).
- [x] Misleading “live staging” naming → `simulated_staging` / `simulated-local`.

Retain MCP, the OpenCode plugin, dashboard, SQLite, simulated staging adapter, and optional OpenTelemetry. Simulated staging must remain clearly marked as a fixture rather than a production integration.

### Documentation

- [x] Rewrite README and current architecture around the post-SD4 surfaces.
- [x] Generate workflow, capability, skill, connector, and model-route catalogs from trusted registries (`docs/catalogs/` — workflows/capabilities/connectors; skills/model-routes documented as registry projections where separate modules exist).
- [x] Archive completed trackers and mark superseded ADRs explicitly (ADR-001 superseded).
- [x] Add compatibility, support, operator, and known-limitations pages (operator notes + governance docs).

### Governance

- [x] Add `SECURITY.md`, `CONTRIBUTING.md`, release/changelog policy, dependency/license audit, secret scanning, and SBOM procedures (pointers to SD5 + governance docs).
- [x] Prepare a licensing decision document covering permissive, copyleft, open-core, and commercial implications.
- [x] Do not add a repository license until product/legal approval selects one.

### SD7 exit checklist

- [x] Each removed surface has replacement and compatibility/deprecation evidence.
- [x] Public docs/catalogs match generated registry/protocol facts.
- [x] Security, contribution, release, dependency/license, secret scanning, and SBOM procedures are actionable.
- [~] Package, upgrade/restore, client-compatibility, and source-absence tests pass (hermetic unit/contract; full package ladder deferred to CI).

## SD8 — Measured performance tuning

### Baseline and instrumentation

- [x] Establish p50/p95 baselines for small, medium, and monorepo fixtures. *(synthetic small/medium hermetic; monorepo deferred)*
- [x] Instrument plan compilation, inventory, prompt assembly, model queue/provider time, tools, validation, SQLite transactions, SSE delay, worker queue, and local saturation. *(MeasurementSession stages + glossary; call-site wiring is opt-in scaffolding)*
- [x] Publish per-run correlation identifiers and a measurement glossary so dashboard/API/scorecard metrics have the same meaning.

### Candidate improvements

- [x] Add reusable safe-inventory and stack-profile caching keyed by base revision plus policy digest. *(inventory cache + invalidation tests; stack-profile cache not claimed)*
- [ ] Optimize database transactions and projection queries from profiles. **Deferred — needs G4 operational arms.**
- [ ] Tune local concurrency against AMD memory and throughput measurements. **Deferred.**
- [ ] Tune context and model selection only while SD6 quality gates remain non-regressed. **Deferred.**
- [ ] Keep non-model orchestration overhead below 10% of typical end-to-end runtime when model latency makes that target meaningful. **Deferred.**

### Acceptance discipline

Every optimization has a pre-registered hypothesis, benchmark fixture/version, before/after p50/p95 and resource profile, correctness/policy regression suite, rollback, and scope statement. Re-run the affected SD6 arm when context/model/concurrency behavior could affect outcome quality. Report a no-change result when profiling does not support the optimization.

### SD8 exit checklist

- [~] Scaffolding + hermetic baselines complete; accepted micro-optimizations: inventory cache only (with invalidation safety). No AMD tuning claimed.
- [x] Cache keys include snapshot and policy digest; invalidation cannot expose prohibited or stale inventory data.
- [ ] Saturation/concurrency settings have measured AMD bounds and safe admission behavior. **Deferred pending G4.**
- [ ] Projection/SSE changes retain correctness and live-update expectations. *(no projection rewrite in this slice)*
- [x] Operator documentation describes metrics, limits, and rollback (SD8 evidence README honesty statement).

## Shared tests and constraints

SD7 owns source/dependency/documentation/catalog checks and compatibility removal tests. SD8 owns profiler/metric unit tests, cache safety tests, benchmark fixtures, and end-to-end non-regression runs. Neither package may use browser requests to trigger maintenance deletion, relax capture/classification/retention policy, introduce a second orchestration state store, or claim performance from a single unrepresentative run.

## Evidence record

```text
Implementation: sd/sd7-sd8-simplify (see docs/evidence/sustainable-development/sd7/ and sd8/)
Hermetic verification: uv run pytest -q -m "not integration" + tests/unit/test_sd7_simplification.py + tests/unit/test_sd8_performance.py
Integration verification: deferred (package/browser ladder not re-run as SD8 claim)
Operational proof: deferred — G4 AMD operational proof still required; SD8 tuning deferred
Compatibility/removal or rollback evidence: docs/evidence/sustainable-development/sd7/
Exceptions: eval dual-write retained; G4 operational deferred
```
