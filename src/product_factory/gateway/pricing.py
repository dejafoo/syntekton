"""Pricing helpers for cost estimation."""

from __future__ import annotations

from decimal import Decimal

from product_factory.domain.budgets import parse_decimal


def estimate_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price_per_million: Decimal | float | str,
    output_price_per_million: Decimal | float | str,
    cached_input_tokens: int = 0,
    cached_input_price_per_million: Decimal | float | str | None = None,
) -> Decimal:
    inp = parse_decimal(input_price_per_million)
    out = parse_decimal(output_price_per_million)
    cost = (Decimal(input_tokens) / Decimal(1_000_000)) * inp
    cost += (Decimal(output_tokens) / Decimal(1_000_000)) * out
    if cached_input_tokens and cached_input_price_per_million is not None:
        cached = parse_decimal(cached_input_price_per_million)
        cost += (Decimal(cached_input_tokens) / Decimal(1_000_000)) * cached
    return cost.quantize(Decimal("0.000001"))
