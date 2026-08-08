# SD8 — Measured performance scaffolding (evidence)

**Branch:** `sd/sd7-sd8-simplify`  
**Base:** `sd/sd6-evaluation` @ `fc9338faedef5f9ab9b3fbfe503b4ea2df19c13d`

## Honesty

**Baselines recorded; tuning deferred pending G4 operational proof.**

SD8 exit for this branch is scaffolding + hermetic synthetic baselines only.
No production optimization wins are claimed. AMD concurrency/memory tuning and
SD6 arm non-regression for accepted optimizations remain future work.

## What landed

| Deliverable | Location |
| --- | --- |
| Stage measurement session + glossary + correlation ids | `product_factory.observability.performance` |
| Synthetic small/medium baseline harness | `tests/unit/test_sd8_performance.py` |
| Safe-inventory cache keyed by snapshot + policy digest | `SafeInventoryCache` in `context/safe_inventory.py` |
| Cache invalidation safety tests | `tests/unit/test_sd8_performance.py` |
| Sample baseline artifact | [baselines/synthetic-small-medium.json](baselines/synthetic-small-medium.json) |

## Placement note

```text
Concern: persistence | policy
Owning boundary: observability/performance, context/safe_inventory
Authoritative source: hermetic MeasurementSession payloads; inventory cache keys = snapshot+policy digest
Compatibility: cache is opt-in helper; default build_safe_repository_inventory path unchanged
Guardrail proof: tests/unit/test_sd8_performance.py
Temporary exception: none; AMD tuning and SD6 operational non-regression deferred with G4
```
