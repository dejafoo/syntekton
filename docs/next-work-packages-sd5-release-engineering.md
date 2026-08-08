# SD5 — Reproducible builds and CI

**Status:** `[ ]` planned. **Gate:** G3 (jointly with SD3 and SD4). **Findings:** F-17, F-23, F-24.  
**Depends on:** G1 for truthful task behavior; may begin CI foundations in parallel with SD2/SD3 after G1.

## Outcome

Every released package is reproducibly built, installable, traceable, and covered by a proportionate PR/scheduled/live verification ladder. Pull requests never receive live environment secrets.

## Build determinism

- [ ] Commit `uv.lock` and require `uv sync --frozen` for supported development/CI paths.
- [ ] Use `npm ci` for dashboard and OpenCode plugin installs.
- [ ] Pin release base images by digest or record resolved image digests in provenance.
- [ ] Resolve the Starlette/httpx deprecation warning under the frozen set.
- [ ] Generate release hashes, SBOM, and build provenance.

## Required PR gate

- [ ] Python format, lint, type check, and non-integration tests.
- [ ] Dashboard tests, type check, and production build.
- [ ] OpenCode plugin tests, type check, and package build.
- [ ] OpenAPI/generated-client drift detection.
- [ ] Wheel build/install and packaged dashboard plus health smoke.
- [ ] Playwright coverage for blocked-task diagnosis, SSE refresh, repair lineage, capture policy, costs, and run-scoped content denial.

## Scheduled and environment-owned gates

- [ ] Scheduled: Docker remote restart/recovery, backup/restore, worker shutdown, connector timeout/truncation/reconciliation, and browser package smoke.
- [ ] Live environment-owned jobs produce scorecards; their secrets never enter pull-request logs, artifacts, or forks.
- [ ] Record image/dependency/provenance identities with each scheduled and release run.

## Test/acceptance design

Begin by making current installs and package smoke characterization tests explicit. Use hermetic fixtures for PR package tests and real built distributions in isolated environments for integration verification. Scheduled tests own destructive temporary data roots and prove their cleanup/recovery. A failed generated-client diff is a protocol review event, not a file to regenerate blindly.

G3 contribution is complete when frozen installs work, all first-party packages build and install from clean environments, expected browser/package checks run in CI, scheduled recovery/restore checks are stable, and releases have hashes/SBOM/provenance.

## Must not

Do not hide lock drift, fetch mutable dependencies in a release path without recording resolution, substitute source-tree execution for package smoke, or expose external credentials to PRs.
