"""Gateway contract tests with mock adapter."""

from __future__ import annotations

from decimal import Decimal

import pytest

from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.gateway.errors import BudgetRejectedError
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.pricing import estimate_cost


def test_mock_structured_output() -> None:
    gw = MockGateway()
    resp = gw.complete(
        ModelRequest(
            request_id="r1",
            run_id="run1",
            task_id="t1",
            session_id="s1",
            model_profile="mock_local",
            messages=[CanonicalMessage(role="user", content="hi")],
            output_schema={"type": "object"},
        )
    )
    assert resp.status == "success"
    assert resp.structured_data is not None
    assert resp.provider == "mock"


def test_budget_ceiling_blocks() -> None:
    gw = MockGateway()
    with pytest.raises(BudgetRejectedError):
        gw.complete(
            ModelRequest(
                request_id="r1",
                run_id="run1",
                task_id="t1",
                session_id="s1",
                model_profile="mock_local",
                messages=[CanonicalMessage(role="user", content="hi")],
                max_cost_usd=0,
            )
        )


def test_custom_tool_calls() -> None:
    def responder(req):
        return {
            "status": "tool_calls",
            "tool_calls": [{"id": "1", "name": "list_files", "arguments": {"directory": "."}}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    gw = MockGateway(responder=responder)
    resp = gw.complete(
        ModelRequest(
            request_id="r1",
            run_id="run1",
            task_id="t1",
            session_id="s1",
            model_profile="mock_local",
            messages=[CanonicalMessage(role="user", content="hi")],
        )
    )
    assert resp.status == "tool_calls"
    assert resp.tool_calls[0].name == "list_files"


def test_estimate_cost_decimal() -> None:
    cost = estimate_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        input_price_per_million="0.11",
        output_price_per_million="0.80",
    )
    assert cost == Decimal("0.910000")


def test_catalog_refresh() -> None:
    gw = MockGateway()
    payload = gw.refresh_catalog()
    assert "models" in payload
