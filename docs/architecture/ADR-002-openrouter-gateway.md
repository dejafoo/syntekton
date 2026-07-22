# ADR-002 — Use OpenRouter through a provider-neutral gateway

## Status

Accepted

## Decision

OpenRouter is the initial inference provider, accessed only through `ModelGateway`.

## Rationale

Common API across models, structured outputs, tool calling, usage metadata, and easy comparison across families.

## Consequences

OpenRouter-specific objects remain inside `gateway/openrouter.py`. A `MockGateway` proves local OpenAI-compatible portability without graph changes.
