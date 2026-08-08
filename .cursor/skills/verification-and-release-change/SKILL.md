---
name: verification-and-release-change
description: Verify Product Factory test infrastructure and gate semantics, CI and scheduled jobs, browser coverage, packages, releases, generated artifacts, current-state documentation, trackers, and completion claims. Use whenever changing verification strategy, packaging, release engineering, documentation status, or the evidence used to declare work complete; ordinary tests accompanying a scoped code change do not trigger it alone.
---

# Verification and release change

Match every claim to the boundary that can prove it. Classify evidence as
`implemented`, `hermetically verified`, `integration verified`,
`operationally proven`, or `not verified` before editing a tracker or release
record.

## Rules

- A required gate must execute. Missing credentials, infrastructure, browser,
  or runtime is `unavailable` or deferred, never a passing soft skip.
- Unit tests prove in-process logic only. Use real process, browser, package,
  restart, restore, remote-client, or environment-owned tests for those claims.
- Build and install distributions outside the source tree; verify packaged
  dashboard/client assets and health independently of editable imports.
- Freeze dependency inputs and detect OpenAPI, generated-client, catalog, lock,
  and package drift.
- Keep current architecture, integration guidance, support policy, known
  limitations, and tracker status synchronized with behavior. Mark historical
  handovers as historical rather than rewriting their record.
- Produce release hashes, dependency/license evidence, SBOM, and provenance at
  the release boundary. Never expose secrets to pull-request jobs.

## Procedure and proof

1. State the exact claim and required evidence level.
2. Add a failing or characterization test at the matching boundary.
3. Implement the smallest change and run the canonical area-specific gate.
4. Run package/browser/process verification when the claim crosses that
   boundary.
5. Store or link immutable evidence with commit, command, fixture/environment,
   result, and limitations.

Reject completion when a required job is a placeholder, optional, skipped, or
tests only an implementation detail. Include the root `AGENTS.md` placement
note and identify any deferred operational proof explicitly.
