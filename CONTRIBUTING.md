# Contributing

## Before you change code

1. Read `AGENTS.md` and the matching `.cursor/skills/*` entry for the concern
   you are touching (orchestration, registry, trust, durability, host contract).
2. Treat `docs/handover_sustainable_development.md` and the SD0–SD8 playbooks as
   the target architecture. Do not grow `RunCoordinator` or add named
   workflow/capability branches in shared runtime code.
3. Prefer characterization tests → failing guardrail → implementation → hermetic
   proof. Do not return success for unimplemented work.

## Development setup

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
uv run pytest -q -m "not integration"
```

Use `scripts/verify.sh` before release-facing changes.

## Pull requests

Every substantial PR should include the placement note from `AGENTS.md`:

```text
Concern: <lifecycle | executor | policy | persistence | protocol | UI>
Owning boundary: <service/module>
Authoritative source: <registry/durable record>
Compatibility: <none or migration/version/rollback>
Guardrail proof: <test path and result>
Temporary exception: <none or ADR/removal issue>
```

Link hermetic evidence under `docs/evidence/sustainable-development/<package>/`
when closing an SD work package.

## Releases and changelog

- Version bumps and release notes belong with SD5 release engineering.
- Prefer append-only changelog notes that state compatibility impact
  (migration required, host/v1 vs host/v2, client regeneration).
- Do not claim live/AMD performance or evaluation promotion without operational
  evidence (see SD6 / SD8 honesty rules).

## Licensing

Do not add a repository `LICENSE` file without product/legal approval. See
`docs/governance/licensing-decision.md` for the open decision record.
