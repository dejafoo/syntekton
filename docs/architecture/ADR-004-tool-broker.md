# ADR-004 — Tool broker as sole execution path

## Status

Accepted

## Decision

All tool calls pass through `ToolBroker`, which enforces grants, path scope, registered commands, and audit logging.

## Consequences

LLMs propose tool calls; local code executes them. Unregistered tools and commands are rejected.
