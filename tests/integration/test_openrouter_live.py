"""Opt-in live OpenRouter integration test."""

from __future__ import annotations

import os

import pytest

from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.gateway.openai_compatible import OpenAICompatibleGateway
from product_factory.gateway.openrouter import OpenRouterGateway


@pytest.mark.integration
def test_live_structured_output() -> None:
    if os.environ.get("PRODUCT_FACTORY_LIVE") != "1":
        pytest.skip("Set PRODUCT_FACTORY_LIVE=1 to run")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    gw = OpenRouterGateway(
        profile_models={
            "fast_worker": {
                "model": "z-ai/glm-4.7-flash",
                "pricing": {"input": "0.06", "output": "0.40"},
            }
        }
    )
    resp = gw.complete(
        ModelRequest(
            request_id="live-1",
            run_id="live-run",
            task_id="t",
            session_id="pf:live:fast_worker:t",
            model_profile="fast_worker",
            messages=[
                CanonicalMessage(role="user", content='Return JSON {"ok": true}'),
            ],
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            max_output_tokens=100,
            max_cost_usd=0.05,
        )
    )
    assert resp.status in {"success", "invalid_output"}
    assert resp.provider == "openrouter"


@pytest.mark.integration
def test_openrouter_as_openai_compatible_local_standin() -> None:
    if os.environ.get("OPENROUTER_INTEGRATION") != "1":
        pytest.skip("Set OPENROUTER_INTEGRATION=1 to run")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    gateway = OpenAICompatibleGateway(
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        profile_models={
            "local_standin": {
                "model": "z-ai/glm-4.7-flash",
                "pricing": {"input": "0.06", "output": "0.40"},
            }
        },
    )

    probe = gateway.probe(model="z-ai/glm-4.7-flash")
    response = gateway.complete(
        ModelRequest(
            request_id="local-standin-1",
            run_id="live-run",
            task_id="t",
            session_id="pf:live:local-standin:t",
            model_profile="local_standin",
            messages=[CanonicalMessage(role="user", content="Reply with OK")],
            max_output_tokens=10,
            max_cost_usd=0.05,
        )
    )

    assert probe.healthy and probe.model_available
    assert response.provider == "openai_compatible"
