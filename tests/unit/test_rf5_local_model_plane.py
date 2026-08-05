"""RF5 local-first model plane: probes, circuit breaker, named fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from product_factory.domain.errors import ProviderError
from product_factory.gateway.admission import evaluate_admission
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.gateway.circuit_breaker import CircuitBreaker
from product_factory.gateway.errors import NonRetryableProviderError
from product_factory.gateway.evidence import LocalRouteEvidenceStore
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.openai_compatible import OpenAICompatibleGateway
from product_factory.gateway.probes import LocalRouteController
from product_factory.gateway.router import RoutingGateway


def _request(profile: str = "local") -> ModelRequest:
    return ModelRequest(
        request_id="request-1",
        run_id="run-1",
        task_id="task-1",
        session_id="session-1",
        model_profile=profile,
        messages=[CanonicalMessage(role="user", content="hello")],
        max_cost_usd=1.0,
    )


def _local_profiles(*, fallback_profile: str = "cloud_impl") -> dict[str, dict[str, Any]]:
    return {
        "local": {
            "provider_adapter": "openai_compatible",
            "route_class": "local",
            "model": "local/model",
            "capabilities": ["implementation", "repair"],
            "structured_outputs": True,
            "tool_calling": True,
            "context_soft_limit": 8_000,
            "cloud_fallback": {
                "enabled": True,
                "profile": fallback_profile,
                "allowed_reasons": [
                    "capability_miss",
                    "local_unhealthy",
                    "provider_error",
                ],
            },
            "pricing": {"input": "0", "output": "0"},
        },
        "cloud_impl": {
            "provider_adapter": "openrouter",
            "route_class": "cloud",
            "model": "cloud/model",
            "capabilities": ["implementation", "repair"],
            "pricing": {"input": "0.1", "output": "0.2"},
        },
    }


def test_circuit_breaker_opens_and_recovers() -> None:
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_s=10.0,
        clock=now,
    )
    assert breaker.allow_request()
    breaker.record_failure()
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open"
    assert not breaker.allow_request()

    clock["t"] = 11.0
    assert breaker.allow_request()
    assert breaker.state == "half_open"
    breaker.record_success()
    assert breaker.state == "closed"


def test_admission_requires_proven_protocol_capabilities() -> None:
    denied = evaluate_admission(
        task_capabilities={"implementation"},
        proven={"reachability", "model_identity"},
        primary_role="implementation",
    )
    assert not denied.admitted
    assert "tool_calling" in denied.missing

    admitted = evaluate_admission(
        task_capabilities={"implementation"},
        proven={"reachability", "model_identity", "tool_calling", "latency"},
        primary_role="implementation",
    )
    assert admitted.admitted


def test_named_cloud_fallback_identity(tmp_path: Path) -> None:
    local = MockGateway(
        catalog=[
            {
                "id": "local/model",
                "capabilities": ["implementation"],
                "context_length": 16_000,
            }
        ]
    )
    cloud = MockGateway(
        catalog=[{"id": "cloud/model", "capabilities": ["implementation"]}],
        default_model="cloud/model",
    )
    store = LocalRouteEvidenceStore(tmp_path)
    controller = LocalRouteController(
        profile_name="local",
        profile=_local_profiles()["local"],
        gateway=local,
        enable_deep_probes=True,
        evidence_sink=store.record,
    )
    gateway = RoutingGateway(
        profiles=_local_profiles(),
        profile_gateways={"local": local, "cloud_impl": cloud},
        adapter_gateways={},
        route_controllers={"local": controller},
    )

    response = gateway.complete(_request())

    assert response.routing["route"] == "local"
    assert response.routing["primary_profile"] == "local"
    assert response.routing["fallback_profile"] is None
    evidence = store.read("local")
    assert evidence is not None
    assert evidence["schema_version"] == "local_route_admission.v1"
    assert "tool_calling" in evidence["report"]["proven"]


def test_circuit_open_skips_local_and_uses_named_fallback() -> None:
    class FlakyLocal(MockGateway):
        def complete(self, request: ModelRequest):  # type: ignore[override]
            if request.run_id == "local-route-probe":
                return super().complete(request)
            raise ProviderError("local down")

    local = FlakyLocal(
        catalog=[
            {
                "id": "local/model",
                "context_length": 16_000,
            }
        ]
    )
    cloud = MockGateway(
        catalog=[{"id": "cloud/model"}],
        default_model="cloud/model",
    )
    controller = LocalRouteController(
        profile_name="local",
        profile=_local_profiles()["local"],
        gateway=local,
        breaker=CircuitBreaker(failure_threshold=1, recovery_timeout_s=60.0),
        enable_deep_probes=True,
    )
    gateway = RoutingGateway(
        profiles=_local_profiles(),
        profile_gateways={"local": local, "cloud_impl": cloud},
        adapter_gateways={},
        route_controllers={"local": controller},
    )

    first = gateway.complete(_request())
    assert first.routing["route"] == "cloud"
    assert first.routing["fallback_reason"] == "provider_error"
    assert first.routing["fallback_profile"] == "cloud_impl"
    assert first.routing["primary_profile"] == "local"
    assert controller.breaker.state == "open"

    calls_before = len(local.calls)
    second = gateway.complete(_request())
    assert second.routing["route"] == "cloud"
    assert second.routing["fallback_reason"] == "local_unhealthy"
    assert len(local.calls) == calls_before


def test_fallback_denied_when_circuit_open_and_reason_not_allowed() -> None:
    local = MockGateway(catalog=[])
    cloud = MockGateway(catalog=[{"id": "cloud/model"}])
    profiles = _local_profiles()
    profiles["local"]["cloud_fallback"] = {
        "enabled": True,
        "profile": "cloud_impl",
        "allowed_reasons": ["capability_miss"],
    }
    controller = LocalRouteController(
        profile_name="local",
        profile=profiles["local"],
        gateway=local,
        breaker=CircuitBreaker(failure_threshold=1),
        enable_deep_probes=False,
    )
    # Force open circuit without a permitted fallback reason.
    controller.breaker.record_failure()
    gateway = RoutingGateway(
        profiles=profiles,
        profile_gateways={"local": local, "cloud_impl": cloud},
        adapter_gateways={},
        route_controllers={"local": controller},
    )

    with pytest.raises(NonRetryableProviderError, match="fallback denied"):
        gateway.complete(_request())


def test_deep_probe_false_positive_guard_with_fake_server() -> None:
    """Missing capability advertisements must not count as protocol proof."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "local/model"}]},  # no capabilities field
            )
        payload = json.loads(request.content)
        if payload.get("response_format"):
            return httpx.Response(
                200,
                json={
                    "model": "local/model",
                    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        if payload.get("tools"):
            return httpx.Response(
                200,
                json={
                    "model": "local/model",
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "echo",
                                            "arguments": '{"message":"ping"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "local/model",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:9/v1",
    )
    gateway = OpenAICompatibleGateway(
        base_url="http://127.0.0.1:9/v1",
        profile_models={
            "local": {"model": "local/model", "pricing": {"input": "0", "output": "0"}}
        },
        client=client,
    )
    # Catalog-only probe must not invent capabilities.
    light = gateway.probe(model="local/model", required_capabilities={"implementation"})
    assert light.model_available
    assert not light.supports({"implementation"})

    controller = LocalRouteController(
        profile_name="local",
        profile=_local_profiles()["local"],
        gateway=gateway,
        enable_deep_probes=True,
    )
    reason = controller.evaluate(task_role="implementation")
    assert reason is None
    assert "structured_outputs" in controller.last_report.proven  # type: ignore[union-attr]
    assert "tool_calling" in controller.last_report.proven  # type: ignore[union-attr]
