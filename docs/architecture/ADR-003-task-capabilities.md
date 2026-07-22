# ADR-003 — Dynamic typed tasks instead of generated agents

## Status

Accepted

## Decision

The planner generates `TaskSpec` objects assigned to registered capabilities; it must not create arbitrary agents, tools, or graph nodes.

## Rationale

Validation, security, scheduling, reproducibility, and simpler local deployment.
