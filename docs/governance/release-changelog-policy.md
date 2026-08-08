# Release and changelog policy (SD7)

## Versioning

- Python package version lives in `pyproject.toml`.
- Host protocol versions (`host/v1`, `host/v2`) are independent of package
  version; contract changes follow `host-contract-change` skill rules.

## Changelog expectations

For each release note entry, state:

1. User-visible behavior change (or “none”).
2. Compatibility impact (migration, OpenAPI regen, client bump).
3. Evidence level: hermetic / integration / operational.
4. Explicit non-claims (e.g. SD8 baselines are not AMD performance wins).

## Dependency / SBOM / secrets

- Lockfiles (`uv.lock`, npm lockfiles) are authoritative for CI.
- SBOM and provenance notes are produced under SD5 release engineering.
- Secret scanning: never commit `.env`, API keys, or live connector credentials.
  Connector config files may only reference environment variable names.

## Rollback

Schema and protocol changes must document rollback or forward-fix migration.
Removals require replacement + compatibility evidence (SD7 removal rule).
