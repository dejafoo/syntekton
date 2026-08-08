# SD7 — Simplification/governance and SD8 — measured performance

**Status:** `[ ]` planned. **Dependencies:** G4, and the replacement/deprecation evidence required below.  
**Findings:** SD7 owns F-03, F-05, F-13, F-21, F-22, F-25, F-26; SD8 addresses performance aspects of F-04, F-12, F-19, and F-20.  
**Order:** SD7 removes only proven-obsolete surfaces. SD8 optimizes only from SD6 measurements. Neither expands product scope.

## SD7 — Simplification and governance

### Removal rule

For each removal, record its replacement, active-client inventory, compatibility test, deprecation telemetry (where supported), retention/backup impact, and removal commit. Do not remove merely because a surface appears unused in source search.

- [ ] LangGraph graph demo/state and unused dependencies.
- [ ] WebSocket stream (normally completed in SD0; retain this item as removal verification).
- [ ] Successful task stub (normally completed in SD1; retain verification).
- [ ] Dead workflow configuration.
- [ ] Ignored routing/project fields.
- [ ] Deprecated artifact request alias.
- [ ] JSONL as a protocol-authoritative fallback.
- [ ] Legacy evaluation dual writes and aliases.
- [ ] Misleading “live staging” naming.

Retain MCP, the OpenCode plugin, dashboard, SQLite, simulated staging adapter, and optional OpenTelemetry. Simulated staging must remain clearly marked as a fixture rather than a production integration.

### Documentation

- [ ] Rewrite README and current architecture around the post-SD4 surfaces.
- [ ] Generate workflow, capability, skill, connector, and model-route catalogs from trusted registries.
- [ ] Archive completed trackers and mark superseded ADRs explicitly.
- [ ] Add compatibility, support, operator, and known-limitations pages.

### Governance

- [ ] Add `SECURITY.md`, `CONTRIBUTING.md`, release/changelog policy, dependency/license audit, secret scanning, and SBOM procedures.
- [ ] Prepare a licensing decision document covering permissive, copyleft, open-core, and commercial implications.
- [ ] Do not add a repository license until product/legal approval selects one.

### SD7 exit checklist

- [ ] Each removed surface has replacement and compatibility/deprecation evidence.
- [ ] Public docs/catalogs match generated registry/protocol facts.
- [ ] Security, contribution, release, dependency/license, secret scanning, and SBOM procedures are actionable.
- [ ] Package, upgrade/restore, client-compatibility, and source-absence tests pass.

## SD8 — Measured performance tuning

### Baseline and instrumentation

- [ ] Establish p50/p95 baselines for small, medium, and monorepo fixtures.
- [ ] Instrument plan compilation, inventory, prompt assembly, model queue/provider time, tools, validation, SQLite transactions, SSE delay, worker queue, and local saturation.
- [ ] Publish per-run correlation identifiers and a measurement glossary so dashboard/API/scorecard metrics have the same meaning.

### Candidate improvements

- [ ] Add reusable safe-inventory and stack-profile caching keyed by base revision plus policy digest.
- [ ] Optimize database transactions and projection queries from profiles.
- [ ] Tune local concurrency against AMD memory and throughput measurements.
- [ ] Tune context and model selection only while SD6 quality gates remain non-regressed.
- [ ] Keep non-model orchestration overhead below 10% of typical end-to-end runtime when model latency makes that target meaningful.

### Acceptance discipline

Every optimization has a pre-registered hypothesis, benchmark fixture/version, before/after p50/p95 and resource profile, correctness/policy regression suite, rollback, and scope statement. Re-run the affected SD6 arm when context/model/concurrency behavior could affect outcome quality. Report a no-change result when profiling does not support the optimization.

### SD8 exit checklist

- [ ] Each accepted change links before/after evidence and non-regressed SD6 outcome evidence.
- [ ] Cache keys include snapshot and policy digest; invalidation cannot expose prohibited or stale inventory data.
- [ ] Saturation/concurrency settings have measured AMD bounds and safe admission behavior.
- [ ] Projection/SSE changes retain correctness and live-update expectations.
- [ ] Operator documentation describes metrics, limits, and rollback.

## Shared tests and constraints

SD7 owns source/dependency/documentation/catalog checks and compatibility removal tests. SD8 owns profiler/metric unit tests, cache safety tests, benchmark fixtures, and end-to-end non-regression runs. Neither package may use browser requests to trigger maintenance deletion, relax capture/classification/retention policy, introduce a second orchestration state store, or claim performance from a single unrepresentative run.

## Evidence record

```text
Implementation: <PR/commit and design>
Hermetic verification: <commands and fixture IDs>
Integration verification: <package/browser/benchmark result>
Operational proof: <owned scorecard or maintenance evidence>
Compatibility/removal or rollback evidence: <link>
Exceptions: <none or approved exception with expiry>
```
