# ADR-001 — Use LangGraph as the orchestration kernel

## Status

**Superseded** (SD7, 2026-08-08)

## Decision (historical)

Adopt LangGraph Graph API for explicit stateful control flow, checkpointing, and interrupts.

## Rationale (historical)

Conditional routing, dynamic worker execution, persistence, interrupts, recoverability, and explicit graph semantics.

## Consequences (historical)

Domain logic and provider adapters remain framework-independent where practical. The `RunCoordinator` owns the MVP execution path; `orchestration/graph.py` provides the checkpointed graph skeleton.

## Supersession

Durable orchestration is owned by `RunLifecycleEngine` and related services behind the thin `RunCoordinator` façade. The LangGraph demo (`orchestration/graph.py`, `state.py`, empty `nodes/` / `subgraphs/`, CLI `--graph-demo`) and the `langgraph` / `langgraph-checkpoint-sqlite` / `aiosqlite` dependencies were removed in SD7 after replacement + absence verification. See `docs/evidence/sustainable-development/sd7/`.
