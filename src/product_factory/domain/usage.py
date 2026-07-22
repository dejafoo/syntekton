"""Usage and cost metrics."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from product_factory.domain.budgets import parse_decimal


class UsageMetrics(BaseModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_cost_usd: Decimal = Field(default=Decimal("0"))
    reported_cost_usd: Decimal | None = None
    latency_ms: int = 0
    time_to_first_token_ms: int | None = None
    retries: int = 0

    @field_validator("estimated_cost_usd", "reported_cost_usd", mode="before")
    @classmethod
    def _coerce_cost(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        return parse_decimal(v)  # type: ignore[arg-type]

    def merge(self, other: UsageMetrics) -> UsageMetrics:
        reported: Decimal | None
        if self.reported_cost_usd is None and other.reported_cost_usd is None:
            reported = None
        else:
            reported = (self.reported_cost_usd or Decimal("0")) + (
                other.reported_cost_usd or Decimal("0")
            )
        return UsageMetrics(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            estimated_cost_usd=self.estimated_cost_usd + other.estimated_cost_usd,
            reported_cost_usd=reported,
            latency_ms=self.latency_ms + other.latency_ms,
            retries=self.retries + other.retries,
        )
