"""PM4.D1 local/cloud gateway routing matrix."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

from product_factory.config.loader import load_config
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.gateway.errors import BudgetRejectedError, NonRetryableProviderError
from product_factory.gateway.factory import gateway_from_config
from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.openai_compatible import OpenAICompatibleGateway
from product_factory.gateway.router import RoutingGateway
from product_factory.observability.recorder import TelemetryRecorder


def _request(*, max_cost_usd: float | None = 1.0) -> ModelRequest:
    return ModelRequest(
        request_id="request-1",
        run_id="run-1",
        task_id="task-1",
        session_id="session-1",
        model_profile="local",
        messages=[CanonicalMessage(role="user", content="hello")],
        max_cost_usd=max_cost_usd,
    )


def _profiles(*, fallback_enabled: bool = True) -> dict[str, dict[str, object]]:
    return {
        "local": {
            "provider_adapter": "openai_compatible",
            "route_class": "local",
            "model": "local/model",
            "capabilities": ["implementation"],
            "cloud_fallback": {
                "enabled": fallback_enabled,
                "adapter": "openrouter",
                "allowed_reasons": ["capability_miss"],
            },
        }
    }


def test_local_route_success() -> None:
    local = MockGateway(catalog=[{"id": "local/model", "capabilities": ["implementation"]}])
    cloud = MockGateway(catalog=[{"id": "local/model"}])
    gateway = RoutingGateway(
        profiles=_profiles(),
        profile_gateways={"local": local},
        adapter_gateways={"openrouter": cloud},
    )

    response = gateway.complete(_request())

    assert len(local.calls) == 1
    assert cloud.calls == []
    assert response.routing["route"] == "local"
    assert response.routing["fallback_reason"] is None
    assert response.routing["cost_basis"] == "estimated"


def test_capability_miss_allows_cloud_fallback() -> None:
    local = MockGateway(catalog=[{"id": "different/model"}])
    cloud = MockGateway(catalog=[{"id": "local/model", "capabilities": ["implementation"]}])
    gateway = RoutingGateway(
        profiles=_profiles(),
        profile_gateways={"local": local},
        adapter_gateways={"openrouter": cloud},
    )

    response = gateway.complete(_request())

    assert local.calls == []
    assert len(cloud.calls) == 1
    assert response.routing["route"] == "cloud"
    assert response.routing["fallback_reason"] == "capability_miss"


def test_capability_miss_denies_unapproved_fallback() -> None:
    local = MockGateway(catalog=[{"id": "different/model"}])
    cloud = MockGateway(catalog=[{"id": "local/model"}])
    gateway = RoutingGateway(
        profiles=_profiles(fallback_enabled=False),
        profile_gateways={"local": local},
        adapter_gateways={"openrouter": cloud},
    )

    with pytest.raises(NonRetryableProviderError, match="fallback denied"):
        gateway.complete(_request())

    assert local.calls == []
    assert cloud.calls == []


def test_routing_budget_guard_rejects_before_probe_or_fallback() -> None:
    local = MockGateway(catalog=[{"id": "local/model"}])
    cloud = MockGateway(catalog=[{"id": "local/model"}])
    gateway = RoutingGateway(
        profiles=_profiles(),
        profile_gateways={"local": local},
        adapter_gateways={"openrouter": cloud},
    )

    with pytest.raises(BudgetRejectedError, match="routing budget guard"):
        gateway.complete(_request(max_cost_usd=0))

    assert local.calls == []
    assert cloud.calls == []


def test_forced_mock_construction_is_unchanged() -> None:
    config = load_config()
    coding = config.models.profiles["coding_worker"]
    gateway = gateway_from_config(config, force_mock=True)

    assert coding.route_class == "local"
    assert coding.provider_adapter == "openai_compatible"
    assert coding.base_url == "https://openrouter.ai/api/v1"
    assert coding.api_key_env == "OPENROUTER_API_KEY"
    assert coding.cloud_fallback.profile == "coding_worker_cloud"
    assert coding.cloud_fallback.adapter is None
    assert set(coding.cloud_fallback.allowed_reasons) == {
        "capability_miss",
        "local_unhealthy",
        "provider_error",
    }
    assert isinstance(gateway, MockGateway)
    assert gateway.complete(_request()).provider == "mock"


def test_configured_construction_builds_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    gateway = gateway_from_config(load_config())

    assert isinstance(gateway, RoutingGateway)
    assert isinstance(
        gateway.profile_gateways["coding_worker"],
        OpenAICompatibleGateway,
    )


def test_openai_compatible_completion_and_probe() -> None:
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "local/model",
                            "capabilities": ["implementation"],
                        }
                    ]
                },
            )
        assert request.url.path == "/v1/chat/completions"
        seen_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "local/model",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:8000/v1",
    )
    gateway = OpenAICompatibleGateway(
        base_url="http://127.0.0.1:8000/v1",
        profile_models={
            "local": {
                "model": "local/model",
                "pricing": {"input": "0", "output": "0"},
            }
        },
        client=client,
    )

    probe = gateway.probe(
        model="local/model",
        required_capabilities={"implementation"},
    )
    response = gateway.complete(_request())

    assert probe.supports({"implementation"})
    assert response.provider == "openai_compatible"
    assert seen_payload["model"] == "local/model"
    assert "provider" not in seen_payload


def test_instrumentation_emits_route_dimensions() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        def ensure_trace(self, run_id: str) -> tuple[str, str]:
            return "trace", "parent"

        def emit(self, **event: Any) -> None:
            self.events.append(event)

    local = MockGateway(catalog=[{"id": "local/model", "capabilities": ["implementation"]}])
    router = RoutingGateway(
        profiles=_profiles(),
        profile_gateways={"local": local},
        adapter_gateways={"openrouter": MockGateway()},
    )
    recorder = Recorder()
    gateway = InstrumentedModelGateway(
        router,
        recorder=cast(TelemetryRecorder, recorder),
    )

    gateway.complete(_request())

    completed = next(
        event for event in recorder.events if event["event_type"] == "model.request.completed"
    )
    assert (
        completed["payload"]
        | {
            "route": "local",
            "provider": "mock",
            "model": "mock/local-model",
            "fallback_reason": None,
            "cost_basis": "estimated",
        }
        == completed["payload"]
    )
