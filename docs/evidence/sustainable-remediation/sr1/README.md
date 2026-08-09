# SR1 — Policy authority (G1 closed)

**Package:** SR1  
**Findings:** RF-02  
**Evidence level:** hermetically verified

## Placement note

```text
Concern: policy
Owning boundary: TaskPreparationService + planning.compiler + effective_policy + ValidationRepairService
Authoritative source: workflow_pack.execution_policy.capability_policies[task.capability]
Compatibility: empty intersection falls back to permitted grant classes for legacy packs
Guardrail proof: tests/unit/test_sr1_policy_authority.py
Temporary exception: none
```

## Commands

```text
uv run pytest -q tests/unit/test_sr1_policy_authority.py
```
