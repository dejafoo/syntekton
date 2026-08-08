# Security policy

## Scope

Product Factory is a single-user / private-network orchestration host with SQLite
durability. This document describes how to report vulnerabilities and the
security boundaries the project maintains.

## Reporting

Report suspected vulnerabilities privately to the repository maintainers
(`AUTHORS` / package metadata contact). Do not open a public issue for
exploitable defects until a fix or mitigating guidance is available.

Include: affected version/commit, reproduction steps, impact, and whether any
live credentials or customer data were involved.

## Trust boundaries (authoritative)

- Handoffs and action approvals are resolved from durable records, never from
  caller-shaped request fields alone.
- Repository inventory is confined (no symlink follow / path escape into prompt
  context).
- Remote live streams are authenticated; WebSocket live event routes are removed.
- Deployment effects require durable approval; the in-process
  `simulated_staging` connector is a fixture, not production deploy.
- Capture/classification/retention policy is not bypassed by clients or the
  monitor-only dashboard.

## Non-goals

This project does not currently claim multi-tenant isolation, public internet
hardening, or a production deployment control plane.

## Dependency and secret hygiene

- Prefer `uv sync --frozen` and `npm ci`.
- Never commit API keys; connector secrets stay in environment variables.
- Release engineering (SD5) owns SBOM / provenance notes under
  `docs/evidence/sustainable-development/sd5/`.
