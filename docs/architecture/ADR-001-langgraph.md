# ADR-001 — Use LangGraph as the orchestration kernel

## Status

Accepted

## Decision

Adopt LangGraph Graph API for explicit stateful control flow, checkpointing, and interrupts.

## Rationale

Conditional routing, dynamic worker execution, persistence, interrupts, recoverability, and explicit graph semantics.

## Consequences

Domain logic and provider adapters remain framework-independent where practical. The `RunCoordinator` owns the MVP execution path; `orchestration/graph.py` provides the checkpointed graph skeleton.
