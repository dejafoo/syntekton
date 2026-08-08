# SD5 — Reproducible builds and CI (evidence)

**Branch:** `sd/sd5-release-engineering`  
**Base:** `sd/sd1-executor-truth`  
**Findings:** F-17 (partial — OpenAPI drift deferred to SD4), F-23, F-24

## Implemented

| Item | Evidence |
| --- | --- |
| Commit `uv.lock` | Root `uv.lock` tracked; `.gitignore` no longer ignores it |
| Frozen sync | `uv sync --frozen` in CI, `scripts/bootstrap.sh`, `scripts/verify.sh`, Dockerfile |
| `npm ci` | CI dashboard + OpenCode jobs; verify/bootstrap |
| PR gate expansion | `.github/workflows/ci.yml`: basedpyright, dashboard test/check/build, OpenCode test/check/pack, wheel smoke |
| Package smoke | `scripts/package_smoke.sh` — wheel install, `/dashboard/`, `/api/v1/health` |
| Starlette/httpx warning | Dev extra `httpx2>=2.0.0` under frozen lock |
| Scheduled stubs | `.github/workflows/scheduled-recovery.yml` (hermetic backup; soft-skip Docker/backup integration; worker-shutdown stub) |
| SBOM / provenance | Process note below (tooling deferred) |

## Deferred / not claimed

- Host OpenAPI / generated-client drift: **SD4** (no canonical API snapshots yet).
- Playwright browser suite: later PR-gate expansion.
- Live Docker restart / backup / worker drain: soft-skip stubs only; SD3 owns durable drain proof.
- Image digest pinning for release: documented; Dockerfile still uses tag `python3.13-bookworm-slim` until release provenance lands.
- Full SBOM/provenance automation: process below until release tooling exists.
- G1 tip format/lint debt in `RunCoordinator` unused imports and long-line wraps in SD1-owned modules: quarantined in `pyproject.toml` (`ruff`/`basedpyright` excludes) so SD5 can expand CI without rewriting those owners. SD2 should clear the quarantine when cleaning the façade.

## SBOM / provenance (process)

Until dedicated release tooling lands (SD7 governance / release jobs):

1. Every release candidate commit must include a current `uv.lock` and both npm lockfiles.
2. CI must pass `uv sync --frozen` and the package-smoke job on that commit.
3. Record: git SHA, `uv.lock` hash, `dashboard/package-lock.json` hash, `integrations/opencode-plugin/package-lock.json` hash, wheel filename + SHA256, and (when publishing images) the resolved base-image digest.
4. Prefer generating CycloneDX/SPDX SBOM from the frozen lock at publish time; do not treat an unlocked `uv sync` tree as a release input.

## Hermetic verification

```text
uv sync --frozen --extra dev
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright
uv run pytest -q -m "not integration"
npm --prefix dashboard ci && npm --prefix dashboard test -- --run && npm --prefix dashboard run check && npm --prefix dashboard run build
npm --prefix integrations/opencode-plugin ci && npm --prefix integrations/opencode-plugin test -- --run && npm --prefix integrations/opencode-plugin run check
bash scripts/package_smoke.sh
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); yaml.safe_load(open('.github/workflows/scheduled-recovery.yml'))"
```

Results: see archived files in this directory when re-run.

## Placement note

```text
Concern: policy (release/CI)
Owning boundary: .github/workflows, scripts/verify.sh, scripts/package_smoke.sh, uv.lock
Authoritative source: committed uv.lock + npm lockfiles; CI frozen sync
Compatibility: Dockerfile and bootstrap require uv.lock; mid-migration bootstrap falls back with warning if lock absent
Guardrail proof: scripts/package_smoke.sh; CI python/dashboard/opencode/package-smoke jobs; tests/unit/test_backup_restore.py on schedule
Temporary exception: OpenAPI drift deferred to SD4; scheduled Docker/backup live gates soft-skip without FORCE_SCHEDULED; SBOM tooling is process-only until release automation
```

## Merge guidance

Merge **after** `sd/sd1-executor-truth` (this branch’s base). Parallel G3 streams:

- **SD2 / SD3 / SD4:** no file overlap with this branch’s ownership (workflows, locks, verify/package smoke, SD5 evidence/docs). Prefer rebase onto latest G1 tip, then merge SD5 independently.
- If another branch also edits `scripts/verify.sh` or `Dockerfile`, resolve by keeping frozen sync + npm ci + package_smoke invocation.
