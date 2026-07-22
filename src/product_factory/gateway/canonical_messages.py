"""Gateway canonical message and request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.usage import UsageMetrics


class CanonicalToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class CanonicalMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[CanonicalToolCall] = Field(default_factory=list)


class CanonicalToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderPreferences(BaseModel):
    allow_fallbacks: bool = True
    require_parameters: bool = True
    data_collection: Literal["allow", "deny"] = "deny"
    sort: Literal["price", "throughput", "latency"] | None = "price"
    order: list[str] = Field(default_factory=list)
    zdr: bool | None = None


class ModelRequest(BaseModel):
    request_id: str
    run_id: str
    task_id: str
    session_id: str
    model_profile: str
    messages: list[CanonicalMessage]
    output_schema: dict[str, Any] | None = None
    tools: list[CanonicalToolDefinition] = Field(default_factory=list)
    temperature: float | None = None
    max_output_tokens: int = 4096
    reasoning_effort: str | None = None
    provider_preferences: ProviderPreferences = Field(default_factory=ProviderPreferences)
    timeout_seconds: int = 120
    seed: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    max_cost_usd: float | None = None


class ModelResponse(BaseModel):
    request_id: str
    provider: str
    provider_model_id: str
    resolved_model_id: str
    status: Literal[
        "success",
        "tool_calls",
        "refused",
        "invalid_output",
        "timeout",
        "rate_limited",
        "provider_error",
        "budget_rejected",
    ]
    text: str | None = None
    structured_data: dict[str, Any] | None = None
    tool_calls: list[CanonicalToolCall] = Field(default_factory=list)
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    latency_ms: int = 0
    finish_reason: str | None = None
    response_hash: str = ""
    raw_response_ref: str = ""
