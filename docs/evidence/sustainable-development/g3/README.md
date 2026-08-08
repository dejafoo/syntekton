# G3 — Platform joint verification (evidence)

**Branch:** `sd/g3-platform`  
**Gate:** G3 (SD3 durability + SD4 protocol/clients + SD5 release engineering)  
**Tip at evidence capture:** `bb911c73e657d228b0437fbd8ed7811a6987e87b`

## Merge SHAs

| Stream | Branch tip | Notes |
| --- | --- | --- |
| SD2 (base via SD4) | `f84346e5ca6e4fd40907c49db39f68098d4ae263` | Ancestor of SD4 |
| SD4 (integration base) | `134852d07cee399d4e3d65364e49502a957e3a20` | host/v2 + HostService |
| SD3 | `75f719f15263106a45c88c7f0a249fa2ecc790d7` | Merged first into g3-platform |
| SD5 | `5973cc5976f4b2b992dbfdf805c5030e19045ab6` | Merged second; OpenAPI drift wired |

Merge commits:
- 2f74db7 Merge sd/sd3-durability into sd/g3-platform (G3).
- bfebd59 Merge sd/sd5-release-engineering into sd/g3-platform (G3).

## Conflict resolution (ownership)

- **persistence / workers / retention:** preferred SD3 (repositories, SqliteActor, drain, backup/retention, ops maintain/pin/unpin).
- **host / API / CLI protocol:** preferred SD4 (HostService mutation path; ops help text keeps admin-not-run-semantics wording).
- **.github / uv.lock / verify.sh:** preferred SD5; added `scripts/check_openapi_drift.sh` to `scripts/verify.sh` and `.github/workflows/ci.yml`.
- **orchestration lifecycle:** kept SD2/SD4 tip; fixed G3 hygiene on `WorktreeLineageService.detect_conflicts` (dead wrong arity call) and unused `extract_unified_diff` import so basedpyright stays green.
- **tracker docs:** unioned SD2/SD3/SD4/SD5 checkboxes and evidence links.

## Hermetic proof

| Check | Result | Artifact |
| --- | --- | --- |
| `uv run pytest -q -m "not integration"` | **997 passed**, 3 skipped, 14 deselected (~199s) | [pytest-not-integration.txt](pytest-not-integration.txt) |
| `bash scripts/check_openapi_drift.sh` | OK | [openapi-drift.txt](openapi-drift.txt) |
| `uv run ruff format --check src tests` | OK (after G3 format hygiene) | [ruff-format.txt](ruff-format.txt) |
| `uv run ruff check src tests` | OK (after unused-import / F841 fixes) | [ruff-check.txt](ruff-check.txt) |
| `uv run basedpyright` | 0 errors | [basedpyright.txt](basedpyright.txt) |

Note: an earlier sandboxed pytest run produced false failures (git/SQLite/tmp cleanup permission noise). Re-run with unrestricted local permissions is authoritative.

## Placement note

```text
Concern: persistence | protocol | CI
Owning boundary: g3-platform merge of SD3 repositories/workers + SD4 HostService/v2 + SD5 verify/CI
Authoritative source: durable repositories (SD3); HostService + contracts/host (SD4); uv.lock + workflows (SD5)
Compatibility: host/v1 retained; host/v2 preferred; OpenAPI drift enforced in CI
Guardrail proof: docs/evidence/sustainable-development/g3/ (pytest hermetic + openapi + ruff + basedpyright)
Temporary exception: none for merge; SD5 scheduled Docker/backup live gates remain soft-skip without FORCE_SCHEDULED
```

## G3 exit claim

Joint platform gate is hermetically green on `sd/g3-platform`: recoverable storage paths (SD3), one mutation service + v2 contracts (SD4), frozen CI/locks with OpenAPI drift (SD5).
