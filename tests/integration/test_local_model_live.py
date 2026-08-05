"""Opt-in live evaluation against a real local OpenAI-compatible endpoint (RF5).

Never required in hermetic unit CI. Enable with:

  PRODUCT_FACTORY_LOCAL_LIVE=1
  PRODUCT_FACTORY_LOCAL_BASE_URL=http://127.0.0.1:8000/v1
  PRODUCT_FACTORY_LOCAL_MODEL=<advertised model id>

Optional: PRODUCT_FACTORY_LOCAL_API_KEY_ENV=<env var name holding bearer token>
"""

from __future__ import annotations

import os

import pytest

from product_factory.gateway.admission import evaluate_admission
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.gateway.openai_compatible import OpenAICompatibleGateway
from product_factory.gateway.probes import LocalRouteController


@pytest.mark.integration
def test_live_local_openai_compatible_admission() -> None:
    if os.environ.get("PRODUCT_FACTORY_LOCAL_LIVE") != "1":
        pytest.skip("Set PRODUCT_FACTORY_LOCAL_LIVE=1 to run AMD/local live probes")
    base_url = os.environ.get("PRODUCT_FACTORY_LOCAL_BASE_URL", "").strip()
    model = os.environ.get("PRODUCT_FACTORY_LOCAL_MODEL", "").strip()
    if not base_url or not model:
        pytest.skip("PRODUCT_FACTORY_LOCAL_BASE_URL and PRODUCT_FACTORY_LOCAL_MODEL required")

    api_key_env = os.environ.get("PRODUCT_FACTORY_LOCAL_API_KEY_ENV") or None
    profile = {
        "provider_adapter": "openai_compatible",
        "route_class": "local",
        "model": model,
        "capabilities": ["implementation", "repair"],
        "structured_outputs": True,
        "tool_calling": True,
        "context_soft_limit": 8_000,
        "cloud_fallback": {
            "enabled": True,
            "profile": "coding_worker_cloud",
            "allowed_reasons": [
                "capability_miss",
                "local_unhealthy",
                "provider_error",
            ],
        },
        "pricing": {"input": "0", "output": "0"},
    }
    gateway = OpenAICompatibleGateway(
        base_url=base_url,
        api_key_env=api_key_env,
        profile_models={"coding_worker": profile},
    )
    controller = LocalRouteController(
        profile_name="coding_worker",
        profile=profile,
        gateway=gateway,
        enable_deep_probes=True,
    )
    report = controller.ensure_report(force_deep=True)
    decision = evaluate_admission(
        task_capabilities={"implementation"},
        proven=report.proven,
        primary_role="implementation",
    )
    assert report.healthy, report.as_dict()
    assert report.model_available, report.as_dict()
    assert decision.admitted, decision.as_dict()

    response = gateway.complete(
        ModelRequest(
            request_id="local-live-1",
            run_id="local-live",
            task_id="t",
            session_id="pf:live:local:coding_worker",
            model_profile="coding_worker",
            messages=[CanonicalMessage(role="user", content="Reply with OK")],
            max_output_tokens=16,
            max_cost_usd=0.05,
        )
    )
    assert response.provider == "openai_compatible"
    assert response.status in {"success", "tool_calls", "invalid_output"}
