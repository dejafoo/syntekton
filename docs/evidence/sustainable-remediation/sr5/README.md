# SR5 — Dashboard, documentation, and release truth (first slice)

**Package:** SR5  
**Findings:** RF-06, RF-07, RF-08 (partial)  
**Evidence level:** implemented + hermetically verified (docs honesty, UI meta
helpers, verify soft-skip removal). **Not** integration-verified (Playwright)
or operationally proven (AMD / live scheduled drills / SBOM automation).

**Base commit:** see [`base_commit.txt`](base_commit.txt)

## Placement note

```text
Concern: verification | UI | documentation
Owning boundary: dashboard meta fetch; docs trackers; scripts/verify.sh; scheduled-recovery.yml
Authoritative source: /api/v1/meta dashboard bounds; evidence-level markers in current-state docs
Compatibility: none (honesty corrections; optional live gates remain opt-in)
Guardrail proof: tests/unit/test_sr5_product_release_truth.py; npm --prefix dashboard test
Temporary exception: Playwright matrix, SBOM/provenance automation, full architecture rewrite deferred
```

## Implemented (this pass)

| Slice | Change |
| --- | --- |
| SR5.A (partial) | Dashboard fetches `/meta` at startup; shows loopback guidance; treats `remote_mode=true` as unsupported remote browser use |
| SR5.A | Fixed `isUnsupportedRemoteDashboard` so advertised `loopback_monitor_only` alone is not treated as an active failure |
| SR5.B (partial) | Evidence-level / historical markers on architecture, observability, dashboard, SD trackers, and sustainable-development handover |
| SR5.C (partial) | SD4 browser checkbox reclassified; SD5/G3 no longer imply Playwright or required live soft-skips |
| SR5.D (partial) | Default `verify.sh` no longer soft-passes Docker/deploy/backup integration; scheduled workflow labels required vs optional |

## Deferred (explicitly not claimed)

| Item | Why deferred |
| --- | --- |
| Playwright matrix (empty DB, DAG/kanban agreement, SSE p95, capture policy, cross-run denial, …) | Needs real FastAPI + seeded SQLite browser process; SR5.A remainder |
| Dashboard componentization for isolated view tests | Follows Playwright harness |
| Full architecture / catalog / codebase-structure rewrite | SR5.B remainder |
| Wheel/plugin install smoke expansions beyond existing package_smoke | Already hermetic; further packaging is SR5.D |
| Container image digest recording | Release tooling |
| Release hashes / SBOM / build provenance automation | Process note exists under SD5 evidence; automation still deferred |
| Required live Docker restart / backup / worker drain | Optional until FORCE_SCHEDULED / env-owned schedule |
| AMD / G4 operational scorecards | SR6 |

## Commands

```text
uv run pytest -q tests/unit/test_sr5_product_release_truth.py
npm --prefix dashboard test -- --run
python -c "import yaml; yaml.safe_load(open('.github/workflows/scheduled-recovery.yml'))"
```

## Known limitations

- Meta detection is Vitest-covered and wired in the SPA; it is **not** a
  Playwright browser report.
- Optional scheduled jobs still exit 0 when gated off; they are labelled
  optional so they cannot be mistaken for required gate proof.
