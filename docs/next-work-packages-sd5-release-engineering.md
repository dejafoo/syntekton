# SD5 — Reproducible builds and CI

**Status:** `[~]` foundations landed on `sd/sd5-release-engineering` (G3 still open — needs SD3+SD4). **Gate:** G3 (jointly with SD3 and SD4). **Findings:** F-17, F-23, F-24.  
**Depends on:** G1 for truthful task behavior; may begin CI foundations in parallel with SD2/SD3 after G1.

## Outcome

Every released package is reproducibly built, installable, traceable, and covered by a proportionate PR/scheduled/live verification ladder. Pull requests never receive live environment secrets.

## Build determinism

- [x] Commit `uv.lock` and require `uv sync --frozen` for supported development/CI paths.
- [x] Use `npm ci` for dashboard and OpenCode plugin installs.
- [ ] Pin release base images by digest or record resolved image digests in provenance.
- [x] Resolve the Starlette/httpx deprecation warning under the frozen set.
- [ ] Generate release hashes, SBOM, and build provenance. *(process note in evidence; automation deferred)*

## Required PR gate

- [x] Python format, lint, type check, and non-integration tests.
- [x] Dashboard tests, type check, and production build.
- [x] OpenCode plugin tests, type check, and package build.
- [ ] OpenAPI/generated-client drift detection. *(deferred to SD4 — no host snapshots yet)*
- [x] Wheel build/install and packaged dashboard plus health smoke.
- [ ] Playwright coverage for blocked-task diagnosis, SSE refresh, repair lineage, capture policy, costs, and run-scoped content denial.

## Scheduled and environment-owned gates

- [~] Scheduled: Docker remote restart/recovery, backup/restore, worker shutdown, connector timeout/truncation/reconciliation, and browser package smoke. *(workflow stubs + hermetic backup; live soft-skip)*
- [x] Live environment-owned jobs produce scorecards; their secrets never enter pull-request logs, artifacts, or forks. *(PR workflows remain secret-free; scheduled stubs use no secrets)*
- [ ] Record image/dependency/provenance identities with each scheduled and release run.

## Test/acceptance design

Begin by making current installs and package smoke characterization tests explicit. Use hermetic fixtures for PR package tests and real built distributions in isolated environments for integration verification. Scheduled tests own destructive temporary data roots and prove their cleanup/recovery. A failed generated-client diff is a protocol review event, not a file to regenerate blindly.

G3 contribution is complete when frozen installs work, all first-party packages build and install from clean environments, expected browser/package checks run in CI, scheduled recovery/restore checks are stable, and releases have hashes/SBOM/provenance.

**Evidence:** [`docs/evidence/sustainable-development/sd5/`](evidence/sustainable-development/sd5/).

## Must not

Do not hide lock drift, fetch mutable dependencies in a release path without recording resolution, substitute source-tree execution for package smoke, or expose external credentials to PRs.
