# SR7 — Removal and measured optimization (first slice)

**Package:** SR7  
**Findings:** residual compatibility debt (inventory); optimization deferred  
**Evidence level:** hermetically verified (inventory + unit guardrail only)  
**Gate note:** Full SR7.A/SR7.B removals and measured tuning remain post-G5.
This slice only publishes the compatibility inventory and proves it is present.

## Placement note

```text
Concern: verification
Owning boundary: docs/architecture/sr7-compatibility-inventory.md
Authoritative source: inventory table (owner, removal condition, evidence needed)
Compatibility: none removed; host/v1 explicitly retained
Guardrail proof: tests/unit/test_sr7_compatibility_inventory.py
Temporary exception: none; LangGraph/demo already removed under SD7
```

## Implemented (this pass)

| Slice | Change |
| --- | --- |
| SR7.A (inventory) | `docs/architecture/sr7-compatibility-inventory.md` lists remaining surfaces including host/v1, workflow aliases, RunCoordinator persistence access, temporary lifecycle delegates, eval dual-write, JSONL demotion, connector aliases, and doc debt |
| SR7.A (no-op removal) | No new dependency/demo removal — LangGraph/demo already absent (SD7); no other obviously dead flagged import was safe to cut |
| SR7.B (honesty) | Inventory references SD8 baselines as **baseline-only**; no performance tuning claims |

## Performance honesty

Do not treat this package as an optimization win. SD8 synthetic baselines under
`docs/evidence/sustainable-development/sd8/` remain the measurement reference
until SR6/G5 and before/after SR7.B evidence exist.

## Commands

```text
uv run pytest -q tests/unit/test_sr7_compatibility_inventory.py
```

Archived: [`pytest-sr7-focused.txt`](pytest-sr7-focused.txt), tip in [`base_commit.txt`](base_commit.txt).

## Known limitations

- host/v1 is intentionally not removed.
- Removals listed in the inventory still require replacement, usage/telemetry,
  and compatibility tests before any delete.
- Measured hotspot tuning (SR7.B) is not started.
