# SD7 operator notes

- Prefer `host/v2` request bodies; dead fields (`project_profile`,
  `model_profile_set`, `requested_artifacts`) are rejected there.
- Event tails use observe HTTP or SQLite only — not `events.jsonl`.
- Deployment fixture connector id is `simulated_staging` (config key
  `staging_deploy` still accepted). Target id `simulated-local` (alias:
  `staging-local`).
- Resume no longer supports `--graph-demo`.
- Licensing: see `docs/governance/licensing-decision.md` — no LICENSE file yet.
